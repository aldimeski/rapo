"""Contains the job scheduler: it starts the jobs on their schedules.

It works as the scheduler of the controls (`rapo/core/scheduler.py`) and is
independent of it: its own lease (the row `RAPO.JOBS` of `rapo_scheduler`),
its own stop and start switch, its own options (`[JOBS]`) and its own events
(`rapo_job_event`). The server holding the lease computes the next fire of
every scheduled job and sleeps until the earliest one, or until something
changes. Every fire between two wake-ups is served; a fire found more than a
minute late (the host was suspended, the scheduler was stopped) is recorded
as missed, not run. A fire goes to the run manager with its exact time.
"""

import datetime as dt
import os
import threading as th
import time
import uuid

from .. import options
from ..core import schedule
from ..core.lease import Lease, JOBS_ROW, RENEWED, LOST
from ..core.runner import get_runner_name
from ..logger import logger

from . import store, workflow_store
from .manager import manager as default_manager, MISSED, FAILED
from .workflow_manager import manager as default_workflows

LEASE_INTERVAL = 10
CONFIG_CHECK_INTERVAL = 5
# Fires found later than this after their time are recorded as missed.
LATE_TOLERANCE = 60
MISSED_LIMIT = 100


def setting(name):
    """Get a [JOBS] option."""
    return options.get('JOBS', name)


def is_enabled():
    """Tell whether this server should run the job scheduler: the jobs are
    switched on, and the scheduler is wanted here at all.

    RAPO_SCHEDULER (0/1) overrides `[SCHEDULER] enabled` as for the
    scheduler of the controls, which is how `rapo-server start dev` keeps
    both off by default."""
    if setting('enabled') is False:
        return False
    override = os.environ.get('RAPO_SCHEDULER')
    if override is not None:
        return override.strip().lower() in ('1', 'true', 'y', 'yes')
    return options.get('SCHEDULER', 'enabled') is not False


def _next_multiple(interval):
    """Get the next epoch time that is a multiple of the interval."""
    if not interval:
        return None
    return (int(time.time()) // interval + 1) * interval


class JobScheduler:
    """Represents the scheduler of the jobs running inside the web server.

    Attributes
    ----------
    leader : bool
        Whether this server currently holds the lease.
    schedule : dict
        The schedules of the jobs that are on, by job ID.
    workflow_schedule : dict
        The schedules of the workflows that are on, by workflow ID.
    """

    def __init__(self, manager=None, workflows=None):
        self.manager = manager or default_manager
        self.workflows = workflows or default_workflows
        self.instance_id = f'{get_runner_name()}:{os.getpid()}:' \
                           f'{uuid.uuid4().hex[:8]}:jobs'
        self.lease = Lease(JOBS_ROW, self.instance_id,
                           lambda: setting('lease_timeout'))
        self.leader_since = None
        self.active = False
        self.thread = None
        self.wake = th.Event()
        self.reload = True
        self.schedule = {}
        self.workflow_schedule = {}
        self.cursor = None
        self.signature = None
        self.loaded = 0
        self.checked = 0
        self.heartbeat = 0
        self.next_maintenance = None
        self.last_fire = None
        self.listeners = []

    @property
    def enabled(self):
        """Whether this server runs the job scheduler at all."""
        return is_enabled()

    @property
    def leader(self):
        """Whether this server holds the lease."""
        return self.lease.leader

    def start(self):
        """Start the scheduler thread, unless it is off here."""
        if not self.enabled or self.active:
            return
        self.active = True
        self.thread = th.Thread(name='Job-Scheduler', target=self._run,
                                daemon=True)
        self.thread.start()
        logger.info(f'Job scheduler started as {self.instance_id}')

    def stop(self):
        """Stop the scheduler thread and give the lease up."""
        if not self.active:
            return
        self.active = False
        self.wake.set()
        if self.thread:
            self.thread.join(15)
        self._release()
        logger.info('Job scheduler stopped')

    def sync(self):
        """Follow [JOBS] enabled and the scheduler options after rapo.ini
        was reloaded."""
        if self.enabled and not self.active:
            self.start()
        elif not self.enabled and self.active:
            logger.info('Job scheduler switched off: stopping it')
            self.stop()

    def disable(self):
        """Stop scheduling the jobs on all servers until enabled again."""
        self._set_disabled(True)
        self._release()
        self.wake.set()
        self._notify()

    def enable(self):
        """Resume scheduling from now on, without recording missed fires."""
        self._set_disabled(False)
        self.wake.set()
        self._notify()

    def refresh(self):
        """Request a reload of the schedules of the jobs."""
        self.reload = True
        self.wake.set()

    def status(self):
        """Get the status report of the job scheduler."""
        record = self.lease.read() or {}
        disabled = record.get('disabled') == 'Y'
        alive = bool(record) and self.lease.alive(record)
        if not self.enabled:
            state = 'off'
        elif disabled:
            state = 'stopped'
        elif self.leader:
            state = 'running'
        elif alive:
            state = 'standby'
        else:
            state = 'starting'
        return {
            'state': state,
            'enabled': self.enabled,
            'disabled': disabled,
            'leader': self.leader,
            'instance_id': self.instance_id,
            'holder': {
                'instance_id': record.get('instance_id'),
                'server': record.get('server'),
                'username': record.get('username'),
                'pid': record.get('pid'),
                'start_date': record.get('start_date'),
                'stop_date': record.get('stop_date'),
                'heartbeat': record.get('heartbeat'),
                'alive': alive,
            },
            'scheduled_jobs': len(self.schedule) if self.leader else None,
            'scheduled_workflows': len(self.workflow_schedule)
            if self.leader else None,
            'last_fire': self.last_fire,
            'next_maintenance': (dt.datetime.fromtimestamp(
                self.next_maintenance).replace(microsecond=0)
                if self.leader and self.next_maintenance else None),
            'maintenance_interval': setting('maintenance_interval'),
            'lease_timeout': setting('lease_timeout'),
            'manager': self.manager.status(),
            'workflows': self.workflows.status(),
            'server_time': dt.datetime.now().replace(microsecond=0),
        }

    # The thread

    def _run(self):
        while self.active:
            try:
                self._step()
            except Exception:
                logger.error()
                self.wake.wait(LEASE_INTERVAL)
        logger.debug('Job scheduler thread finished')

    def _step(self):
        self.wake.clear()
        now = time.time()
        if now - self.heartbeat >= LEASE_INTERVAL or not self.leader:
            self._lease()
        if not self.leader:
            self.wake.wait(LEASE_INTERVAL)
            return
        self._load(now)
        self._fire()
        if not self.leader:
            return
        self._maintain(now)
        self.wake.wait(self._delay())

    def _lease(self):
        """Acquire or renew the lease."""
        self.heartbeat = time.time()
        result = self.lease.renew_or_acquire()
        if result is None or result[0] == RENEWED:
            return
        if result[0] == LOST:
            logger.warning('Job scheduler lease lost or scheduler stopped')
            self._release()
            return
        since = result[1]
        self.leader_since = dt.datetime.now()
        self.reload = True
        self.cursor = self.leader_since.replace(microsecond=0)
        self._record_missed(since, self.cursor)
        self.next_maintenance = _next_multiple(
            setting('maintenance_interval'))
        logger.info(f'Job scheduler lease acquired by {self.instance_id}')
        self._notify()

    def _release(self):
        if not self.leader:
            return
        self.leader_since = None
        self.schedule = {}
        self.workflow_schedule = {}
        self.lease.release()
        logger.info('Job scheduler lease released')
        self._notify()

    def _set_disabled(self, disabled):
        self.lease.set_disabled(disabled)
        logger.info(f'Job scheduler {"stopped" if disabled else "started"} '
                    'from the UI')

    def _load(self, now):
        """Reload the schedules when a job changed."""
        if not self.reload and now - self.checked >= CONFIG_CHECK_INTERVAL:
            self.checked = now
            if self._signature() != self.signature:
                self.reload = True
        if not self.reload and now - self.loaded < \
                setting('refresh_interval'):
            return
        # Read before the schedules, so that a change committed while they
        # are read is seen again by the next check.
        signature = self._signature()
        self.schedule = store.read_scheduled()
        self.workflow_schedule = self._read_workflows()
        self.signature = signature
        self.loaded = self.checked = now
        self.reload = False
        logger.debug(f'Job schedule: {len(self.schedule)} jobs and '
                     f'{len(self.workflow_schedule)} workflows found')

    def _signature(self):
        """What changes when a job, a workflow or a schedule does."""
        return store.signature(), self._workflow_signature()

    def _workflow_signature(self):
        try:
            return workflow_store.signature()
        except store.StoreError:
            self._tables_missing()
            return None

    def _read_workflows(self):
        try:
            return workflow_store.read_scheduled()
        except store.StoreError:
            self._tables_missing()
            return {}

    def _tables_missing(self):
        """Tell once that the tables of the workflows are missing: the
        jobs go on without them."""
        if not getattr(self, 'warned_tables', False):
            self.warned_tables = True
            logger.warning('The workflow tables are missing: run '
                           'migrations/v0.10.0/upgrade.sql. The workflows '
                           'are not scheduled.')

    def _fire(self):
        """Submit every fire between the cursor and now."""
        now = dt.datetime.now().replace(microsecond=0)
        if now <= self.cursor:
            return
        border = now + dt.timedelta(seconds=1)
        fires = []
        for job_id, item in self.schedule.items():
            for moment in schedule.next_fire(item['schedule'], self.cursor,
                                             limit=MISSED_LIMIT,
                                             before=border):
                fires.append((moment, 'job', job_id, item['job_name']))
        for workflow_id, item in self.workflow_schedule.items():
            for moment in schedule.next_fire(item['schedule'], self.cursor,
                                             limit=MISSED_LIMIT,
                                             before=border):
                fires.append((moment, 'workflow', workflow_id,
                              item['workflow_name']))
        self.cursor = now
        for moment, kind, item_id, name in sorted(fires):
            # A pass that took longer than the lease renews it here, so a
            # server that lost it while busy does not submit fires the new
            # holder has already recorded as missed.
            if time.time() - self.heartbeat >= LEASE_INTERVAL:
                self._lease()
            if not self.leader:
                logger.warning('Job scheduler lease lost, remaining fires '
                               'of this pass abandoned')
                break
            late = (now - moment).total_seconds()
            workflow_id = item_id if kind == 'workflow' else None
            job_id = None if kind == 'workflow' else item_id
            if late > LATE_TOLERANCE:
                self._record(job_id, MISSED, moment,
                             f'Fire found {int(late)} seconds late, '
                             'scheduler was not running.', workflow_id)
                continue
            try:
                (self.workflows if kind == 'workflow'
                 else self.manager).submit(name, store.SCHEDULE,
                                           scheduled_time=moment)
            except Exception as error:
                if not isinstance(error, store.StoreError):
                    logger.error()
                else:
                    logger.warning(f'The {kind} {name} fire of {moment} not '
                                   f'run: {error}')
                self._record(job_id, FAILED, moment, str(error), workflow_id)
            self.last_fire = moment
        if fires:
            self._notify()

    def _record(self, job_id, event_type, moment, message,
                workflow_id=None):
        try:
            store.add_event(job_id, store.SCHEDULE, event_type,
                            scheduled_time=moment, message=message,
                            runner=self.manager.name,
                            workflow_id=workflow_id)
        except Exception:
            logger.error()

    def _record_missed(self, since, until):
        """Record fires that fell into the downtime of the scheduler."""
        if since is None:
            return
        since = max(since, until - dt.timedelta(
            hours=setting('missed_window_hours')))
        if since >= until:
            return
        fires = []
        for job_id, item in store.read_scheduled().items():
            for moment in schedule.next_fire(item['schedule'], since,
                                             limit=MISSED_LIMIT,
                                             before=until):
                fires.append((moment, job_id, None))
        for workflow_id, item in self._read_workflows().items():
            for moment in schedule.next_fire(item['schedule'], since,
                                             limit=MISSED_LIMIT,
                                             before=until):
                fires.append((moment, None, workflow_id))
        fires = sorted(fires, key=lambda item: (item[0], item[1] or 0,
                                                item[2] or 0))[-MISSED_LIMIT:]
        for moment, job_id, workflow_id in fires:
            self._record(job_id, MISSED, moment,
                         'Job scheduler was not running.', workflow_id)
        if fires:
            logger.warning(f'{len(fires)} missed job fires recorded since '
                           f'{since:%Y-%m-%d %H:%M:%S}')

    def _maintain(self, now):
        if self.next_maintenance and now >= self.next_maintenance:
            self.next_maintenance = _next_multiple(
                setting('maintenance_interval'))
            th.Thread(name='Job-Maintainer', target=self._clean,
                      daemon=True).start()

    def _clean(self):
        logger.info('Starting job maintenance')
        try:
            days = setting('history_retention_days')
            deleted = store.clean_runs(days)
            logger.info(f'{deleted} job runs older than {days} days deleted')
            try:
                deleted = workflow_store.clean_runs(days)
                logger.info(f'{deleted} workflow runs older than {days} '
                            'days deleted')
            except store.StoreError:
                self._tables_missing()
            days = setting('event_retention_days')
            deleted = store.clean_events(days)
            logger.info(f'{deleted} job scheduler events older than {days} '
                        'days deleted')
        except Exception:
            logger.error()
        logger.info('Job maintenance performed')

    def _delay(self):
        """Get seconds to sleep until the next thing to do."""
        now = time.time()
        wakes = [self.heartbeat + LEASE_INTERVAL,
                 self.checked + CONFIG_CHECK_INTERVAL]
        if self.next_maintenance:
            wakes.append(self.next_maintenance)
        for item in list(self.schedule.values()) + list(
                self.workflow_schedule.values()):
            fire = schedule.next_fire(item['schedule'], self.cursor)
            if fire:
                wakes.append(fire[0].timestamp())
        return max(min(wakes) - now, 0)

    def _notify(self):
        for listener in self.listeners:
            try:
                listener()
            except Exception:
                logger.error()


def upcoming(hours=24, job_name=None, limit=1000):
    """Get the fires of the jobs that are on in the next `hours`, as
    (moment, job_name) in order."""
    now = dt.datetime.now().replace(microsecond=0)
    until = now + dt.timedelta(hours=hours)
    fires = []
    for item in store.read_scheduled().values():
        if job_name and item['job_name'] != job_name:
            continue
        for moment in schedule.next_fire(item['schedule'], now,
                                         limit=limit, before=until):
            fires.append((moment, item['job_name']))
    return sorted(fires)[:limit]


def upcoming_workflows(hours=24, workflow_name=None, limit=1000):
    """Get the fires of the workflows that are on in the next `hours`, as
    (moment, workflow_name) in order."""
    now = dt.datetime.now().replace(microsecond=0)
    until = now + dt.timedelta(hours=hours)
    fires = []
    try:
        scheduled = workflow_store.read_scheduled()
    except store.StoreError:
        return []
    for item in scheduled.values():
        if workflow_name and item['workflow_name'] != workflow_name:
            continue
        for moment in schedule.next_fire(item['schedule'], now,
                                         limit=limit, before=until):
            fires.append((moment, item['workflow_name']))
    return sorted(fires)[:limit]


scheduler = JobScheduler()
