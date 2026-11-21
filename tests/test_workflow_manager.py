"""Tests of the run manager of the workflows (rapo/jobs/workflow_manager.py).
Run from the project root:

    python -m unittest tests.test_workflow_manager -v

The steps are real runs of jobs in processes of their own (the `spawn` method
imports the real rapo), so they take a while. The database is the SQLite one
of tests/test_transfer.py.
"""
import os
import time
import unittest

import psutil

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)
from tests.test_job_manager import FakeMailer, SLEEP, py

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
test_transfer._package('rapo.jobs', os.path.join(ROOT, 'rapo', 'jobs'))

from rapo import config as rapo_config  # noqa: E402
from rapo.database import db  # noqa: E402
from rapo.jobs import manager as job_managers  # noqa: E402
from rapo.jobs import notify, store, workflow_manager  # noqa: E402
from rapo.jobs import workflow_store as ws  # noqa: E402

WAIT = 120
FAIL = py('print("about to fail")\nraise SystemExit(3)')
NO_FILE = {'kind': 'FILE_EXISTS', 'path': os.path.join(ROOT, 'no-such-file')}


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        section = rapo_config.Configuration('JOBS')
        section['parallelism'] = 3
        rapo_config.config['JOBS'] = section
        db.engine.dispose()
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)
        self.mailer = FakeMailer()
        self.original = notify._mailer
        notify._mailer = lambda: self.mailer
        self.original_poll = workflow_manager.POLL_SECONDS
        workflow_manager.POLL_SECONDS = 0.1
        self.jobs = job_managers.JobManager()
        self.jobs.start()
        self.workflows = workflow_manager.WorkflowManager(self.jobs)
        self.workflows.start()
        self.assertTrue(self.workflows.active)

    def tearDown(self):
        self.workflows.stop()
        self.jobs.stop()
        notify._mailer = self.original
        workflow_manager.POLL_SECONDS = self.original_poll
        rapo_config.config.pop('JOBS', None)

    def job(self, name, main=None, email=None, **parts):
        value = {'main': main or py(f'print("{name} ran")')}
        value.update(parts)
        return store.save_job(name, value, notify_email=email)

    def flow(self, *names, name='nightly', **settings):
        value = {'steps': list(names)}
        value.update(settings)
        email = value.pop('email', None)
        return ws.save_workflow(name, value, notify_email=email)

    def finished(self, run_id):
        end = time.time() + WAIT
        while time.time() < end:
            row = ws.read_run(run_id)
            if row['status'] not in store.ACTIVE:
                return row
            time.sleep(0.2)
        self.fail(f'workflow run {run_id} did not end: '
                  f'{ws.read_run(run_id)}')

    def until(self, check, what):
        end = time.time() + WAIT
        while time.time() < end:
            if check():
                return
            time.sleep(0.1)
        self.fail(f'did not get to: {what}')

    @staticmethod
    def steps(row):
        return [(step['step_no'], step['job_name'], step['status'])
                for step in row['steps']]


class FlowTests(WorkflowCase):
    def test_the_steps_run_one_after_another(self):
        for name in ('load', 'process', 'report'):
            self.job(name)
        self.flow('load', 'process', 'report')
        row = self.finished(self.workflows.submit('nightly', 'manual',
                                                  requested_by='ana'))
        self.assertEqual(row['status'], 'D', row['message'])
        self.assertEqual(self.steps(row), [(1, 'load', 'D'),
                                           (2, 'process', 'D'),
                                           (3, 'report', 'D')])
        self.assertEqual((row['step_no'], row['steps_total']), (3, 3))
        runs = {step['job_name']: store.read_run(step['job_run_id'])
                for step in row['steps']}
        for name, run in runs.items():
            self.assertEqual((run['status'], run['trigger_type'],
                              run['workflow_run_id'], run['requested_by']),
                             ('D', 'workflow', row['run_id'], 'ana'), name)
            self.assertIn(f'{name} ran', run['run_log'])
        # One after another: a step starts when the one before it has ended.
        self.assertLessEqual(runs['load']['end_time'],
                             runs['process']['start_time'])
        self.assertLessEqual(runs['process']['end_time'],
                             runs['report']['start_time'])
        self.assertIn('Step 1 of 3: job load', row['run_log'])
        self.assertIn('Workflow nightly is done: 3 steps.', row['run_log'])
        self.assertEqual(self.mailer.sent, [])
        self.assertEqual([item['event_type'] for item in store.read_events()
                          if item['workflow_id']], ['STARTED'])

    def test_a_failed_step_stops_the_workflow_and_skips_the_rest(self):
        self.job('load')
        self.job('process', main=FAIL, email='job@x.com')
        self.job('report')
        self.flow('load', 'process', 'report', email='flow@x.com')
        row = self.finished(self.workflows.submit('nightly', 'schedule'))
        self.assertEqual(row['status'], 'E')
        self.assertEqual(self.steps(row), [(1, 'load', 'D'),
                                           (2, 'process', 'E'),
                                           (3, 'report', 'K')])
        self.assertIn('Step 2 of 3 (job process) failed', row['message'])
        self.assertEqual(row['step_no'], 2)
        self.assertIn('Skipped', row['steps'][2]['message'])
        # The step that was skipped never ran.
        self.assertEqual(store.read_runs(store.read_job('report')['job_id']),
                         [])
        error_lines = [line for line in row['lines']
                       if line['level'] == 'ERROR']
        self.assertTrue(error_lines)

    def test_one_mail_per_workflow_and_none_from_the_job(self):
        self.job('process', main=FAIL, email='job@x.com')
        self.flow('process', email='flow@x.com; ops@x.com')
        row = self.finished(self.workflows.submit('nightly', 'schedule'))
        self.assertEqual(row['status'], 'E')
        self.assertEqual(len(self.mailer.sent), 1)
        message, recipients = self.mailer.sent[0]
        self.assertEqual(recipients, ['flow@x.com', 'ops@x.com'])
        self.assertNotIn('job@x.com', recipients)
        self.assertIn('Workflow nightly failed', message['Subject'])
        body = message.get_content()
        self.assertIn('Failed at:  step 1 of 1, the job process', body)
        self.assertIn('1. process: error', body)
        # The output of the step that failed is in the mail.
        self.assertIn('of the log of step 1 (the job process)', body)
        self.assertIn('about to fail', body)
        self.assertTrue(row['mail_status'].startswith('Sent to flow@x.com'))
        step = store.read_run(row['steps'][0]['job_run_id'])
        self.assertEqual(step['status'], 'E')
        self.assertIn('step of a workflow', step['mail_status'])

    def test_a_workflow_without_an_address_uses_the_default_one(self):
        self.job('process', main=FAIL, email='job@x.com')
        self.flow('process')
        section = rapo_config.config['JOBS']
        section['default_email'] = 'ops@example.com'
        row = self.finished(self.workflows.submit('nightly', 'manual'))
        self.assertEqual(self.mailer.sent[0][1], ['ops@example.com'])
        self.assertEqual(row['status'], 'E')

    def test_a_mail_that_can_not_be_sent_is_told_in_the_run(self):
        self.job('process', main=FAIL)
        self.flow('process')
        row = self.finished(self.workflows.submit('nightly', 'manual'))
        self.assertIn('no address', row['mail_status'])
        self.assertEqual(row['status'], 'E')

    def test_a_condition_not_met_stops_the_workflow_as_a_warning(self):
        self.job('load')
        self.job('process', condition=NO_FILE, email='job@x.com')
        self.job('report')
        self.flow('load', 'process', 'report', email='flow@x.com')
        row = self.finished(self.workflows.submit('nightly', 'schedule'))
        self.assertEqual(row['status'], 'C')
        self.assertEqual(self.steps(row), [(1, 'load', 'D'),
                                           (2, 'process', 'C'),
                                           (3, 'report', 'K')])
        self.assertEqual(
            row['message'], 'Stopped at step 2 of 3 (job process): The '
                            'condition was not met.')
        warning = [line for line in row['lines']
                   if line['level'] == 'WARNING']
        self.assertEqual([line['text'] for line in warning], [row['message']])
        self.assertEqual(self.mailer.sent, [])
        self.assertIsNone(row['mail_status'])
        self.assertEqual([item['event_type'] for item in store.read_events()
                          if item['workflow_id']], ['CANCELED'])

    def test_the_condition_of_the_workflow_is_checked_before_the_first_step(
            self):
        self.job('prep')
        self.job('load')
        self.flow('load', condition={'kind': 'RUN_STATUS', 'targets': [
            {'type': 'job', 'name': 'prep', 'window': {'kind': 'today'}}]})
        row = self.finished(self.workflows.submit('nightly', 'schedule'))
        self.assertEqual((row['status'], self.steps(row)),
                         ('C', [(1, 'load', 'K')]))
        self.assertTrue(row['message'].startswith(
            'Stopped at the start of the workflow: The condition was not '
            'met: the job prep has not run today'), row['message'])
        self.assertEqual(self.mailer.sent, [])
        self.assertEqual([line['level'] for line in row['lines']
                          if line['level'] == 'WARNING'], ['WARNING'])
        # Once prep is done, the workflow goes on.
        prep = store.read_job('prep')
        run_id = store.add_run(prep, store.MANUAL)
        store.update_run(run_id, status=store.DONE, end_time=store.now())
        row = self.finished(self.workflows.submit('nightly', 'schedule'))
        self.assertEqual((row['status'], self.steps(row)),
                         ('D', [(1, 'load', 'D')]), row['message'])

    def test_a_workflow_condition_on_a_file(self):
        self.job('load')
        self.flow('load', condition={
            'kind': 'FILE_EXISTS', 'path': os.path.join(os.getcwd(), 'no-such-file-xyz')})
        row = self.finished(self.workflows.submit('nightly', 'schedule'))
        self.assertEqual(row['status'], 'C')
        self.assertTrue(row['message'].endswith('The condition was not met.'))

    def test_a_condition_that_can_not_be_checked_is_an_error(self):
        self.job('load')
        self.flow('load', condition={'kind': 'RUN_STATUS', 'targets': [
            {'type': 'control', 'name': 'nope',
             'window': {'kind': 'today'}}]}, email='flow@x.com')
        row = self.finished(self.workflows.submit('nightly', 'schedule'))
        self.assertEqual(row['status'], 'E')
        self.assertIn('could not be checked', row['message'])

    def test_a_job_that_is_off_still_runs_as_a_step(self):
        self.job('load')
        store.set_status('load', False)
        self.flow('load')
        row = self.finished(self.workflows.submit('nightly', 'manual'))
        self.assertEqual(row['status'], 'D')

    def test_a_job_of_a_workflow_still_runs_by_itself(self):
        self.job('load')
        self.flow('load')
        run_id = self.jobs.submit('load', store.MANUAL)
        end = time.time() + WAIT
        while time.time() < end and store.read_run(run_id)['status'] in 'IS':
            time.sleep(0.2)
        row = store.read_run(run_id)
        self.assertEqual((row['status'], row['trigger_type'],
                          row['workflow_run_id']), ('D', 'manual', None))

    def test_what_can_not_be_started(self):
        self.job('load')
        self.flow('load')
        with self.assertRaises(store.StoreError) as caught:
            self.workflows.submit('missing', 'manual')
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(store.StoreError) as caught:
            self.workflows.submit('nightly', 'manual',
                                  parameters={'nope': 'x'})
        self.assertEqual(caught.exception.status, 400)
        store.delete_job('load')
        ws.remove_job('load')
        with self.assertRaises(store.StoreError) as caught:
            self.workflows.submit('nightly', 'manual')
        self.assertEqual(caught.exception.status, 409)
        self.workflows.stop()
        with self.assertRaises(store.StoreError) as caught:
            self.workflows.submit('nightly', 'manual')
        self.assertEqual(caught.exception.status, 503)

    def test_a_job_that_was_deleted_after_the_workflow_was_saved(self):
        self.job('load')
        self.job('process')
        self.flow('load', 'process')
        store.delete_job('process')
        row = self.finished(self.workflows.submit('nightly', 'manual'))
        self.assertEqual(row['status'], 'E')
        self.assertEqual(self.steps(row), [(1, 'load', 'D'),
                                           (2, 'process', 'E')])
        self.assertIn('there is no job process', row['message'])


class ParameterTests(WorkflowCase):
    PRINT = py('import os\nprint("who", os.environ.get("RAPO_PARAM_WHO"), '
               '"pw", os.environ.get("RAPO_PARAM_PW"))')

    def log(self, row, number):
        return store.read_run(row['steps'][number - 1]['job_run_id'])[
            'run_log']

    def test_a_parameter_of_the_workflow_overrides_that_of_the_job(self):
        self.job('a', main=self.PRINT,
                 parameters=[{'name': 'who', 'value': 'job-a'}])
        self.job('b', main=self.PRINT,
                 parameters=[{'name': 'who', 'value': 'job-b'}])
        self.job('c', main=self.PRINT)
        self.flow('a', 'b', 'c', parameters=[{'name': 'who',
                                              'value': 'workflow'}])
        row = self.finished(self.workflows.submit('nightly', 'manual'))
        self.assertEqual(row['status'], 'D', row['message'])
        self.assertIn('who workflow', self.log(row, 1))
        self.assertIn('who workflow', self.log(row, 2))
        # A job that has no such parameter is not given it.
        self.assertIn('who None', self.log(row, 3))

    def test_a_run_by_hand_may_give_other_values(self):
        self.job('a', main=self.PRINT,
                 parameters=[{'name': 'who', 'value': 'job-a'}])
        self.flow('a', parameters=[{'name': 'who', 'value': 'workflow'}])
        row = self.finished(self.workflows.submit(
            'nightly', 'manual', parameters={'who': 'bob'}))
        self.assertIn('who bob', self.log(row, 1))
        # The job itself is not changed.
        self.assertIn('job-a', store.read_job('a')['definition'])

    def test_a_secret_of_the_workflow_is_hidden_in_the_log_of_the_step(self):
        # The job does not know the parameter is a secret: the workflow does.
        self.job('a', main=self.PRINT,
                 parameters=[{'name': 'pw', 'value': 'plain'}])
        self.flow('a', parameters=[{'name': 'pw', 'value': 'hunter2',
                                    'secret': True}])
        row = self.finished(self.workflows.submit('nightly', 'manual'))
        self.assertEqual(row['status'], 'D', row['message'])
        log = self.log(row, 1)
        self.assertNotIn('hunter2', log)
        self.assertIn('pw ***', log)
        self.assertNotIn('hunter2', row['run_log'])


class StopTests(WorkflowCase):
    def test_a_cancel_stops_the_step_that_runs_and_skips_the_rest(self):
        self.job('load', main=SLEEP)
        self.job('process')
        self.flow('load', 'process', email='flow@x.com')
        run_id = self.workflows.submit('nightly', 'manual')
        self.until(lambda: ws.read_steps(run_id)[0]['status'] == 'S',
                   'the first step runs')
        step = ws.read_steps(run_id)[0]
        pid = self.jobs.status()['running'][0]['pid']
        self.assertTrue(self.workflows.cancel(run_id))
        row = self.finished(run_id)
        self.assertEqual(row['status'], 'C')
        self.assertEqual(row['message'], 'The workflow was canceled on request.')
        self.assertEqual(self.steps(row), [(1, 'load', 'C'),
                                           (2, 'process', 'K')])
        self.assertEqual(store.read_run(step['job_run_id'])['status'], 'C')
        for _ in range(50):
            if not psutil.pid_exists(pid):
                break
            time.sleep(0.1)
        self.assertFalse(psutil.pid_exists(pid))
        self.assertEqual(self.mailer.sent, [])
        self.assertFalse(self.workflows.cancel(run_id))
        self.assertEqual(self.workflows.status()['running'], [])

    def test_a_workflow_over_its_timeout_is_stopped_as_an_error(self):
        self.job('load', main=SLEEP)
        self.job('process')
        self.flow('load', 'process', timeout=3, email='flow@x.com')
        row = self.finished(self.workflows.submit('nightly', 'manual'))
        self.assertEqual(row['status'], 'E')
        self.assertIn('timeout of 3 seconds, at step 1 of 2 (job load)',
                      row['message'])
        self.assertEqual(self.steps(row), [(1, 'load', 'C'),
                                           (2, 'process', 'K')])
        self.assertEqual(len(self.mailer.sent), 1)

    def test_the_manager_that_stops_cancels_what_runs(self):
        self.job('load', main=SLEEP)
        self.flow('load')
        run_id = self.workflows.submit('nightly', 'manual')
        self.until(lambda: ws.read_steps(run_id)[0]['status'] == 'S',
                   'the step runs')
        self.workflows.stop()
        row = ws.read_run(run_id)
        self.assertEqual(row['status'], 'C')
        self.assertIn('Stopped because the workflow manager was stopped',
                      row['message'])

    def test_runs_left_active_by_a_crash_are_finished_at_start(self):
        self.job('load')
        saved = self.flow('load')
        self.workflows.stop()
        mine = ws.add_run(saved, 'manual', runner=self.workflows.name)
        self.workflows = workflow_manager.WorkflowManager(self.jobs)
        self.workflows.start()
        row = ws.read_run(mine)
        self.assertEqual(row['status'], 'E')
        self.assertIn('unexpectedly', row['message'])


class OverlapTests(WorkflowCase):
    def test_skip_refuses_a_manual_start_and_records_a_scheduled_one(self):
        self.job('load', main=SLEEP)
        self.flow('load')
        first = self.workflows.submit('nightly', 'manual')
        self.until(lambda: ws.read_steps(first)[0]['status'] == 'S',
                   'the first runs')
        with self.assertRaises(store.StoreError) as caught:
            self.workflows.submit('nightly', 'manual')
        self.assertEqual(caught.exception.status, 409)
        skipped = self.workflows.submit('nightly', 'schedule')
        row = ws.read_run(skipped)
        self.assertEqual(row['status'], 'C')
        self.assertIn('Skipped', row['message'])
        self.assertEqual([step['status'] for step in row['steps']], ['K'])
        self.assertEqual(self.mailer.sent, [])
        self.workflows.cancel(first)
        self.finished(first)

    def test_queue_waits_for_the_previous_run(self):
        self.job('load', main=py('import time\ntime.sleep(2)'))
        self.flow('load', overlap='queue')
        first = self.workflows.submit('nightly', 'manual')
        second = self.workflows.submit('nightly', 'manual')
        self.until(lambda: ws.read_steps(first)[0]['status'] == 'S',
                   'the first runs')
        self.assertEqual(ws.read_run(second)['status'], 'I')
        one, two = self.finished(first), self.finished(second)
        self.assertEqual((one['status'], two['status']), ('D', 'D'))
        self.assertLessEqual(one['end_time'], two['start_time'])
        self.assertIn('Waiting for the previous run', two['run_log'])

    def test_allow_starts_the_second_run_at_once_but_a_job_runs_once(self):
        # Both runs are going, but a step waits for a run of its job that is
        # still going, whatever the overlap of the job: the job is not run
        # twice at once.
        self.job('load', main=py('import time\ntime.sleep(3)'),
                 overlap='allow')
        self.flow('load', overlap='allow')
        first = self.workflows.submit('nightly', 'manual')
        self.until(lambda: ws.read_steps(first)[0]['status'] == 'S',
                   'the first runs')
        second = self.workflows.submit('nightly', 'manual')
        self.until(lambda: ws.read_run(second)['status'] == 'S',
                   'the second is going')
        self.assertEqual(ws.read_run(first)['status'], 'S')
        one, two = self.finished(first), self.finished(second)
        self.assertEqual((one['status'], two['status']), ('D', 'D'))
        runs = [store.read_run(row['steps'][0]['job_run_id'])
                for row in (one, two)]
        self.assertLessEqual(runs[0]['end_time'], runs[1]['start_time'])

    def test_a_step_waits_for_its_job_when_it_is_running_by_itself(self):
        # The job refuses to overlap with itself, which a manual start of
        # it would be refused for: the step waits instead.
        self.job('load', main=py('import time\ntime.sleep(3)\nprint("done")'))
        self.job('process')
        self.flow('load', 'process')
        alone = self.jobs.submit('load', store.MANUAL)
        self.until(lambda: store.read_run(alone)['status'] == 'S',
                   'the job runs by itself')
        run_id = self.workflows.submit('nightly', 'manual')
        row = self.finished(run_id)
        self.assertEqual(row['status'], 'D', row['message'])
        by_itself = store.read_run(alone)
        step = store.read_run(row['steps'][0]['job_run_id'])
        self.assertEqual((by_itself['status'], step['status']), ('D', 'D'))
        self.assertLessEqual(by_itself['end_time'], step['start_time'])

    def test_a_step_does_not_hold_a_slot_while_the_workflow_waits(self):
        # Two slots, a workflow of one job: the workflow itself takes none.
        rapo_config.config['JOBS']['parallelism'] = 1
        self.job('load', main=py('import time\ntime.sleep(2)'))
        self.job('other')
        self.flow('load')
        run_id = self.workflows.submit('nightly', 'manual')
        self.until(lambda: ws.read_steps(run_id)[0]['status'] == 'S',
                   'the step runs')
        self.assertEqual(len(self.jobs.status()['running']), 1)
        self.assertEqual(self.jobs.status()['capacity'], 1)
        self.finished(run_id)


if __name__ == '__main__':
    unittest.main()
