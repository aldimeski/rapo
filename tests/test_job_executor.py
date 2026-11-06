"""Tests of the execution of jobs (rapo/jobs/executor.py, processes.py).
Run from the project root:

    python -m unittest tests.test_job_executor -v

They start real programs (the Python that runs the tests, and the shell of
the OS), so they take a few seconds.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import psutil
import sqlalchemy as sa

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
test_transfer._package('rapo.jobs', os.path.join(ROOT, 'rapo', 'jobs'))

from rapo import config as rapo_config  # noqa: E402
from rapo.database import db  # noqa: E402
from rapo.jobs import catalogue, executor, processes  # noqa: E402
from rapo.jobs import definition as definitions  # noqa: E402
from rapo.transfer import store as transfer_store  # noqa: E402

WINDOWS = os.name == 'nt'


def py(code, **settings):
    """A PYTHON task of the given code."""
    return dict({'type': 'PYTHON', 'code': code}, **settings)


class Messages(list):
    """What a run emitted, as it is handed over."""

    @property
    def lines(self):
        return [line for kind, payload in self if kind == 'log'
                for line in payload]

    def text(self, stream=None):
        return '\n'.join(line[3] for line in self.lines
                         if stream is None or line[2] == stream)

    def kinds(self, kind):
        return [payload for name, payload in self if name == kind]


class ExecutorCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='rapo-job-exec-')
        self.marker = os.path.join(self.folder, 'marker.txt')
        interpreters = catalogue.interpreters()
        interpreters['PYTHON'] = sys.executable
        types = catalogue.os_types()
        self.ctx = {'platform': catalogue.platform(), 'types': types,
                    'os_types': types, 'interpreters': interpreters,
                    'allowed_dirs': [self.folder,
                                     os.path.dirname(sys.executable)]}
        rapo_config.config.pop('JOBS', None)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def spec(self, main=None, **parts):
        value = {'main': main or py('print("hi")')}
        value.update(parts)
        return {'job_name': 'nightly', 'job_id': 7, 'run_id': 42,
                'trigger': 'manual', 'scheduled_time': None,
                'definition': definitions.normalize(value)}

    def run_job(self, main=None, extra=None, **parts):
        spec = self.spec(main, **parts)
        spec.update(extra or {})
        messages = Messages()
        outcome = executor.execute(
            spec, lambda kind, payload: messages.append((kind, payload)),
            ctx=self.ctx)
        return outcome, messages

    def mark(self, name):
        """Python code that adds a line to the marker file."""
        return py("import os\nopen(os.environ['RAPO_PARAM_MARKER'], 'a')"
                  f".write('{name}\\n')")

    def marks(self):
        if not os.path.exists(self.marker):
            return []
        with open(self.marker) as file:
            return file.read().split()

    MARKER = [{'name': 'marker', 'value': None}]

    def parts(self, **tasks):
        """Parts that write to the marker, with `tasks` to replace some."""
        value = {'pre': self.mark('PRE'), 'main': self.mark('MAIN'),
                 'post': self.mark('POST'),
                 'parameters': [{'name': 'marker', 'value': self.marker}]}
        value.update(tasks)
        return value


class FlowTests(ExecutorCase):
    def test_a_main_task_alone(self):
        outcome, messages = self.run_job(py('print("hello")'))
        self.assertEqual((outcome['status'], outcome['stage'],
                          outcome['exit_code']), ('D', 'MAIN', 0))
        self.assertIsNone(outcome['message'])
        self.assertIn('hello', messages.text('out'))
        self.assertEqual(messages.kinds('stage'), ['MAIN'])

    def test_the_parts_run_in_order(self):
        outcome, messages = self.run_job(**self.parts(
            condition={'kind': 'FILE_EXISTS', 'path': self.folder}))
        self.assertEqual(outcome['status'], 'D')
        self.assertEqual(self.marks(), ['PRE', 'MAIN', 'POST'])
        self.assertEqual(messages.kinds('stage'),
                         ['PRE', 'CONDITION', 'MAIN', 'POST'])
        self.assertEqual(messages.kinds('condition'), ['exists'])

    def test_a_failed_pre_stops_everything(self):
        outcome, _ = self.run_job(**self.parts(pre=py('raise SystemExit(3)')))
        self.assertEqual((outcome['status'], outcome['stage']),
                         ('E', 'PRE'))
        self.assertTrue(outcome['message'].startswith('PRE-STEP failed'))
        self.assertEqual(self.marks(), [])

    def test_a_condition_not_met_cancels_without_an_error(self):
        outcome, _ = self.run_job(**self.parts(condition={
            'kind': 'FILE_EXISTS',
            'path': os.path.join(self.folder, 'missing')}))
        self.assertEqual((outcome['status'], outcome['stage'],
                          outcome['condition_value']),
                         ('C', 'CONDITION', 'missing'))
        self.assertEqual(outcome['message'], 'The condition was not met.')
        # PRE runs before the condition, as in a control; MAIN and POST not.
        self.assertEqual(self.marks(), ['PRE'])

    def test_a_failed_main_skips_post_and_keeps_its_exit_code(self):
        outcome, _ = self.run_job(**self.parts(
            main=py('raise SystemExit(7)')))
        self.assertEqual((outcome['status'], outcome['stage'],
                          outcome['exit_code']), ('E', 'MAIN', 7))
        self.assertTrue(outcome['message'].startswith('MAIN failed'))
        self.assertEqual(self.marks(), ['PRE'])

    def test_a_failed_post_is_an_error_but_main_stays_recorded(self):
        outcome, _ = self.run_job(**self.parts(post=py('raise SystemExit(1)')))
        self.assertEqual((outcome['status'], outcome['stage'],
                          outcome['exit_code']), ('E', 'POST', 0))
        self.assertTrue(outcome['message'].startswith('POST-STEP failed'))
        self.assertEqual(self.marks(), ['PRE', 'MAIN'])

    def test_stderr_alone_is_not_a_failure(self):
        outcome, messages = self.run_job(py(
            'import sys\nsys.stderr.write("warned\\n")'))
        self.assertEqual(outcome['status'], 'D')
        self.assertIn('warned', messages.text('err'))
        self.assertNotIn('warned', messages.text('out'))

    def test_a_program_that_is_not_there_is_an_error(self):
        main = {'type': 'EXECUTABLE',
                'path': os.path.join(self.folder, 'nothing')}
        outcome, _ = self.run_job(main)
        self.assertEqual(outcome['status'], 'E')
        self.assertIn('not fit to run', outcome['message'])

    def test_a_definition_this_server_does_not_allow_is_not_run(self):
        self.ctx['types'] = ['SQL']
        outcome, _ = self.run_job(py('print(1)'))
        self.assertEqual(outcome['status'], 'E')
        self.assertIn('enabled_types', outcome['message'])
        self.assertIsNone(outcome['stage'])


class ProgramTests(ExecutorCase):
    def test_variables_and_parameters_reach_the_program(self):
        outcome, messages = self.run_job(
            {'type': 'EXECUTABLE', 'path': sys.executable,
             'args': ['-c', 'import sys; print(sys.argv[1], sys.argv[2])',
                      '{job_name}', '{who}'],
             'cwd': self.folder},
            parameters=[{'name': 'who', 'value': 'ana'}])
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertIn('nightly ana', messages.text('out'))

    def test_a_run_may_give_other_values_for_the_parameters(self):
        outcome, messages = self.run_job(
            py('import os\nprint(os.environ["RAPO_PARAM_WHO"])'),
            extra={'parameters': {'who': 'bob'}},
            parameters=[{'name': 'who', 'value': 'ana'}])
        self.assertEqual(outcome['status'], 'D')
        self.assertIn('bob', messages.text('out'))

    def test_the_date_formats_of_the_run_time_work(self):
        outcome, messages = self.run_job(py(
            'print("day={scheduled_time:%Y%m%d}")'),
            extra={'scheduled_time': __import__('datetime').datetime(
                2026, 11, 5, 3, 0)})
        self.assertIn('day=20261105', messages.text('out'))

    def test_braces_of_the_code_are_left_alone(self):
        outcome, messages = self.run_job(py(
            'print({"a": 1}["a"], "{job_name}")'))
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertIn('1 nightly', messages.text('out'))

    def test_a_script_file_gets_its_arguments(self):
        script = os.path.join(self.folder, 'tool.py')
        with open(script, 'w') as file:
            file.write('import sys\nprint("args", sys.argv[1:])\n')
        outcome, messages = self.run_job({
            'type': 'PYTHON', 'file': script, 'args': ['a', '{run_id}']})
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertIn("args ['a', '42']", messages.text('out'))

    def test_a_command_runs_through_the_shell(self):
        outcome, messages = self.run_job({'type': 'COMMAND',
                                          'command': 'echo shell-says-hi'})
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertIn('shell-says-hi', messages.text('out'))

    def test_the_environment_is_a_cleaned_one(self):
        os.environ['RAPO_TEST_SECRET'] = 'leak'
        try:
            outcome, messages = self.run_job(
                py('import os\nprint(sorted(os.environ))'),
                parameters=[{'name': 'pw', 'value': 'x'}])
        finally:
            del os.environ['RAPO_TEST_SECRET']
        text = messages.text('out')
        self.assertNotIn('RAPO_TEST_SECRET', text)
        for name in ('RAPO_JOB_NAME', 'RAPO_JOB_ID', 'RAPO_RUN_ID',
                     'RAPO_TRIGGER', 'RAPO_PARAM_PW', 'PATH'):
            self.assertIn(name, text.upper() if name == 'PATH' else text)

    def test_the_env_of_a_task_is_added(self):
        outcome, messages = self.run_job(py(
            'import os\nprint(os.environ["MODE"])',
            env={'MODE': 'daily-{job_name}'}))
        self.assertIn('daily-nightly', messages.text('out'))

    def test_secrets_are_hidden_in_the_log(self):
        outcome, messages = self.run_job(
            py('import os\nprint("pw is", os.environ["RAPO_PARAM_PW"])'),
            parameters=[{'name': 'pw', 'value': 'hunter2', 'secret': True}])
        self.assertEqual(outcome['status'], 'D')
        self.assertNotIn('hunter2', messages.text())
        self.assertIn('pw is ***', messages.text('out'))

    def test_the_output_of_a_run_is_limited(self):
        outcome, messages = self.run_job(
            py('for number in range(3000):\n    print("x" * 50, number)'),
            extra={'max_output_kb': 1})
        self.assertEqual(outcome['status'], 'D')
        size = sum(len(line[3]) for line in messages.lines
                   if line[2] == 'out')
        self.assertLess(size, 2048)
        self.assertIn('the rest is not kept', messages.text('rapo'))

    def test_a_non_utf8_output_is_still_read(self):
        outcome, messages = self.run_job(py(
            'import sys\nsys.stdout.buffer.write(b"caf\\xe9\\n")\n'
            'sys.stdout.flush()'))
        self.assertEqual(outcome['status'], 'D')
        self.assertIn('caf', messages.text('out'))

    def test_a_file_outside_the_allowed_folders_is_not_run(self):
        other = tempfile.mkdtemp(prefix='rapo-job-other-')
        try:
            script = os.path.join(other, 'a.py')
            with open(script, 'w') as file:
                file.write('print(1)')
            outcome, _ = self.run_job({'type': 'PYTHON', 'file': script})
            self.assertEqual(outcome['status'], 'E')
            self.assertIn('allowed_dirs', outcome['message'])
        finally:
            shutil.rmtree(other, ignore_errors=True)

    @unittest.skipUnless(WINDOWS, 'a Windows type')
    def test_a_batch_file_with_spaces_in_its_path(self):
        folder = os.path.join(self.folder, 'a folder')
        os.mkdir(folder)
        script = os.path.join(folder, 'run me.cmd')
        with open(script, 'w') as file:
            file.write('@echo off\r\necho batch-says %1\r\n')
        outcome, messages = self.run_job({'type': 'BATCH', 'file': script,
                                          'args': ['hi']})
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertIn('batch-says hi', messages.text('out'))

    @unittest.skipUnless(WINDOWS, 'a Windows type')
    def test_batch_code_and_its_exit_code(self):
        outcome, messages = self.run_job({'type': 'BATCH',
                                          'code': 'echo from-code\nexit /b 4'})
        self.assertEqual((outcome['status'], outcome['exit_code']),
                         ('E', 4))
        self.assertIn('from-code', messages.text('out'))

    @unittest.skipUnless(WINDOWS and catalogue.interpreters().get(
        'POWERSHELL'), 'PowerShell on Windows')
    def test_powershell_code(self):
        outcome, messages = self.run_job({
            'type': 'POWERSHELL',
            'code': 'Write-Output "ps-says-$env:RAPO_JOB_NAME"'})
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertIn('ps-says-nightly', messages.text('out'))

    @unittest.skipIf(WINDOWS, 'a Linux type')
    def test_shell_code(self):
        outcome, messages = self.run_job({
            'type': 'SHELL', 'code': 'echo "sh-says-$RAPO_JOB_NAME"\nexit 0'})
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertIn('sh-says-nightly', messages.text('out'))


class ConditionTests(ExecutorCase):
    def stage(self, condition):
        outcome, _ = self.run_job(condition=condition)
        return outcome

    def test_exit_code(self):
        met = self.stage({'kind': 'EXIT_CODE', 'task': py('pass')})
        self.assertEqual((met['status'], met['condition_value']),
                         ('D', '0'))
        not_met = self.stage({'kind': 'EXIT_CODE',
                              'task': py('raise SystemExit(2)')})
        self.assertEqual((not_met['status'], not_met['condition_value']),
                         ('C', '2'))

    def test_run_status_says_why_it_is_not_met(self):
        condition = {'kind': 'RUN_STATUS', 'targets': [
            {'type': 'job', 'name': 'prep', 'window': {'kind': 'today'}}]}
        seen = []

        def fake(value, owner, now):
            seen.append(owner)
            return False, 'the job prep has not run today'
        original = executor.dependencies.check
        executor.dependencies.check = fake
        try:
            outcome = self.stage(condition)
        finally:
            executor.dependencies.check = original
        self.assertEqual((outcome['status'], outcome['stage']),
                         ('C', 'CONDITION'))
        self.assertEqual(outcome['message'], 'The condition was not met: '
                         'the job prep has not run today.')
        self.assertEqual(outcome['condition_value'],
                         'the job prep has not run today')
        self.assertEqual(seen[0][0], 'job')

    def test_a_file_that_should_be_missing(self):
        path = os.path.join(self.folder, 'lock')
        condition = {'kind': 'FILE_EXISTS', 'path': path, 'exists': False}
        self.assertEqual(self.stage(condition)['status'], 'D')
        open(path, 'w').close()
        self.assertEqual(self.stage(condition)['status'], 'C')

    def test_a_path_with_a_date_token(self):
        name = time.strftime('%Y%m%d') + '.ok'
        open(os.path.join(self.folder, name), 'w').close()
        outcome = self.stage({'kind': 'FILE_EXISTS',
                              'path': os.path.join(self.folder,
                                                   '<YYYYMMDD>.ok')})
        self.assertEqual(outcome['status'], 'D')

    def test_a_file_count(self):
        for number in range(3):
            open(os.path.join(self.folder, f'a{number}.csv'), 'w').close()
        open(os.path.join(self.folder, 'b.txt'), 'w').close()
        condition = {'kind': 'FILE_COUNT', 'directory': self.folder,
                     'mask': r'\.csv$', 'min': 3, 'max': 3}
        outcome = self.stage(condition)
        self.assertEqual((outcome['status'], outcome['condition_value']),
                         ('D', '3'))
        self.assertEqual(self.stage(dict(condition, min=4, max=9))['status'],
                         'C')
        self.assertEqual(self.stage(dict(condition, mask=None, min=4,
                                         max=4))['status'], 'D')

    def test_a_missing_folder_is_an_error(self):
        outcome = self.stage({'kind': 'FILE_COUNT', 'min': 1,
                              'directory': os.path.join(self.folder, 'no')})
        self.assertEqual((outcome['status'], outcome['stage']),
                         ('E', 'CONDITION'))
        self.assertTrue(outcome['message'].startswith('CONDITION failed'))

    def test_a_query(self):
        with db.engine.begin() as connection:
            connection.execute(sa.text('drop table if exists dual_'))
            connection.execute(sa.text('create table dual_ (x integer)'))
            connection.execute(sa.text('insert into dual_ values (1)'))
        for text, status, value in (('select 5 from dual_', 'D', '5'),
                                    ('select 0 from dual_', 'C', '0'),
                                    ('select x from dual_ where 1 = 0',
                                     'C', ''),
                                    ("select 'N' from dual_", 'C', 'N')):
            outcome = self.stage({'kind': 'SQL', 'text': text})
            self.assertEqual((outcome['status'], outcome['condition_value']),
                             (status, value), msg=text)

    def test_a_failing_query_is_an_error(self):
        outcome = self.stage({'kind': 'SQL', 'text': 'select 1 from nowhere'})
        self.assertEqual((outcome['status'], outcome['stage']),
                         ('E', 'CONDITION'))


class SqlTests(ExecutorCase):
    def setUp(self):
        super().setUp()
        with db.engine.begin() as connection:
            connection.execute(sa.text('drop table if exists jt'))
            connection.execute(sa.text('create table jt (a integer)'))

    def rows(self):
        with db.engine.begin() as connection:
            return sorted(row[0] for row in connection.execute(
                sa.text('select a from jt')))

    def test_a_script_runs_and_commits(self):
        outcome, messages = self.run_job({
            'type': 'SQL',
            'text': 'insert into jt values (1);\ninsert into jt values '
                    '(2);\nupdate jt set a = a + {step}'},
            parameters=[{'name': 'step', 'value': '10'}])
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertEqual(self.rows(), [11, 12])
        self.assertIn('Committed', messages.text('rapo'))
        self.assertIn('2 row(s) updated', messages.text('rapo'))

    def test_a_query_shows_its_rows(self):
        self.run_job({'type': 'SQL', 'text': 'insert into jt values (4)'})
        outcome, messages = self.run_job({'type': 'SQL',
                                          'text': 'select a from jt'})
        self.assertEqual(outcome['status'], 'D')
        self.assertIn('4', messages.text('out').split())

    def test_an_error_rolls_the_whole_script_back(self):
        outcome, _ = self.run_job({
            'type': 'SQL', 'text': 'insert into jt values (5);\n'
                                   'insert into nowhere values (1)'})
        self.assertEqual((outcome['status'], outcome['stage']),
                         ('E', 'MAIN'))
        self.assertIn('SQL 2 of 2 (INSERT)', outcome['message'])
        self.assertEqual(self.rows(), [])

    def test_sql_as_pre_and_post(self):
        outcome, _ = self.run_job(
            {'type': 'SQL', 'text': 'update jt set a = a * 2'},
            pre={'type': 'SQL', 'text': 'insert into jt values (3)'},
            post={'type': 'SQL', 'text': 'update jt set a = a + 1'})
        self.assertEqual(outcome['status'], 'D')
        self.assertEqual(self.rows(), [7])


class TransferTests(ExecutorCase):
    def setUp(self):
        super().setUp()
        self.source = os.path.join(self.folder, 'in')
        self.target = os.path.join(self.folder, 'out')
        os.mkdir(self.source)
        os.mkdir(self.target)
        for name in ('a.txt', 'b.csv'):
            with open(os.path.join(self.source, name), 'w') as file:
                file.write('x')
        section = rapo_config.Configuration('TRANSFER')
        section['param_dir'] = self.folder
        rapo_config.config['TRANSFER'] = section
        transfer_store.metadata.drop_all(db.engine)
        transfer_store.metadata.create_all(db.engine)
        self.text = (f'InputDir:{self.source}\nOutputDir:{self.target}\n'
                     'FileMask:\\.txt$\nLogMode:None\n')

    def tearDown(self):
        rapo_config.config.pop('TRANSFER', None)
        super().tearDown()

    def test_a_param_kept_as_a_file_runs(self):
        transfer_store.write('daily', self.text, transfer_store.FILE,
                             create=True)
        outcome, messages = self.run_job({'type': 'TRANSFER',
                                          'param': 'daily'})
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertEqual(os.listdir(self.target), ['a.txt'])
        self.assertIn('1 transferred', messages.text('rapo'))

    def test_a_param_kept_in_the_database_runs(self):
        transfer_store.write('in-base', self.text, transfer_store.DATABASE,
                             create=True)
        outcome, _ = self.run_job({'type': 'TRANSFER', 'param': 'in-base'})
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertEqual(os.listdir(self.target), ['a.txt'])

    def test_arguments_replace_parameters_for_this_job_only(self):
        transfer_store.write('daily', self.text, transfer_store.FILE,
                             create=True)
        outcome, _ = self.run_job({
            'type': 'TRANSFER', 'param': 'daily',
            'arguments': ['FileMask:\\.csv$', 'KeepInput:N']})
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertEqual(os.listdir(self.target), ['b.csv'])
        self.assertEqual(sorted(os.listdir(self.source)), ['a.txt'])
        self.assertEqual(transfer_store.read('daily')[0], self.text)

    def test_a_variable_in_an_argument(self):
        transfer_store.write('daily', self.text, transfer_store.FILE,
                             create=True)
        outcome, _ = self.run_job(
            {'type': 'TRANSFER', 'param': 'daily',
             'arguments': ['FileMask:\\.{ext}$']},
            parameters=[{'name': 'ext', 'value': 'csv'}])
        self.assertEqual(outcome['status'], 'D', outcome['message'])
        self.assertEqual(os.listdir(self.target), ['b.csv'])

    def test_a_failing_transfer_fails_the_job(self):
        transfer_store.write('daily', self.text, transfer_store.FILE,
                             create=True)
        outcome, _ = self.run_job({
            'type': 'TRANSFER', 'param': 'daily',
            'arguments': [f'OutputDir:{os.path.join(self.folder, "nope")}']})
        self.assertEqual((outcome['status'], outcome['stage']),
                         ('E', 'MAIN'))

    def test_a_param_that_does_not_exist(self):
        outcome, _ = self.run_job({'type': 'TRANSFER', 'param': 'nothing'})
        self.assertEqual(outcome['status'], 'E')
        self.assertIn('not found', outcome['message'])


class ProcessTests(unittest.TestCase):
    def test_a_process_is_stopped_with_the_programs_it_started(self):
        code = ('import subprocess, sys, time\n'
                'child = subprocess.Popen([sys.executable, "-c", '
                '"import time; time.sleep(60)"])\n'
                'print(child.pid, flush=True)\ntime.sleep(60)\n')
        parent = subprocess.Popen([sys.executable, '-c', code],
                                  stdout=subprocess.PIPE)
        child = int(parent.stdout.readline())
        self.assertTrue(psutil.pid_exists(child))
        started = time.time()
        processes.kill_tree(parent.pid, grace=3)
        parent.wait(timeout=10)
        parent.stdout.close()
        self.assertLess(time.time() - started, 8)
        for _ in range(50):
            if not psutil.pid_exists(child):
                break
            time.sleep(0.1)
        self.assertFalse(psutil.pid_exists(child))

    def test_a_process_that_is_gone_is_not_an_error(self):
        parent = subprocess.Popen([sys.executable, '-c', 'pass'])
        parent.wait()
        processes.kill_tree(parent.pid, grace=1)
        processes.kill_tree(2 ** 22 + 12345, grace=1)

    def test_the_environment_keeps_the_essentials_only(self):
        os.environ['RAPO_TEST_SECRET'] = 'x'
        try:
            environment = processes.clean_environment({'A': 'b'})
        finally:
            del os.environ['RAPO_TEST_SECRET']
        self.assertNotIn('RAPO_TEST_SECRET', environment)
        self.assertEqual(environment['A'], 'b')
        self.assertTrue(any(key.upper() == 'PATH' for key in environment))


class OperateTests(ExecutorCase):
    def test_a_run_in_its_own_process_reports_through_the_connection(self):
        class Connection:
            def __init__(self):
                self.sent, self.closed = [], False

            def send(self, item):
                self.sent.append(item)

            def close(self):
                self.closed = True

        connection = Connection()
        spec = self.spec(py('print("from the process")'))
        original = catalogue.context
        catalogue.context = lambda *args: self.ctx
        try:
            executor.operate(connection, spec, os.getppid())
        finally:
            catalogue.context = original
        kinds = [kind for kind, _ in connection.sent]
        self.assertEqual(kinds[-1], 'outcome')
        self.assertIn('stage', kinds)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.sent[-1][1]['status'], 'D')
        text = ' '.join(line[3] for kind, payload in connection.sent
                        if kind == 'log' for line in payload)
        self.assertIn('from the process', text)


if __name__ == '__main__':
    unittest.main()
