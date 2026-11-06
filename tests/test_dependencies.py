"""Tests of the condition RUN_STATUS (rapo/jobs/dependencies.py and its
definition). Run from the project root:

    python -m unittest tests.test_dependencies -v

The database is the SQLite one of tests/test_transfer.py.
"""
import datetime as dt
import os
import unittest

import sqlalchemy as sa

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
test_transfer._package('rapo.jobs', os.path.join(ROOT, 'rapo', 'jobs'))

from rapo.database import db  # noqa: E402
from rapo.jobs import definition as definitions  # noqa: E402
from rapo.jobs import dependencies, store, workflow, workflow_store  # noqa: E402
from rapo.transfer import store as transfer_store  # noqa: E402
from tests.test_jobs import context  # noqa: E402

NOW = dt.datetime(2026, 11, 6, 12, 0, 0)
metadata = sa.MetaData()
config = sa.Table('rapo_config', metadata,
                  sa.Column('control_id', sa.Integer, primary_key=True),
                  sa.Column('control_name', sa.String(45)))
log = sa.Table('rapo_log', metadata,
               sa.Column('process_id', sa.Integer, primary_key=True),
               sa.Column('control_id', sa.Integer),
               sa.Column('start_date', sa.DateTime),
               sa.Column('end_date', sa.DateTime),
               sa.Column('status', sa.String(1)),
               sa.Column('date_from', sa.DateTime))


def target(kind, name, window=None):
    return {'type': kind, 'name': name, 'window': window or {'kind': 'today'}}


def condition(*targets):
    return {'kind': 'RUN_STATUS', 'targets': list(targets)}


def ago(**delta):
    return NOW - dt.timedelta(**delta)


class DefinitionTests(unittest.TestCase):
    def problems(self, value, owner=None):
        problems = []
        definitions.check_condition(
            value, dict(context(), owner=owner) if owner else context(),
            problems)
        return problems

    def test_a_good_condition(self):
        self.assertEqual(self.problems(condition(
            target('control', 'c1'), target('transfer', 't1'),
            target('job', 'j', {'kind': 'hours', 'hours': 6}),
            target('workflow', 'w', {'kind': 'since_last_run'}),
            target('control', 'c2', {'kind': 'data_date', 'days_ago': 1}))),
            [])

    def test_a_condition_needs_targets_with_a_type_and_a_name(self):
        self.assertTrue(self.problems(condition()))
        self.assertTrue(self.problems({'kind': 'RUN_STATUS'}))
        self.assertTrue(self.problems(condition({'type': 'file',
                                                 'name': 'x',
                                                 'window': {'kind': 'today'}})))
        self.assertTrue(self.problems(condition(target('job', ' '))))

    def test_windows_are_checked(self):
        for window in ({'kind': 'week'}, {'kind': 'hours'},
                       {'kind': 'hours', 'hours': 0},
                       {'kind': 'hours', 'hours': 721},
                       {'kind': 'hours', 'hours': True},
                       {'kind': 'today', 'hours': 3}, 'today', None):
            self.assertTrue(self.problems(condition(
                {'type': 'job', 'name': 'j', 'window': window})),
                msg=window)

    def test_the_date_of_the_data_is_for_controls_only(self):
        window = {'kind': 'data_date', 'days_ago': 0}
        self.assertEqual(self.problems(condition(target('control', 'c',
                                                        window))), [])
        problems = self.problems(condition(target('job', 'j', window)))
        self.assertIn('for a control', problems[0])
        for days in (-1, 32, 'x'):
            self.assertTrue(self.problems(condition(target(
                'control', 'c', {'kind': 'data_date', 'days_ago': days}))))

    def test_a_target_is_given_once_and_not_to_itself(self):
        problems = self.problems(condition(target('job', 'j'),
                                           target('job', 'j')))
        self.assertIn('given twice', problems[0])
        problems = self.problems(condition(target('job', 'j')),
                                 owner=('job', 'j'))
        self.assertIn('can not wait for itself', problems[0])
        # Another type with the same name is another thing.
        self.assertEqual(self.problems(condition(target('workflow', 'j')),
                                       owner=('job', 'j')), [])

    def test_a_workflow_has_some_kinds_only(self):
        for kind, ok in (('RUN_STATUS', True), ('FILE_EXISTS', True),
                         ('SQL', False), ('EXIT_CODE', False)):
            body = {'RUN_STATUS': {'targets': [target('job', 'j')]},
                    'FILE_EXISTS': {'path': '/x'},
                    'SQL': {'text': 'select 1 from dual'},
                    'EXIT_CODE': {'task': {}}}[kind]
            value = workflow.normalize({'steps': ['a'], 'condition': dict(
                body, kind=kind)})
            problems = workflow.validate(value)
            if ok:
                self.assertEqual(problems, [], msg=kind)
            else:
                self.assertTrue(problems, msg=kind)

    def test_a_workflow_does_not_wait_for_itself(self):
        value = workflow.normalize({'steps': ['a'], 'condition': condition(
            target('workflow', 'me'))})
        self.assertTrue(workflow.validate(value, owner='me'))
        self.assertEqual(workflow.validate(value, owner='other'), [])


class EvaluateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for table in (store.metadata, metadata):
            table.drop_all(db.engine)
            table.create_all(db.engine)
        workflow_store.metadata.create_all(db.engine)
        transfer_store.metadata.create_all(db.engine)

    def setUp(self):
        with db.engine.begin() as connection:
            for table in (log, config, store.run_table, store.job_table,
                          workflow_store.run_table,
                          workflow_store.workflow_table,
                          transfer_store.run_table):
                connection.execute(table.delete())
            connection.execute(config.insert(), [
                {'control_id': 1, 'control_name': 'c1'},
                {'control_id': 2, 'control_name': 'c2'}])

    def control_run(self, control, status, end, date_from=None, start=None):
        with db.engine.begin() as connection:
            connection.execute(log.insert().values(
                control_id=control, status=status, end_date=end,
                start_date=start or (end and end - dt.timedelta(minutes=5)),
                date_from=date_from))

    def run_of(self, table, **values):
        with db.engine.begin() as connection:
            connection.execute(table.insert().values(**values))

    def job_run(self, name, status, end, start=None):
        self.run_of(store.run_table, job_id=1, job_name=name,
                    trigger_type='manual', status=status, end_time=end,
                    start_time=start or end - dt.timedelta(minutes=1))

    def met(self, *targets, since=None):
        return dependencies.evaluate(condition(*targets), NOW, since)

    def test_a_control_done_today_is_met(self):
        self.control_run(1, 'D', ago(hours=2))
        self.assertEqual(self.met(target('control', 'c1')), (True, ''))

    def test_a_control_that_did_not_end_well_or_not_today_is_not_met(self):
        for status in 'ECX':
            self.control_run(1, status, ago(hours=1))
            met, why = self.met(target('control', 'c1'))
            self.assertFalse(met)
            self.assertIn('did not end well', why)
            with db.engine.begin() as connection:
                connection.execute(log.delete())
        self.control_run(1, 'D', ago(days=1))
        met, why = self.met(target('control', 'c1'))
        self.assertFalse(met)
        self.assertIn('has not ended today', why)
        with db.engine.begin() as connection:
            connection.execute(log.delete())
        met, why = self.met(target('control', 'c1'))
        self.assertIn('has not run today', why)

    def test_only_the_last_finished_run_counts(self):
        self.control_run(1, 'E', ago(hours=3))
        self.control_run(1, 'D', ago(hours=1))
        self.assertTrue(self.met(target('control', 'c1'))[0])
        self.control_run(1, 'E', ago(minutes=10))
        self.assertFalse(self.met(target('control', 'c1'))[0])

    def test_a_run_going_means_not_met(self):
        self.control_run(1, 'D', ago(hours=1))
        self.control_run(1, 'S', None, start=ago(minutes=2))
        met, why = self.met(target('control', 'c1'))
        self.assertFalse(met)
        self.assertIn('is running', why)
        self.job_run('j', 'D', ago(hours=1))
        self.job_run('j', 'S', None, start=ago(minutes=2))
        self.assertIn('is running', self.met(target('job', 'j'))[1])

    def test_hours_window(self):
        self.job_run('j', 'D', ago(hours=5))
        self.assertTrue(self.met(target('job', 'j',
                                        {'kind': 'hours', 'hours': 6}))[0])
        met, why = self.met(target('job', 'j', {'kind': 'hours', 'hours': 4}))
        self.assertFalse(met)
        self.assertIn('last 4 hours', why)

    def test_since_last_run_window(self):
        self.job_run('j', 'D', ago(hours=5))
        window = {'kind': 'since_last_run'}
        self.assertTrue(self.met(target('job', 'j', window),
                                 since=ago(hours=6))[0])
        self.assertFalse(self.met(target('job', 'j', window),
                                  since=ago(hours=4))[0])
        # No bound: any done run counts.
        self.assertTrue(self.met(target('job', 'j', window))[0])

    def test_data_date_window_looks_at_the_day_of_the_data(self):
        window = {'kind': 'data_date', 'days_ago': 1}
        yesterday = dt.datetime(2026, 11, 5)
        self.control_run(1, 'D', ago(hours=1), date_from=yesterday)
        self.assertTrue(self.met(target('control', 'c1', window))[0])
        # Run today for today's data: yesterday has no run.
        self.control_run(2, 'D', ago(hours=1),
                         date_from=dt.datetime(2026, 11, 6))
        met, why = self.met(target('control', 'c2', window))
        self.assertFalse(met)
        self.assertIn('no run for the data of 2026-11-05', why)
        self.assertTrue(self.met(target('control', 'c2',
                                        {'kind': 'data_date',
                                         'days_ago': 0}))[0])
        # A later failed run of the same day cancels an earlier good one.
        self.control_run(1, 'E', ago(minutes=5), date_from=yesterday)
        self.assertFalse(self.met(target('control', 'c1', window))[0])

    def test_transfers_workflows_and_jobs(self):
        self.run_of(transfer_store.run_table, param_name='t1',
                    trigger_type='manual', status='D', end_time=ago(hours=1))
        self.run_of(workflow_store.run_table, workflow_id=1,
                    workflow_name='w', trigger_type='manual', status='C',
                    end_time=ago(hours=1))
        self.job_run('j', 'D', ago(hours=1))
        self.assertTrue(self.met(target('transfer', 't1'))[0])
        self.assertTrue(self.met(target('job', 'j'))[0])
        self.assertFalse(self.met(target('workflow', 'w'))[0])

    def test_every_target_must_be_met_and_every_reason_is_told(self):
        self.job_run('j', 'D', ago(hours=1))
        met, why = self.met(target('job', 'j'), target('control', 'c1'),
                            target('control', 'c2'))
        self.assertFalse(met)
        self.assertIn('control c1', why)
        self.assertIn('control c2', why)
        self.assertNotIn('job j', why)

    def test_a_missing_control_is_an_error(self):
        with self.assertRaises(dependencies.DependencyError):
            self.met(target('control', 'nope'))

    def test_missing_targets_are_listed(self):
        self.job_run('j', 'D', ago(hours=1))
        value = condition(target('control', 'c1'), target('control', 'zz'),
                          target('job', 'nope'))
        problems = dependencies.missing(value, {'job': {'j'}})
        self.assertEqual(problems, ['The control zz does not exist.',
                                    'The job nope does not exist.'])
        self.assertEqual(dependencies.missing({'kind': 'SQL'}), [])

    def test_since_of_is_the_last_done_end_else_the_creation(self):
        created = dt.datetime(2026, 11, 1)
        self.run_of(store.job_table, job_id=1, job_name='j', job_type='SQL',
                    definition='{}', status='A', version=1,
                    created_date=created)
        self.assertEqual(dependencies.since_of('job', 'j'), created)
        self.job_run('j', 'E', ago(hours=1))
        self.assertEqual(dependencies.since_of('job', 'j'), created)
        self.job_run('j', 'D', ago(hours=2))
        self.assertEqual(dependencies.since_of('job', 'j'), ago(hours=2))

    def unmet_run(self, name, end):
        self.run_of(store.run_table, job_id=1, job_name=name,
                    trigger_type='schedule', status='C', stage='CONDITION',
                    end_time=end, start_time=end,
                    message='The condition was not met.')

    def test_the_lists_show_the_last_real_run_and_count_the_unmet_ones(self):
        self.job_run('j', 'D', ago(hours=9))
        self.unmet_run('j', ago(hours=3))
        self.unmet_run('j', ago(hours=2))
        self.unmet_run('j', ago(hours=1))
        last = store.read_last_runs()
        self.assertEqual(last[1]['status'], 'D')
        self.assertEqual(last[1]['end_time'], ago(hours=9))
        self.assertEqual(store.read_unmet(),
                         {1: {'count': 3, 'last': ago(hours=1)}})
        # A real run after them starts the count again.
        self.job_run('j', 'E', ago(minutes=5))
        self.assertEqual(store.read_last_runs()[1]['status'], 'E')
        self.assertEqual(store.read_unmet(), {})
        self.unmet_run('j', ago(minutes=1))
        self.assertEqual(store.read_unmet()[1]['count'], 1)

    def test_hide_unmet_leaves_those_runs_out_of_the_history(self):
        self.job_run('j', 'D', ago(hours=9))
        self.unmet_run('j', ago(hours=1))
        self.assertEqual(len(store.read_runs()), 2)
        shown = store.read_runs(hide_unmet=True)
        self.assertEqual([row['status'] for row in shown], ['D'])

    def test_a_job_that_only_stopped_has_no_last_run(self):
        self.unmet_run('j', ago(hours=1))
        self.assertEqual(store.read_last_runs(), {})
        self.assertEqual(store.read_unmet()[1]['count'], 1)

    def test_workflow_runs_stopped_at_the_start_are_unmet(self):
        self.run_of(workflow_store.run_table, workflow_id=1,
                    workflow_name='w', trigger_type='schedule', status='D',
                    end_time=ago(hours=5), message='done')
        self.run_of(workflow_store.run_table, workflow_id=1,
                    workflow_name='w', trigger_type='schedule', status='C',
                    end_time=ago(hours=1),
                    message='Stopped at the start of the workflow: The '
                            'condition was not met.')
        self.run_of(workflow_store.run_table, workflow_id=2,
                    workflow_name='v', trigger_type='schedule', status='C',
                    end_time=ago(hours=1),
                    message='Stopped at step 2 of 3 (job a): The condition '
                            'was not met.')
        last = workflow_store.read_last_runs()
        self.assertEqual(last[1]['status'], 'D')
        self.assertEqual(last[2]['status'], 'C')
        self.assertEqual(workflow_store.read_unmet(),
                         {1: {'count': 1, 'last': ago(hours=1)}})
        self.assertEqual(len(workflow_store.read_runs(hide_unmet=True)), 2)


if __name__ == '__main__':
    unittest.main()
