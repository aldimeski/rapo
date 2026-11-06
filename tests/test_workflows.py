"""Tests of the definition and the storage of the workflows
(rapo/jobs/workflow.py, workflow_store.py). Run from the project root:

    python -m unittest tests.test_workflows -v

The database is the SQLite one of tests/test_transfer.py.
"""
import datetime as dt
import json
import os
import unittest

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
test_transfer._package('rapo.jobs', os.path.join(ROOT, 'rapo', 'jobs'))

from rapo import config as rapo_config  # noqa: E402
from rapo.database import db  # noqa: E402
from rapo.jobs import definition as definitions  # noqa: E402
from rapo.jobs import store, workflow, workflow_store  # noqa: E402
from tests.test_jobs import context, job  # noqa: E402


def flow(*names, **settings):
    """A workflow definition of the jobs, changed by `settings`."""
    value = {'steps': [{'job': name} for name in names]}
    value.update(settings)
    return value


class DefinitionTests(unittest.TestCase):
    def problems(self, value, known=None):
        return workflow.validate(workflow.normalize(value), known)

    def test_the_smallest_workflow_is_fine(self):
        self.assertEqual(self.problems(flow('a')), [])
        self.assertEqual(self.problems(flow('a', 'b', 'c')), [])

    def test_defaults_are_filled_in_and_json_text_is_read(self):
        value = workflow.normalize(json.dumps(flow('a')))
        self.assertEqual((value['version'], value['overlap'],
                          value['parameters'], value['timeout']),
                         (1, 'skip', [], None))
        with self.assertRaises(definitions.DefinitionError):
            workflow.normalize('{nonsense')

    def test_a_step_may_be_given_as_the_name_of_the_job(self):
        value = workflow.normalize({'steps': ['a', {'job': 'b'}]})
        self.assertEqual(workflow.job_names(value), ['a', 'b'])

    def test_a_workflow_needs_a_step(self):
        for value in ({}, flow(), {'steps': None}, {'steps': 'a'}):
            problems = self.problems(value)
            self.assertTrue(problems, msg=value)
        self.assertIn('at least one step', self.problems(flow())[0])

    def test_a_job_can_be_in_a_workflow_once_and_the_message_says_where(self):
        problems = self.problems(flow('load', 'run', 'load'))
        self.assertEqual(len(problems), 1)
        self.assertIn('load is repeated, at steps 1 and 3', problems[0])
        problems = self.problems(flow('a', 'b', 'a', 'b', 'a'))
        self.assertEqual(len(problems), 2)
        self.assertIn('a is repeated, at steps 1, 3 and 5', problems[0])
        self.assertIn('b is repeated, at steps 2 and 4', problems[1])

    def test_a_step_names_a_job_that_exists(self):
        problems = self.problems(flow('a', 'nope'), known={'a'})
        self.assertEqual(problems, ['Step 2: there is no job nope.'])
        self.assertEqual(self.problems(flow('a', 'nope')), [])

    def test_a_bad_step_is_refused(self):
        for step in (5, None, {'job': ''}, {'job': '../x'}, {'job': 'a b'},
                     {}, {'job': 'a', 'when': 'always'}):
            self.assertTrue(self.problems({'steps': [step]}), msg=step)

    def test_unknown_settings_are_refused(self):
        self.assertTrue(self.problems(flow('a', color='red')))

    def test_parameters_timeout_and_overlap_as_for_a_job(self):
        self.assertEqual(self.problems(flow('a', parameters=[
            {'name': 'day', 'value': 'x'},
            {'name': 'pw', 'value': 'y', 'secret': True}])), [])
        for parameters in ([{'name': 'a'}, {'name': 'a'}],
                           [{'name': 'run_id', 'value': '1'}],
                           [{'name': '1a'}], 'a'):
            self.assertTrue(self.problems(flow('a', parameters=parameters)),
                            msg=parameters)
        for timeout in (None, 0, 60):
            self.assertEqual(self.problems(flow('a', timeout=timeout)), [])
        for timeout in (-1, '60', True):
            self.assertTrue(self.problems(flow('a', timeout=timeout)))
        for overlap in ('skip', 'queue', 'allow'):
            self.assertEqual(self.problems(flow('a', overlap=overlap)), [])
        self.assertTrue(self.problems(flow('a', overlap='twice')))

    def test_check_gives_the_definition_or_every_problem(self):
        self.assertEqual(workflow.check(flow('a'))['steps'], [{'job': 'a'}])
        with self.assertRaises(workflow.WorkflowError) as caught:
            workflow.check(flow('a', 'a', timeout=-1))
        self.assertEqual(len(caught.exception.problems), 2)

    def test_secrets_are_hidden_as_for_a_job(self):
        value = flow('a', parameters=[{'name': 'pw', 'value': 'hunter2',
                                       'secret': True}])
        masked = definitions.mask_secrets(value)
        self.assertEqual(masked['parameters'][0]['value'], definitions.MASK)
        self.assertEqual(definitions.restore_secrets(masked, value)
                         ['parameters'][0]['value'], 'hunter2')

    def test_a_job_is_taken_out_of_the_steps(self):
        value, found = workflow.remove_job(flow('a', 'b', 'c'), 'b')
        self.assertEqual((workflow.job_names(value), found),
                         (['a', 'c'], True))
        value, found = workflow.remove_job(flow('a'), 'zz')
        self.assertEqual((workflow.job_names(value), found), (['a'], False))

    def test_repeated(self):
        self.assertEqual(workflow.repeated(['a', 'b', 'a']), {'a': [1, 3]})
        self.assertEqual(workflow.repeated(['a', 'b']), {})


class StoreCase(unittest.TestCase):
    def setUp(self):
        rapo_config.config.pop('JOBS', None)
        db.engine.dispose()
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)
        for name in ('load', 'process', 'report'):
            store.save_job(name, job(), ctx=context())

    def save(self, name='nightly', value=None, **kwargs):
        return workflow_store.save_workflow(
            name, value or flow('load', 'process'), **kwargs)


class WorkflowStoreTests(StoreCase):
    def test_a_new_workflow_is_on_with_version_one(self):
        saved = self.save(description='Nightly', notify_email='a@x.com')
        self.assertEqual((saved['workflow_name'], saved['status'],
                          saved['version']), ('nightly', 'Y', 1))
        self.assertEqual(saved['notify_email'], 'a@x.com')
        self.assertIsNone(saved['schedule_config'])
        self.assertEqual(workflow.job_names(json.loads(saved['definition'])),
                         ['load', 'process'])
        rows = workflow_store.read_workflows()
        self.assertEqual([(row['workflow_name'], row['steps'])
                          for row in rows],
                         [('nightly', ['load', 'process'])])
        self.assertNotIn('definition', rows[0])

    def test_names(self):
        for name in ('../x', 'a b', '', '.hidden', 'x' * 129):
            with self.assertRaises(store.StoreError, msg=name):
                self.save(name)

    def test_the_jobs_must_exist_and_may_not_repeat(self):
        for value, text in ((flow('load', 'nope'), 'no job nope'),
                            (flow('load', 'load'), 'repeated, at steps 1 and 2'),
                            (flow(), 'at least one step')):
            with self.assertRaises(store.StoreError) as caught:
                self.save(value=value)
            self.assertEqual(caught.exception.status, 422)
            self.assertIn(text, str(caught.exception))
        self.assertEqual(workflow_store.read_workflows(), [])

    def test_create_refuses_an_existing_name(self):
        self.save()
        with self.assertRaises(store.StoreError) as caught:
            self.save(create=True)
        self.assertEqual(caught.exception.status, 409)

    def test_a_change_makes_a_version(self):
        first = self.save()
        again = self.save()
        self.assertEqual(again['version'], 1)
        changed = self.save(value=flow('load', 'process', 'report'))
        self.assertEqual(changed['version'], 2)
        versions = workflow_store.read_versions(first['workflow_id'])
        self.assertEqual([item['version'] for item in versions], [2, 1])
        self.assertEqual(workflow.job_names(json.loads(
            versions[1]['definition'])), ['load', 'process'])

    def test_what_is_not_given_stays_and_a_schedule_is_a_version(self):
        self.save(description='D', notify_email='a@x.com',
                  schedule_config='{"hour": "3"}')
        saved = self.save(value=flow('load'))
        self.assertEqual((saved['description'], saved['notify_email'],
                          saved['schedule_config']),
                         ('D', 'a@x.com', '{"hour": "3"}'))
        scheduled = workflow_store.write_schedule('nightly',
                                                  '{"hour": "4"}')
        self.assertEqual(scheduled['version'], saved['version'] + 1)
        with self.assertRaises(store.StoreError):
            workflow_store.write_schedule('nightly', 'not json')
        workflow_store.set_status('nightly', False)
        self.assertEqual(workflow_store.get_workflow('nightly')['status'],
                         'N')
        self.assertEqual(workflow_store.get_workflow('nightly')['version'],
                         scheduled['version'])

    def test_bad_addresses_and_status_are_refused(self):
        with self.assertRaises(store.StoreError):
            self.save(notify_email='nobody')
        with self.assertRaises(store.StoreError):
            self.save(status='X')

    def test_a_workflow_that_is_not_there(self):
        with self.assertRaises(store.StoreError) as caught:
            workflow_store.get_workflow('missing')
        self.assertEqual(caught.exception.status, 404)

    def test_scheduled_ones_that_are_on(self):
        on = self.save('on', schedule_config='{"hour": "3"}')
        self.save('off', schedule_config='{"hour": "3"}', status='N')
        self.save('none')
        self.assertEqual(list(workflow_store.read_scheduled()),
                         [on['workflow_id']])

    def test_the_signature_changes_with_a_workflow(self):
        self.save()
        first = workflow_store.signature()
        self.assertEqual(first, workflow_store.signature())
        workflow_store.set_status('nightly', False)
        self.assertNotEqual(first, workflow_store.signature())

    def test_delete_is_refused_while_a_run_is_active(self):
        saved = self.save()
        run_id = workflow_store.add_run(saved, workflow_store.MANUAL)
        with self.assertRaises(store.StoreError) as caught:
            workflow_store.delete_workflow('nightly')
        self.assertEqual(caught.exception.status, 409)
        workflow_store.update_run(run_id, status=store.DONE)
        workflow_store.delete_workflow('nightly')
        self.assertEqual(workflow_store.read_workflows(), [])
        self.assertEqual(workflow_store.read_versions(saved['workflow_id']),
                         [])
        self.assertEqual(len(workflow_store.read_runs()), 1)


class JobsOfWorkflowsTests(StoreCase):
    def test_which_workflows_have_a_job(self):
        self.save('a', value=flow('load', 'process'))
        self.save('b', value=flow('process', 'report'))
        self.save('c', value=flow('report'))
        self.assertEqual(workflow_store.workflows_using('process'),
                         ['a', 'b'])
        self.assertEqual(workflow_store.workflows_using('load'), ['a'])
        self.assertEqual(workflow_store.workflows_using('zz'), [])

    def test_a_job_is_taken_out_with_a_version_for_each_workflow(self):
        a = self.save('a', value=flow('load', 'process'))
        b = self.save('b', value=flow('process', 'report'))
        self.save('c', value=flow('report'))
        changed = workflow_store.remove_job('process')
        self.assertEqual([(item['workflow_name'], item['steps'],
                           item['switched_off']) for item in changed],
                         [('a', 1, False), ('b', 1, False)])
        self.assertEqual(workflow.job_names(json.loads(
            workflow_store.get_workflow('a')['definition'])), ['load'])
        self.assertEqual(workflow_store.get_workflow('a')['version'],
                         a['version'] + 1)
        self.assertEqual(workflow_store.get_workflow('b')['version'],
                         b['version'] + 1)
        self.assertEqual(workflow_store.get_workflow('c')['version'], 1)
        self.assertEqual(workflow_store.workflows_using('process'), [])

    def test_a_workflow_left_without_steps_is_switched_off(self):
        self.save('a', value=flow('load'))
        changed = workflow_store.remove_job('load')
        self.assertEqual([(item['steps'], item['switched_off'])
                          for item in changed], [(0, True)])
        row = workflow_store.get_workflow('a')
        self.assertEqual(row['status'], 'N')
        self.assertEqual(workflow.job_names(json.loads(row['definition'])),
                         [])
        # It is listed, with no steps; it can not be saved as it is.
        self.assertEqual(workflow_store.read_workflows()[0]['steps'], [])
        with self.assertRaises(store.StoreError):
            workflow_store.save_workflow('a', json.loads(row['definition']))
        workflow_store.save_workflow('a', flow('report'))

    def test_nothing_changes_when_no_workflow_has_the_job(self):
        self.save('a', value=flow('load'))
        self.assertEqual(workflow_store.remove_job('process'), [])
        self.assertEqual(workflow_store.get_workflow('a')['version'], 1)


class RunStoreTests(StoreCase):
    def test_a_run_has_a_waiting_row_for_each_step(self):
        saved = self.save(value=flow('load', 'process', 'report'))
        run_id = workflow_store.add_run(saved, workflow_store.SCHEDULE,
                                        scheduled_time=dt.datetime(2026, 11, 5, 3),
                                        runner='host:8080')
        row = workflow_store.read_run(run_id)
        self.assertEqual((row['status'], row['steps_total'],
                          row['definition_version'], row['trigger_type']),
                         ('I', 3, 1, 'schedule'))
        self.assertEqual([(step['step_no'], step['job_name'], step['status'])
                          for step in row['steps']],
                         [(1, 'load', 'W'), (2, 'process', 'W'),
                          (3, 'report', 'W')])
        self.assertTrue(all(step['job_id'] for step in row['steps']))
        self.assertTrue(workflow_store.has_active(saved['workflow_id']))

    def test_steps_are_changed_and_skipped(self):
        saved = self.save(value=flow('load', 'process', 'report'))
        run_id = workflow_store.add_run(saved, workflow_store.MANUAL)
        workflow_store.update_step(run_id, 1, status='D', job_run_id=7,
                                   start_time=store.now(),
                                   end_time=store.now())
        workflow_store.update_step(run_id, 2, status='E', message='x' * 5000)
        workflow_store.skip_steps(run_id, 2, 'Stopped.')
        steps = {step['step_no']: step
                 for step in workflow_store.read_steps(run_id)}
        self.assertEqual((steps[1]['status'], steps[1]['job_run_id']),
                         ('D', 7))
        # Only a step that has not started is skipped.
        self.assertEqual(steps[2]['status'], 'E')
        self.assertEqual(len(steps[2]['message']), 4000)
        self.assertEqual((steps[3]['status'], steps[3]['message']),
                         ('K', 'Stopped.'))

    def test_runs_and_their_last(self):
        saved = self.save()
        first = workflow_store.add_run(saved, workflow_store.MANUAL,
                                       requested_by='ana')
        second = workflow_store.add_run(saved, workflow_store.SCHEDULE)
        workflow_store.update_run(first, status='C', message='m' * 5000,
                                  mail_status='x' * 300, step_no=2)
        row = workflow_store.read_run(first)
        self.assertEqual((row['status'], row['step_no'],
                          row['requested_by']), ('C', 2, 'ana'))
        self.assertEqual(len(row['message']), 4000)
        self.assertEqual(len(row['mail_status']), 200)
        runs = workflow_store.read_runs(saved['workflow_id'])
        self.assertEqual([item['run_id'] for item in runs],
                         [second, first])
        self.assertEqual(workflow_store.read_last_runs()
                         [saved['workflow_id']]['run_id'], second)
        self.assertIsNone(workflow_store.read_run(999))

    def test_the_log_of_a_run(self):
        saved = self.save()
        run_id = workflow_store.add_run(saved, workflow_store.MANUAL)
        workflow_store.add_log_line(run_id, saved['workflow_id'], 1, 'INFO',
                                    'Step 1 of 2: load')
        workflow_store.add_log_line(run_id, saved['workflow_id'], 2,
                                    'WARNING', 'Stopped at step 1')
        row = workflow_store.read_run(run_id)
        self.assertEqual([(item['level'], item['text'])
                          for item in row['lines']],
                         [('INFO', 'Step 1 of 2: load'),
                          ('WARNING', 'Stopped at step 1')])
        self.assertEqual(row['run_log'], 'Step 1 of 2: load\nStopped at step 1')

    def test_the_log_is_kept_per_workflow(self):
        saved = self.save()
        wid = saved['workflow_id']
        first = workflow_store.add_run(saved, workflow_store.MANUAL)
        second = workflow_store.add_run(saved, workflow_store.MANUAL)
        for number in (1, 2, 3):
            workflow_store.add_log_line(first, wid, number, 'INFO',
                                        f'a{number}')
        for number in (1, 2):
            workflow_store.add_log_line(second, wid, number, 'INFO',
                                        f'b{number}')
        workflow_store.trim_log(wid, 3)
        self.assertEqual(workflow_store.read_run(first)['run_log'], 'a3')
        self.assertEqual(workflow_store.read_run(second)['run_log'], 'b1\nb2')

    def test_runs_a_crash_left_active_are_finished(self):
        saved = self.save(value=flow('load', 'process'))
        mine = workflow_store.add_run(saved, workflow_store.MANUAL,
                                      runner='me:1')
        other = workflow_store.add_run(saved, workflow_store.MANUAL,
                                       runner='another:1')
        workflow_store.update_step(mine, 1, status='S')
        self.assertEqual(workflow_store.sweep('me:1'), 1)
        row = workflow_store.read_run(mine)
        self.assertEqual(row['status'], 'E')
        self.assertIn('unexpectedly', row['message'])
        self.assertEqual([step['status'] for step in row['steps']],
                         ['K', 'K'])
        self.assertEqual(workflow_store.read_run(other)['status'], 'I')

    def test_old_runs_go_with_their_steps_and_log(self):
        saved = self.save()
        wid = saved['workflow_id']
        old = workflow_store.add_run(saved, workflow_store.MANUAL)
        active = workflow_store.add_run(saved, workflow_store.MANUAL)
        recent = workflow_store.add_run(saved, workflow_store.MANUAL)
        workflow_store.add_log_line(old, wid, 1, 'INFO', 'x')
        long_ago = store.now() - dt.timedelta(days=100)
        workflow_store.update_run(old, status='D', queued_time=long_ago)
        workflow_store.update_run(active, queued_time=long_ago)
        workflow_store.update_run(recent, status='D')
        self.assertEqual(workflow_store.clean_runs(90), 1)
        self.assertEqual(sorted(item['run_id']
                                for item in workflow_store.read_runs()),
                         sorted([active, recent]))
        self.assertIsNone(workflow_store.read_run(old))
        self.assertEqual(workflow_store.read_steps(old), [])

    def test_a_job_run_says_which_workflow_run_it_is_a_step_of(self):
        saved = self.save()
        run_id = workflow_store.add_run(saved, workflow_store.MANUAL)
        row = store.read_job('load')
        job_run = store.add_run(row, 'workflow', workflow_run_id=run_id)
        self.assertEqual(store.read_run(job_run)['workflow_run_id'], run_id)
        self.assertEqual(store.read_run(job_run)['trigger_type'], 'workflow')

    def test_an_event_can_be_about_a_workflow(self):
        saved = self.save()
        store.add_event(None, store.SCHEDULE, 'FIRED',
                        workflow_id=saved['workflow_id'], run_id=3)
        event = store.read_events()[0]
        self.assertEqual((event['workflow_id'], event['job_id'],
                          event['job_name']),
                         (saved['workflow_id'], None, None))

    def test_missing_tables_are_reported(self):
        workflow_store.metadata.drop_all(db.engine)
        with self.assertRaises(store.StoreError) as caught:
            workflow_store.read_workflows()
        self.assertEqual(caught.exception.status, 503)
        self.assertIn('v0.10.0', str(caught.exception))
        self.assertEqual(workflow_store.sweep('me:1'), 0)
        workflow_store.metadata.create_all(db.engine)


if __name__ == '__main__':
    unittest.main()
