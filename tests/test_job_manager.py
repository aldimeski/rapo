"""Tests of the run manager of the jobs (rapo/jobs/manager.py). Run from the
project root:

    python -m unittest tests.test_job_manager -v

Every run is performed in a process of its own, started with the `spawn`
method, which imports the real rapo; so they take a while. The database is
the SQLite one of tests/test_transfer.py.
"""
import os
import subprocess
import sys
import time
import unittest

import psutil

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
test_transfer._package('rapo.jobs', os.path.join(ROOT, 'rapo', 'jobs'))

from rapo import config as rapo_config  # noqa: E402
from rapo.database import db  # noqa: E402
from rapo.jobs import manager as managers, notify, store  # noqa: E402

WAIT = 90


class FakeMailer:
    """What the manager sends mail with, instead of an SMTP server."""

    def __init__(self, enabled=True, sender='rapo@example.com'):
        self.params = {'enabled': enabled, 'sender': sender,
                       'sender_name': 'RAPO'}
        self.sent = []

    def settings(self):
        return self.params

    @staticmethod
    def addresses(values):
        if isinstance(values, str):
            values = values.replace(';', ',').split(',')
        return [item.strip() for item in values or [] if item.strip()]

    def deliver(self, message, recipients):
        self.sent.append((message, list(recipients)))
        return {}


def py(code):
    return {'type': 'PYTHON', 'code': code}


SLEEP = py('import time\ntime.sleep(60)')


class ManagerCase(unittest.TestCase):
    def setUp(self):
        section = rapo_config.Configuration('JOBS')
        section['parallelism'] = 2
        rapo_config.config['JOBS'] = section
        db.engine.dispose()
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)
        self.mailer = FakeMailer()
        self.original = notify._mailer
        notify._mailer = lambda: self.mailer
        self.manager = managers.JobManager()
        self.manager.start()
        self.assertTrue(self.manager.active)

    def tearDown(self):
        self.manager.stop()
        notify._mailer = self.original
        rapo_config.config.pop('JOBS', None)

    def option(self, name, value):
        rapo_config.config['JOBS'][name] = value

    def make(self, name='nightly', main=None, **definition):
        value = {'main': main or py('print("hi")')}
        value.update(definition)
        return store.save_job(name, value)

    def finished(self, run_id, status=None):
        """Wait until the run is over, get it."""
        end = time.time() + WAIT
        while time.time() < end:
            row = store.read_run(run_id)
            if row['status'] not in store.ACTIVE:
                return row
            time.sleep(0.2)
        self.fail(f'run {run_id} did not finish: {store.read_run(run_id)}')

    def until(self, run_id, status):
        end = time.time() + WAIT
        while time.time() < end:
            if store.read_run(run_id)['status'] == status:
                return
            time.sleep(0.1)
        self.fail(f'run {run_id} did not get to {status}')


class RunTests(ManagerCase):
    def test_a_run_is_performed_and_recorded(self):
        self.make()
        run_id = self.manager.submit('nightly', store.MANUAL,
                                     requested_by='ana')
        row = self.finished(run_id)
        self.assertEqual((row['status'], row['stage'], row['exit_code']),
                         ('D', 'MAIN', 0))
        self.assertEqual(row['requested_by'], 'ana')
        self.assertEqual(row['definition_version'], 1)
        self.assertIsNotNone(row['start_time'])
        self.assertIsNotNone(row['end_time'])
        self.assertIn('hi', row['run_log'].split('\n'))
        self.assertEqual(row['log_lines'], len(row['run_log'].split('\n')))
        self.assertFalse(row['log_trimmed'])
        self.assertEqual(self.mailer.sent, [])
        events = store.read_events()
        self.assertEqual([item['event_type'] for item in events],
                         ['STARTED'])
        self.assertEqual(events[0]['run_id'], run_id)
        self.assertFalse(self.manager.busy(row['job_id']))

    def test_a_failed_run_is_an_error_and_sends_one_mail(self):
        self.make(main=py('print("before")\nraise SystemExit(3)'),
                  post=py('print("never")'))
        store.save_job('nightly', store.read_job('nightly')['definition'],
                       notify_email='ana@x.com; bob@y.org')
        run_id = self.manager.submit('nightly', store.SCHEDULE)
        row = self.finished(run_id)
        self.assertEqual((row['status'], row['stage'], row['exit_code']),
                         ('E', 'MAIN', 3))
        self.assertTrue(row['message'].startswith('MAIN failed'))
        self.assertNotIn('never', row['run_log'])
        self.assertEqual(len(self.mailer.sent), 1)
        message, recipients = self.mailer.sent[0]
        self.assertEqual(recipients, ['ana@x.com', 'bob@y.org'])
        self.assertIn('nightly', message['Subject'])
        body = message.get_content()
        self.assertIn('MAIN failed', body)
        self.assertIn('before', body)
        self.assertIn('Exit code:  3', body)
        self.assertTrue(row['mail_status'].startswith('Sent to ana@x.com'))

    def test_without_an_address_the_default_one_is_used(self):
        self.make(main=py('raise SystemExit(1)'))
        self.option('default_email', 'ops@example.com')
        row = self.finished(self.manager.submit('nightly', store.MANUAL))
        self.assertEqual(self.mailer.sent[0][1], ['ops@example.com'])
        self.assertEqual(row['status'], 'E')

    def test_a_mail_that_can_not_be_sent_is_told_in_the_run(self):
        self.make(main=py('raise SystemExit(1)'))
        row = self.finished(self.manager.submit('nightly', store.MANUAL))
        self.assertIn('no address', row['mail_status'])
        self.mailer.params['enabled'] = False
        store.save_job('nightly', store.read_job('nightly')['definition'],
                       notify_email='a@x.com')
        row = self.finished(self.manager.submit('nightly', store.MANUAL))
        self.assertIn('[EMAIL] enabled is off', row['mail_status'])
        self.assertEqual(row['status'], 'E')
        self.assertEqual(self.mailer.sent, [])

    def test_a_condition_not_met_is_canceled_without_a_mail(self):
        self.make(condition={'kind': 'FILE_EXISTS',
                             'path': os.path.join(ROOT, 'nothing-here')})
        store.save_job('nightly', store.read_job('nightly')['definition'],
                       notify_email='a@x.com')
        row = self.finished(self.manager.submit('nightly', store.MANUAL))
        self.assertEqual((row['status'], row['stage'],
                          row['condition_value']),
                         ('C', 'CONDITION', 'missing'))
        self.assertEqual(self.mailer.sent, [])

    def test_parameters_are_given_for_one_run(self):
        self.make(main=py('import os\nprint("who", os.environ['
                          '"RAPO_PARAM_WHO"])'),
                  parameters=[{'name': 'who', 'value': 'ana'}])
        first = self.finished(self.manager.submit('nightly', store.MANUAL))
        second = self.finished(self.manager.submit(
            'nightly', store.MANUAL, parameters={'who': 'bob'}))
        self.assertIn('who ana', first['run_log'])
        self.assertIn('who bob', second['run_log'])
        with self.assertRaises(store.StoreError) as caught:
            self.manager.submit('nightly', store.MANUAL,
                                parameters={'nope': 'x'})
        self.assertEqual(caught.exception.status, 400)

    def test_a_job_that_is_not_there_or_a_manager_that_is_off(self):
        with self.assertRaises(store.StoreError) as caught:
            self.manager.submit('missing', store.MANUAL)
        self.assertEqual(caught.exception.status, 404)
        self.manager.stop()
        with self.assertRaises(store.StoreError) as caught:
            self.manager.submit('missing', store.MANUAL)
        self.assertEqual(caught.exception.status, 503)

    def test_a_process_that_dies_is_an_error(self):
        self.make(main=SLEEP)
        run_id = self.manager.submit('nightly', store.MANUAL)
        self.until(run_id, 'S')
        pid = self.manager.status()['running'][0]['pid']
        psutil.Process(pid).kill()
        row = self.finished(run_id)
        self.assertEqual(row['status'], 'E')
        self.assertIn('ended unexpectedly', row['message'])


class StopTests(ManagerCase):
    def test_a_cancel_kills_the_run_and_what_it_started(self):
        code = ('import subprocess, sys, time\n'
                'child = subprocess.Popen([sys.executable, "-c", '
                '"import time; time.sleep(60)"])\n'
                'print("child", child.pid, flush=True)\ntime.sleep(60)\n')
        self.make(main=py(code))
        store.save_job('nightly', store.read_job('nightly')['definition'],
                       notify_email='a@x.com')
        run_id = self.manager.submit('nightly', store.MANUAL)
        child = None
        end = time.time() + WAIT
        while time.time() < end and child is None:
            for line in (store.read_run(run_id)['run_log'] or '').split('\n'):
                if line.startswith('child '):
                    child = int(line.split()[1])
            time.sleep(0.2)
        self.assertIsNotNone(child, 'the log of a running job is written')
        self.assertTrue(psutil.pid_exists(child))
        pid = self.manager.status()['running'][0]['pid']
        self.assertTrue(self.manager.cancel(run_id))
        row = self.finished(run_id)
        self.assertEqual(row['status'], 'C')
        self.assertIn('canceled', row['message'])
        self.assertEqual(self.mailer.sent, [])
        for _ in range(50):
            if not psutil.pid_exists(child) and not psutil.pid_exists(pid):
                break
            time.sleep(0.1)
        self.assertFalse(psutil.pid_exists(child))
        self.assertFalse(psutil.pid_exists(pid))
        self.assertFalse(self.manager.cancel(run_id))
        self.assertEqual(self.manager.status()['running'], [])

    def test_a_waiting_run_can_be_canceled(self):
        self.option('parallelism', 1)
        self.make('slow', main=SLEEP)
        self.make('other')
        first = self.manager.submit('slow', store.MANUAL)
        self.until(first, 'S')
        second = self.manager.submit('other', store.MANUAL)
        self.assertEqual(store.read_run(second)['status'], 'I')
        self.assertTrue(self.manager.cancel(second))
        self.assertEqual(store.read_run(second)['status'], 'C')
        self.manager.cancel(first)
        self.finished(first)

    def test_a_run_over_its_timeout_is_stopped_and_mailed(self):
        self.make(main=SLEEP, timeout=10)
        store.save_job('nightly', store.read_job('nightly')['definition'],
                       notify_email='a@x.com')
        row = self.finished(self.manager.submit('nightly', store.SCHEDULE))
        self.assertEqual(row['status'], 'E')
        self.assertIn('timeout of 10 seconds', row['message'])
        self.assertEqual(len(self.mailer.sent), 1)

    def test_stop_kills_every_run(self):
        self.make(main=SLEEP)
        run_id = self.manager.submit('nightly', store.MANUAL)
        self.until(run_id, 'S')
        pid = self.manager.status()['running'][0]['pid']
        self.manager.stop()
        row = store.read_run(run_id)
        self.assertEqual(row['status'], 'C')
        self.assertFalse(psutil.pid_exists(pid))
        self.assertEqual(self.mailer.sent, [])

    def test_runs_left_active_by_a_crash_are_finished_at_start(self):
        self.manager.stop()
        job = self.make()
        run_id = store.add_run(job, store.SCHEDULE, runner=self.manager.name)
        other = store.add_run(job, store.SCHEDULE, runner='another:1')
        self.manager = managers.JobManager()
        self.manager.start()
        self.assertEqual(store.read_run(run_id)['status'], 'E')
        self.assertIn('unexpectedly', store.read_run(run_id)['message'])
        self.assertEqual(store.read_run(other)['status'], 'I')


class SlotsTests(ManagerCase):
    def test_no_more_runs_than_slots_and_the_rest_wait(self):
        self.option('parallelism', 1)
        code = 'import time\nprint("start", time.time())\ntime.sleep(2)'
        self.make('first', main=py(code))
        self.make('second', main=py(code))
        one = self.manager.submit('first', store.MANUAL)
        two = self.manager.submit('second', store.MANUAL)
        self.until(one, 'S')
        self.assertEqual(store.read_run(two)['status'], 'I')
        status = self.manager.status()
        self.assertEqual((status['capacity'], len(status['running']),
                          len(status['queued'])), (1, 1, 1))
        first, second = self.finished(one), self.finished(two)
        self.assertEqual((first['status'], second['status']), ('D', 'D'))
        self.assertLessEqual(first['end_time'], second['start_time'])

    def test_two_slots_run_two_jobs_side_by_side(self):
        code = 'import time\ntime.sleep(4)'
        self.make('first', main=py(code))
        self.make('second', main=py(code))
        one = self.manager.submit('first', store.MANUAL)
        two = self.manager.submit('second', store.MANUAL)
        self.until(one, 'S')
        self.until(two, 'S')
        self.assertEqual(len(self.manager.status()['running']), 2)
        self.finished(one)
        self.finished(two)

    def test_skip_refuses_a_manual_start_and_records_a_scheduled_one(self):
        self.make(main=SLEEP)
        first = self.manager.submit('nightly', store.MANUAL)
        self.until(first, 'S')
        with self.assertRaises(store.StoreError) as caught:
            self.manager.submit('nightly', store.MANUAL)
        self.assertEqual(caught.exception.status, 409)
        skipped = self.manager.submit('nightly', store.SCHEDULE)
        row = store.read_run(skipped)
        self.assertEqual(row['status'], 'C')
        self.assertIn('Skipped', row['message'])
        self.assertEqual(self.mailer.sent, [])
        self.assertEqual(len(self.manager.status()['running']), 1)
        self.manager.cancel(first)
        self.finished(first)

    def test_queue_waits_for_the_previous_run_of_the_job(self):
        self.make(main=py('import time\ntime.sleep(2)'), overlap='queue')
        one = self.manager.submit('nightly', store.MANUAL)
        two = self.manager.submit('nightly', store.MANUAL)
        self.until(one, 'S')
        # A slot is free, but the job may not overlap with itself.
        time.sleep(1)
        self.assertEqual(store.read_run(two)['status'], 'I')
        first, second = self.finished(one), self.finished(two)
        self.assertEqual((first['status'], second['status']), ('D', 'D'))
        self.assertLessEqual(first['end_time'], second['start_time'])

    def test_allow_runs_the_same_job_twice_at_once(self):
        self.make(main=py('import time\ntime.sleep(4)'), overlap='allow')
        one = self.manager.submit('nightly', store.MANUAL)
        two = self.manager.submit('nightly', store.MANUAL)
        self.until(one, 'S')
        self.until(two, 'S')
        self.finished(one)
        self.finished(two)

    def test_more_slots_after_a_reload_are_used_at_once(self):
        self.option('parallelism', 1)
        self.make('first', main=py('import time\ntime.sleep(4)'))
        self.make('second', main=py('import time\ntime.sleep(4)'))
        one = self.manager.submit('first', store.MANUAL)
        two = self.manager.submit('second', store.MANUAL)
        self.until(one, 'S')
        self.assertEqual(store.read_run(two)['status'], 'I')
        self.option('parallelism', 2)
        self.until(two, 'S')
        self.finished(one)
        self.finished(two)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        db.engine.dispose()
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)
        rapo_config.config.pop('JOBS', None)

    def tearDown(self):
        rapo_config.config.pop('JOBS', None)

    def test_off_starts_nothing_and_sync_follows_the_option(self):
        section = rapo_config.Configuration('JOBS')
        section['enabled'] = False
        rapo_config.config['JOBS'] = section
        manager = managers.JobManager()
        manager.start()
        self.assertFalse(manager.active)
        self.assertEqual(manager.threads, [])
        section['enabled'] = True
        manager.sync()
        try:
            self.assertTrue(manager.active)
            self.assertEqual(len(manager.threads), 2)
            section['enabled'] = False
            manager.sync()
            self.assertFalse(manager.active)
            self.assertEqual(manager.threads, [])
        finally:
            manager.stop()

    def test_the_manager_is_on_by_default(self):
        self.assertTrue(managers.JobManager.enabled())


if __name__ == '__main__':
    unittest.main()
