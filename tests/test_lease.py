"""Tests of the scheduler lease (rapo/core/lease.py). Run from the project
root:

    python -m unittest tests.test_lease -v

They use the bootstrap of tests/test_transfer.py: the database is a SQLite
one, with a table of the columns of `rapo_scheduler`.
"""
import datetime as dt
import os
import unittest

import sqlalchemy as sa

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)

from rapo.database import db  # noqa: E402
from rapo.core import lease as leases  # noqa: E402

metadata = sa.MetaData()
table = sa.Table(
    'rapo_scheduler', metadata,
    sa.Column('id', sa.String(15), primary_key=True),
    sa.Column('server', sa.String(255)),
    sa.Column('username', sa.String(128)),
    sa.Column('pid', sa.Integer),
    sa.Column('start_date', sa.DateTime),
    sa.Column('stop_date', sa.DateTime),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('heartbeat', sa.DateTime),
    sa.Column('instance_id', sa.String(64)),
    sa.Column('disabled', sa.String(1), nullable=False, default='N'))

DEAD_PID = 3999999


def lease(row, name, timeout=60, **kwargs):
    return leases.Lease(row, name, lambda: timeout, table=table, **kwargs)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        db.engine.dispose()
        metadata.drop_all(db.engine)
        metadata.create_all(db.engine)
        for row in (leases.SCHEDULER_ROW, leases.JOBS_ROW):
            db.execute(table.insert().values(id=row, status='N',
                                             disabled='N'))
        self.row = leases.SCHEDULER_ROW

    def record(self, row=None):
        return db.execute(table.select().where(
            table.c.id == (row or self.row)), as_dict=True)

    def age(self, seconds, row=None):
        """Make the heartbeat of a row older."""
        db.execute(table.update().where(
            table.c.id == (row or self.row)).values(
            heartbeat=dt.datetime.now() - dt.timedelta(seconds=seconds)))

    def test_a_free_lease_is_taken_and_renewed(self):
        first = lease(self.row, 'a')
        result = first.renew_or_acquire()
        self.assertEqual(result[0], leases.ACQUIRED)
        self.assertIsNone(result[1])
        self.assertTrue(first.leader)
        record = self.record()
        self.assertEqual((record['status'], record['instance_id']),
                         ('Y', 'a'))
        self.assertEqual(first.renew_or_acquire(), (leases.RENEWED,))

    def test_another_server_waits_while_the_holder_is_alive(self):
        first, second = lease(self.row, 'a'), lease(self.row, 'b')
        first.renew_or_acquire()
        self.assertIsNone(second.renew_or_acquire())
        self.assertFalse(second.leader)
        self.assertEqual(self.record()['instance_id'], 'a')
        self.assertTrue(second.alive(self.record()))

    def test_a_silent_holder_is_taken_over_and_learns_it(self):
        first, second = lease(self.row, 'a'), lease(self.row, 'b')
        first.renew_or_acquire()
        self.age(120)
        result = second.renew_or_acquire()
        self.assertEqual(result[0], leases.ACQUIRED)
        self.assertIsNotNone(result[1])
        self.assertEqual(self.record()['instance_id'], 'b')
        self.assertEqual(first.renew_or_acquire(), (leases.LOST,))

    def test_a_holder_on_this_host_whose_process_is_gone_is_taken_over(self):
        first, second = lease(self.row, 'a'), lease(self.row, 'b')
        first.renew_or_acquire()
        db.execute(table.update().where(table.c.id == self.row).values(
            pid=DEAD_PID, server=second.server))
        self.assertEqual(second.renew_or_acquire()[0], leases.ACQUIRED)

    def test_release_frees_it_at_once(self):
        first, second = lease(self.row, 'a'), lease(self.row, 'b')
        first.renew_or_acquire()
        first.release()
        self.assertFalse(first.leader)
        record = self.record()
        self.assertEqual(record['status'], 'N')
        self.assertIsNotNone(record['stop_date'])
        result = second.renew_or_acquire()
        self.assertEqual(result[0], leases.ACQUIRED)
        # What it knows of the downtime starts at the release.
        self.assertIsNotNone(result[1])

    def test_a_release_does_not_take_a_lease_over_from_another(self):
        first, second = lease(self.row, 'a'), lease(self.row, 'b')
        first.renew_or_acquire()
        self.age(120)
        second.renew_or_acquire()
        first.release()
        self.assertEqual(self.record()['instance_id'], 'b')
        self.assertEqual(self.record()['status'], 'Y')

    def test_a_stopped_scheduler_is_not_taken_and_the_holder_loses_it(self):
        first, second = lease(self.row, 'a'), lease(self.row, 'b')
        first.renew_or_acquire()
        first.set_disabled(True)
        self.assertEqual(first.renew_or_acquire(), (leases.LOST,))
        first.release()
        self.assertIsNone(second.renew_or_acquire())
        first.set_disabled(False)
        record = self.record()
        self.assertEqual(record['disabled'], 'N')
        # Starting again resumes from now: no downtime is carried over.
        result = second.renew_or_acquire()
        self.assertEqual(result[0], leases.ACQUIRED)
        self.assertLess((dt.datetime.now() - result[1]).total_seconds(), 5)

    def test_the_two_schedulers_do_not_touch_each_other(self):
        controls = lease(leases.SCHEDULER_ROW, 'c1')
        jobs = lease(leases.JOBS_ROW, 'j1')
        controls.renew_or_acquire()
        self.assertEqual(self.record(leases.JOBS_ROW)['status'], 'N')
        jobs.renew_or_acquire()
        # The same server may hold both, with instances of their own.
        self.assertEqual(self.record(leases.SCHEDULER_ROW)['instance_id'],
                         'c1')
        self.assertEqual(self.record(leases.JOBS_ROW)['instance_id'], 'j1')
        controls.set_disabled(True)
        self.assertEqual(self.record(leases.JOBS_ROW)['disabled'], 'N')
        self.assertEqual(jobs.renew_or_acquire(), (leases.RENEWED,))
        self.assertEqual(controls.renew_or_acquire(), (leases.LOST,))
        controls.release()
        self.assertEqual(self.record(leases.JOBS_ROW)['status'], 'Y')
        self.assertEqual(self.record(leases.SCHEDULER_ROW)['status'], 'N')

    def test_rows_in_the_same_state_are_not_taken_together(self):
        # Both rows are free with empty heartbeats, as a new database has
        # them: taking one must not take the other.
        controls = lease(leases.SCHEDULER_ROW, 'c1')
        controls.renew_or_acquire()
        other = self.record(leases.JOBS_ROW)
        self.assertEqual((other['status'], other['instance_id']),
                         ('N', None))

    def test_a_standalone_scheduler_of_an_older_version(self):
        db.execute(table.update().where(table.c.id == self.row).values(
            status='Y', pid=os.getpid(), server=lease(self.row, 'x').server,
            heartbeat=dt.datetime.now()))
        modern = lease(self.row, 'a', standalone=True)
        # Alive: it is not taken, and warned about once.
        self.assertIsNone(modern.renew_or_acquire())
        self.assertTrue(modern.warned)
        # Gone, on this host: taken.
        db.execute(table.update().where(table.c.id == self.row).values(
            pid=DEAD_PID))
        self.assertEqual(modern.renew_or_acquire()[0], leases.ACQUIRED)

    def test_the_jobs_row_has_no_standalone_scheduler(self):
        db.execute(table.update().where(table.c.id == leases.JOBS_ROW).values(
            status='Y', heartbeat=dt.datetime.now() - dt.timedelta(
                seconds=300)))
        jobs = lease(leases.JOBS_ROW, 'j1')
        self.assertEqual(jobs.renew_or_acquire()[0], leases.ACQUIRED)

    def test_a_missing_row_gives_nothing(self):
        db.execute(table.delete().where(table.c.id == leases.JOBS_ROW))
        self.assertIsNone(lease(leases.JOBS_ROW, 'j1').renew_or_acquire())
        self.assertIsNone(lease(leases.JOBS_ROW, 'j1').read())


if __name__ == '__main__':
    unittest.main()
