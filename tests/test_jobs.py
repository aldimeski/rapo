"""Tests of the jobs (rapo/jobs). Run from the project root:

    python -m unittest tests.test_jobs -v

Like tests/test_transfer.py, whose bootstrap they share, they load the
modules under test without the rest of rapo: the database is replaced by a
SQLite one with the same `execute` interface.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
test_transfer._package('rapo.jobs', os.path.join(ROOT, 'rapo', 'jobs'))

from rapo import config as rapo_config  # noqa: E402
from rapo.database import db  # noqa: E402
from rapo.jobs import catalogue, definition, store  # noqa: E402


def context(system='linux', folders=None, **missing):
    """What a definition is checked against: a server of the OS with all
    its types, the given allowed folders and its interpreters (`name=None`
    in `missing` removes one)."""
    types = catalogue.os_types(system)
    interpreters = {'PYTHON': '/usr/bin/python3', 'PERL': '/usr/bin/perl'}
    if system == 'windows':
        interpreters.update(COMMAND='C:/Windows/cmd.exe',
                            BATCH='C:/Windows/cmd.exe',
                            POWERSHELL='C:/Windows/pwsh.exe')
    else:
        interpreters.update(COMMAND='/bin/sh', SHELL='/bin/bash')
    interpreters.update(missing)
    return {'platform': system, 'types': types, 'os_types': types,
            'allowed_dirs': folders or [], 'interpreters': interpreters}


def job(**parts):
    """A definition with a main task, changed by `parts`."""
    value = {'main': {'type': 'SQL', 'text': 'update t set a = 1'}}
    value.update(parts)
    return value


class CatalogueTests(unittest.TestCase):
    def test_every_os_has_the_common_types(self):
        for system in ('linux', 'windows'):
            types = catalogue.os_types(system)
            for name in ('SQL', 'TRANSFER', 'EXECUTABLE', 'COMMAND',
                         'PYTHON', 'PERL'):
                self.assertIn(name, types)

    def test_shell_is_linux_and_batch_powershell_windows(self):
        self.assertIn('SHELL', catalogue.os_types('linux'))
        self.assertNotIn('BATCH', catalogue.os_types('linux'))
        self.assertNotIn('POWERSHELL', catalogue.os_types('linux'))
        self.assertIn('BATCH', catalogue.os_types('windows'))
        self.assertIn('POWERSHELL', catalogue.os_types('windows'))
        self.assertNotIn('SHELL', catalogue.os_types('windows'))

    def test_enabled_types_cut_the_offer(self):
        self.assertEqual(
            catalogue.offered_types('linux', ['SQL', 'SHELL', 'BATCH']),
            ['SQL', 'SHELL'])
        self.assertEqual(catalogue.offered_types('linux', None),
                         catalogue.os_types('linux'))

    def test_platform_follows_the_os(self):
        self.assertEqual(catalogue.platform(),
                         'windows' if os.name == 'nt' else 'linux')

    def test_the_server_describes_itself(self):
        described = catalogue.describe()
        names = [item['type'] for item in described['types']]
        self.assertEqual(names, catalogue.os_types())
        self.assertTrue(
            next(item for item in described['types']
                 if item['type'] == 'SQL')['available'])
        self.assertIn('FILE_COUNT',
                      [item['kind'] for item in
                       described['condition_kinds']])


class SqlTests(unittest.TestCase):
    def test_statements_are_split_at_semicolons(self):
        self.assertEqual(
            definition.split_sql('update a set x=1; delete from b;'),
            ['update a set x=1', 'delete from b'])

    def test_a_block_ends_at_a_slash_line_not_at_its_semicolons(self):
        text = ('begin\n  update a set x=1;\n  delete from b;\nend;\n/\n'
                'insert into c values (1);')
        self.assertEqual(definition.split_sql(text), [
            'begin\n  update a set x=1;\n  delete from b;\nend;',
            'insert into c values (1)'])

    def test_quotes_and_comments_hide_semicolons_and_slashes(self):
        text = ("insert into a values ('x;y', q'[a;b]', \"c;d\");\n"
                "-- not; a statement\n/* neither; */ update a set b=1")
        self.assertEqual(definition.split_sql(text), [
            "insert into a values ('x;y', q'[a;b]', \"c;d\")",
            '-- not; a statement\n/* neither; */ update a set b=1'])

    def test_only_comments_or_nothing_is_no_statement(self):
        self.assertEqual(definition.split_sql(''), [])
        self.assertEqual(definition.split_sql('-- x\n/* y */\n;'), [])

    def test_first_keyword(self):
        self.assertEqual(definition.first_keyword('  /* c */ (SELECT 1)'),
                         'select')
        self.assertEqual(definition.first_keyword('-- c\nDrop table t'),
                         'drop')

    def check(self, text, query=False):
        problems = []
        definition.check_sql(text, 'Main', problems, query=query)
        return problems

    def test_dml_and_plsql_are_allowed(self):
        for text in ('insert into a values (1)', 'update a set b=1',
                     'delete from a', 'merge into a using b on (1=1) '
                     'when matched then update set x=1',
                     'begin null; end;', 'declare x number; begin null; '
                     'end;', 'commit', 'select 1 from dual',
                     'with t as (select 1 a from dual) select * from t'):
            self.assertEqual(self.check(text), [], msg=text)

    def test_ddl_is_refused(self):
        for text in ('create table t (a number)', 'drop table t',
                     'truncate table t', 'alter session set x=1',
                     'grant select on t to u', 'execute foo',
                     'update a set b=1; drop table t'):
            problems = self.check(text)
            self.assertEqual(len(problems), 1, msg=text)
            self.assertIn('only DML and PL/SQL', problems[0])

    def test_a_condition_is_one_query(self):
        self.assertEqual(self.check('select count(*) from t', True), [])
        self.assertTrue(self.check('select 1 from dual; select 2 from '
                                   'dual', True))
        self.assertTrue(self.check('update t set a=1', True))

    def test_empty_sql_is_refused(self):
        self.assertTrue(self.check('  '))
        self.assertTrue(self.check(None))


class RenderTests(unittest.TestCase):
    def test_known_variables_are_replaced(self):
        when = dt.datetime(2026, 11, 5, 7, 30)
        text = definition.render('{job_name} {scheduled_time:%Y%m%d} {n}',
                                 {'job_name': 'load', 'scheduled_time': when,
                                  'n': 3})
        self.assertEqual(text, 'load 20261105 3')

    def test_other_braces_stay(self):
        text = "awk '{print $1}' ${HOME} {x:y} {{a}} {run_id}"
        self.assertEqual(definition.render(text, {'run_id': 7}),
                         "awk '{print $1}' ${HOME} {x:y} {{a}} 7")

    def test_a_bad_format_is_an_error(self):
        with self.assertRaises(definition.DefinitionError):
            definition.render('{run_id:%Y}', {'run_id': 7})


class DefinitionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='rapo-jobs-test-')
        self.ctx = context(folders=[self.folder])

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def problems(self, value, ctx=None, check_paths=False):
        return definition.validate(definition.normalize(value),
                                   ctx or self.ctx, check_paths)

    def path(self, name='run.sh'):
        return os.path.join(self.folder, name)

    def test_the_smallest_job_is_fine(self):
        self.assertEqual(self.problems(job()), [])

    def test_a_job_needs_a_main_task(self):
        self.assertIn('A job needs a main task.',
                      self.problems({'pre': {'type': 'SQL',
                                             'text': 'commit'}}))

    def test_json_text_is_read_and_defaults_are_filled(self):
        value = definition.normalize(json.dumps(job()))
        self.assertEqual(value['overlap'], 'skip')
        self.assertEqual(value['parameters'], [])
        self.assertIsNone(value['post'])
        with self.assertRaises(definition.DefinitionError):
            definition.load('{nonsense')
        with self.assertRaises(definition.DefinitionError):
            definition.load('[1]')

    def test_type_names_are_upper_cased(self):
        value = definition.normalize(job(main={'type': 'sql',
                                               'text': 'commit'}))
        self.assertEqual(definition.job_type(value), 'SQL')

    def test_unknown_settings_are_refused(self):
        self.assertTrue(self.problems(job(color='red')))
        self.assertTrue(self.problems(
            job(main={'type': 'SQL', 'text': 'commit', 'cwd': '/tmp'})))

    def test_types_of_the_other_os_are_refused(self):
        batch = {'type': 'BATCH', 'code': 'echo 1'}
        shell = {'type': 'SHELL', 'code': 'echo 1'}
        self.assertTrue(self.problems(job(main=batch)))
        self.assertEqual(self.problems(job(main=shell)), [])
        windows = context('windows', [self.folder])
        self.assertEqual(self.problems(job(main=batch), windows), [])
        self.assertTrue(self.problems(job(main=shell), windows))

    def test_types_outside_enabled_types_are_refused(self):
        ctx = context(folders=[self.folder])
        ctx['types'] = ['SQL']
        self.assertTrue(self.problems(
            job(main={'type': 'PYTHON', 'code': 'print(1)'}), ctx))
        self.assertEqual(self.problems(job(), ctx), [])

    def test_a_missing_interpreter_is_refused(self):
        ctx = context(folders=[self.folder], PERL=None)
        problems = self.problems(
            job(main={'type': 'PERL', 'code': 'print 1'}), ctx)
        self.assertTrue(any('interpreter' in text for text in problems))

    def test_script_files_must_be_in_allowed_dirs(self):
        main = {'type': 'PYTHON', 'file': self.path('a.py')}
        self.assertEqual(self.problems(job(main=main)), [])
        outside = {'type': 'PYTHON', 'file': os.path.join(
            tempfile.gettempdir(), 'elsewhere', 'a.py')}
        self.assertTrue(self.problems(job(main=outside)))
        self.assertTrue(self.problems(
            job(main={'type': 'PYTHON', 'file': 'relative.py'})))
        self.assertTrue(self.problems(job(main=main),
                                      context(folders=[])))

    def test_dotdot_does_not_leave_the_allowed_folder(self):
        sneaky = os.path.join(self.folder, '..', 'other', 'a.py')
        self.assertTrue(self.problems(
            job(main={'type': 'PYTHON', 'file': sneaky})))

    def test_a_file_that_is_not_there_is_found_when_asked(self):
        main = {'type': 'EXECUTABLE', 'path': self.path('tool')}
        self.assertEqual(self.problems(job(main=main)), [])
        self.assertTrue(self.problems(job(main=main), check_paths=True))
        with open(self.path('tool'), 'w') as file:
            file.write('x')
        self.assertEqual(self.problems(job(main=main), check_paths=True),
                         [])

    def test_a_script_is_a_file_or_code_not_both_or_neither(self):
        base = {'type': 'PYTHON'}
        self.assertTrue(self.problems(job(main=dict(base))))
        self.assertTrue(self.problems(job(main=dict(
            base, file=self.path('a.py'), code='print(1)'))))
        self.assertEqual(self.problems(job(main=dict(
            base, code='print(1)'))), [])

    def test_command_args_env_cwd_and_shell(self):
        self.assertEqual(self.problems(job(main={
            'type': 'COMMAND', 'command': 'dir', 'cwd': '/tmp',
            'env': {'MODE': 'x'}})), [])
        self.assertTrue(self.problems(job(main={
            'type': 'COMMAND', 'command': ' '})))
        self.assertTrue(self.problems(job(main={
            'type': 'COMMAND', 'command': 'x', 'env': {'1BAD': 'x'}})))
        self.assertTrue(self.problems(job(main={
            'type': 'SHELL', 'code': 'x', 'shell': 'zsh'})))
        self.assertEqual(self.problems(job(main={
            'type': 'SHELL', 'code': 'x', 'shell': 'bash'})), [])
        self.assertTrue(self.problems(job(main={
            'type': 'EXECUTABLE', 'path': self.path('t'),
            'args': 'not a list'})))

    def test_transfer_arguments(self):
        main = {'type': 'TRANSFER', 'param': 'daily-load',
                'arguments': ['FileMask:<YYYYMMDD-1d>\\.csv', 'KeepInput:N']}
        self.assertEqual(self.problems(job(main=main)), [])
        for arguments in (['Nonsense:1'], ['KeepInput'],
                          ['KeepInput:Y', 'KeepInput:N'], 'KeepInput:Y'):
            self.assertTrue(self.problems(job(main=dict(
                main, arguments=arguments))), msg=arguments)
        self.assertTrue(self.problems(job(main={'type': 'TRANSFER',
                                                'param': '../x'})))

    def test_pre_and_post_are_tasks_of_any_type(self):
        value = job(pre={'type': 'SQL', 'text': 'delete from t'},
                    post={'type': 'COMMAND', 'command': 'echo done'})
        self.assertEqual(self.problems(value), [])
        value['pre'] = {'type': 'SQL', 'text': 'drop table t'}
        problems = self.problems(value)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith('Pre:'))

    def test_condition_kinds(self):
        good = [
            {'kind': 'SQL', 'text': 'select count(*) from t'},
            {'kind': 'EXIT_CODE', 'task': {'type': 'COMMAND',
                                           'command': 'test -f x'}},
            {'kind': 'FILE_EXISTS', 'path': '/data/<YYYYMMDD>.ok'},
            {'kind': 'FILE_EXISTS', 'path': '/data/x', 'exists': False},
            {'kind': 'FILE_COUNT', 'directory': '/data', 'min': 1},
            {'kind': 'FILE_COUNT', 'directory': '/data', 'mask': r'\.csv$',
             'min': 1, 'max': 5},
        ]
        for condition in good:
            self.assertEqual(self.problems(job(condition=condition)), [],
                             msg=condition)
        bad = [
            {'kind': 'NOPE'},
            {'kind': 'SQL', 'text': 'update t set a=1'},
            {'kind': 'EXIT_CODE', 'task': {'type': 'SQL', 'text': 'commit'}},
            {'kind': 'FILE_EXISTS', 'path': ''},
            {'kind': 'FILE_EXISTS', 'path': '/x', 'exists': 'yes'},
            {'kind': 'FILE_COUNT', 'directory': '/data'},
            {'kind': 'FILE_COUNT', 'directory': '/data', 'min': 5,
             'max': 1},
            {'kind': 'FILE_COUNT', 'directory': '/data', 'min': -1},
            {'kind': 'FILE_COUNT', 'directory': '/data', 'min': 1,
             'mask': '('},
            {'kind': 'FILE_EXISTS', 'path': '/x', 'text': 'select 1'},
            'file exists',
        ]
        for condition in bad:
            self.assertTrue(self.problems(job(condition=condition)),
                            msg=condition)

    def test_parameters(self):
        self.assertEqual(self.problems(job(parameters=[
            {'name': 'user', 'value': 'x'},
            {'name': 'pass', 'value': 'y', 'secret': True}])), [])
        for parameters in ([{'name': 'a'}, {'name': 'a'}],
                           [{'name': 'run_id', 'value': '1'}],
                           [{'name': '1a'}], [{'name': 'a', 'value': 1}],
                           [{'name': 'a', 'secret': 'yes'}], 'a', ['a']):
            self.assertTrue(self.problems(job(parameters=parameters)),
                            msg=parameters)

    def test_timeout_and_overlap(self):
        for timeout in (None, 0, 60):
            self.assertEqual(self.problems(job(timeout=timeout)), [])
        for timeout in (-1, '60', True, 1.5):
            self.assertTrue(self.problems(job(timeout=timeout)))
        for overlap in ('skip', 'queue', 'allow'):
            self.assertEqual(self.problems(job(overlap=overlap)), [])
        self.assertTrue(self.problems(job(overlap='twice')))

    def test_check_gives_the_normalized_definition_or_every_problem(self):
        self.assertEqual(
            definition.check(job(), self.ctx)['overlap'], 'skip')
        with self.assertRaises(definition.DefinitionError) as caught:
            definition.check(job(main={'type': 'SQL', 'text': 'drop table t'},
                                 timeout=-5), self.ctx)
        self.assertEqual(len(caught.exception.problems), 2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='rapo-jobs-test-')
        self.ctx = context(folders=[self.folder])
        rapo_config.config.pop('JOBS', None)
        db.engine.dispose()
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def save(self, name='nightly', value=None, **kwargs):
        return store.save_job(name, value or job(), ctx=self.ctx, **kwargs)

    def test_names(self):
        for name in ('../x', 'a b', '', '.hidden', 'x' * 129):
            with self.assertRaises(store.StoreError, msg=name):
                self.save(name)
        self.assertEqual(store.check_name(' load-1.daily_x '),
                         'load-1.daily_x')

    def test_a_new_job_is_on_with_version_one(self):
        saved = self.save(description='Nightly load',
                          notify_email='a@x.com; b@y.org')
        self.assertEqual(saved['job_name'], 'nightly')
        self.assertEqual(saved['job_type'], 'SQL')
        self.assertEqual((saved['status'], saved['version']), ('Y', 1))
        self.assertEqual(saved['notify_email'], 'a@x.com, b@y.org')
        self.assertEqual(saved['description'], 'Nightly load')
        self.assertIsNone(saved['schedule_config'])
        self.assertEqual(json.loads(saved['definition'])['main']['text'],
                         'update t set a = 1')
        self.assertEqual([item['job_name'] for item in store.read_jobs()],
                         ['nightly'])
        self.assertNotIn('definition', store.read_jobs()[0])

    def test_an_invalid_job_is_refused_with_422_and_not_saved(self):
        with self.assertRaises(store.StoreError) as caught:
            self.save(value=job(main={'type': 'SQL',
                                      'text': 'drop table t'}))
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(store.read_jobs(), [])

    def test_create_refuses_an_existing_name(self):
        self.save()
        with self.assertRaises(store.StoreError) as caught:
            self.save(create=True)
        self.assertEqual(caught.exception.status, 409)

    def test_a_change_makes_a_version_and_keeps_the_old_one(self):
        first = self.save()
        again = self.save()
        self.assertEqual(again['version'], 1)
        changed = self.save(value=job(main={'type': 'SQL',
                                            'text': 'update t set a = 2'}))
        self.assertEqual(changed['version'], 2)
        versions = store.read_versions(first['job_id'])
        self.assertEqual([item['version'] for item in versions], [2, 1])
        self.assertIn('a = 1', versions[1]['definition'])
        self.assertIn('a = 2', versions[0]['definition'])

    def test_the_same_content_in_another_order_is_no_new_version(self):
        self.save(value={'overlap': 'skip',
                         'main': {'text': 'commit', 'type': 'SQL'}})
        saved = self.save(value={'main': {'type': 'SQL', 'text': 'commit'}})
        self.assertEqual(saved['version'], 1)

    def test_what_is_not_given_stays(self):
        self.save(description='D', notify_email='a@x.com',
                  schedule_config='{"hour": "3", "min": "0"}')
        saved = self.save(value=job(timeout=30))
        self.assertEqual(saved['description'], 'D')
        self.assertEqual(saved['notify_email'], 'a@x.com')
        self.assertEqual(saved['schedule_config'],
                         '{"hour": "3", "min": "0"}')
        cleared = self.save(value=job(timeout=30), description='',
                            notify_email='', schedule_config=None)
        self.assertIsNone(cleared['description'])
        self.assertIsNone(cleared['notify_email'])
        self.assertIsNone(cleared['schedule_config'])

    def test_a_schedule_change_is_a_version_a_status_change_is_not(self):
        self.save()
        scheduled = store.write_schedule('nightly', '{"hour": "3"}')
        self.assertEqual(scheduled['version'], 2)
        store.set_status('nightly', False)
        self.assertEqual(store.get_job('nightly')['status'], 'N')
        self.assertEqual(store.get_job('nightly')['version'], 2)
        store.set_status('nightly', True)
        self.assertEqual(store.get_job('nightly')['status'], 'Y')
        with self.assertRaises(store.StoreError):
            store.write_schedule('nightly', 'not json')

    def test_bad_addresses_and_status_are_refused(self):
        for email in ('nobody', 'a@x', 'a@x.com b@y.org'):
            with self.assertRaises(store.StoreError, msg=email):
                self.save(notify_email=email)
        with self.assertRaises(store.StoreError):
            self.save(status='X')
        with self.assertRaises(store.StoreError):
            self.save(description='x' * 501)

    def test_a_job_that_is_not_there(self):
        with self.assertRaises(store.StoreError) as caught:
            store.get_job('missing')
        self.assertEqual(caught.exception.status, 404)
        self.assertIsNone(store.read_job('missing'))

    def test_delete_is_refused_while_a_run_is_active(self):
        saved = self.save()
        run_id = store.add_run(saved, store.MANUAL)
        with self.assertRaises(store.StoreError) as caught:
            store.delete_job('nightly')
        self.assertEqual(caught.exception.status, 409)
        store.update_run(run_id, status=store.DONE)
        store.delete_job('nightly')
        self.assertEqual(store.read_jobs(), [])
        self.assertEqual(store.read_versions(saved['job_id']), [])
        # The history stays until the retention deletes it.
        self.assertEqual(len(store.read_runs(saved['job_id'])), 1)

    def test_runs(self):
        saved = self.save()
        when = dt.datetime(2026, 11, 5, 3, 0)
        first = store.add_run(saved, store.SCHEDULE, scheduled_time=when,
                              runner='host:8080')
        second = store.add_run(saved, store.MANUAL, requested_by='ana')
        self.assertTrue(store.has_active(saved['job_id']))
        store.update_run(first, status=store.RUNNING, stage='MAIN',
                         start_time=store.now())
        store.update_run(first, status=store.ERROR, exit_code=2,
                         message='x' * 5000, mail_status='sent',
                         end_time=store.now())
        row = store.read_run(first)
        self.assertEqual((row['status'], row['stage'], row['exit_code']),
                         ('E', 'MAIN', 2))
        self.assertEqual(len(row['message']), 4000)
        self.assertEqual(row['definition_version'], 1)
        self.assertEqual(row['trigger_type'], 'schedule')
        self.assertEqual(row['job_name'], 'nightly')
        store.update_run(second, status=store.CANCELED,
                         message='Condition not met.',
                         condition_value='0')
        self.assertFalse(store.has_active(saved['job_id']))
        runs = store.read_runs(saved['job_id'])
        self.assertEqual([item['run_id'] for item in runs],
                         [second, first])
        self.assertEqual(store.read_last_runs()[saved['job_id']]['run_id'],
                         second)
        self.assertIsNone(store.read_run(999))

    def test_the_log_is_kept_per_job_newest_lines_first(self):
        saved = self.save()
        job_id = saved['job_id']
        first = store.add_run(saved, store.MANUAL)
        second = store.add_run(saved, store.MANUAL)
        now = time.time()
        store.add_log_lines(first, job_id, [
            (now, 'INFO', 'rapo', 'start'), (now, 'INFO', 'out', 'one'),
            (now, 'ERROR', 'err', 'boom')], limit=10)
        store.update_run(first, log_lines=3)
        store.add_log_lines(second, job_id, [
            (now, 'INFO', 'out', 'two'), (now, 'INFO', 'out', 'three')],
            limit=4)
        store.update_run(second, log_lines=2)
        self.assertEqual(store.read_run(second)['run_log'], 'two\nthree')
        older = store.read_run(first)
        self.assertEqual(older['run_log'], 'one\nboom')
        self.assertTrue(older['log_trimmed'])
        self.assertFalse(store.read_run(second)['log_trimmed'])

    def test_old_runs_go_with_their_log_but_active_ones_stay(self):
        saved = self.save()
        job_id = saved['job_id']
        old = store.add_run(saved, store.MANUAL)
        active = store.add_run(saved, store.MANUAL)
        recent = store.add_run(saved, store.MANUAL)
        store.add_log_lines(old, job_id, [(time.time(), 'INFO', 'out', 'x')],
                            limit=10)
        long_ago = store.now() - dt.timedelta(days=100)
        store.update_run(old, status=store.DONE, queued_time=long_ago)
        store.update_run(active, queued_time=long_ago)
        store.update_run(recent, status=store.DONE)
        self.assertEqual(store.clean_runs(90), 1)
        self.assertEqual(sorted(item['run_id']
                                for item in store.read_runs()),
                         sorted([active, recent]))
        self.assertEqual(store.read_run(old), None)

    def test_events(self):
        saved = self.save()
        store.add_event(saved['job_id'], store.SCHEDULE, 'fire',
                        scheduled_time=dt.datetime(2026, 11, 5, 3, 0),
                        run_id=1, runner='host:8080')
        store.add_event(saved['job_id'], store.SCHEDULE, 'missed',
                        message='x' * 5000)
        store.add_event(None, store.MANUAL, 'start')
        events = store.read_events()
        self.assertEqual([item['event_type'] for item in events],
                         ['start', 'missed', 'fire'])
        self.assertEqual(len(events[1]['message']), 4000)
        self.assertEqual(len(store.read_events(job_id=saved['job_id'])), 2)
        old = events[2]['event_id']
        db.execute(store.event_table.update().where(
            store.event_table.c.event_id == old).values(
            event_time=store.now() - dt.timedelta(days=100)))
        self.assertEqual(store.clean_events(90), 1)
        self.assertEqual(len(store.read_events()), 2)

    def test_missing_tables_are_reported(self):
        store.metadata.drop_all(db.engine)
        with self.assertRaises(store.StoreError) as caught:
            store.read_jobs()
        self.assertEqual(caught.exception.status, 503)
        self.assertIn('v0.9.0', str(caught.exception))
        store.metadata.create_all(db.engine)


class SecretsTests(unittest.TestCase):
    def definition(self, value='hunter2', secret=True, **extra):
        return {'main': {'type': 'SQL', 'text': 'commit'},
                'parameters': [{'name': 'pw', 'value': value,
                                'secret': secret},
                               {'name': 'user', 'value': 'ana'}], **extra}

    def test_a_secret_value_is_hidden_from_the_browser(self):
        masked = definition.mask_secrets(self.definition())
        self.assertEqual(masked['parameters'][0]['value'], definition.MASK)
        self.assertEqual(masked['parameters'][1]['value'], 'ana')
        # The original is left as it was.
        original = self.definition()
        definition.mask_secrets(original)
        self.assertEqual(original['parameters'][0]['value'], 'hunter2')

    def test_an_empty_secret_and_a_plain_value_are_shown_as_they_are(self):
        masked = definition.mask_secrets(self.definition(value=''))
        self.assertEqual(masked['parameters'][0]['value'], '')
        masked = definition.mask_secrets(self.definition(secret=False))
        self.assertEqual(masked['parameters'][0]['value'], 'hunter2')

    def test_a_hidden_value_sent_back_keeps_the_stored_secret(self):
        sent = definition.mask_secrets(self.definition())
        restored = definition.restore_secrets(sent, self.definition())
        self.assertEqual(restored['parameters'][0]['value'], 'hunter2')
        # A new value replaces it.
        sent['parameters'][0]['value'] = 'new'
        restored = definition.restore_secrets(sent, self.definition())
        self.assertEqual(restored['parameters'][0]['value'], 'new')

    def test_a_hidden_value_that_was_never_known_is_refused(self):
        sent = definition.mask_secrets(self.definition())
        for stored in (None, {}, self.definition(secret=False),
                       {'parameters': [{'name': 'other', 'value': 'x',
                                        'secret': True}]}):
            with self.assertRaises(definition.DefinitionError):
                definition.restore_secrets(sent, stored)

    def test_a_secret_is_not_in_the_text_of_a_message_to_the_browser(self):
        masked = definition.mask_secrets(self.definition())
        self.assertNotIn('hunter2', json.dumps(masked))


class UsesSqlTests(unittest.TestCase):
    def check(self, **parts):
        value = {'main': {'type': 'COMMAND', 'command': 'x'}}
        value.update(parts)
        return definition.uses_sql(value)

    def test_sql_in_any_part_or_in_the_condition(self):
        self.assertFalse(self.check())
        self.assertTrue(self.check(main={'type': 'SQL', 'text': 'commit'}))
        self.assertTrue(self.check(pre={'type': 'SQL', 'text': 'commit'}))
        self.assertTrue(self.check(post={'type': 'SQL', 'text': 'commit'}))
        self.assertTrue(self.check(condition={'kind': 'SQL',
                                              'text': 'select 1'}))
        self.assertFalse(self.check(condition={'kind': 'FILE_EXISTS',
                                               'path': '/x'}))
        self.assertFalse(self.check(pre=None, post=None, condition=None))


class StoreHelperTests(unittest.TestCase):
    def setUp(self):
        db.engine.dispose()
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)

    def test_schedule_json(self):
        self.assertIsNone(store.schedule_json(None))
        self.assertIsNone(store.schedule_json({}))
        self.assertIsNone(store.schedule_json({'hour': None, 'min': ''}))
        text = store.schedule_json({'hour': '3', 'min': '0', 'other': 'x'})
        self.assertEqual(json.loads(text), {
            'mday': None, 'wday': None, 'hour': '3', 'min': '0',
            'sec': None})

    def test_events_have_the_name_of_their_job_and_can_be_filtered(self):
        one = store.save_job('one', job(), ctx=context())
        two = store.save_job('two', job(), ctx=context())
        store.add_event(one['job_id'], 'schedule', 'FIRED')
        store.add_event(two['job_id'], 'manual', 'MISSED')
        store.add_event(one['job_id'], 'schedule', 'MISSED')
        events = store.read_events()
        self.assertEqual([item['job_name'] for item in events],
                         ['one', 'two', 'one'])
        self.assertEqual(len(store.read_events(job_id=one['job_id'])), 2)
        self.assertEqual(len(store.read_events(event_type='MISSED')), 2)
        self.assertEqual(len(store.read_events(trigger_type='manual')), 1)
        self.assertEqual(len(store.read_events(
            since=dt.datetime.now() + dt.timedelta(hours=1))), 0)
        self.assertEqual(len(store.read_events(limit=1)), 1)
        store.delete_job('two')
        names = [item['job_name'] for item in store.read_events()]
        self.assertEqual(names, ['one', None, 'one'])


if __name__ == '__main__':
    unittest.main()
