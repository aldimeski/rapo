"""Contains the run manager of the workflows.

A workflow run is recorded at once (`rapo_workflow_run`, status `I`, with a
waiting row for each step) and run by a thread of this server, which takes no
slot of `[JOBS] parallelism`. Each step is an ordinary run of its job, queued
in the job manager (`manager.submit` with `workflow_run_id`): it waits for a
slot, for a run of its job that is still going, runs in a process of its own,
and sends no failure mail. The thread follows the step run and decides what
the workflow does, see docs/jobs-design.md, section 11.2:

* the step is done: the next step starts; after the last the workflow is done;
* the step is an error: the workflow stops as an error, the later steps are
  skipped, and one failure mail goes out;
* the step is canceled because its condition was not met, or by the user: the
  workflow stops as canceled (a warning, not an error), the later steps are
  skipped, and no mail goes out.

A workflow run that is canceled cancels the step that runs. One over its
timeout does the same and is an error.
"""

import collections
import datetime as dt
import threading as th
import time
import traceback

from .. import options
from ..core.runner import get_runner_name
from ..logger import logger

from . import definition as definitions
from . import executor
from . import manager as job_managers
from . import notify, store, workflow, workflow_store

POLL_SECONDS = 0.5
# How long a step that was canceled is waited for before the workflow goes on.
CANCEL_WAIT_SECONDS = 60
TAIL_LINES = 40
CONDITION_NOT_MET = 'The condition was not met'

# Types of the events of the job scheduler, as the job manager has them.
FIRED, STARTED, MISSED, FAILED, CANCELED = (
    job_managers.FIRED, job_managers.STARTED, job_managers.MISSED,
    job_managers.FAILED, job_managers.CANCELED)


def setting(name):
    """Get a [JOBS] option."""
    return options.get('JOBS', name)


def _seconds(start, end):
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds()))


class WorkflowRun:
    """Represents one workflow run owned by the manager."""

    def __init__(self, run_id, row, definition, trigger, scheduled_time,
                 requested_by, given, event_id=None):
        self.run_id = run_id
        self.workflow = {'workflow_id': row['workflow_id'],
                         'workflow_name': row['workflow_name'],
                         'notify_email': row['notify_email']}
        self.workflow_id = row['workflow_id']
        self.name = row['workflow_name']
        self.definition = definition
        self.trigger = trigger
        self.scheduled_time = scheduled_time
        self.requested_by = requested_by
        self.given = given
        self.event_id = event_id
        self.queued = dt.datetime.now()
        self.started = None
        self.step_no = None
        self.job_run_id = None
        self.cancel = th.Event()
        self.cancel_reason = None
        self.thread = None
        self.lines = 0
        self.tail = collections.deque(maxlen=TAIL_LINES)

    def say(self, level, text):
        """Write a line of the log of the run."""
        self.lines += 1
        self.tail.append(text)
        try:
            workflow_store.add_log_line(self.run_id, self.workflow_id,
                                        self.lines, level, text)
        except Exception:
            logger.error()

    def describe(self):
        """Get the run for the status report."""
        return {'run_id': self.run_id, 'workflow_id': self.workflow_id,
                'workflow_name': self.name, 'trigger_type': self.trigger,
                'queued': self.queued, 'started': self.started,
                'step_no': self.step_no}


class WorkflowManager:
    """Represents the run manager of the workflows of this server."""

    def __init__(self, jobs=None):
        self.name = get_runner_name()
        self.jobs = jobs or job_managers.manager
        self.lock = th.Lock()
        self.submitting = th.Lock()
        self.runs = {}
        self.active = False
        self.listeners = []

    @staticmethod
    def enabled():
        """Tell whether the jobs are switched on ([JOBS] enabled)."""
        return setting('enabled') is not False

    def start(self):
        """Start the manager, unless the jobs are switched off."""
        if self.active:
            return
        if not self.enabled():
            logger.info('Workflow manager not started: [JOBS] enabled is '
                        'off')
            return
        self.active = True
        try:
            swept = workflow_store.sweep(self.name)
            if swept:
                logger.warning(f'{swept} orphaned workflow runs finished')
        except Exception:
            logger.error()
        logger.info(f'Workflow manager started as {self.name}')

    def sync(self):
        """Follow [JOBS] enabled after rapo.ini was reloaded."""
        if self.enabled() and not self.active:
            self.start()
        elif not self.enabled() and self.active:
            logger.info('[JOBS] enabled switched off: stopping the workflow '
                        'manager')
            self.stop()

    def stop(self, reason='the workflow manager was stopped'):
        """Stop the manager: the runs that wait or run are canceled."""
        if not self.active:
            return
        self.active = False
        with self.lock:
            runs = list(self.runs.values())
        for run in runs:
            run.cancel_reason = f'Stopped because {reason}.'
            run.cancel.set()
        for run in runs:
            if run.thread is not None:
                run.thread.join(CANCEL_WAIT_SECONDS + 15)
        logger.info('Workflow manager stopped')

    # Starting

    def submit(self, name, trigger, scheduled_time=None, requested_by=None,
               parameters=None):
        """Initiate a run of the workflow and start it. Returns the ID of
        the run.

        A start while the workflow still runs or waits follows its
        `overlap`: refused for a manual start, recorded as a canceled run for
        a scheduled one (`skip`); waits for the previous run (`queue`); or
        runs at once (`allow`). `parameters` are values for this run of the
        parameters the workflow defines.
        """
        if not self.active:
            raise store.StoreError('The workflow manager is not running.',
                                   503)
        row = workflow_store.get_workflow(name)
        definition = workflow.normalize(row['definition'])
        if not definition['steps']:
            raise store.StoreError(
                f'Workflow {name} has no steps: add a job.', 409)
        given = self._check_parameters(definition, parameters)
        with self.submitting:
            busy = workflow_store.has_active(row['workflow_id'])
            if busy and definition['overlap'] == 'skip':
                return self._skip(row, trigger, scheduled_time, requested_by)
            run_id = workflow_store.add_run(
                row, trigger, scheduled_time=scheduled_time,
                requested_by=requested_by, runner=self.name)
        event_id = self._record(row['workflow_id'], trigger, FIRED,
                                scheduled_time, run_id)
        run = WorkflowRun(run_id, row, definition, trigger, scheduled_time,
                          requested_by, given, event_id)
        run.thread = th.Thread(name=f'Workflow-{run_id}', target=self._run,
                               args=(run,), daemon=True)
        with self.lock:
            self.runs[run_id] = run
        run.thread.start()
        logger.info(f'Workflow {name} started as run {run_id} ({trigger})')
        self._notify()
        return run_id

    @staticmethod
    def _check_parameters(definition, parameters):
        given = {}
        names = {item['name'] for item in definition.get('parameters') or []}
        for key, value in (parameters or {}).items():
            if key not in names:
                raise store.StoreError(
                    f'The workflow has no parameter {key}.', 400)
            given[key] = '' if value is None else str(value)
        return given

    def _skip(self, row, trigger, scheduled_time, requested_by):
        """Refuse a manual start, record a scheduled one as canceled, of a
        workflow that is still running or waiting."""
        if trigger == store.MANUAL:
            raise store.StoreError(
                f'Workflow {row["workflow_name"]} is already running or '
                'waiting.', 409)
        run_id = workflow_store.add_run(
            row, trigger, scheduled_time=scheduled_time,
            requested_by=requested_by, runner=self.name)
        message = 'Skipped: the previous run is still running or waiting.'
        workflow_store.skip_steps(run_id, 1, message)
        workflow_store.update_run(run_id, status=store.CANCELED,
                                  end_time=store.now(), message=message)
        self._record(row['workflow_id'], trigger, CANCELED, scheduled_time,
                     run_id, message)
        logger.info(f'Workflow {row["workflow_name"]} start skipped: '
                    f'{message}')
        return run_id

    def cancel(self, run_id):
        """Cancel a run of this manager, False when it is not its own or has
        ended. The step that runs is canceled and the rest skipped."""
        with self.lock:
            run = self.runs.get(run_id)
        if run is None:
            return False
        run.cancel_reason = 'The workflow was canceled on request.'
        run.cancel.set()
        return True

    def busy(self, workflow_id):
        """Check whether a run of the workflow is going here."""
        with self.lock:
            return any(run.workflow_id == workflow_id
                       for run in self.runs.values())

    def status(self):
        """Get the manager status report."""
        with self.lock:
            running = [run.describe() for run in self.runs.values()]
        return {'runner': self.name, 'active': self.active,
                'enabled': self.enabled(), 'running': running}

    # Running

    def _run(self, run):
        """The thread of a run: never raises."""
        try:
            self._go(run)
        except BaseException as error:
            logger.error()
            try:
                run.say('ERROR', f'The workflow failed: '
                                 f'{type(error).__name__}: {error}')
                for line in traceback.format_exc().splitlines():
                    run.say('ERROR', line)
                self._stop(run, store.ERROR,
                           f'The workflow failed: {type(error).__name__}: '
                           f'{error}', step_no=run.step_no,
                           skip_from=(run.step_no or 0) + 1)
            except Exception:
                logger.error()
        finally:
            with self.lock:
                self.runs.pop(run.run_id, None)
            try:
                workflow_store.trim_log(run.workflow_id,
                                        int(setting('log_max_lines')))
            except Exception:
                logger.error()
            self._notify()

    def _go(self, run):
        names = workflow.job_names(run.definition)
        total = len(names)
        if not self._wait_turn(run):
            return self._stop(run, store.CANCELED,
                              run.cancel_reason or 'The workflow was '
                              'canceled.', 'WARNING')
        run.started = dt.datetime.now()
        workflow_store.update_run(
            run.run_id, status=store.RUNNING,
            start_time=run.started.replace(microsecond=0))
        self._event(run, STARTED)
        run.say('INFO', f'Workflow {run.name}, run {run.run_id} '
                        f'({run.trigger}), {total} step'
                        f'{"" if total == 1 else "s"}')
        values, secrets = self._values(run)
        stopped = self._condition(run, values, total)
        if stopped:
            return stopped
        timeout = run.definition.get('timeout')
        if timeout is None:
            timeout = int(setting('timeout') or 0)
        deadline = time.time() + timeout if timeout else None
        for number, name in enumerate(names, 1):
            if run.cancel.is_set():
                return self._stop(run, store.CANCELED, run.cancel_reason,
                                  'WARNING', step_no=number - 1 or None,
                                  skip_from=number)
            run.step_no = number
            workflow_store.update_run(run.run_id, step_no=number)
            run.say('INFO', f'Step {number} of {total}: job {name}')
            outcome = self._step(run, number, total, name, values, secrets,
                                 deadline, timeout)
            if outcome is not None:
                status, message, level = outcome
                return self._stop(run, status, message, level,
                                  step_no=number, skip_from=number + 1)
        self._stop(run, store.DONE, f'Workflow {run.name} is done: {total} '
                                    f'step{"" if total == 1 else "s"}.',
                   'INFO', step_no=total, skip_from=total + 1)

    def _condition(self, run, values, total):
        """Check the condition of the workflow before its first step. Returns
        the end of the run when it is not met (or can not be checked), else
        None."""
        condition = run.definition.get('condition')
        if not condition:
            return None
        try:
            met, value = executor.evaluate_condition(
                condition, values, {}, None, None, None, None,
                owner=('workflow', run.name))
        except (executor.TaskFailed, definitions.DefinitionError) as error:
            return self._stop(run, store.ERROR,
                              f'The condition of the workflow could not be '
                              f'checked: {error}', 'ERROR')
        run.say('INFO', f'Condition {condition["kind"]}: {value}; '
                        f'{"met" if met else "not met"}')
        if met:
            return None
        detail = f': {value}.' if condition['kind'] == 'RUN_STATUS' else '.'
        return self._stop(
            run, store.CANCELED, f'Stopped at the start of the workflow: '
            f'{CONDITION_NOT_MET}{detail}', 'WARNING', skip_from=1) or True

    def _wait_turn(self, run):
        """With the overlap `queue` wait until the earlier runs of the
        workflow have ended. False when the run was canceled meanwhile."""
        if run.definition['overlap'] != 'queue':
            return not run.cancel.is_set()
        announced = False
        while not run.cancel.is_set():
            earlier = [item for item in workflow_store.read_runs(
                run.workflow_id) if item['run_id'] < run.run_id
                and item['status'] in store.ACTIVE]
            if not earlier:
                return True
            if not announced:
                run.say('INFO', 'Waiting for the previous run to end '
                                f'(run {earlier[0]["run_id"]}).')
                announced = True
            time.sleep(POLL_SECONDS)
        return False

    @staticmethod
    def _values(run):
        """Get the values of the parameters of the workflow for this run, and
        the secret ones among them."""
        values, secrets = {}, []
        for item in run.definition.get('parameters') or []:
            value = run.given.get(item['name'], item.get('value', ''))
            values[item['name']] = value
            if item.get('secret') and value:
                secrets.append(value)
        return values, secrets

    def _step(self, run, number, total, name, values, secrets, deadline,
              timeout):
        """Run one step. Returns None when the workflow goes on, else
        (status, message, level) of the end of the workflow."""
        job = store.read_job(name)
        if job is None:
            workflow_store.update_step(run.run_id, number, status=store.ERROR,
                                       message=f'There is no job {name}.')
            return (store.ERROR, f'Step {number} of {total}: there is no job '
                                 f'{name}.', 'ERROR')
        definition = definitions.normalize(job['definition'])
        names = {item['name'] for item in definition.get('parameters') or []}
        given = {key: value for key, value in values.items() if key in names}
        try:
            job_run_id = self.jobs.submit(
                name, 'workflow', scheduled_time=run.scheduled_time,
                requested_by=run.requested_by, parameters=given,
                workflow_run_id=run.run_id, extra_secrets=secrets)
        except store.StoreError as error:
            workflow_store.update_step(run.run_id, number, status=store.ERROR,
                                       message=str(error))
            return (store.ERROR, f'Step {number} of {total} (job {name}) '
                                 f'could not be started: {error}', 'ERROR')
        run.job_run_id = job_run_id
        workflow_store.update_step(run.run_id, number, status=store.INITIATED,
                                   job_run_id=job_run_id)
        header, reason = self._follow(run, number, job_run_id, deadline)
        run.job_run_id = None
        status = header['status']
        took = _seconds(header.get('start_time'), header.get('end_time'))
        if reason == 'timeout':
            return (store.ERROR,
                    f'The workflow was stopped after the timeout of '
                    f'{timeout} seconds, at step {number} of {total} (job '
                    f'{name}).', 'ERROR')
        if reason == 'canceled' or (run.cancel.is_set()
                                    and status == store.CANCELED):
            return (store.CANCELED, run.cancel_reason or
                    'The workflow was canceled.', 'WARNING')
        if status == store.DONE:
            run.say('INFO', f'Step {number} of {total}: job {name} is done'
                            f' in {took} s.')
            return None
        message = header.get('message') or ''
        if status == store.ERROR:
            return (store.ERROR, f'Step {number} of {total} (job {name}) '
                                 f'failed: {message}', 'ERROR')
        # Canceled: its condition was not met, or someone canceled it.
        if header.get('stage') == 'CONDITION' and \
                message.startswith(CONDITION_NOT_MET):
            return (store.CANCELED, f'Stopped at step {number} of {total} '
                                    f'(job {name}): {message}', 'WARNING')
        return (store.CANCELED, f'Stopped at step {number} of {total} (job '
                                f'{name}): the run was canceled: {message}',
                'WARNING')

    def _follow(self, run, number, job_run_id, deadline):
        """Wait for the run of the job of a step to end, keeping the step
        row as the run is. Returns its row and why it was stopped by the
        workflow ('canceled', 'timeout') or None."""
        reason = None
        stopped_at = None
        started = False
        while True:
            header = store.read_run_header(job_run_id)
            if header is None:
                return {'status': store.ERROR, 'message':
                        'The run of the job is gone.'}, reason
            status = header['status']
            if status == store.RUNNING and not started:
                started = True
                workflow_store.update_step(
                    run.run_id, number, status=store.RUNNING,
                    start_time=header.get('start_time'))
            if status not in store.ACTIVE:
                workflow_store.update_step(
                    run.run_id, number, status=status,
                    start_time=header.get('start_time'),
                    end_time=header.get('end_time'),
                    message=header.get('message'))
                return header, reason
            if reason is None and run.cancel.is_set():
                reason = 'canceled'
            if reason is None and deadline and time.time() > deadline:
                reason = 'timeout'
            if reason is not None and stopped_at is None:
                stopped_at = time.time()
                run.say('WARNING', f'Canceling step {number} (run '
                                   f'{job_run_id}).')
                self.jobs.cancel(job_run_id)
            if stopped_at and time.time() - stopped_at > CANCEL_WAIT_SECONDS:
                return {'status': store.CANCELED, 'message':
                        'The run did not end after it was canceled.'}, reason
            time.sleep(POLL_SECONDS)

    def _stop(self, run, status, message, level='ERROR', step_no=None,
              skip_from=1):
        """End the run: skip the steps from `skip_from` that have not
        started, record the result and send the mail of an error."""
        workflow_store.skip_steps(
            run.run_id, skip_from,
            'Skipped: the workflow stopped before it got to this step.'
            if step_no is None else
            f'Skipped: the workflow stopped at step {step_no}.')
        run.say(level, message)
        workflow_store.update_run(run.run_id, status=status,
                                  end_time=store.now(), message=message,
                                  step_no=step_no, log_lines=run.lines)
        if status == store.CANCELED:
            self._event(run, CANCELED, message)
        elif status == store.ERROR:
            self._event(run, FAILED, message)
            self._mail(run)

    def _mail(self, run):
        try:
            row = workflow_store.read_run(run.run_id) or {}
            steps = row.get('steps') or []
            failed = next((step for step in steps
                           if step['status'] == store.ERROR
                           and step.get('job_run_id')), None)
            step_tail = []
            if failed:
                step_run = store.read_run(failed['job_run_id']) or {}
                step_tail = [line['text'] for line in
                             step_run.get('lines') or []]
            status = notify.send_workflow_failure(
                run.workflow, row, steps, list(run.tail), step_tail)
            workflow_store.update_run(run.run_id, mail_status=status)
        except Exception:
            logger.error()

    # Events

    def _record(self, workflow_id, trigger, event_type, scheduled_time=None,
                run_id=None, message=None):
        try:
            return store.add_event(None, trigger, event_type,
                                   scheduled_time=scheduled_time,
                                   run_id=run_id, message=message,
                                   runner=self.name,
                                   workflow_id=workflow_id)
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


manager = WorkflowManager()
