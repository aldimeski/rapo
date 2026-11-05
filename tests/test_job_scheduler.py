"""Tests of the job scheduler (rapo/jobs/scheduler.py) and of the mail of a
failed run (rapo/jobs/notify.py). Run from the project root:

    python -m unittest tests.test_job_scheduler -v

The run manager is replaced by one that only notes what it is asked to
start, and the database is the SQLite one of tests/test_transfer.py.
"""
import datetime as dt
import os
import unittest

import sqlalchemy as sa

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)
from tests.test_job_manager import FakeMailer
from tests.test_lease import metadata as lease_metadata, table as lease_table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
test_transfer._package('rapo.jobs', os.path.join(ROOT, 'rapo', 'jobs'))

from rapo import config as rapo_config  # noqa: E402
from rapo.core import lease as leases  # noqa: E402
from rapo.database import db  # noqa: E402
from rapo.jobs import notify, scheduler as schedulers, store  # noqa: E402

EVERY_SECOND = '{"sec": "/1"}'
EVERY_MINUTE = '{"min": "/1", "sec": "0"}'


class FakeManager:
    name = 'test:8080'

    def __init__(self):
        self.submitted = []
        self.fail = None

    def submit(self, name, trigger, scheduled_time=None, **kwargs):
        if self.fail:
            raise self.fail
        self.submitted.append((name, trigger, scheduled_time))
        return len(self.submitted)

    def status(self):
        return {'active': True}


def job(name='nightly', schedule=EVERY_SECOND, status='Y'):
    saved = store.save_job(name, {'main': {'type': 'SQL', 'text': 'commit'}},
                           schedule_config=schedule)
    if status == 'N':
        store.set_status(name, False)
    return saved


class SchedulerCase(unittest.TestCase):
    def setUp(self):
        rapo_config.config.pop('JOBS', None)
        db.engine.dispose()
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)
        lease_metadata.drop_all(db.engine)
        lease_metadata.create_all(db.engine)
        for row in (leases.SCHEDULER_ROW, leases.JOBS_ROW):
            db.execute(lease_table.insert().values(id=row, status='N',
                                                   disabled='N'))
        self.manager = FakeManager()
        self.scheduler = schedulers.JobScheduler(self.manager)
        self.scheduler.lease._table = lease_table

    def tearDown(self):
        rapo_config.config.pop('JOBS', None)

    def lead(self):
        self.scheduler._lease()
        self.assertTrue(self.scheduler.leader)
        self.scheduler._load(0)

    def events(self):
        return list(reversed(store.read_events()))


class LeaseTests(SchedulerCase):
    def test_the_lease_is_the_row_of_the_jobs(self):
        self.lead()
        rows = {row['id']: row for row in db.execute(
            lease_table.select(), as_table=True)}
        self.assertEqual(rows[leases.JOBS_ROW]['status'], 'Y')
        self.assertEqual(rows[leases.JOBS_ROW]['instance_id'],
                         self.scheduler.instance_id)
        self.assertEqual(rows[leases.SCHEDULER_ROW]['status'], 'N')
        self.assertIsNone(rows[leases.SCHEDULER_ROW]['instance_id'])

    def test_it_does_not_depend_on_the_scheduler_of_the_controls(self):
        controls = leases.Lease(leases.SCHEDULER_ROW, 'controls:1',
                                lambda: 60, table=lease_table)
        controls.renew_or_acquire()
        self.lead()
        # Stopping the controls leaves the jobs running, and the other way.
        controls.set_disabled(True)
        self.assertEqual(self.scheduler.status()['state'], 'running')
        self.scheduler.disable()
        record = db.execute(lease_table.select().where(
            lease_table.c.id == leases.SCHEDULER_ROW), as_dict=True)
        self.assertEqual(record['disabled'], 'Y')
        self.assertEqual(self.scheduler.status()['state'], 'stopped')
        controls.set_disabled(False)
        self.assertEqual(self.scheduler.status()['disabled'], True)
        self.scheduler.enable()
        self.assertEqual(self.scheduler.status()['disabled'], False)

    def test_the_status(self):
        status = self.scheduler.status()
        self.assertEqual((status['state'], status['leader']),
                         ('starting', False))
        self.lead()
        job()
        self.scheduler.reload = True
        self.scheduler._load(1000)
        status = self.scheduler.status()
        self.assertEqual((status['state'], status['scheduled_jobs']),
                         ('running', 1))
        self.assertEqual(status['holder']['instance_id'],
                         self.scheduler.instance_id)
        self.assertEqual(status['manager'], {'active': True})
        self.scheduler._release()
        self.assertEqual(self.scheduler.status()['state'], 'starting')
        self.assertIsNone(self.scheduler.status()['scheduled_jobs'])

    def test_a_standby_server_sees_the_holder(self):
        other = schedulers.JobScheduler(FakeManager())
        other.lease._table = lease_table
        other._lease()
        self.assertTrue(other.leader)
        self.scheduler._lease()
        self.assertFalse(self.scheduler.leader)
        self.assertEqual(self.scheduler.status()['state'], 'standby')

    def test_the_options_decide_whether_it_runs_here(self):
        section = rapo_config.Configuration('JOBS')
        section['enabled'] = False
        rapo_config.config['JOBS'] = section
        self.assertFalse(self.scheduler.enabled)
        self.scheduler.start()
        self.assertFalse(self.scheduler.active)
        self.assertEqual(self.scheduler.status()['state'], 'off')


class LoadTests(SchedulerCase):
    def test_only_jobs_that_are_on_and_scheduled_are_loaded(self):
        on = job('on')
        job('off', status='N')
        store.save_job('plain', {'main': {'type': 'SQL', 'text': 'commit'}})
        with self.assertRaises(store.StoreError):
            job('broken', schedule='not json')
        self.lead()
        self.assertEqual(list(self.scheduler.schedule), [on['job_id']])

    def test_a_change_is_seen_by_the_signature(self):
        job()
        first = store.signature()
        self.assertEqual(first, store.signature())
        store.set_status('nightly', False)
        self.assertNotEqual(first, store.signature())
        second = store.signature()
        store.write_schedule('nightly', '{"hour": "3"}')
        self.assertNotEqual(second, store.signature())

    def test_a_schedule_that_can_not_be_read_is_left_out(self):
        saved = job()
        db.execute(store.job_table.update().where(
            store.job_table.c.job_id == saved['job_id']).values(
            schedule_config='{broken'))
        self.assertEqual(store.read_scheduled(), {})

    def test_the_schedule_is_reloaded_when_a_job_changes(self):
        self.lead()
        self.assertEqual(self.scheduler.schedule, {})
        job()
        self.scheduler.checked = 0
        self.scheduler._load(1000)
        self.assertEqual(len(self.scheduler.schedule), 1)


class FireTests(SchedulerCase):
    def setUp(self):
        super().setUp()
        self.now = dt.datetime.now().replace(microsecond=0)

    def test_every_fire_since_the_cursor_is_submitted_in_order(self):
        job()
        self.lead()
        self.scheduler.cursor = self.now - dt.timedelta(seconds=3)
        self.scheduler._fire()
        moments = [item[2] for item in self.manager.submitted]
        self.assertGreaterEqual(len(moments), 3)
        self.assertEqual(moments, sorted(moments))
        self.assertTrue(all(item[:2] == ('nightly', 'schedule')
                            for item in self.manager.submitted))
        self.assertIsNotNone(self.scheduler.last_fire)
        # Nothing is fired twice.
        count = len(self.manager.submitted)
        self.scheduler._fire()
        self.assertLessEqual(len(self.manager.submitted) - count, 1)

    def test_a_fire_found_late_is_recorded_as_missed_not_run(self):
        saved = job(schedule=EVERY_MINUTE)
        self.lead()
        self.scheduler.cursor = self.now - dt.timedelta(minutes=5)
        self.scheduler._fire()
        submitted = [item[2] for item in self.manager.submitted]
        missed = [item for item in self.events()
                  if item['event_type'] == 'MISSED']
        self.assertGreaterEqual(len(missed), 3)
        self.assertTrue(all(item['job_id'] == saved['job_id']
                            for item in missed))
        for moment in submitted:
            self.assertLessEqual((self.now - moment).total_seconds(), 60)
        self.assertEqual(len(submitted) + len(missed), 5)
        self.assertIn('late', missed[0]['message'])

    def test_a_job_that_can_not_be_started_is_recorded_as_failed(self):
        job()
        self.lead()
        self.scheduler.cursor = self.now - dt.timedelta(seconds=2)
        self.manager.fail = store.StoreError('Job nightly not found.', 404)
        self.scheduler._fire()
        failed = [item for item in self.events()
                  if item['event_type'] == 'FAILED']
        self.assertGreaterEqual(len(failed), 2)
        self.assertEqual(failed[0]['message'], 'Job nightly not found.')

    def test_it_stops_firing_when_the_lease_is_lost(self):
        job()
        self.lead()
        self.scheduler.cursor = self.now - dt.timedelta(seconds=3)
        self.scheduler.heartbeat = 0
        db.execute(lease_table.update().where(
            lease_table.c.id == leases.JOBS_ROW).values(
            instance_id='someone-else'))
        self.scheduler._fire()
        self.assertEqual(self.manager.submitted, [])
        self.assertFalse(self.scheduler.leader)

    def test_downtime_is_recorded_when_the_lease_is_taken_over(self):
        saved = job(schedule=EVERY_MINUTE)
        until = self.now
        self.scheduler._record_missed(until - dt.timedelta(minutes=10),
                                      until)
        missed = [item for item in self.events()
                  if item['event_type'] == 'MISSED']
        self.assertGreaterEqual(len(missed), 9)
        self.assertEqual(missed[0]['job_id'], saved['job_id'])
        self.assertEqual(missed[0]['message'],
                         'Job scheduler was not running.')
        before = len(store.read_events())
        self.scheduler._record_missed(None, until)
        self.assertEqual(len(store.read_events()), before)

    def test_downtime_older_than_the_window_is_forgotten(self):
        job(schedule=EVERY_MINUTE)
        until = self.now
        self.scheduler._record_missed(until - dt.timedelta(days=30), until)
        window = 24 * 60
        missed = [item for item in store.read_events(limit=5000)
                  if item['event_type'] == 'MISSED']
        self.assertLessEqual(len(missed), schedulers.MISSED_LIMIT)
        self.assertGreater(len(missed), 0)
        self.assertLessEqual(window, 24 * 60)

    def test_the_next_wake_is_the_earliest_fire(self):
        job(schedule='{"hour": "3", "min": "0", "sec": "0"}')
        self.lead()
        self.scheduler.cursor = self.now
        delay = self.scheduler._delay()
        self.assertLessEqual(delay, schedulers.LEASE_INTERVAL)
        job('often', schedule=EVERY_SECOND)
        self.scheduler.reload = True
        self.scheduler._load(1000)
        self.assertLessEqual(self.scheduler._delay(), 1.5)

    def test_upcoming_fires_of_the_jobs_that_are_on(self):
        job('hourly', schedule='{"min": "0", "sec": "0"}')
        job('off', schedule='{"min": "0", "sec": "0"}', status='N')
        fires = schedulers.upcoming(hours=3)
        self.assertEqual({name for _, name in fires}, {'hourly'})
        self.assertIn(len(fires), (3, 4))
        self.assertEqual(fires, sorted(fires))
        self.assertEqual(schedulers.upcoming(3, job_name='nope'), [])


class MaintenanceTests(SchedulerCase):
    def test_old_runs_and_events_are_deleted(self):
        saved = job()
        old = dt.timedelta(days=400)
        run = store.add_run(saved, store.SCHEDULE)
        store.update_run(run, status=store.DONE,
                         queued_time=store.now() - old)
        event = store.add_event(saved['job_id'], store.SCHEDULE, 'FIRED')
        db.execute(store.event_table.update().where(
            store.event_table.c.event_id == event).values(
            event_time=store.now() - old))
        self.scheduler._clean()
        self.assertEqual(store.read_runs(), [])
        self.assertEqual(store.read_events(), [])

    def test_the_maintenance_runs_at_its_time_only(self):
        self.lead()
        self.scheduler.next_maintenance = 10 ** 12
        self.scheduler._maintain(1)
        self.assertEqual(self.scheduler.next_maintenance, 10 ** 12)


class NotifyTests(unittest.TestCase):
    def setUp(self):
        rapo_config.config.pop('JOBS', None)
        self.mailer = FakeMailer()
        self.original = notify._mailer
        notify._mailer = lambda: self.mailer
        self.job = {'job_name': 'nightly', 'notify_email': 'a@x.com'}
        self.run = {'run_id': 5, 'trigger_type': 'schedule',
                    'scheduled_time': dt.datetime(2026, 11, 5, 3, 0),
                    'start_time': dt.datetime(2026, 11, 5, 3, 0, 2),
                    'end_time': dt.datetime(2026, 11, 5, 3, 1),
                    'stage': 'MAIN', 'exit_code': 2,
                    'message': 'MAIN failed: The exit code is 2.'}

    def tearDown(self):
        notify._mailer = self.original
        rapo_config.config.pop('JOBS', None)

    def test_the_message(self):
        message = notify.build_message(
            self.job, self.run, ['one', 'two'], 'rapo@example.com', 'RAPO',
            'reply@example.com', ['a@x.com', 'b@y.org'])
        self.assertEqual(message['Subject'], 'Job nightly failed')
        self.assertEqual(message['To'], 'a@x.com, b@y.org')
        self.assertIn('rapo@example.com', message['From'])
        self.assertEqual(message['Reply-To'], 'reply@example.com')
        body = message.get_content()
        for text in ('Run:        5 (schedule)', 'Stage:      MAIN',
                     'Exit code:  2', 'MAIN failed: The exit code is 2.',
                     'Last 2 lines of the log:', 'one\ntwo'):
            self.assertIn(text, body)

    def test_the_instance_name_is_in_the_subject(self):
        section = rapo_config.Configuration('SCHEDULER')
        section['instance_name'] = 'AUT Prod'
        rapo_config.config['SCHEDULER'] = section
        try:
            message = notify.build_message(self.job, self.run, [], 'r@x.com')
        finally:
            rapo_config.config.pop('SCHEDULER', None)
        self.assertEqual(message['Subject'], '[AUT Prod] Job nightly failed')

    def test_a_mail_is_sent_to_the_job_address(self):
        status = notify.send_failure(self.job, self.run, ['line'])
        self.assertEqual(status, 'Sent to a@x.com')
        self.assertEqual(self.mailer.sent[0][1], ['a@x.com'])

    def test_the_default_address_serves_a_job_without_one(self):
        section = rapo_config.Configuration('JOBS')
        section['default_email'] = 'ops@example.com'
        rapo_config.config['JOBS'] = section
        self.job['notify_email'] = None
        self.assertEqual(notify.send_failure(self.job, self.run),
                         'Sent to ops@example.com')
        self.job['notify_email'] = 'own@x.com'
        notify.send_failure(self.job, self.run)
        self.assertEqual(self.mailer.sent[1][1], ['own@x.com'])

    def test_why_a_mail_is_not_sent(self):
        self.job['notify_email'] = None
        self.assertIn('no address', notify.send_failure(self.job, self.run))
        self.job['notify_email'] = 'a@x.com'
        self.mailer.params['enabled'] = False
        self.assertIn('enabled is off',
                      notify.send_failure(self.job, self.run))
        self.mailer.params.update(enabled=True, sender=None)
        self.assertIn('sender is not set',
                      notify.send_failure(self.job, self.run))
        self.assertEqual(self.mailer.sent, [])

    def test_a_refused_address_and_a_failing_server_do_not_raise(self):
        self.job['notify_email'] = 'a@x.com, b@y.org'
        self.mailer.deliver = lambda message, to: {'b@y.org': (550, 'no')}
        status = notify.send_failure(self.job, self.run)
        self.assertEqual(status, 'Sent to 1 of 2, refused: b@y.org')

        def refuse(message, to):
            raise OSError('connection refused')
        self.mailer.deliver = refuse
        status = notify.send_failure(self.job, self.run)
        self.assertEqual(status, 'Not sent: OSError: connection refused')


if __name__ == '__main__':
    unittest.main()
