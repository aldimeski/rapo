"""Contains the file transfer manager of the web server.

Every transfer run, scheduled or started from the UI, is recorded at once in
`rapo_transfer_run`, waits in a FIFO queue for one of `[TRANSFER]
parallelism` slots and is performed in its own spawned OS process, so that a
stuck SFTP connection or a huge file can not hold the server. One param runs
once at a time on a server. A supervisor thread collects the outcome of every
process and stops it when it exceeds `[TRANSFER] timeout`.

The schedule of a param is kept like the one of a control (rapo/core/
schedule.py). It is served by the scheduler of the server holding the lease
(`fire`), so a transfer scheduled in rapo never also goes through the
filetransfer-py script. The params the crontab starts are found by
`rapo/transfer/crontab.py` and only reported.
"""

import collections
import datetime as dt
import json
import multiprocessing as mp
import os
import sys
import threading as th
import time

import sqlalchemy as sa

from .. import options
from ..core import schedule
from ..core.runner import get_runner_name
from ..database import db
from ..logger import logger

from . import crontab
from . import engine
from . import store

SUPERVISION_INTERVAL = 1
WATCHDOG_INTERVAL = 5
# Fires found later than this after their time (e.g. after the host was
# suspended) are recorded as missed instead of being run.
LATE_TOLERANCE = 60
MISSED_LIMIT = 100
SCHEDULE_CACHE_SECONDS = 5
SCHEDULE = 'schedule'
MANUAL = 'manual'


def setting(name):
    """Get a [TRANSFER] option."""
    return options.get('TRANSFER', name)


class Job:
    """Represents one transfer run owned by the manager."""

    def __init__(self, run_id, name, text, trigger):
        self.run_id = run_id
        self.name = name
        self.text = text
        self.trigger = trigger
        self.queued = dt.datetime.now()
        self.started = None
        self.process = None
        self.connection = None
        self.canceled = False
        self.reason = None

    def describe(self):
        """Get the job for the status report."""
        return {'run_id': self.run_id, 'param_name': self.name,
                'trigger_type': self.trigger, 'queued': self.queued,
                'started': self.started,
                'pid': self.process.pid if self.process else None}


def operate(connection, name, text, parent_pid, log_limit=5000):
    """Perform one transfer in the spawned process."""
    th.Thread(name='Watchdog', target=_watch, args=(parent_pid,),
              daemon=True).start()
    try:
        result = engine.execute(name, text, log_limit=log_limit)
        connection.send({'status': result.status,
                         'transferred': result.transferred,
                         'failed': result.failed, 'message': result.message,
                         'log': result.log, 'db_log': result.db_log,
                         'db_dropped': result.db_dropped})
    except BaseException as error:
        connection.send({'status': store.ERROR, 'transferred': 0,
                         'failed': 0, 'log': [], 'db_log': None,
                         'message': f'{type(error).__name__}: {error}'})
    finally:
        connection.close()


def _watch(parent_pid):
    """Exit the process at once when the server process is gone."""
    while True:
        if os.getppid() != parent_pid:
            os._exit(1)
        time.sleep(WATCHDOG_INTERVAL)


class TransferManager:
    """Represents the file transfer manager."""

    def __init__(self):
        self.name = get_runner_name()
        self.condition = th.Condition()
        self.pending = collections.deque()
        self.running = []
        self.active = False
        self.threads = []
        self.listeners = []
        self.schedules = {}
        self.schedules_read = 0
        self.entries = []
        self.entries_read = 0
        self.lock = th.Lock()
        self.next_clean = 0

    @property
    def capacity(self):
        """Get the number of execution slots."""
        return max(int(setting('parallelism') or 1), 1)

    def start(self):
        """Start the manager threads."""
        if self.active:
            return
        self.active = True
        try:
            self.sweep()
        except Exception:
            logger.error()
        for name, target in [('Transfer-Dispatcher', self._dispatch),
                             ('Transfer-Supervisor', self._supervise)]:
            thread = th.Thread(name=name, target=target, daemon=True)
            thread.start()
            self.threads.append(thread)
        logger.info(f'Transfer manager started as {self.name} '
                    f'with {self.capacity} slots')

    def stop(self):
        """Stop the manager, killing the running transfers."""
        if not self.active:
            return
        with self.condition:
            self.active = False
            jobs = list(self.pending) + list(self.running)
            for job in jobs:
                job.canceled = True
                job.reason = 'the server shutdown'
            self.pending.clear()
            self.condition.notify_all()
        for job in jobs:
            self._kill(job)
            self._finish(job, store.CANCELED,
                         'Transfer stopped because of the server shutdown.')
        logger.info('Transfer manager stopped')

    def sweep(self):
        """Finish the runs of this server left active by a crash."""
        table = store.run_table
        try:
            done = db.execute(table.update().where(
                table.c.runner == self.name,
                table.c.status.in_(store.ACTIVE)).values(
                status=store.ERROR, end_time=store.now(),
                message='Transfer stopped because the server stopped '
                        'unexpectedly.'))
        except sa.exc.DatabaseError:
            # The tables are missing: reported where they are used.
            return
        if done.rowcount:
            logger.warning(f'{done.rowcount} orphaned transfer runs finished')

    # Running

    def submit(self, name, trigger, scheduled_time=None, requested_by=None):
        """Initiate a run of the param and queue it.

        Returns the ID of the run. A param already waiting or running is
        refused, so a slow transfer is never run twice at once.
        """
        if not self.active:
            raise store.StoreError('The transfer manager is not running.',
                                   503)
        name = store.check_name(name)
        text, source = store.read(name)
        store.validate(text)
        if source == store.FILE and store.uses_database_log(text):
            raise store.StoreError(
                f'Param {name} is a file with LogDatabase:Y, which only a '
                'param kept in the database can have.', 400)
        with self.condition:
            busy = [job for job in list(self.pending) + self.running
                    if job.name == name]
        if busy:
            raise store.StoreError(f'Transfer {name} is already running or '
                                   'waiting.', 409)
        run_id = store.add_run(name, trigger, scheduled_time=scheduled_time,
                               requested_by=requested_by, runner=self.name)
        job = Job(run_id, name, text, trigger)
        with self.condition:
            self.pending.append(job)
            self.condition.notify_all()
        logger.info(f'Transfer {name} queued as run {run_id} ({trigger}), '
                    f'{len(self.pending)} in queue')
        self._notify()
        return run_id

    def cancel(self, run_id):
        """Cancel a run of this manager, False when it is not its own."""
        with self.condition:
            job = next((job for job in self.pending
                        if job.run_id == run_id), None)
            if job:
                self.pending.remove(job)
            else:
                job = next((job for job in self.running
                            if job.run_id == run_id), None)
                if not job:
                    return False
                self.running.remove(job)
                self.condition.notify_all()
            job.canceled = True
        self._kill(job)
        self._finish(job, store.CANCELED,
                     'Transfer stopped because of the request.')
        self._notify()
        return True

    def status(self):
        """Get the manager status report."""
        with self.condition:
            pending = [job.describe() for job in self.pending]
            running = [job.describe() for job in self.running]
        return {'runner': self.name, 'active': self.active,
                'capacity': self.capacity, 'queued': pending,
                'running': running}

    def busy(self, name):
        """Check whether a run of the param waits or runs here."""
        with self.condition:
            return any(job.name == name
                       for job in list(self.pending) + self.running)

    def _dispatch(self):
        while True:
            with self.condition:
                while True:
                    if not self.active:
                        return
                    if self.pending and len(self.running) < self.capacity:
                        break
                    self.condition.wait()
                job = self.pending.popleft()
                self.running.append(job)
            try:
                self._spawn(job)
            except Exception as error:
                logger.error()
                with self.condition:
                    if job in self.running:
                        self.running.remove(job)
                self._finish(job, store.ERROR,
                             f'Transfer process could not be started: '
                             f'{error}')
            self._notify()

    def _spawn(self, job):
        context = mp.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(name=f'transfer-{job.name}', target=operate,
                                  args=(sender, job.name, job.text,
                                        os.getpid(),
                                        int(setting('log_max_lines'))))
        # Started under the lock, so that a cancel arriving right now either
        # stops the job here or finds a process it can terminate.
        with self.condition:
            if job.canceled:
                return
            job.process = process
            job.connection = receiver
            process.start()
        sender.close()
        job.started = dt.datetime.now()
        store.update_run(job.run_id, status=store.RUNNING,
                         start_time=job.started.replace(microsecond=0))
        logger.info(f'Transfer {job.name} running as process on PID '
                    f'{process.pid} (run {job.run_id})')

    def _supervise(self):
        while self.active:
            with self.condition:
                running = [job for job in self.running if job.process]
            finished = False
            for job in running:
                try:
                    finished = self._check(job) or finished
                except Exception:
                    logger.error()
            if finished:
                self._notify()
            time.sleep(SUPERVISION_INTERVAL)

    def _check(self, job):
        """Collect the outcome of a job, stop it on timeout; True when it
        finished."""
        outcome = None
        if job.connection.poll():
            try:
                outcome = job.connection.recv()
            except (EOFError, OSError):
                outcome = None
        if outcome is not None or not job.process.is_alive():
            job.process.join(10)
            with self.condition:
                if job in self.running:
                    self.running.remove(job)
                    self.condition.notify_all()
                else:
                    # Canceled meanwhile, and finished by the cancel.
                    return False
            if outcome is None:
                self._finish(job, store.ERROR,
                             'Transfer process finished unexpectedly '
                             f'(exit code {job.process.exitcode}).')
            else:
                self._finish(job, outcome['status'], outcome['message'],
                             outcome)
            logger.info(f'Transfer {job.name} process at PID '
                        f'{job.process.pid} returns '
                        f'{job.process.exitcode}')
            return True
        timeout = setting('timeout')
        if timeout and (dt.datetime.now()
                        - job.started).total_seconds() > timeout:
            with self.condition:
                if job not in self.running:
                    return False
                self.running.remove(job)
                self.condition.notify_all()
            self._kill(job)
            self._finish(job, store.ERROR,
                         f'Transfer stopped after the timeout of {timeout} '
                         'seconds.')
            return True
        return False

    def _kill(self, job):
        process = job.process
        if process is not None and process.is_alive():
            logger.info(f'Transfer {job.name} terminating process at PID '
                        f'{process.pid}...')
            process.terminate()
            process.join(10)
        if job.connection is not None:
            try:
                job.connection.close()
            except OSError:
                pass

    def _finish(self, job, status, message, outcome=None):
        values = {'status': status, 'end_time': store.now(),
                  'message': message}
        if outcome:
            values.update(transferred=outcome['transferred'],
                          failed=outcome['failed'],
                          run_log='\n'.join(outcome['log']) or None)
            # LogDatabase:Y: the log is rows of the log table, not run_log.
            if outcome.get('db_log') is not None:
                try:
                    store.add_log_lines(job.run_id, job.name,
                                        outcome['db_log'],
                                        int(setting('log_max_lines')))
                    # The lines the run logged, kept or cut.
                    values.update(run_log=None, log_lines=len(
                        outcome['db_log']) + outcome.get('db_dropped', 0))
                except Exception as error:
                    # The run must still end; its log is lost.
                    logger.error()
                    values['message'] = (f'{values["message"] or ""}\nThe '
                                         f'log could not be written: {error}'
                                         ).strip()
        try:
            store.update_run(job.run_id, **values)
        except Exception:
            logger.error()

    def _notify(self):
        for listener in self.listeners:
            try:
                listener()
            except Exception:
                logger.error()

    # Schedules

    def invalidate(self):
        """Forget the schedules read, after a change."""
        with self.lock:
            self.schedules_read = 0
            self.entries_read = 0

    def read_schedules(self):
        """Get {name: {schedule, enabled, config}} of the transfers
        scheduled in rapo, read at most every few seconds."""
        with self.lock:
            if time.monotonic() - self.schedules_read < SCHEDULE_CACHE_SECONDS:
                return self.schedules
        result = {}
        for name, row in store.read_schedules().items():
            try:
                parsed = schedule.parse(row['schedule_config'])
            except Exception:
                logger.warning(f'Transfer {name} has invalid schedule '
                               'configuration')
                parsed = None
            result[name] = {'schedule': parsed,
                            'config': row['schedule_config'],
                            'enabled': row['status'] == 'Y',
                            'updated': row['updated_date']}
        with self.lock:
            self.schedules = result
            self.schedules_read = time.monotonic()
        return result

    def fire(self, since, until):
        """Submit every scheduled transfer fire in (since, until].

        Called by the scheduler holding the lease, which is the only one to
        fire, as for the controls.
        """
        if not self.active:
            return
        border = until + dt.timedelta(seconds=1)
        fires = []
        for name, item in self.read_schedules().items():
            if not item['enabled'] or not item['schedule']:
                continue
            for moment in schedule.next_fire(item['schedule'], since,
                                             limit=MISSED_LIMIT,
                                             before=border):
                fires.append((moment, name))
        for moment, name in sorted(fires):
            late = (until - moment).total_seconds()
            try:
                if late > LATE_TOLERANCE:
                    run_id = store.add_run(name, SCHEDULE,
                                           scheduled_time=moment,
                                           runner=self.name)
                    store.update_run(
                        run_id, status=store.CANCELED, end_time=store.now(),
                        message=f'Fire found {int(late)} seconds late, '
                                'scheduler was not running.')
                    continue
                self.submit(name, SCHEDULE, scheduled_time=moment)
            except store.StoreError as error:
                stamp = moment.strftime('%Y-%m-%d %H:%M:%S')
                logger.warning(f'Transfer {name} fire of {stamp} not run: '
                               f'{error}')
            except Exception:
                logger.error()

    def next_wake(self, after):
        """Get the timestamp of the earliest scheduled fire, None if none."""
        wakes = []
        for item in self.read_schedules().values():
            if item['enabled'] and item['schedule']:
                fire = schedule.next_fire(item['schedule'], after)
                if fire:
                    wakes.append(fire[0].timestamp())
        return min(wakes) if wakes else None

    def maintain(self):
        """Delete the old runs."""
        days = setting('history_retention_days')
        deleted = store.clean_runs(days)
        logger.info(f'{deleted} transfer runs older than {days} days '
                    'deleted')

    # The crontab

    def read_crontab(self, force=False):
        """Get the crontab entries starting transfers, read at most every
        `crontab_scan_interval` seconds."""
        # Only Linux has a crontab to read.
        if not setting('crontab_scan') or not crontab_supported():
            return []
        interval = setting('crontab_scan_interval')
        with self.lock:
            fresh = time.monotonic() - self.entries_read < interval
            if fresh and not force and self.entries_read:
                return self.entries
        entries = crontab.scan(setting('script_name'))
        with self.lock:
            self.entries = entries
            self.entries_read = time.monotonic()
        return entries

    # The list

    def table(self, force=False):
        """Get the list of every param with its place and scheduling."""
        files = store.read_files()
        database = store.read_database()
        schedules = self.read_schedules()
        last_runs = store.read_last_runs()
        entries = self.read_crontab(force)
        now = dt.datetime.now()

        names = set(files) | set(database or ())
        by_param = collections.defaultdict(list)
        for entry in entries:
            by_param[entry['param']].append(entry)
        rows = []
        for name in sorted(names | set(by_param)):
            in_file, in_database = name in files, name in (database or {})
            if in_file and in_database:
                source = 'both'
            elif in_file:
                source = store.FILE
            elif in_database:
                source = store.DATABASE
            else:
                source = 'external'
            info = (files.get(name) or (database or {}).get(name) or {})
            planned = schedules.get(name)
            rapo = None
            if planned:
                fire = None
                if planned['enabled'] and planned['schedule']:
                    found = schedule.next_fire(planned['schedule'], now)
                    fire = found[0] if found else None
                rapo = {'config': planned['config'],
                        'enabled': planned['enabled'],
                        'scheduled': bool(planned['schedule']),
                        'next_fire': fire}
            cron = [{key: entry[key] for key in (
                'cron', 'command', 'script', 'param_path', 'next_fire',
                'user')} for entry in by_param.get(name, ())]
            if rapo and rapo['enabled'] and rapo['scheduled'] and cron:
                how = 'both'
            elif rapo and rapo['enabled'] and rapo['scheduled']:
                how = 'rapo'
            elif cron:
                how = 'crontab'
            else:
                how = None
            rows.append({
                'param_name': name,
                'source': source,
                'description': info.get('description'),
                'modified': info.get('modified'),
                'size': info.get('size'),
                'scheduling': how,
                'rapo': rapo,
                'crontab': cron,
                'running': self.busy(name),
                'last_run': last_runs.get(name),
            })
        return {'params': rows,
                'param_dir': store.param_dir(),
                'database_available': database is not None,
                'crontab_scan': bool(setting('crontab_scan')),
                'crontab_supported': crontab_supported()}


def crontab_supported():
    """Check whether the host can be asked for its crontab (Linux only)."""
    return sys.platform.startswith('linux')


def schedule_json(units):
    """Get the stored JSON of schedule units, None when none is set."""
    if not units or not any(units.values()):
        return None
    return json.dumps({unit: units.get(unit) for unit in schedule.UNITS})


manager = TransferManager()
