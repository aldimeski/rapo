"""Tests of the groups of file transfers (rapo/transfer/group_store.py,
groups.py). Run from the project root:

    python -m unittest tests.test_transfer_groups -v

The database is the SQLite one of tests/test_transfer.py. The transfer
manager is the real one, with a thread of the test in place of its dispatcher
and its processes (`Worker`).
"""
import datetime as dt
import threading
import time
import unittest

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)
from tests.test_transfer import StoreTests, when  # noqa: E402

from rapo import config as rapo_config  # noqa: E402
from rapo.database import db  # noqa: E402
from rapo.transfer import group_store as store  # noqa: E402
from rapo.transfer import groups, manager, store as transfer_store  # noqa


TEXT = StoreTests.TEXT


class Worker(threading.Thread):
    """Stands in for the dispatcher and the processes of the transfer
    manager: it starts the runs that can start, one at a time, and ends each
    as `fail` and `hold` say. `started` is the order the runs were started
    in, `going` what ran at the same time."""

    def __init__(self, transfers, fail=(), hold=None):
        super().__init__(daemon=True)
        self.transfers = transfers
        self.fail = set(fail)
        self.hold = hold or {}
        self.started = []
        self.most = 0
        self.stopped = False

    def run(self):
        transfers = self.transfers
        while not self.stopped:
            with transfers.condition:
                job = transfers._eligible()
                if job:
                    transfers.pending.remove(job)
                    transfers.running.append(job)
                    self.most = max(self.most, len(transfers.running))
            if not job:
                time.sleep(0.01)
                continue
            self.started.append(job.name)
            transfer_store.update_run(job.run_id, status='S',
                                      start_time=transfer_store.now())
            gate = self.hold.get(job.name)
            if gate:
                gate.wait(5)
            if job.canceled:
                continue
            ok = job.name not in self.fail
            transfers._finish(job, 'D' if ok else 'E',
                              'ok' if ok else 'The server refused.',
                              {'transferred': 1 if ok else 0,
                               'failed': 0 if ok else 1,
                               'log': [f'log of {job.name}']})
            with transfers.condition:
                transfers.running.remove(job)
                transfers.condition.notify_all()

    def stop(self):
        self.stopped = True


class GroupCase(unittest.TestCase):
    def setUp(self):
        StoreTests.setUp(self)
        rapo_config.config['TRANSFER']['enabled'] = True
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)
        self.transfers = manager.TransferManager()
        self.transfers.active = True
        self.groups = groups.GroupManager(self.transfers)
        self.workers = []
        for name in ('a', 'b', 'c', 'd'):
            transfer_store.write(name, TEXT, transfer_store.FILE,
                                 create=True)

    def tearDown(self):
        # The threads of the runs must be done before the next test takes
        # the database away.
        end = time.time() + 10
        while self.groups.runs and time.time() < end:
            time.sleep(0.02)
        for worker in self.workers:
            worker.stop()
        for worker in self.workers:
            worker.join(5)
        StoreTests.tearDown(self)

    def worker(self, **kwargs):
        worker = Worker(self.transfers, **kwargs)
        self.workers.append(worker)
        worker.start()
        return worker

    def finished(self, run_id, seconds=10):
        """Get the run once it has ended and the manager is done with it
        (its mail is written)."""
        end = time.time() + seconds
        while time.time() < end:
            row = store.read_run(run_id)
            if row['status'] not in store.ACTIVE and not any(
                    item.run_id == run_id for item in self.groups.runs):
                return row
            time.sleep(0.02)
        self.fail(f'The run {run_id} did not end: {row}')

    def save(self, name='nightly', members=('a', 'b', 'c'), **kwargs):
        return store.save_group(name, list(members), **kwargs)


class StoreTests_(GroupCase):
    def test_a_new_group_is_on_skips_and_keeps_the_order(self):
        saved = self.save(members=('c', 'a', 'b'), description='Nightly')
        self.assertEqual((saved['group_name'], saved['status'],
                          saved['overlap']), ('nightly', 'Y', 'skip'))
        self.assertEqual(saved['members'], ['c', 'a', 'b'])
        self.assertEqual(store.read_groups()[0]['members'], ['c', 'a', 'b'])

    def test_the_order_is_changed_by_saving_the_members_again(self):
        self.save(members=('a', 'b', 'c'))
        saved = store.save_group('nightly', ['c', 'b', 'a', 'd'])
        self.assertEqual(saved['members'], ['c', 'b', 'a', 'd'])
        saved = store.save_group('nightly', ['b'])
        self.assertEqual(saved['members'], ['b'])

    def test_what_is_not_given_stays(self):
        self.save(description='x', notify_email='a@x.com', overlap='queue')
        saved = store.save_group('nightly', status='N')
        self.assertEqual((saved['description'], saved['notify_email'],
                          saved['overlap'], saved['status'],
                          saved['members']),
                         ('x', 'a@x.com', 'queue', 'N', ['a', 'b', 'c']))

    def test_what_is_refused(self):
        for members, status in (([], 400), (['a', 'a'], 422),
                                (['a', 'nope'], 422), ('a', 400),
                                (['a b'], 400)):
            with self.assertRaises(store.StoreError, msg=members) as caught:
                store.save_group('g', members)
            self.assertEqual(caught.exception.status, status, msg=members)
        for kwargs in ({'overlap': 'wait'}, {'status': 'X'},
                       {'notify_email': 'nobody'},
                       {'description': 'x' * 501},
                       {'schedule_config': '{nonsense'}):
            with self.assertRaises(store.StoreError, msg=kwargs):
                self.save(**kwargs)
        with self.assertRaises(store.StoreError):
            store.save_group('bad name', ['a'])
        with self.assertRaises(store.StoreError):
            store.save_group('new-one')
        self.save()
        with self.assertRaises(store.StoreError) as caught:
            self.save(create=True)
        self.assertEqual(caught.exception.status, 409)

    def test_a_transfer_may_be_in_several_groups(self):
        self.save('one', ('a', 'b'))
        self.save('two', ('b', 'c'))
        found = store.read_membership()
        self.assertEqual([(item['group_name'], item['position'])
                          for item in found['b']], [('one', 2), ('two', 1)])

    def test_taking_a_transfer_out_closes_the_gap(self):
        self.save('one', ('a', 'b', 'c'))
        self.save('solo', ('b',))
        changed = store.remove_param('b')
        self.assertEqual(
            sorted((item['group_name'], item['members'],
                    item['switched_off']) for item in changed),
            [('one', 2, False), ('solo', 0, True)])
        self.assertEqual(store.read_group('one')['members'], ['a', 'c'])
        self.assertEqual(store.read_group('solo')['status'], 'N')
        self.assertEqual(store.remove_param('b'), [])

    def test_delete_is_refused_while_a_run_is_active(self):
        group = self.save()
        run_id = store.add_run(group, store.MANUAL)
        with self.assertRaises(store.StoreError) as caught:
            store.delete_group('nightly')
        self.assertEqual(caught.exception.status, 409)
        store.update_run(run_id, status=store.DONE)
        store.delete_group('nightly')
        self.assertEqual(store.read_groups(), [])
        self.assertEqual(len(store.read_runs()), 1)

    def test_a_run_has_a_waiting_step_for_each_transfer(self):
        run_id = store.add_run(self.save(), store.MANUAL)
        row = store.read_run(run_id)
        self.assertEqual([(step['step_no'], step['param_name'],
                           step['status']) for step in row['steps']],
                         [(1, 'a', 'W'), (2, 'b', 'W'), (3, 'c', 'W')])
        self.assertEqual(row['steps_total'], 3)

    def test_the_log_of_the_group_holds_the_logs_of_its_transfers(self):
        run_id = store.add_run(self.save(), store.MANUAL)
        transfer_run = transfer_store.add_run('a', 'group',
                                              group_run_id=run_id)
        transfer_store.update_run(transfer_run, run_log='one\ntwo')
        store.update_run(run_id, run_log='start\n'
                        + store.LOG_MARK.format(transfer_run) + '\nend')
        row = store.read_run(run_id)
        self.assertEqual(row['run_log'], 'start\n    | one\n    | two\nend')
        runs = transfer_store.read_runs('a')
        self.assertEqual(runs[0]['group_run_id'], run_id)

    def test_old_runs_go_with_their_steps(self):
        run_id = store.add_run(self.save(), store.MANUAL)
        store.update_run(run_id, status='D',
                         queued_time=when('2000-01-01 00:00:00'))
        self.assertEqual(store.clean_runs(30), 1)
        self.assertEqual(store.read_steps(run_id), [])

    def test_a_crash_leaves_no_run_active(self):
        run_id = store.add_run(self.save(), store.MANUAL,
                               runner='test:8080')
        self.assertEqual(store.sweep('test:8080'), 1)
        row = store.read_run(run_id)
        self.assertEqual(row['status'], 'E')
        self.assertEqual({step['status'] for step in row['steps']}, {'C'})


class RunTests(GroupCase):
    def test_the_transfers_run_one_after_another_in_the_order_of_the_group(
            self):
        self.save(members=('c', 'a', 'b'))
        worker = self.worker()
        row = self.finished(self.groups.submit('nightly', 'manual'))
        self.assertEqual(row['status'], 'D')
        self.assertEqual(worker.started, ['c', 'a', 'b'])
        self.assertEqual(worker.most, 1)
        self.assertEqual([step['status'] for step in row['steps']],
                         ['D', 'D', 'D'])
        self.assertEqual(row['step_no'], 3)
        self.assertIn('All 3 transfers done', row['message'])

    def test_every_transfer_has_its_run_and_the_log_of_the_group_has_them(
            self):
        self.save(members=('a', 'b'))
        self.worker()
        run_id = self.groups.submit('nightly', 'manual', requested_by='me')
        row = self.finished(run_id)
        runs = transfer_store.read_runs()
        self.assertEqual(sorted((item['param_name'], item['group_run_id'],
                                 item['trigger_type'], item['status'])
                                for item in runs),
                         [('a', run_id, 'group', 'D'),
                          ('b', run_id, 'group', 'D')])
        self.assertEqual([step['transfer_run_id'] for step in row['steps']],
                         sorted(item['run_id'] for item in runs))
        self.assertIn('    | log of a', row['run_log'])
        self.assertIn('    | log of b', row['run_log'])
        self.assertLess(row['run_log'].index('log of a'),
                        row['run_log'].index('log of b'))
        # And the log is seen with the transfer.
        shown = transfer_store.read_run(row['steps'][0]['transfer_run_id'])
        self.assertEqual(shown['run_log'], 'log of a')

    def test_a_failed_transfer_stops_the_group(self):
        self.save(members=('a', 'b', 'c'))
        worker = self.worker(fail=('b',))
        sent = []
        self.groups._mail = lambda run: sent.append(run.run_id) or 'sent'
        run_id = self.groups.submit('nightly', 'manual')
        row = self.finished(run_id)
        self.assertEqual(row['status'], 'E')
        self.assertEqual(worker.started, ['a', 'b'])
        self.assertEqual([step['status'] for step in row['steps']],
                         ['D', 'E', 'C'])
        self.assertIn('Step 2 of 3, transfer b, failed', row['message'])
        self.assertIn('The server refused.', row['message'])
        self.assertEqual(sent, [run_id])
        self.assertEqual(row['mail_status'], 'sent')

    def test_a_group_that_works_sends_no_mail(self):
        self.save(members=('a',))
        self.worker()
        sent = []
        self.groups._mail = lambda run: sent.append(run) or 'sent'
        self.finished(self.groups.submit('nightly', 'manual'))
        self.assertEqual(sent, [])

    def test_a_transfer_that_is_not_there_any_more_is_an_error_step(self):
        self.save(members=('a', 'b'))
        transfer_store.delete('b')
        self.worker()
        self.groups._mail = lambda run: 'no mail'
        row = self.finished(self.groups.submit('nightly', 'manual'))
        self.assertEqual(row['status'], 'E')
        self.assertEqual([step['status'] for step in row['steps']],
                         ['D', 'E'])

    def test_the_group_is_canceled_with_the_transfer_that_goes(self):
        self.save(members=('a', 'b'))
        gate = threading.Event()
        worker = self.worker(hold={'a': gate})
        run_id = self.groups.submit('nightly', 'manual')
        end = time.time() + 5
        while 'a' not in worker.started and time.time() < end:
            time.sleep(0.01)
        self.assertTrue(self.groups.cancel(run_id))
        gate.set()
        row = self.finished(run_id)
        self.assertEqual(row['status'], 'C')
        self.assertEqual(worker.started, ['a'])
        self.assertEqual([step['status'] for step in row['steps']],
                         ['C', 'C'])
        self.assertFalse(self.groups.cancel(run_id))

    def test_overlap_skip_refuses_a_second_start(self):
        self.save(members=('a',))
        gate = threading.Event()
        self.worker(hold={'a': gate})
        first = self.groups.submit('nightly', 'manual')
        with self.assertRaises(store.StoreError) as caught:
            self.groups.submit('nightly', 'manual')
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(self.groups.busy('nightly'))
        gate.set()
        self.assertEqual(self.finished(first)['status'], 'D')
        self.assertEqual(len(store.read_runs()), 1)

    def test_overlap_queue_runs_them_one_after_the_other(self):
        self.save(members=('a', 'b'), overlap='queue')
        gate = threading.Event()
        worker = self.worker(hold={'a': gate})
        first = self.groups.submit('nightly', 'manual')
        second = self.groups.submit('nightly', 'manual')
        time.sleep(0.3)
        self.assertEqual(store.read_run(second)['status'], 'I')
        self.assertEqual(worker.started, ['a'])
        gate.set()
        self.assertEqual(self.finished(first)['status'], 'D')
        self.assertEqual(self.finished(second)['status'], 'D')
        self.assertEqual(worker.started, ['a', 'b', 'a', 'b'])
        self.assertLessEqual(store.read_run(first)['end_time'],
                             store.read_run(second)['start_time'])

    def test_overlap_allow_lets_the_runs_go_together_but_a_transfer_waits(
            self):
        self.save(members=('a', 'b'), overlap='allow')
        gate = threading.Event()
        worker = self.worker(hold={'a': gate})
        first = self.groups.submit('nightly', 'manual')
        second = self.groups.submit('nightly', 'manual')
        time.sleep(0.3)
        # Both are going, and the second one's transfer a waits for the
        # first one's.
        self.assertEqual(store.read_run(second)['status'], 'S')
        self.assertEqual(worker.started, ['a'])
        self.assertEqual(worker.most, 1)
        gate.set()
        self.assertEqual(self.finished(first)['status'], 'D')
        self.assertEqual(self.finished(second)['status'], 'D')
        self.assertEqual(sorted(worker.started), ['a', 'a', 'b', 'b'])

    def test_a_transfer_started_by_hand_while_the_group_runs_it_waits(self):
        self.save(members=('a', 'b'))
        gate = threading.Event()
        worker = self.worker(hold={'a': gate})
        run_id = self.groups.submit('nightly', 'manual')
        end = time.time() + 5
        while 'a' not in worker.started and time.time() < end:
            time.sleep(0.01)
        by_hand = self.transfers.submit('a', manager.MANUAL)
        time.sleep(0.2)
        self.assertEqual(transfer_store.read_status(by_hand)['status'], 'I')
        gate.set()
        self.assertEqual(self.finished(run_id)['status'], 'D')
        end = time.time() + 5
        while (transfer_store.read_status(by_hand)['status']
               in transfer_store.ACTIVE and time.time() < end):
            time.sleep(0.02)
        self.assertEqual(transfer_store.read_status(by_hand)['status'], 'D')
        self.assertEqual(worker.most, 1)

    def test_what_can_not_be_started(self):
        with self.assertRaises(store.StoreError) as caught:
            self.groups.submit('missing', 'manual')
        self.assertEqual(caught.exception.status, 404)
        self.save()
        self.transfers.active = False
        with self.assertRaises(store.StoreError) as caught:
            self.groups.submit('nightly', 'manual')
        self.assertEqual(caught.exception.status, 503)

    def test_a_member_is_a_step_of_a_group_that_runs(self):
        self.save(members=('a', 'b'))
        gate = threading.Event()
        self.worker(hold={'a': gate})
        self.groups.submit('nightly', 'manual')
        self.assertEqual(self.groups.in_group_now('b'), ['nightly'])
        self.assertEqual(self.groups.in_group_now('d'), [])
        gate.set()


class ScheduleTests(GroupCase):
    def schedule(self, **units):
        return manager.schedule_json(units)

    def test_a_fire_starts_the_group(self):
        self.save(members=('a',), schedule_config=self.schedule(
            hour='3', min='0', sec='0'))
        self.worker()
        self.groups.fire(when('2026-05-01 02:00:00'),
                         when('2026-05-01 03:00:00'))
        run = store.read_runs('nightly')[0]
        self.assertEqual(run['trigger_type'], 'schedule')
        self.assertEqual(run['scheduled_time'], when('2026-05-01 03:00:00'))

    def test_an_off_group_and_an_unscheduled_one_do_not_fire(self):
        self.save('off', ('a',), status='N', schedule_config=self.schedule(
            hour='3', min='0', sec='0'))
        self.save('plain', ('b',))
        self.groups.fire(when('2026-05-01 02:00:00'),
                         when('2026-05-01 04:00:00'))
        self.assertEqual(store.read_runs(), [])
        self.assertIsNone(self.groups.next_wake(when('2026-05-01 02:00:00')))

    def test_a_late_fire_is_recorded_not_run(self):
        self.save(members=('a',), schedule_config=self.schedule(
            hour='3', min='0', sec='0'))
        self.groups.fire(when('2026-05-01 02:00:00'),
                         when('2026-05-01 03:30:00'))
        run = store.read_runs('nightly')[0]
        self.assertEqual(run['status'], 'C')
        self.assertIn('late', run['message'])
        self.assertEqual(len(self.groups.runs), 0)

    def test_a_fire_while_the_group_runs_is_recorded_as_canceled(self):
        self.save(members=('a',), schedule_config=self.schedule(
            hour='3', min='0', sec='0'))
        gate = threading.Event()
        self.worker(hold={'a': gate})
        first = self.groups.submit('nightly', 'manual')
        self.groups.fire(when('2026-05-01 02:00:00'),
                         when('2026-05-01 03:00:00'))
        runs = {row['run_id']: row for row in store.read_runs('nightly')}
        other = [row for row in runs.values() if row['run_id'] != first][0]
        self.assertEqual(other['status'], 'C')
        self.assertIn('already running', other['message'])
        gate.set()
        self.finished(first)

    def test_the_next_wake_is_the_earliest_fire(self):
        self.save('one', ('a',), schedule_config=self.schedule(
            hour='5', min='0', sec='0'))
        self.save('two', ('b',), schedule_config=self.schedule(
            hour='4', min='0', sec='0'))
        self.groups.invalidate()
        wake = self.groups.next_wake(when('2026-05-01 00:00:00'))
        self.assertEqual(dt.datetime.fromtimestamp(wake),
                         when('2026-05-01 04:00:00'))


class TableTests(GroupCase):
    def schedule(self, **units):
        return manager.schedule_json(units)

    def test_each_transfer_has_its_own_start_and_its_place_in_the_group(
            self):
        self.save(members=('a', 'b', 'c'), schedule_config=self.schedule(
            hour='3', min='0', sec='0'))
        transfer_store.write_schedule('b', self.schedule(
            hour='5', min='0', sec='0'), True)
        # a took 10 minutes the last time, b 20.
        for name, minutes in (('a', 10), ('b', 20)):
            run_id = transfer_store.add_run(name, 'manual')
            transfer_store.update_run(
                run_id, status='D', start_time=when('2026-04-30 01:00:00'),
                end_time=when('2026-04-30 01:00:00')
                + dt.timedelta(minutes=minutes))
        self.groups.invalidate()
        self.transfers.invalidate()
        table = self.groups.table()
        group = table['groups'][0]
        self.assertTrue(group['scheduled'])
        first, second, third = group['members']
        self.assertEqual([item['position'] for item in group['members']],
                         [1, 2, 3])
        self.assertEqual(second['own_next_fire'].hour, 5)
        self.assertIsNone(first['own_next_fire'])
        self.assertEqual(first['group_start'], group['next_fire'])
        self.assertEqual(second['group_start'] - first['group_start'],
                         dt.timedelta(minutes=10))
        self.assertEqual(third['group_start'] - second['group_start'],
                         dt.timedelta(minutes=20))

    def test_the_list_of_transfers_shows_every_start(self):
        self.save('nightly', ('a', 'b'), schedule_config=self.schedule(
            hour='3', min='0', sec='0'))
        self.save('weekly', ('b',), schedule_config=self.schedule(
            hour='4', min='0', sec='0'))
        transfer_store.write_schedule('b', self.schedule(
            hour='2', min='0', sec='0'), True)
        self.groups.invalidate()
        self.transfers.invalidate()
        answer = self.groups.annotate(self.transfers.table())
        self.assertTrue(answer['groups_available'])
        by_name = {row['param_name']: row for row in answer['params']}
        b = by_name['b']
        self.assertEqual([(item['source'], item['name'])
                          for item in b['next_starts']],
                         [('rapo', None), ('group', 'nightly'),
                          ('group', 'weekly')])
        self.assertEqual([(item['group_name'], item['position'], item['size'])
                          for item in b['groups']],
                         [('nightly', 2, 2), ('weekly', 1, 1)])
        self.assertEqual(b['next_start'], b['next_starts'][0]['next_fire'])
        self.assertEqual([item['source'] for item
                          in by_name['a']['next_starts']], ['group'])
        self.assertEqual(by_name['c']['next_starts'], [])
        self.assertEqual(by_name['c']['groups'], [])

    def test_the_list_works_while_the_group_tables_are_missing(self):
        store.metadata.drop_all(db.engine)
        answer = self.groups.annotate(self.transfers.table())
        self.assertFalse(answer['groups_available'])
        self.assertIn('upgrade.sql', answer['groups_error'])
        self.assertEqual(len(answer['params']), 4)
        self.groups.invalidate()
        self.assertEqual(self.groups.read_schedules(), {})
        self.assertEqual(store.remove_param('a'), [])


if __name__ == '__main__':
    unittest.main()
