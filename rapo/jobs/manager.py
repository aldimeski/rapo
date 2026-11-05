"""Contains the run manager of the jobs.

A run is recorded at once (`rapo_job_run`, status `I`), waits in a queue for
one of the `[JOBS] parallelism` slots, and is performed in a process of its
own (`executor.operate`) that reports its stage, its log and its outcome
through a pipe. The manager writes the log as it comes, stops a run that is
canceled or over its timeout by killing the process with everything it
started, and sends the mail of a run that failed.

The `overlap` of a job says what a start does while the job is still
running or waiting: `skip` (a scheduled start is recorded as canceled, a
manual one refused), `queue` (it waits for the previous run to end) or
`allow` (they run side by side).
"""

import collections
import datetime as dt
import multiprocessing as mp
import os
import threading as th
import time

from .. import options
from ..core.runner import get_runner_name
from ..logger import logger

from . import definition as definitions
from . import executor, notify, processes, store

SUPERVISION_INTERVAL = 0.5
WAIT_SECONDS = 1.0
# Messages taken from a run per round, so a chatty one does not hold up the
# others.
MESSAGES_PER_ROUND = 200
TAIL_LINES = 40

# Types of the events of the job scheduler.
FIRED, STARTED, MISSED, FAILED, CANCELED = (
    'FIRED', 'STARTED', 'MISSED', 'FAILED', 'CANCELED')


def setting(name):
    """Get a [JOBS] option."""
    return options.get('JOBS', name)


class Run:
    """Represents one run owned by the manager."""

    def __init__(self, run_id, job, trigger, spec, overlap, timeout,
                 event_id=None):
        self.run_id = run_id
        self.job = {'job_id': job['job_id'], 'job_name': job['job_name'],
                    'notify_email': job['notify_email']}
        self.job_id = job['job_id']
        self.name = job['job_name']
        self.trigger = trigger
        self.spec = spec
        self.overlap = overlap
        self.timeout = timeout
        self.event_id = event_id
        self.queued = dt.datetime.now()
        self.started = None
        self.process = None
        self.connection = None
        self.canceled = False
        self.stage = None
        self.lines = 0
        self.tail = collections.deque(maxlen=TAIL_LINES)

    def describe(self):
        """Get the run for the status report."""
        return {'run_id': self.run_id, 'job_id': self.job_id,
                'job_name': self.name, 'trigger_type': self.trigger,
                'queued': self.queued, 'started': self.started,
                'stage': self.stage,
                'pid': self.process.pid if self.process else None}


class JobManager:
    """Represents the run manager of the jobs of this server."""

    def __init__(self):
        self.name = get_runner_name()
        self.condition = th.Condition()
        self.submitting = th.Lock()
        self.pending = collections.deque()
        self.running = []
        self.active = False
        self.threads = []
        self.listeners = []

    @property
    def capacity(self):
        """Get the number of execution slots."""
        return max(int(setting('parallelism') or 1), 1)

    @staticmethod
    def enabled():
        """Tell whether the jobs are switched on ([JOBS] enabled)."""
        return setting('enabled') is not False

    def start(self):
        """Start the manager threads, unless the jobs are switched off."""
        if self.active:
            return
        if not self.enabled():
            logger.info('Job manager not started: [JOBS] enabled is off')
            return
        self.active = True
        try:
            swept = store.sweep(self.name)
            if swept:
                logger.warning(f'{swept} orphaned job runs finished')
        except Exception:
            logger.error()
        for name, target in [('Job-Dispatcher', self._dispatch),
                             ('Job-Supervisor', self._supervise)]:
            thread = th.Thread(name=name, target=target, daemon=True)
            thread.start()
            self.threads.append(thread)
        logger.info(f'Job manager started as {self.name} '
                    f'with {self.capacity} slots')

    def sync(self):
        """Follow [JOBS] enabled after rapo.ini was reloaded: start the
        manager when it was switched on, stop it (canceling what waits or
        runs) when it was switched off."""
        if self.enabled() and not self.active:
            self.start()
        elif not self.enabled() and self.active:
            logger.info('[JOBS] enabled switched off: stopping the job '
                        'manager')
            self.stop()

    def stop(self, reason='the job manager was stopped'):
        """Stop the manager, killing the runs that wait or run."""
        if not self.active:
            return
        with self.condition:
            self.active = False
            runs = list(self.pending) + list(self.running)
            for run in runs:
                run.canceled = True
            self.pending.clear()
            self.running.clear()
            self.condition.notify_all()
        for run in runs:
            self._kill(run)
            self._finish(run, store.CANCELED,
                         f'The run was stopped because {reason}.',
                         mail=False)
            self._event(run, CANCELED, f'Stopped because {reason}.')
        self.threads = []
        logger.info('Job manager stopped')

    # Running

    def submit(self, name, trigger, scheduled_time=None, requested_by=None,
               parameters=None):
        """Initiate a run of the job and queue it. Returns the ID of the run.

        A start while the job still runs or waits follows the job's
        `overlap`: refused for a manual start, recorded as a canceled run
        for a scheduled one (`skip`); queued (`queue`); or run at once
        (`allow`). `parameters` are values for this run of parameters the
        job defines.
        """
        if not self.active:
            raise store.StoreError('The job manager is not running.', 503)
        job = store.get_job(name)
        definition = definitions.normalize(job['definition'])
        overlap = definition['overlap']
        given = self._check_parameters(definition, parameters)
        timeout = definition.get('timeout')
        if timeout is None:
            timeout = int(setting('timeout') or 0)
        with self.submitting:
            busy = self.busy(job['job_id']) or \
                store.has_active(job['job_id'])
            if busy and overlap == 'skip':
                return self._skip(job, trigger, scheduled_time,
                                  requested_by)
            run_id = store.add_run(job, trigger,
                                   scheduled_time=scheduled_time,
                                   requested_by=requested_by,
                                   runner=self.name)
        spec = {'job_name': job['job_name'], 'job_id': job['job_id'],
                'run_id': run_id, 'trigger': trigger,
                'scheduled_time': scheduled_time, 'definition': definition,
                'parameters': given,
                'max_output_kb': int(setting('max_output_kb'))}
        event_id = self._record(job['job_id'], trigger, FIRED,
                                scheduled_time, run_id)
        run = Run(run_id, job, trigger, spec, overlap, timeout, event_id)
        with self.condition:
            self.pending.append(run)
            self.condition.notify_all()
        logger.info(f'Job {job["job_name"]} queued as run {run_id} '
                    f'({trigger}), {len(self.pending)} in queue')
        self._notify()
        return run_id

    @staticmethod
    def _check_parameters(definition, parameters):
        given = {}
        names = {item['name'] for item in definition.get('parameters') or []}
        for key, value in (parameters or {}).items():
            if key not in names:
                raise store.StoreError(
                    f'The job has no parameter {key}.', 400)
            given[key] = '' if value is None else str(value)
        return given

    def _skip(self, job, trigger, scheduled_time, requested_by):
        """Refuse a manual start, record a scheduled one as canceled, of a
        job that is still running or waiting."""
        if trigger == store.MANUAL:
            raise store.StoreError(
                f'Job {job["job_name"]} is already running or waiting.', 409)
        run_id = store.add_run(job, trigger, scheduled_time=scheduled_time,
                               requested_by=requested_by, runner=self.name)
        message = 'Skipped: the previous run is still running or waiting.'
        store.update_run(run_id, status=store.CANCELED, end_time=store.now(),
                         message=message)
        self._record(job['job_id'], trigger, CANCELED, scheduled_time,
                     run_id, message)
        logger.info(f'Job {job["job_name"]} start skipped: {message}')
        return run_id

    def cancel(self, run_id):
        """Cancel a run of this manager, False when it is not its own."""
        with self.condition:
            run = next((run for run in self.pending
                        if run.run_id == run_id), None)
            if run:
                self.pending.remove(run)
            else:
                run = next((run for run in self.running
                            if run.run_id == run_id), None)
                if not run:
                    return False
                self.running.remove(run)
                self.condition.notify_all()
            run.canceled = True
        self._kill(run)
        self._drain(run)
        self._finish(run, store.CANCELED, 'The run was canceled on request.',
                     mail=False)
        self._event(run, CANCELED, 'Canceled on request.')
        self._notify()
        return True

    def status(self):
        """Get the manager status report."""
        with self.condition:
            pending = [run.describe() for run in self.pending]
            running = [run.describe() for run in self.running]
        return {'runner': self.name, 'active': self.active,
                'enabled': self.enabled(), 'capacity': self.capacity,
                'queued': pending, 'running': running}

    def busy(self, job_id):
        """Check whether a run of the job waits or runs here."""
        with self.condition:
            return any(run.job_id == job_id
                       for run in list(self.pending) + self.running)

    def _next(self):
        """Get the next run that may start, None when there is none (no
        free slot, or the waiting ones are of jobs that are running and
        may not overlap). Called with the lock held."""
        if len(self.running) >= self.capacity:
            return None
        running = {run.job_id for run in self.running}
        for run in self.pending:
            if run.overlap == 'allow' or run.job_id not in running:
                self.pending.remove(run)
                return run
        return None

    def _dispatch(self):
        while True:
            with self.condition:
                while True:
                    if not self.active:
                        return
                    run = self._next()
                    if run:
                        break
                    # A timeout, so that a larger `parallelism` of a reloaded
                    # rapo.ini is seen without anything else happening.
                    self.condition.wait(WAIT_SECONDS)
                self.running.append(run)
            try:
                self._spawn(run)
            except Exception as error:
                logger.error()
                with self.condition:
                    if run in self.running:
                        self.running.remove(run)
                        self.condition.notify_all()
                self._finish(run, store.ERROR,
                             f'The run process could not be started: '
                             f'{error}')
                self._event(run, FAILED, str(error))
            self._notify()

    def _spawn(self, run):
        context = mp.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(name=f'job-{run.name}',
                                  target=executor.operate,
                                  args=(sender, run.spec, os.getpid()))
        # Started under the lock, so that a cancel arriving right now either
        # stops the run here or finds a process it can kill.
        with self.condition:
            if run.canceled:
                return
            run.process = process
            run.connection = receiver
            process.start()
        sender.close()
        run.started = dt.datetime.now()
        store.update_run(run.run_id, status=store.RUNNING,
                         start_time=run.started.replace(microsecond=0))
        self._event(run, STARTED)
        logger.info(f'Job {run.name} running as process on PID '
                    f'{process.pid} (run {run.run_id})')

    def _supervise(self):
        while self.active:
            with self.condition:
                running = [run for run in self.running if run.process]
            finished = False
            for run in running:
                try:
                    finished = self._check(run) or finished
                except Exception:
                    logger.error()
            if finished:
                self._notify()
            time.sleep(SUPERVISION_INTERVAL)

    def _drain(self, run):
        """Take what the run process has reported, write its log; get its
        outcome when it has reported one."""
        outcome = None
        if run.connection is None:
            return None
        try:
            for _ in range(MESSAGES_PER_ROUND):
                if not run.connection.poll():
                    break
                kind, payload = run.connection.recv()
                if kind == 'log':
                    self._write_log(run, payload)
                elif kind == 'stage':
                    run.stage = payload
                    store.update_run(run.run_id, stage=payload)
                elif kind == 'condition':
                    store.update_run(run.run_id, condition_value=payload)
                elif kind == 'outcome':
                    outcome = payload
        except (EOFError, OSError):
            pass
        return outcome

    def _write_log(self, run, lines):
        run.tail.extend(line[3] for line in lines)
        try:
            store.add_log_lines(run.run_id, run.job_id, lines,
                                int(setting('log_max_lines')),
                                start=run.lines + 1, trim=False)
        except Exception:
            # The run goes on; its log is what is lost.
            logger.error()
        run.lines += len(lines)

    def _check(self, run):
        """Collect what a run reports, stop it on timeout; True when it
        finished."""
        outcome = self._drain(run)
        if outcome is not None or not run.process.is_alive():
            if outcome is None:
                # It is gone: what it sent last may still be in the pipe.
                while True:
                    before = run.lines
                    outcome = self._drain(run)
                    if outcome is not None or run.lines == before:
                        break
            run.process.join(10)
            with self.condition:
                if run in self.running:
                    self.running.remove(run)
                    self.condition.notify_all()
                else:
                    # Canceled meanwhile, and finished by the cancel.
                    return False
            if outcome is None:
                self._finish(run, store.ERROR,
                             'The run process ended unexpectedly (exit code '
                             f'{run.process.exitcode}).')
            else:
                self._finish(run, outcome['status'], outcome['message'],
                             outcome)
            self._close(run)
            logger.info(f'Job {run.name} process at PID {run.process.pid} '
                        f'returns {run.process.exitcode}')
            return True
        if run.timeout and run.started and (
                dt.datetime.now() - run.started
        ).total_seconds() > run.timeout:
            with self.condition:
                if run not in self.running:
                    return False
                self.running.remove(run)
                self.condition.notify_all()
            self._kill(run)
            self._drain(run)
            where = f' in {run.stage}' if run.stage else ''
            self._finish(run, store.ERROR,
                         f'The job was stopped after the timeout of '
                         f'{run.timeout} seconds{where}.')
            return True
        return False

    def _kill(self, run):
        """Stop the process of a run with everything it started."""
        process = run.process
        if process is not None and process.is_alive():
            logger.info(f'Job {run.name} stopping process at PID '
                        f'{process.pid}...')
            processes.kill_tree(process.pid)
            process.join(10)

    @staticmethod
    def _close(run):
        if run.connection is not None:
            try:
                run.connection.close()
            except OSError:
                pass

    def _finish(self, run, status, message, outcome=None, mail=True):
        """End the run: record its result, trim the log of the job and send
        the mail of an error."""
        self._close(run)
        values = {'status': status, 'end_time': store.now(),
                  'message': message, 'log_lines': run.lines,
                  'stage': run.stage}
        if outcome:
            values.update(stage=outcome.get('stage') or run.stage,
                          exit_code=outcome.get('exit_code'),
                          condition_value=outcome.get('condition_value'))
        try:
            store.update_run(run.run_id, **values)
            store.trim_log(run.job_id, int(setting('log_max_lines')))
        except Exception:
            logger.error()
        if status == store.ERROR and mail:
            self._mail(run)

    def _mail(self, run):
        try:
            row = store.read_run(run.run_id) or {}
            tail = list(run.tail) or (row.get('run_log') or ''
                                      ).splitlines()[-TAIL_LINES:]
            row.setdefault('run_id', run.run_id)
            status = notify.send_failure(run.job, row, tail)
            store.update_run(run.run_id, mail_status=status)
        except Exception:
            logger.error()

    # Events

    def _record(self, job_id, trigger, event_type, scheduled_time=None,
                run_id=None, message=None):
        try:
            return store.add_event(job_id, trigger, event_type,
                                   scheduled_time=scheduled_time,
                                   run_id=run_id, message=message,
                                   runner=self.name)
        except Exception:
            logger.error()
            return None

    def _event(self, run, event_type, message=None):
        if run.event_id is None:
            return
        try:
            store.update_event(run.event_id, event_type=event_type,
                               **({'message': message} if message else {}))
        except Exception:
            logger.error()

    def _notify(self):
        for listener in self.listeners:
            try:
                listener()
            except Exception:
                logger.error()


manager = JobManager()
