"""Tests of the file transfers (rapo/transfer). Run from the project root:

    python -m unittest tests.test_transfer -v

The package `rapo` itself needs a Linux host and an Oracle database, so the
modules under test are loaded without it: the database is replaced by a
SQLite one with the same `execute` interface.
"""
import contextvars
import datetime as dt
import gzip
import os
import shutil
import sys
import tempfile
import types
import unittest

import sqlalchemy as sa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _package(name, path):
    module = types.ModuleType(name)
    module.__path__ = [path]
    sys.modules[name] = module
    return module


class SqliteDatabase:
    """The execute interface of rapo.database.Database over SQLite."""

    def __init__(self):
        self.engine = sa.create_engine(
            'sqlite://', poolclass=sa.pool.StaticPool,
            connect_args={'check_same_thread': False})

    def execute(self, statement, auto_commit=True, as_table=False,
                as_dict=False, as_scalar=False, **kwargs):
        with self.engine.begin() as connection:
            result = connection.execute(statement)
            if as_table:
                return [dict(row._mapping) for row in result]
            if as_dict:
                row = result.first()
                return dict(row._mapping) if row else row
            if as_scalar:
                return result.scalar()
            return result


class Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _bootstrap():
    if 'rapo.transfer.store' in sys.modules:
        return
    _package('rapo', os.path.join(ROOT, 'rapo'))
    _package('rapo.core', os.path.join(ROOT, 'rapo', 'core'))
    _package('rapo.transfer', os.path.join(ROOT, 'rapo', 'transfer'))
    logger = types.ModuleType('rapo.logger')
    logger.logger = Logger()
    sys.modules['rapo.logger'] = logger
    database = types.ModuleType('rapo.database')
    database.db = SqliteDatabase()
    database.CLIENT = contextvars.ContextVar('rapo_client', default=None)
    sys.modules['rapo.database'] = database
    runner = types.ModuleType('rapo.core.runner')
    runner.get_runner_name = lambda: 'test:8080'
    sys.modules['rapo.core.runner'] = runner


_bootstrap()

from rapo import config as rapo_config  # noqa: E402
from rapo.database import db  # noqa: E402
from rapo.transfer import catalogue, crontab, engine, manager, store  # noqa: E402


def when(text):
    return dt.datetime.strptime(text, '%Y-%m-%d %H:%M:%S')


def _count_log(name):
    return db.execute(sa.select(sa.func.count()).select_from(
        store.log_table).where(store.log_table.c.param_name == name),
        as_scalar=True)


class DateTokenTests(unittest.TestCase):
    def expand(self, text, now):
        return engine.expand_date_tokens(text, when(now))

    def test_today_parts(self):
        self.assertEqual(self.expand('<YYYY><MM><DD>', '2026-05-07 13:45:09'),
                         '20260507')

    def test_single_token_shifts_days(self):
        self.assertEqual(self.expand('<DD-5>', '2026-05-01 00:00:00'), '26')
        self.assertEqual(self.expand('<DD+1>', '2026-12-31 00:00:00'), '01')

    def test_month_end_is_respected(self):
        self.assertEqual(self.expand('<MM-1>', '2026-03-31 00:00:00'), '02')

    def test_block_shifts_the_whole_date(self):
        self.assertEqual(self.expand('<YYYYMMDD-5d>', '2026-01-03 00:00:00'),
                         '20251229')
        self.assertEqual(self.expand('<YYYYMM-1mo>', '2026-01-15 00:00:00'),
                         '202512')
        self.assertEqual(self.expand('<YYYYMMDD HH-2h>',
                                     '2026-05-01 01:00:00'), '20260430 23')

    def test_regex_without_tokens_is_untouched(self):
        mask = r'^DR_P11_\d{8}.*\.(zip|gz)$'
        self.assertEqual(self.expand(mask, '2026-05-01 00:00:00'), mask)

    def test_invalid_blocks_are_rejected(self):
        for bad in ('<YYYYMMDD-5>', '<YYYYMMDD-5m>', '<YYYYMMDD-d>'):
            with self.assertRaises(engine.ConfigError, msg=bad):
                self.expand(bad, '2026-05-01 00:00:00')


class DateFilterTests(unittest.TestCase):
    def test_one_day(self):
        date_filter = engine.build_date_filter('2026-05-01')
        self.assertTrue(date_filter.matches(when('2026-05-01 23:59:59')))
        self.assertFalse(date_filter.matches(when('2026-05-02 00:00:00')))

    def test_range(self):
        date_filter = engine.build_date_filter('2026-05-01&date2026-05-03')
        self.assertTrue(date_filter.matches(when('2026-05-02 10:00:00')))
        self.assertTrue(date_filter.matches(when('2026-05-03 23:00:00')))
        self.assertFalse(date_filter.matches(when('2026-05-04 00:00:00')))

    def test_reversed_range_is_rejected(self):
        with self.assertRaises(engine.ConfigError):
            engine.build_date_filter('2026-05-03&date2026-05-01')


class ParamTests(unittest.TestCase):
    BASE = ('InputDir:/in\nOutputDir:/out\nFileMask:^a\\.txt$\n'
            'LogMode:None\n')

    def test_valid_param(self):
        cfg = engine.validate(self.BASE)
        self.assertEqual(cfg.output_dirs, ['/out'])
        self.assertTrue(cfg.keep_input)

    def test_value_may_contain_colon(self):
        cfg = engine.validate(self.BASE + 'SftpCheck:N\nLogMode:None\n'
                              'ArchiveCheck:Y\nArchiveDir:C:\\arch\n')
        self.assertEqual(cfg.archive_dir, 'C:\\arch')

    def test_missing_mask(self):
        with self.assertRaises(engine.ConfigError):
            engine.validate('InputDir:/in\nOutputDir:/out\n')

    def test_bad_flag_and_gzip_conflict(self):
        with self.assertRaises(engine.ConfigError):
            engine.validate(self.BASE + 'KeepInput:maybe\n')
        with self.assertRaises(engine.ConfigError):
            engine.validate(self.BASE + 'GzipInput:Y\nGzipOutput:Y\n')

    def test_sftp_needs_credentials(self):
        text = self.BASE + 'SftpCheck:Y\nSftpHost:h\nSftpUser:u\n'
        with self.assertRaises(engine.ConfigError):
            engine.validate(text)
        engine.validate(text + 'SftpType:Key\nSftpKey:/k\n')

    def test_line_without_colon(self):
        with self.assertRaises(engine.ConfigError):
            engine.validate('InputDir /in\n')


class CatalogueTests(unittest.TestCase):
    def test_every_parameter_the_engine_reads_is_in_the_catalogue(self):
        import re
        with open(os.path.join(ROOT, 'rapo', 'transfer', 'engine.py'),
                  encoding='utf-8') as file:
            source = file.read()
        read = set(re.findall(r"_(?:text|flag|number)\(\s*params,\s*'(\w+)'",
                              source))
        listed = {item['name'] for item in catalogue.parameters()}
        self.assertEqual(read - listed, set())
        self.assertEqual(listed - read, set())

    def test_defaults_and_choices_are_valid(self):
        for item in catalogue.parameters():
            self.assertIn(item['group'], catalogue.GROUPS)
            if item['kind'] == 'flag':
                self.assertIn(item['default'], ('Y', 'N'))
            if item['kind'] == 'choice':
                self.assertTrue(item['choices'])
                self.assertIn(item['default'], [None] + item['choices'])
            self.assertTrue(item['description'])

    def test_a_param_made_of_the_defaults_and_the_required_is_valid(self):
        values = {item['name']: item['default'] for item in
                  catalogue.parameters() if item['default']}
        values.update(InputDir='/in', OutputDir='/out', FileMask='x',
                      LogDir='/log', LogFile='x.log')
        text = '\n'.join(f'{key}:{value}' for key, value in values.items())
        engine.validate(text)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='rapo-transfer-test-')
        self.input = self.directory('in')
        self.output = self.directory('out')
        self.second = self.directory('out2')
        self.archive = self.directory('archive')
        self.logs = self.directory('logs')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def directory(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        return path

    def put(self, name, content=b'data', folder=None):
        path = os.path.join(folder or self.input, name)
        with open(path, 'wb') as file:
            file.write(content)
        return path

    def param(self, **extra):
        values = {'InputDir': self.input, 'OutputDir': self.output,
                  'FileMask': r'\.txt$', 'LogMode': 'Full',
                  'LogDir': self.logs, 'LogFile': 'job.log'}
        values.update(extra)
        return '\n'.join(f'{key}:{value}' for key, value in values.items())

    def test_copy_keeps_input(self):
        self.put('a.txt')
        self.put('b.csv')
        result = engine.execute('job', self.param())
        self.assertEqual((result.status, result.transferred, result.failed),
                         ('D', 1, 0))
        self.assertEqual(os.listdir(self.output), ['a.txt'])
        self.assertTrue(os.path.exists(os.path.join(self.input, 'a.txt')))
        self.assertTrue(any('END Transit files' in line
                            for line in result.log))
        self.assertTrue(os.path.getsize(os.path.join(self.logs, 'job.log')))

    def test_move_removes_input_and_no_tmp_left(self):
        self.put('a.txt')
        result = engine.execute('job', self.param(KeepInput='N'))
        self.assertEqual(result.status, 'D')
        self.assertFalse(os.path.exists(os.path.join(self.input, 'a.txt')))
        self.assertEqual(os.listdir(self.output), ['a.txt'])

    def test_several_targets_and_archive(self):
        self.put('a.txt', b'xyz')
        result = engine.execute('job', self.param(
            OutputDir=f'{self.output},{self.second}', ArchiveCheck='Y',
            ArchiveDir=self.archive))
        self.assertEqual(result.status, 'D')
        for folder in (self.output, self.second, self.archive):
            with open(os.path.join(folder, 'a.txt'), 'rb') as file:
                self.assertEqual(file.read(), b'xyz')

    def test_gzip_and_gunzip(self):
        self.put('a.txt', b'hello')
        engine.execute('job', self.param(GzipInput='Y'))
        with gzip.open(os.path.join(self.output, 'a.txt.gz')) as file:
            self.assertEqual(file.read(), b'hello')
        self.put('b.txt.gz', gzip.compress(b'unpack'))
        engine.execute('job', self.param(
            GzipOutput='Y', FileMask=r'\.gz$', OutputDir=self.second))
        with open(os.path.join(self.second, 'b.txt'), 'rb') as file:
            self.assertEqual(file.read(), b'unpack')

    def test_control_file_is_required_and_removed(self):
        self.put('a.txt')
        self.put('b.txt')
        self.put('b.ok', b'')
        result = engine.execute('job', self.param(
            CtlFileCheck='Y', CtlFileExtension='ok', KeepInput='N',
            FileMask=r'\.txt$'))
        self.assertEqual(result.status, 'D')
        self.assertEqual(os.listdir(self.output), ['b.txt'])
        self.assertEqual(sorted(os.listdir(self.input)), ['a.txt'])

    def test_date_token_in_mask(self):
        stamp = (dt.datetime.now() - dt.timedelta(days=2)).strftime('%Y%m%d')
        self.put(f'rep_{stamp}.csv')
        self.put('rep_19990101.csv')
        result = engine.execute('job', self.param(
            FileMask=r'^rep_<YYYYMMDD-2d>\.csv$'))
        self.assertEqual(result.transferred, 1)
        self.assertEqual(os.listdir(self.output), [f'rep_{stamp}.csv'])

    def test_file_date_filter(self):
        old = self.put('old.txt')
        self.put('new.txt')
        past = dt.datetime.now() - dt.timedelta(days=10)
        os.utime(old, (past.timestamp(), past.timestamp()))
        today = dt.date.today().strftime('%Y-%m-%d')
        result = engine.execute('job', self.param(
            FileDateCheck='Y', FileCreationDate=today))
        self.assertEqual(os.listdir(self.output), ['new.txt'])
        self.assertEqual(result.transferred, 1)

    def test_sub_directories(self):
        os.makedirs(os.path.join(self.input, 'sub'))
        self.put('deep.txt', folder=os.path.join(self.input, 'sub'))
        engine.execute('job', self.param())
        self.assertEqual(os.listdir(self.output), [])
        engine.execute('job', self.param(SubDirCheck='Y'))
        self.assertEqual(os.listdir(self.output), ['deep.txt'])

    def test_missing_directory_is_an_error(self):
        result = engine.execute('job', self.param(OutputDir='/no/such/dir'))
        self.assertEqual(result.status, 'E')
        self.assertIn('does not exist', result.message)

    def test_same_input_and_output_is_refused(self):
        result = engine.execute('job', self.param(OutputDir=self.input))
        self.assertEqual(result.status, 'E')
        self.assertIn('same as InputDir', result.message)

    def test_bad_param_never_raises(self):
        result = engine.execute('job', 'nonsense')
        self.assertEqual(result.status, 'E')
        self.assertIn('Configuration error', result.message)

    def test_nothing_to_transfer_is_done(self):
        result = engine.execute('job', self.param())
        self.assertEqual((result.status, result.transferred), ('D', 0))


class LogDatabaseTests(unittest.TestCase):
    BASE = 'InputDir:/in\nOutputDir:/out\nFileMask:x\n'

    def test_log_dir_and_file_are_not_needed(self):
        cfg = engine.validate(self.BASE + 'LogMode:Basic\nLogDatabase:Y\n')
        self.assertTrue(cfg.log_database)
        with self.assertRaises(engine.ConfigError):
            engine.validate(self.BASE + 'LogMode:Basic\nLogDatabase:N\n')
        with self.assertRaises(engine.ConfigError):
            engine.validate(self.BASE + 'LogDatabase:maybe\n')

    def run_param(self, mode, database=True, **extra):
        root = tempfile.mkdtemp(prefix='rapo-logdb-')
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for folder in ('in', 'out', 'log'):
            os.makedirs(os.path.join(root, folder))
        with open(os.path.join(root, 'in', 'a.txt'), 'w') as file:
            file.write('x')
        values = {'InputDir': os.path.join(root, 'in'),
                  'OutputDir': os.path.join(root, 'out'),
                  'FileMask': r'\.txt$', 'LogMode': mode}
        if database:
            values['LogDatabase'] = 'Y'
            # Ignored, even when it points nowhere.
            values.update(LogDir=os.path.join(root, 'no-such'), LogFile='x')
        else:
            values.update(LogDir=os.path.join(root, 'log'), LogFile='x.log')
        values.update(extra)
        text = '\n'.join(f'{key}:{value}' for key, value in values.items())
        return engine.execute('job', text, **{}), root

    def test_full_writes_debug_lines_to_the_result_not_to_a_file(self):
        result, root = self.run_param('Full')
        self.assertEqual((result.status, result.transferred), ('D', 1))
        self.assertEqual(result.log, [])
        levels = {item[1] for item in result.db_log}
        self.assertIn('DEBUG', levels)
        self.assertTrue(any('END Transit files' in item[2]
                            for item in result.db_log))
        self.assertEqual(os.listdir(os.path.join(root, 'log')), [])

    def test_basic_leaves_the_debug_lines_out(self):
        result, _ = self.run_param('Basic')
        levels = {item[1] for item in result.db_log}
        self.assertNotIn('DEBUG', levels)
        self.assertIn('INFO', levels)

    def test_none_keeps_no_lines_but_the_errors(self):
        result, _ = self.run_param('None')
        self.assertEqual(result.db_log, [])
        result, _ = self.run_param('None', OutputDir='/no/such/dir')
        self.assertEqual(result.status, 'E')
        self.assertEqual(result.db_log, [])
        self.assertIn('does not exist', result.message)

    def test_without_the_parameter_the_file_log_and_history_stay(self):
        result, root = self.run_param('Full', database=False)
        self.assertIsNone(result.db_log)
        self.assertTrue(result.log)
        self.assertTrue(os.path.getsize(os.path.join(root, 'log', 'x.log')))

    def test_the_newest_lines_are_kept(self):
        collector = engine.Collector(database=True,
                                     database_level=engine.logging.INFO,
                                     database_limit=3)
        log = engine.logging.getLogger('rapo.transfer.limit-test')
        log.setLevel(engine.logging.DEBUG)
        log.propagate = False
        log.addHandler(collector)
        for number in range(5):
            log.info('line %d', number)
        self.assertEqual([item[2].split('[ ')[1] for item in
                          collector.records],
                         ['line 2 ]', 'line 3 ]', 'line 4 ]'])
        self.assertEqual(collector.dropped, 2)


class CronTests(unittest.TestCase):
    def test_every_five_minutes(self):
        cron = crontab.Cron('*/5 * * * *')
        self.assertEqual(cron.next_fire(when('2026-05-01 10:02:30')),
                         when('2026-05-01 10:05:00'))
        self.assertEqual(cron.next_fire(when('2026-05-01 10:55:00')),
                         when('2026-05-01 11:00:00'))

    def test_daily_and_macros(self):
        self.assertEqual(
            crontab.Cron('30 2 * * *').next_fire(when('2026-05-01 03:00:00')),
            when('2026-05-02 02:30:00'))
        self.assertEqual(
            crontab.Cron('@hourly').next_fire(when('2026-05-01 10:00:00')),
            when('2026-05-01 11:00:00'))

    def test_weekday_names_and_ranges(self):
        # 2026-05-01 is a Friday.
        cron = crontab.Cron('0 8 * * mon-fri')
        self.assertEqual(cron.next_fire(when('2026-05-01 09:00:00')),
                         when('2026-05-04 08:00:00'))
        cron = crontab.Cron('0 8 * * 7')
        self.assertEqual(cron.next_fire(when('2026-05-01 09:00:00')),
                         when('2026-05-03 08:00:00'))

    def test_day_of_month_or_weekday(self):
        # Both restricted: either matches, as cron does.
        cron = crontab.Cron('0 0 13 * fri')
        self.assertEqual(cron.next_fire(when('2026-05-01 09:00:00')),
                         when('2026-05-08 00:00:00'))

    def test_months_and_lists(self):
        cron = crontab.Cron('15,45 6 1 jan,jul *')
        self.assertEqual(cron.next_fire(when('2026-02-01 00:00:00')),
                         when('2026-07-01 06:15:00'))

    def test_bad_expressions(self):
        for bad in ('* * * *', '61 * * * *', '*/0 * * * *', 'a b c d e',
                    '5-1 * * * *'):
            with self.assertRaises(crontab.CronError, msg=bad):
                crontab.Cron(bad)

    TEXT = '''
# comment
MAILTO=me@example.com
*/5 * * * * /opt/filetransfer-py/file-transfer.py /opt/p/job-a.param
0 2 * * * /opt/bin/nightly.sh >> /var/log/nightly.log 2>&1
30 6 * * 1 /opt/bin/other.sh
@daily /opt/bin/daily.sh
@reboot /opt/bin/boot.sh
'''
    SCRIPTS = {
        '/opt/bin/nightly.sh': '#!/bin/sh\n'
                               'cd /opt/filetransfer-py\n'
                               './file-transfer.py $P/job-b.param\n',
        '/opt/bin/other.sh': '#!/bin/sh\necho nothing\n',
        '/opt/bin/daily.sh': '#!/bin/sh\n/opt/bin/inner.sh\n',
        '/opt/bin/inner.sh': 'python3 file-transfer.py /opt/p/job-c.param\n',
    }

    def read(self, path):
        return self.SCRIPTS.get(path)

    def test_parse_crontab(self):
        entries = crontab.parse_crontab(self.TEXT)
        self.assertEqual([item[0] for item in entries],
                         ['*/5 * * * *', '0 2 * * *', '30 6 * * 1',
                          '@daily'])

    def test_scan_finds_direct_script_and_nested_calls(self):
        found = crontab.scan('file-transfer.py', self.TEXT,
                             when('2026-05-01 10:00:00'), self.read)
        by_param = {item['param']: item for item in found}
        self.assertEqual(sorted(by_param), ['job-a', 'job-b', 'job-c'])
        self.assertIsNone(by_param['job-a']['script'])
        self.assertEqual(by_param['job-a']['next_fire'],
                         when('2026-05-01 10:05:00'))
        self.assertEqual(by_param['job-b']['script'], '/opt/bin/nightly.sh')
        self.assertEqual(by_param['job-b']['cron'], '0 2 * * *')
        self.assertEqual(by_param['job-c']['script'], '/opt/bin/daily.sh')
        self.assertEqual(by_param['job-c']['next_fire'],
                         when('2026-05-02 00:00:00'))

    def test_script_taking_the_param_as_argument(self):
        scripts = {'/opt/bin/run.sh': 'file-transfer.py "$1"\n'}
        found = crontab.scan('file-transfer.py',
                             '0 1 * * * /opt/bin/run.sh /opt/p/arg.param\n',
                             when('2026-05-01 00:00:00'), scripts.get)
        self.assertEqual([item['param'] for item in found], ['arg'])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='rapo-param-test-')
        section = rapo_config.Configuration('TRANSFER')
        section['param_dir'] = self.folder
        section['crontab_scan'] = False
        rapo_config.config['TRANSFER'] = section
        db.engine.dispose()
        store.metadata.drop_all(db.engine)
        store.metadata.create_all(db.engine)
        manager.manager.invalidate()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    TEXT = 'InputDir:/in\nOutputDir:/out\nFileMask:\\.txt$\nLogMode:None\n'

    def test_save_read_file_and_database(self):
        store.write('a-file', self.TEXT, store.FILE, create=True)
        store.write('a-base', self.TEXT, store.DATABASE, note='Nightly',
                    create=True)
        self.assertTrue(os.path.isfile(os.path.join(self.folder,
                                                    'a-file.param')))
        self.assertEqual(store.read('a-file'), (self.TEXT, store.FILE))
        self.assertEqual(store.read('a-base'), (self.TEXT, store.DATABASE))
        self.assertEqual(store.description('a-base'), 'Nightly')

    def test_invalid_names_and_texts_are_refused(self):
        for name in ('../x', 'a b', '', '.hidden'):
            with self.assertRaises(store.StoreError, msg=name):
                store.write(name, self.TEXT, store.FILE)
        with self.assertRaises(store.StoreError):
            store.write('ok', 'nonsense', store.FILE)
        with self.assertRaises(store.StoreError):
            store.write('ok', self.TEXT, 'tape')

    def test_create_refuses_an_existing_name_in_either_place(self):
        store.write('x', self.TEXT, store.FILE, create=True)
        with self.assertRaises(store.StoreError):
            store.write('x', self.TEXT, store.FILE, create=True)
        with self.assertRaises(store.StoreError):
            store.write('x', self.TEXT, store.DATABASE, create=True)

    def test_move_keeps_text_and_schedule(self):
        store.write('m', self.TEXT, store.DATABASE, create=True)
        store.write_schedule('m', '{"hour": "3", "min": "0"}', True)
        # No LogDir and LogFile: as a file it has no logging, so it asks.
        with self.assertRaises(store.StoreError) as raised:
            store.move('m', store.FILE)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(store.exists('m'), [store.DATABASE])
        store.move('m', store.FILE, confirm=True)
        self.assertEqual(store.exists('m'), [store.FILE])
        self.assertEqual(store.read('m')[1], store.FILE)
        self.assertIn('m', store.read_schedules())
        store.move('m', store.DATABASE)
        self.assertEqual(store.exists('m'), [store.DATABASE])
        self.assertEqual(store.read('m')[0].strip(), self.TEXT.strip())
        with self.assertRaises(store.StoreError):
            store.move('m', store.DATABASE)

    DB_LOG = ('InputDir:/in\nOutputDir:/out\nFileMask:x\nLogMode:Full\n'
              'LogDatabase:Y\n')

    def test_log_database_is_refused_for_a_file(self):
        with self.assertRaises(store.StoreError) as raised:
            store.write('f', self.DB_LOG, store.FILE, create=True)
        self.assertIn('LogDatabase', str(raised.exception))
        store.write('d', self.DB_LOG, store.DATABASE, create=True)
        self.assertTrue(store.uses_database_log(store.read('d')[0]))

    def test_moving_to_a_file_drops_log_database_and_asks_without_log_dir(self):
        store.write('d', self.DB_LOG, store.DATABASE, create=True)
        plan = store.plan_move('d', store.FILE)
        self.assertEqual(len(plan['warnings']), 1)
        self.assertIn('no logging', plan['warnings'][0])
        self.assertNotIn('LogDatabase', plan['text'])
        self.assertIn('LogMode:None', plan['text'])
        with self.assertRaises(store.StoreError):
            store.move('d', store.FILE)
        store.move('d', store.FILE, confirm=True)
        text, source = store.read('d')
        self.assertEqual(source, store.FILE)
        self.assertNotIn('LogDatabase', text)
        self.assertIn('LogMode:None', text)
        engine.validate(text)

    def test_moving_with_log_dir_and_file_needs_no_confirmation(self):
        text = self.DB_LOG + 'LogDir:/log\nLogFile:d.log\n# kept\n'
        store.write('d', text, store.DATABASE, create=True)
        plan = store.plan_move('d', store.FILE)
        self.assertEqual(plan['warnings'], [])
        self.assertEqual(len(plan['notes']), 1)
        store.move('d', store.FILE)
        moved = store.read('d')[0]
        self.assertIn('LogDir:/log', moved)
        self.assertIn('# kept', moved)
        self.assertIn('LogMode:Full', moved)
        self.assertNotIn('LogDatabase', moved)

    def test_a_file_moved_to_the_database_is_unchanged(self):
        text = self.TEXT + '# keep me\n'
        store.write('u', text, store.FILE, create=True)
        self.assertEqual(store.plan_move('u', store.DATABASE)['warnings'], [])
        store.move('u', store.DATABASE)
        self.assertEqual(store.read('u')[0].strip(), text.strip())

    def test_parameter_helpers_keep_the_comments(self):
        text = '# c\nA:1\n\nB:2\n'
        self.assertEqual(store.drop_parameter(text, 'A'), '# c\n\nB:2\n')
        self.assertEqual(store.set_parameter(text, 'B', '9'),
                         '# c\nA:1\n\nB:9\n')
        self.assertEqual(store.set_parameter(text, 'C', '3'),
                         '# c\nA:1\n\nB:2\nC:3\n')

    def test_log_lines_are_trimmed_to_the_newest_per_param(self):
        run_one = store.add_run('p', 'manual')
        store.add_log_lines(run_one, 'p', [(1.0, 'INFO', f'one {n}')
                                           for n in range(4)], 6)
        run_two = store.add_run('p', 'manual')
        other = store.add_run('q', 'manual')
        store.add_log_lines(other, 'q', [(1.0, 'INFO', 'other')], 6)
        store.update_run(run_one, log_lines=4)
        store.update_run(run_two, log_lines=4)
        store.add_log_lines(run_two, 'p', [(2.0, 'ERROR', f'two {n}')
                                           for n in range(4)], 6)
        first = store.read_run(run_one)
        second = store.read_run(run_two)
        self.assertEqual(first['run_log'].splitlines(), ['one 2', 'one 3'])
        self.assertTrue(first['log_trimmed'])
        self.assertEqual(len(second['run_log'].splitlines()), 4)
        self.assertFalse(second['log_trimmed'])
        # Another param is not touched.
        other_run = store.add_run('q', 'manual')
        store.update_run(other_run, log_lines=0)
        self.assertEqual(store.read_run(other)['run_log'] or '', '')
        self.assertEqual(_count_log('q'), 1)

    def test_a_run_without_database_log_keeps_run_log(self):
        run_id = store.add_run('p', 'manual')
        store.update_run(run_id, run_log='a\nb')
        row = store.read_run(run_id)
        self.assertEqual(row['run_log'], 'a\nb')
        self.assertFalse(row['log_trimmed'])

    def test_old_runs_take_their_log_lines_with_them(self):
        run_id = store.add_run('p', 'manual')
        store.update_run(run_id, status='D', queued_time=when(
            '2000-01-01 00:00:00'))
        store.add_log_lines(run_id, 'p', [(1.0, 'INFO', 'old')], 10)
        self.assertEqual(_count_log('p'), 1)
        store.clean_runs(30)
        self.assertEqual(_count_log('p'), 0)

    def test_param_in_both_places_is_refused(self):
        store.write('dup', self.TEXT, store.DATABASE, create=True)
        with open(os.path.join(self.folder, 'dup.param'), 'w') as file:
            file.write(self.TEXT)
        with self.assertRaises(store.StoreError) as raised:
            store.read('dup')
        self.assertEqual(raised.exception.status, 409)

    def test_delete_removes_schedule_too(self):
        store.write('d', self.TEXT, store.FILE, create=True)
        store.write_schedule('d', '{"min": "/5"}', True)
        store.delete('d')
        self.assertEqual(store.exists('d'), [])
        self.assertEqual(store.read_schedules(), {})

    def test_runs(self):
        run_id = store.add_run('r', 'manual', requested_by='me')
        store.update_run(run_id, status=store.DONE, transferred=2, failed=0,
                         run_log='line 1\nline 2')
        runs = store.read_runs('r')
        self.assertEqual(runs[0]['status'], 'D')
        self.assertNotIn('run_log', runs[0])
        self.assertEqual(store.read_run(run_id)['run_log'], 'line 1\nline 2')
        self.assertEqual(store.read_last_runs()['r']['run_id'], run_id)
        self.assertEqual(store.clean_runs(30), 0)


class ManagerTests(unittest.TestCase):
    TEXT = StoreTests.TEXT

    def setUp(self):
        StoreTests.setUp(self)
        self.manager = manager.TransferManager()
        self.manager.active = True
        # The crontab is read on Linux only; the tests feed it themselves.
        self.original = manager.crontab_supported
        manager.crontab_supported = lambda: True

    def tearDown(self):
        manager.crontab_supported = self.original
        StoreTests.tearDown(self)

    def test_crontab_is_ignored_where_it_is_not_supported(self):
        manager.crontab_supported = lambda: False
        rapo_config.config['TRANSFER']['crontab_scan'] = True
        self.manager.entries = [{'param': 'x'}]
        self.manager.entries_read = float('inf')
        self.assertEqual(self.manager.read_crontab(), [])
        self.assertFalse(self.manager.table()['crontab_supported'])
        self.assertEqual(crontab.scan('file-transfer.py') if sys.platform
                         != 'linux' else [], [])

    def test_table_shows_source_rapo_schedule_and_crontab(self):
        store.write('in-file', self.TEXT, store.FILE, create=True)
        store.write('in-base', self.TEXT, store.DATABASE, create=True)
        store.write_schedule('in-base', manager.schedule_json(
            {'hour': '4', 'min': '30', 'sec': '0'}), True)
        store.write_schedule('in-file', manager.schedule_json(
            {'min': '/10', 'sec': '0'}), False)
        self.manager.entries = [{
            'param': 'in-file', 'param_path': '/p/in-file.param',
            'script': '/opt/run.sh', 'cron': '0 1 * * *',
            'command': '/opt/run.sh', 'next_fire': when('2030-01-01 01:00:00'),
            'user': 'root'}, {
            'param': 'outside', 'param_path': '/x/outside.param',
            'script': None, 'cron': '*/5 * * * *', 'command': 'c',
            'next_fire': None, 'user': 'root'}]
        self.manager.entries_read = float('inf')
        setting = rapo_config.config['TRANSFER']
        setting['crontab_scan'] = True
        table = self.manager.table()
        rows = {row['param_name']: row for row in table['params']}
        self.assertEqual(rows['in-file']['source'], 'file')
        self.assertEqual(rows['in-base']['source'], 'db')
        self.assertEqual(rows['outside']['source'], 'external')
        self.assertEqual(rows['in-base']['scheduling'], 'rapo')
        self.assertEqual(rows['in-base']['rapo']['next_fire'].minute, 30)
        # Disabled in rapo, started by the crontab script.
        self.assertEqual(rows['in-file']['scheduling'], 'crontab')
        self.assertEqual(rows['in-file']['crontab'][0]['script'],
                         '/opt/run.sh')
        self.assertEqual(rows['outside']['scheduling'], 'crontab')
        self.assertTrue(table['database_available'])

    def test_both_schedulers_are_flagged(self):
        store.write('twice', self.TEXT, store.FILE, create=True)
        store.write_schedule('twice', manager.schedule_json(
            {'min': '/5', 'sec': '0'}), True)
        rapo_config.config['TRANSFER']['crontab_scan'] = True
        self.manager.entries = [{
            'param': 'twice', 'param_path': 'twice.param', 'script': None,
            'cron': '*/5 * * * *', 'command': 'c', 'next_fire': None,
            'user': 'u'}]
        self.manager.entries_read = float('inf')
        row = self.manager.table()['params'][0]
        self.assertEqual(row['scheduling'], 'both')

    def test_fire_submits_due_schedules_only(self):
        store.write('due', self.TEXT, store.FILE, create=True)
        store.write('later', self.TEXT, store.FILE, create=True)
        store.write('off', self.TEXT, store.FILE, create=True)
        store.write_schedule('due', manager.schedule_json(
            {'hour': '3', 'min': '0', 'sec': '0'}), True)
        store.write_schedule('later', manager.schedule_json(
            {'hour': '9', 'min': '0', 'sec': '0'}), True)
        store.write_schedule('off', manager.schedule_json(
            {'hour': '3', 'min': '0', 'sec': '0'}), False)
        self.manager.fire(when('2026-05-01 02:59:58'),
                          when('2026-05-01 03:00:01'))
        self.assertEqual([job.name for job in self.manager.pending], ['due'])
        run = store.read_runs('due')[0]
        self.assertEqual(run['trigger_type'], 'schedule')
        self.assertEqual(run['scheduled_time'], when('2026-05-01 03:00:00'))

    def test_late_fire_is_recorded_not_run(self):
        store.write('late', self.TEXT, store.FILE, create=True)
        store.write_schedule('late', manager.schedule_json(
            {'hour': '3', 'min': '0', 'sec': '0'}), True)
        self.manager.fire(when('2026-05-01 02:00:00'),
                          when('2026-05-01 03:30:00'))
        self.assertEqual(len(self.manager.pending), 0)
        run = store.read_runs('late')[0]
        self.assertEqual(run['status'], 'C')
        self.assertIn('late', run['message'])

    def test_one_run_of_a_param_at_a_time(self):
        store.write('once', self.TEXT, store.FILE, create=True)
        self.manager.submit('once', manager.MANUAL)
        with self.assertRaises(store.StoreError) as raised:
            self.manager.submit('once', manager.MANUAL)
        self.assertEqual(raised.exception.status, 409)

    def test_cancel_a_queued_run(self):
        store.write('c', self.TEXT, store.FILE, create=True)
        run_id = self.manager.submit('c', manager.MANUAL)
        self.assertTrue(self.manager.cancel(run_id))
        self.assertEqual(store.read_run(run_id)['status'], 'C')
        self.assertFalse(self.manager.cancel(run_id))

    def test_a_file_with_log_database_is_not_run(self):
        with open(os.path.join(self.folder, 'hand.param'), 'w') as file:
            file.write(self.TEXT + 'LogDatabase:Y\n')
        with self.assertRaises(store.StoreError) as raised:
            self.manager.submit('hand', manager.MANUAL)
        self.assertIn('LogDatabase', str(raised.exception))
        self.assertEqual(store.read_runs('hand'), [])

    def test_finish_writes_the_database_log_and_not_run_log(self):
        store.write('dbl', self.TEXT + 'LogDatabase:Y\n', store.DATABASE,
                    create=True)
        run_id = self.manager.submit('dbl', manager.MANUAL)
        job = self.manager.pending.popleft()
        self.manager._finish(job, 'D', None, {
            'transferred': 1, 'failed': 0, 'log': [],
            'db_log': [(1.0, 'INFO', 'a line'), (2.0, 'DEBUG', 'b line')]})
        row = store.read_run(run_id)
        self.assertEqual(row['status'], 'D')
        self.assertEqual(row['log_lines'], 2)
        self.assertEqual(row['run_log'], 'a line\nb line')

    def test_invalid_param_is_not_queued(self):
        with open(os.path.join(self.folder, 'bad.param'), 'w') as file:
            file.write('nonsense')
        with self.assertRaises(store.StoreError):
            self.manager.submit('bad', manager.MANUAL)
        self.assertEqual(store.read_runs('bad'), [])

    def test_operate_sends_the_result(self):
        import multiprocessing
        source = tempfile.mkdtemp(prefix='rapo-in-')
        target = tempfile.mkdtemp(prefix='rapo-out-')
        try:
            with open(os.path.join(source, 'a.txt'), 'w') as file:
                file.write('x')
            text = (f'InputDir:{source}\nOutputDir:{target}\n'
                    'FileMask:\\.txt$\nLogMode:None\n')
            receiver, sender = multiprocessing.Pipe(duplex=False)
            manager.operate(sender, 'op', text, os.getppid())
            outcome = receiver.recv()
            self.assertEqual((outcome['status'], outcome['transferred']),
                             ('D', 1))
            self.assertEqual(os.listdir(target), ['a.txt'])
        finally:
            shutil.rmtree(source, ignore_errors=True)
            shutil.rmtree(target, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
