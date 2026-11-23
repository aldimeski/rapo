"""Contains the manager of the groups of file transfers.

A group is run, scheduled or started from the UI, as a run of its own
(`rapo_transfer_group_run`): the transfers of the group are started one after
another in the order of the group, each as a run of the transfer manager
(`rapo_transfer_run`), and the next one is started when the one before has
ended. A run of the group lives in a thread of its own and only waits; the
transfers themselves are performed by the transfer manager, in its processes
and slots. That is also what keeps one transfer from running twice at once:
a transfer that is started by a group while it runs (started by hand, by its
own schedule or by another group) waits behind that run.

A transfer that ends in Error stops the group: the transfers after it are not
started, the run is an Error and one mail names the step. A transfer that is
canceled stops the group as Canceled, with no mail.

The schedule of a group is kept like the one of a transfer and is served by
the scheduler of the server holding the lease (`fire`).
"""

import datetime as dt
import threading as th
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .. import options
from ..core import schedule
from ..logger import logger

from . import group_store as store
from . import manager as transfer_manager
from .group_store import StoreError

GROUP = 'group'
# Fires found later than this after their time are recorded as missed.
LATE_TOLERANCE = transfer_manager.LATE_TOLERANCE
MISSED_LIMIT = transfer_manager.MISSED_LIMIT
SCHEDULE_CACHE_SECONDS = 5
POLL_SECONDS = 1
# Lines of the log of the transfer that failed that go into the mail.
MAIL_LOG_LINES = 40
WORDS = {'W': 'waiting', 'I': 'queued', 'S': 'running', 'D': 'done',
         'E': 'error', 'C': 'canceled'}


def _stamp():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


class GroupRun:
    """Represents one run of a group owned by the manager."""

    def __init__(self, run_id, group, trigger, requested_by=None):
        self.run_id = run_id
        self.group_id = group['group_id']
        self.name = group['group_name']
        self.overlap = group['overlap']
        self.notify_email = group.get('notify_email')
        # The transfers as they were when the run was started.
        self.members = list(group['members'])
        self.trigger = trigger
        self.requested_by = requested_by
        self.queued = dt.datetime.now()
        self.started = None
        self.canceled = False
        self.reason = None
        # The run of the transfer that is going, a `manager.Job`.
        self.current = None
        self.step_no = 0
        self.lines = []
        self.done = th.Event()

    def describe(self):
        """Get the run for the status report."""
        return {'run_id': self.run_id, 'group_name': self.name,
                'trigger_type': self.trigger, 'queued': self.queued,
                'started': self.started, 'step_no': self.step_no,
                'steps_total': len(self.members)}


class GroupManager:
    """Represents the manager of the groups of transfers."""

    def __init__(self, transfers=None):
        self.transfers = transfers or transfer_manager.manager
        self.condition = th.Condition()
        # Every run of a group that waits or goes, in the order started.
        self.runs = []
        self.listeners = []
        self.schedules = {}
        self.schedules_read = 0
        self.lock = th.Lock()

    @property
    def name(self):
        """Get the name of this server as a runner."""
        return self.transfers.name

    @property
    def active(self):
        """Tell whether groups can be run: as long as the transfers can."""
        return self.transfers.active

    def start(self):
        """Finish the runs a crash left active."""
        try:
            done = store.sweep(self.name)
            if done:
                logger.warning(f'{done} orphaned transfer group runs '
                               'finished')
        except StoreError as error:
            logger.warning(str(error))
        except Exception:
            logger.error()

    def stop(self):
        """Cancel every run of a group, and wait a little for them to end."""
        with self.condition:
            runs = list(self.runs)
            for run in runs:
                run.canceled = True
                run.reason = 'the server shutdown'
            self.condition.notify_all()
        for run in runs:
            self._cancel_current(run)
        for run in runs:
            run.done.wait(10)

    def status(self):
        """Get the report on the runs of the groups."""
        with self.condition:
            return {'runs': [run.describe() for run in self.runs]}

    def busy(self, name):
        """Check whether a run of the group waits or goes here."""
        with self.condition:
            return any(run.name == name for run in self.runs)

    def in_group_now(self, param_name):
        """Get the names of the groups that have a run waiting or going here
        and the transfer in it."""
        with self.condition:
            return sorted({run.name for run in self.runs
                           if param_name in run.members})

    def _notify(self):
        for listener in self.listeners:
            try:
                listener()
            except Exception:
                logger.error()

    # Starting

    def submit(self, name, trigger, scheduled_time=None, requested_by=None):
        """Initiate a run of the group and start it; get the ID of the run.

        A group that is running or waiting is refused (`409`) when its
        `overlap` is `skip`; `queue` makes the new run wait for the one
        before and `allow` lets them go together (a transfer that is in both
        still waits for its own run to end).
        """
        if not self.active:
            raise StoreError('The transfer manager is not running.', 503)
        group = store.get_group(name)
        if not group['members']:
            raise StoreError(f'Group {name} has no transfers.', 400)
        with self.condition:
            if group['overlap'] == store.SKIP and any(
                    run.group_id == group['group_id'] for run in self.runs):
                raise StoreError(f'Group {name} is already running or '
                                 'waiting.', 409)
            run_id = store.add_run(group, trigger,
                                   scheduled_time=scheduled_time,
                                   requested_by=requested_by,
                                   runner=self.name)
            run = GroupRun(run_id, group, trigger, requested_by)
            self.runs.append(run)
        th.Thread(name=f'Transfer-Group-{run_id}', target=self._perform,
                  args=(run,), daemon=True).start()
        logger.info(f'Transfer group {name} queued as run {run_id} '
                    f'({trigger}), {len(run.members)} transfers')
        self._notify()
        return run_id

    def cancel(self, run_id):
        """Cancel a run of a group of this server (the transfer that is going
        is canceled too and the ones after it are not started); False when it
        is not its own."""
        with self.condition:
            run = next((item for item in self.runs
                        if item.run_id == run_id), None)
            if not run:
                return False
            run.canceled = True
            run.reason = 'the request'
            self.condition.notify_all()
        self._cancel_current(run)
        return True

    def _cancel_current(self, run):
        job = run.current
        if job is not None and not job.done.is_set():
            self.transfers.cancel(job.run_id)

    # Performing

    def _log(self, run, text):
        run.lines.append(f'[{_stamp()}] {text}')
        self._write_log(run)

    def _log_transfer(self, run, job):
        """Make the log of a transfer run a part of the log of the group."""
        run.lines.append(store.LOG_MARK.format(job.run_id))
        self._write_log(run)

    def _write_log(self, run):
        try:
            store.update_run(run.run_id, run_log='\n'.join(run.lines))
        except Exception:
            logger.error()

    def _waits(self, run):
        """Tell whether a run of the same group ahead of this one still goes,
        for a group that queues its runs."""
        if run.overlap != store.QUEUE:
            return False
        for other in self.runs:
            if other is run:
                return False
            if other.group_id == run.group_id:
                return True
        return False

    def _perform(self, run):
        status, message = store.ERROR, None
        steps = {number: store.WAITING
                 for number in range(1, len(run.members) + 1)}
        try:
            with self.condition:
                while self.active and not run.canceled and self._waits(run):
                    self.condition.wait()
            if run.canceled:
                status = store.CANCELED
                message = f'Group stopped before it started: {run.reason}.'
            else:
                status, message = self._steps(run, steps)
        except Exception as error:
            logger.error()
            status, message = store.ERROR, \
                f'The group stopped unexpectedly: {type(error).__name__}: ' \
                f'{error}'
        try:
            self._close(run, status, message, steps)
        except Exception:
            logger.error()
        finally:
            with self.condition:
                if run in self.runs:
                    self.runs.remove(run)
                self.condition.notify_all()
            run.done.set()
            self._notify()

    def _steps(self, run, steps):
        """Run the transfers of the group one after another. Returns the
        status of the run and its message."""
        total = len(run.members)
        run.started = dt.datetime.now()
        store.update_run(run.run_id, status=store.RUNNING,
                         start_time=store.now(), step_no=0)
        self._log(run, f'Group {run.name} started ({run.trigger}), '
                       f'{total} transfer{"" if total == 1 else "s"}: '
                       f'{", ".join(run.members)}')
        self._notify()
        for number, param in enumerate(run.members, 1):
            if run.canceled:
                return store.CANCELED, f'Group canceled: {run.reason}.'
            run.step_no = number
            store.update_run(run.run_id, step_no=number)
            step_status, step_message = self._step(run, number, param,
                                                   total)
            steps[number] = step_status
            if step_status == store.CANCELED:
                return store.CANCELED, (
                    f'Group canceled at step {number} of {total} '
                    f'(transfer {param}): {step_message or run.reason}')
            if step_status != store.DONE:
                return store.ERROR, (
                    f'Step {number} of {total}, transfer {param}, failed: '
                    f'{step_message or "no message"}')
        return store.DONE, f'All {total} transfer' \
                           f'{"" if total == 1 else "s"} done.'

    def _step(self, run, number, param, total):
        """Run one transfer of the group and wait for its end. Returns the
        status of the step and its message."""
        moment = store.now()
        self._log(run, f'Step {number} of {total}: transfer {param} starts')
        try:
            job = self.transfers.enqueue(
                param, GROUP, requested_by=run.requested_by,
                group_run_id=run.run_id)
        except StoreError as error:
            self._log(run, f'Step {number}: transfer {param} was not '
                           f'started: {error}')
            store.update_step(run.run_id, number, status=store.ERROR,
                              start_time=moment, end_time=store.now(),
                              message=str(error))
            return store.ERROR, str(error)
        run.current = job
        store.update_step(run.run_id, number, transfer_run_id=job.run_id,
                          status=store.INITIATED)
        self._log_transfer(run, job)
        if run.canceled:
            self._cancel_current(run)
        running = False
        while not job.done.wait(POLL_SECONDS):
            if running:
                continue
            seen = self.transfers_status(job.run_id)
            if seen and seen['status'] == store.RUNNING:
                running = True
                store.update_step(run.run_id, number, status=store.RUNNING,
                                  start_time=seen['start_time'])
        seen = self.transfers_status(job.run_id) or {}
        status = seen.get('status')
        if status not in (store.DONE, store.ERROR, store.CANCELED):
            status = store.ERROR
        text = seen.get('message')
        store.update_step(run.run_id, number, status=status,
                          start_time=seen.get('start_time') or moment,
                          end_time=seen.get('end_time') or store.now(),
                          message=text)
        self._log(run, f'Step {number}: transfer {param} '
                       f'{WORDS[status]}' + (f': {text}' if text else ''))
        return status, text

    @staticmethod
    def transfers_status(run_id):
        """Get the status of a run of a transfer, None when it is not
        there."""
        try:
            return store.transfers.read_status(run_id)
        except Exception:
            logger.error()
            return None

    def _close(self, run, status, message, steps):
        """End the run: the steps that were not reached are canceled, the
        status is written and a mail is sent for an error."""
        left = [number for number, step in steps.items()
                if step == store.WAITING]
        for number in left:
            store.update_step(run.run_id, number, status=store.CANCELED,
                              end_time=store.now(),
                              message='Not run: the group stopped before '
                                      'this step.')
        if left and status != store.DONE:
            names = ', '.join(run.members[number - 1] for number in left)
            self._log(run, f'Not run: {names}')
        self._log(run, f'Group {run.name} ended {WORDS[status]}'
                       + (f': {message}' if message else ''))
        store.update_run(run.run_id, status=status, end_time=store.now(),
                         message=message)
        logger.info(f'Transfer group {run.name} run {run.run_id} ended '
                    f'{WORDS[status]}')
        if status == store.ERROR:
            mail = self._mail(run)
            store.update_run(run.run_id, mail_status=mail)

    # The mail of a failed group

    def _mail(self, run):
        """Send the one mail of a failed run. Never raises; returns the text
        for `mail_status`."""
        try:
            from ..core import mailer
            group = store.read_group_by_id(run.group_id) or {
                'group_name': run.name, 'notify_email': run.notify_email}
            to = mailer.addresses(group.get('notify_email')) or \
                mailer.addresses(options.get('JOBS', 'default_email'))
            if not to:
                return 'Not sent: the group has no address and [JOBS] ' \
                       'default_email is not set.'
            params = mailer.settings()
            if not params.get('enabled'):
                return 'Not sent: [EMAIL] enabled is off in rapo.ini.'
            if not params.get('sender'):
                return 'Not sent: [EMAIL] sender is not set in rapo.ini.'
            row = store.read_run(run.run_id)
            message = build_message(
                row, params['sender'], params.get('sender_name'),
                params.get('reply_to'), to)
            refused = mailer.deliver(message, to)
            if refused:
                logger.warning(f'Transfer group {run.name}: mail refused '
                               f'for {refused}')
                return f'Sent to {len(to) - len(refused)} of {len(to)}, ' \
                       f'refused: {", ".join(refused)}'
            logger.info(f'Transfer group {run.name}: failure mail sent to '
                        f'{", ".join(to)}')
            return f'Sent to {", ".join(to)}'
        except Exception as error:
            logger.error()
            return f'Not sent: {type(error).__name__}: {error}'

    # Schedules

    def invalidate(self):
        """Forget the schedules read, after a change."""
        with self.lock:
            self.schedules_read = 0

    def read_schedules(self):
        """Get {group_id: {group_name, config, schedule, enabled}} of the
        groups that have a schedule, read at most every few seconds."""
        with self.lock:
            if time.monotonic() - self.schedules_read < SCHEDULE_CACHE_SECONDS:
                return self.schedules
        found = store.read_scheduled()
        with self.lock:
            self.schedules = found
            self.schedules_read = time.monotonic()
        return found

    def fire(self, since, until):
        """Start every scheduled group fire in (since, until]. Called by the
        scheduler holding the lease, which is the only one to fire."""
        if not self.active:
            return
        border = until + dt.timedelta(seconds=1)
        fires = []
        for group_id, item in self.read_schedules().items():
            if not item['enabled']:
                continue
            for moment in schedule.next_fire(item['schedule'], since,
                                             limit=MISSED_LIMIT,
                                             before=border):
                fires.append((moment, item['group_name'], group_id))
        for moment, name, group_id in sorted(fires):
            late = (until - moment).total_seconds()
            try:
                if late > LATE_TOLERANCE:
                    self._record(group_id, moment, 'Fire found '
                                 f'{int(late)} seconds late, scheduler was '
                                 'not running.')
                    continue
                self.submit(name, store.SCHEDULE, scheduled_time=moment)
            except StoreError as error:
                if error.status == 409:
                    self._record(group_id, moment, f'Not run: {error}')
                stamp = moment.strftime('%Y-%m-%d %H:%M:%S')
                logger.warning(f'Transfer group {name} fire of {stamp} not '
                               f'run: {error}')
            except Exception:
                logger.error()

    @staticmethod
    def _record(group_id, moment, message):
        """Record a fire that was not run as a canceled run."""
        group = store.read_group_by_id(group_id)
        if not group:
            return
        run_id = store.add_run(group, store.SCHEDULE, scheduled_time=moment,
                               runner=transfer_manager.manager.name)
        for number in range(1, len(group['members']) + 1):
            store.update_step(run_id, number, status=store.CANCELED,
                              message='Not run.')
        store.update_run(run_id, status=store.CANCELED,
                         end_time=store.now(), message=message)

    def next_wake(self, after):
        """Get the timestamp of the earliest scheduled fire, None if none."""
        if not self.transfers.enabled():
            return None
        wakes = []
        for item in self.read_schedules().values():
            if item['enabled']:
                fire = schedule.next_fire(item['schedule'], after)
                if fire:
                    wakes.append(fire[0].timestamp())
        return min(wakes) if wakes else None

    def maintain(self):
        """Delete the old runs of the groups."""
        if not self.transfers.enabled():
            return
        days = transfer_manager.setting('history_retention_days')
        try:
            deleted = store.clean_runs(days)
        except StoreError as error:
            logger.warning(str(error))
            return
        logger.info(f'{deleted} transfer group runs older than {days} days '
                    'deleted')

    # What the groups will do

    def typical_seconds(self, last_runs):
        """Get {param: seconds} the transfers took in their last run that
        ended Done, for the estimate of when a step of a group starts."""
        found = {}
        for name, row in last_runs.items():
            if row.get('status') == store.DONE and row.get('start_time') \
                    and row.get('end_time'):
                found[name] = max((row['end_time']
                                   - row['start_time']).total_seconds(), 0)
        return found

    def table(self):
        """Get every group with its schedule, its next start, its
        transfers (each with its own next start and the moment it is
        expected to start inside the group) and its last run."""
        now = dt.datetime.now()
        own = self.transfers.read_schedules()
        last_runs = store.transfers.read_last_runs()
        group_runs = store.read_last_runs()
        seconds = self.typical_seconds(last_runs)
        scheduled = self.read_schedules()
        rows = []
        for group in store.read_groups():
            item = scheduled.get(group['group_id'])
            fire = None
            if item and item['enabled']:
                found = schedule.next_fire(item['schedule'], now)
                fire = found[0] if found else None
            members, expected = [], fire
            for number, name in enumerate(group['members'], 1):
                planned = own.get(name)
                own_fire = None
                if planned and planned['enabled'] and planned['schedule']:
                    found = schedule.next_fire(planned['schedule'], now)
                    own_fire = found[0] if found else None
                members.append({
                    'position': number, 'param_name': name,
                    'own_next_fire': own_fire,
                    'group_start': expected,
                    'running': self.transfers.busy(name),
                    'last_run': last_runs.get(name)})
                if expected is not None:
                    expected = expected + dt.timedelta(
                        seconds=seconds.get(name, 0))
            rows.append({
                'group_name': group['group_name'],
                'group_id': group['group_id'],
                'description': group['description'],
                'status': group['status'],
                'overlap': group['overlap'],
                'notify_email': group['notify_email'],
                'schedule_config': group['schedule_config'],
                'scheduled': bool(item),
                'next_fire': fire,
                'members': members,
                'running': self.busy(group['group_name']),
                'last_run': group_runs.get(group['group_name'])})
        return {'groups': rows, 'manager': self.status()}

    def annotate(self, answer):
        """Add the groups to the list of the transfers (`answer` of
        `TransferManager.table`): each param gets `groups` (the group, its
        position, the next start of the group and when the transfer is
        expected to start in it) and `next_starts`, every start it has - its
        own schedule, each group, the crontab - the earliest first. Without
        the group tables the list is left as it is."""
        try:
            table = self.table()['groups']
        except StoreError as error:
            answer['groups_available'] = False
            answer['groups_error'] = str(error)
            return answer
        answer['groups_available'] = True
        by_param = {}
        for group in table:
            for member in group['members']:
                by_param.setdefault(member['param_name'], []).append({
                    'group_name': group['group_name'],
                    'position': member['position'],
                    'size': len(group['members']),
                    'enabled': group['status'] == 'Y',
                    'scheduled': group['scheduled'],
                    'next_fire': group['next_fire'],
                    'expected_start': member['group_start'],
                    'running': group['running']})
        for row in answer['params']:
            groups = by_param.get(row['param_name'], [])
            starts = []
            rapo = row.get('rapo')
            if rapo and rapo.get('next_fire'):
                starts.append({'source': 'rapo', 'name': None,
                               'next_fire': rapo['next_fire'],
                               'position': None})
            for group in groups:
                if group['enabled'] and group['expected_start']:
                    starts.append({'source': 'group',
                                   'name': group['group_name'],
                                   'next_fire': group['expected_start'],
                                   'position': group['position'],
                                   'group_start': group['next_fire']})
            for entry in row.get('crontab') or []:
                if entry.get('next_fire'):
                    starts.append({'source': 'crontab',
                                   'name': entry.get('script'),
                                   'next_fire': entry['next_fire'],
                                   'position': None})
            starts.sort(key=lambda item: item['next_fire'])
            row['groups'] = groups
            row['next_starts'] = starts
            row['next_start'] = starts[0]['next_fire'] if starts else None
        return answer


def build_message(run, sender, sender_name=None, reply_to=None, to=()):
    """Get the e-mail of a failed run of a group: `run` is a row of
    `read_run` (with its steps and its whole log)."""
    instance = options.get('SCHEDULER', 'instance_name')
    prefix = f'[{instance}] ' if instance else ''
    message = EmailMessage()
    message['From'] = formataddr((sender_name or '', str(sender)))
    message['To'] = ', '.join(to)
    if reply_to:
        message['Reply-To'] = str(reply_to)
    name = run['group_name']
    message['Subject'] = f'{prefix}Transfer group {name} failed'
    message['Date'] = dt.datetime.now().astimezone()
    message['Message-ID'] = make_msgid()

    def when(moment):
        return f'{moment:%Y-%m-%d %H:%M:%S}' if moment else '-'
    steps = run.get('steps') or []
    failed = next((step for step in steps if step['status'] == 'E'), None)
    lines = [f'The transfer group {name} did not succeed.', '',
             f'Run:        {run["run_id"]} ({run.get("trigger_type")})',
             f'Scheduled:  {when(run.get("scheduled_time"))}',
             f'Started:    {when(run.get("start_time"))}',
             f'Ended:      {when(run.get("end_time"))}']
    if failed:
        lines.append(f'Failed at:  step {failed["step_no"]} of '
                     f'{len(steps)}, the transfer {failed["param_name"]}')
    if instance:
        lines.append(f'Instance:   {instance}')
    lines += ['', 'What happened:', run.get('message') or '-', '', 'Steps:']
    for step in steps:
        text = f'  {step["step_no"]}. {step["param_name"]}: ' \
               f'{WORDS.get(step["status"], step["status"])}'
        if step.get('message'):
            text += f' - {step["message"]}'
        lines.append(text)
    tail = (run.get('run_log') or '').split('\n')[-MAIL_LOG_LINES:]
    if any(tail):
        lines += ['', f'Last {len(tail)} lines of the log of the group:'
                  ] + tail
    message.set_content('\n'.join(lines) + '\n')
    return message


manager = GroupManager()
