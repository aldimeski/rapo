"""Tests of what is started outside rapo (rapo/jobs/external.py). Run from the
project root:

    python -m unittest tests.test_external -v
"""
import datetime as dt
import json
import os
import unittest

from tests import test_transfer  # noqa: F401  (stubs rapo and the database)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
test_transfer._package('rapo.jobs', os.path.join(ROOT, 'rapo', 'jobs'))

from rapo.jobs import external  # noqa: E402

NOW = dt.datetime(2026, 11, 7, 12, 0, 0)
CRONTAB = '''# a comment
MAILTO=ops@example.com
*/15 * * * * /opt/rapo/load.sh --full
30 6 * * 1-5 /usr/bin/python3 /opt/jobs/report.py
@reboot /opt/start.sh
@daily /opt/rapo/transfer.sh
@nonsense something
0 3 * * *
'''
FILES = {
    '/opt/rapo/load.sh': 'cd /opt\n/usr/bin/python3 /opt/jobs/load_files.py\n',
    '/opt/rapo/transfer.sh': 'python file-transfer.py /params/daily-report.param\n',
}


def read(path):
    return FILES.get(path)


class CrontabTests(unittest.TestCase):
    def entries(self):
        return external.read_crontab_entries(CRONTAB, NOW)

    def test_every_entry_is_listed_whatever_it_runs(self):
        found = self.entries()
        self.assertEqual([(item['schedule'], item['command']) for item in found],
                         [('*/15 * * * *', '/opt/rapo/load.sh --full'),
                          ('30 6 * * 1-5',
                           '/usr/bin/python3 /opt/jobs/report.py'),
                          ('At reboot', '/opt/start.sh'),
                          ('@daily', '/opt/rapo/transfer.sh')])
        self.assertTrue(all(item['source'] == 'crontab' for item in found))

    def test_the_next_start_is_computed_and_a_reboot_has_none(self):
        found = self.entries()
        self.assertEqual(found[0]['next_fire'], dt.datetime(2026, 11, 7, 12, 15))
        self.assertEqual(found[1]['next_fire'], dt.datetime(2026, 11, 9, 6, 30))
        self.assertIsNone(found[2]['next_fire'])
        self.assertEqual(found[3]['next_fire'], dt.datetime(2026, 11, 8, 0, 0))

    def test_no_crontab_is_an_empty_list(self):
        self.assertEqual(external.read_crontab_entries('', NOW), [])


TASKS = json.dumps([
    {'Name': 'Nightly', 'Path': '\\Rapo\\', 'State': 'Ready', 'User': 'aleks',
     'Actions': {'Execute': 'C:\\Program Files\\Python\\python.exe',
                 'Arguments': 'D:\\jobs\\load.py --full',
                 'Directory': 'D:\\jobs'},
     'Triggers': [{'Type': 'MSFT_TaskDailyTrigger',
                   'Start': '2026-11-01T03:00:00', 'Interval': '',
                   'Enabled': True, 'DaysInterval': 1}],
     'Next': '2026-11-08T03:00:00', 'Last': '2026-11-07T03:00:00',
     'Result': 0},
    {'Name': 'Poll', 'Path': '\\', 'State': 'Disabled', 'User': 'aleks',
     'Actions': [{'Execute': 'cmd.exe', 'Arguments': '/c echo', 'Directory': ''}],
     'Triggers': [{'Type': 'MSFT_TaskWeeklyTrigger',
                   'Start': '2026-11-01T08:30:00', 'Interval': 'PT1H30M',
                   'Enabled': True, 'WeeksInterval': 1, 'DaysOfWeek': 2 | 8}],
     'Next': None, 'Last': None, 'Result': 267011},
    {'Name': 'Boot', 'Path': '\\', 'State': 'Ready', 'User': 'aleks',
     'Actions': [], 'Triggers': [{'Type': 'MSFT_TaskBootTrigger',
                                  'Start': '', 'Enabled': False}],
     'Next': None, 'Last': None, 'Result': None},
])


class TaskSchedulerTests(unittest.TestCase):
    def test_the_json_of_the_scheduler_is_read(self):
        found = external.parse_tasks(TASKS)
        self.assertEqual([item['name'] for item in found],
                         ['\\Rapo\\Nightly', '\\Poll', '\\Boot'])
        first = found[0]
        self.assertEqual(first['source'], 'task_scheduler')
        self.assertEqual(first['command'], '"C:\\Program Files\\Python\\'
                                           'python.exe" D:\\jobs\\load.py --full')
        self.assertEqual(first['schedule'], 'Daily at 03:00')
        self.assertEqual(first['next_fire'], dt.datetime(2026, 11, 8, 3, 0))
        self.assertEqual((first['last_result'], first['directory'],
                          first['state']), (0, 'D:\\jobs', 'Ready'))

    def test_triggers_are_told_in_words(self):
        found = external.parse_tasks(TASKS)
        self.assertEqual(found[1]['schedule'],
                         'Weekly on Mon, Wed at 08:30, repeating every 1 h '
                         '30 min')
        self.assertEqual(found[2]['schedule'], 'At startup (disabled)')
        self.assertEqual(found[2]['command'], '')
        self.assertEqual(found[1]['last_result'], 267011)

    def test_one_task_is_not_a_list_in_powershell_json(self):
        one = json.dumps(json.loads(TASKS)[0])
        self.assertEqual(len(external.parse_tasks(one)), 1)
        self.assertEqual(external.parse_tasks(''), [])

    def test_a_reply_that_is_not_json_is_an_error(self):
        with self.assertRaises(external.ExternalError):
            external.parse_tasks('Access denied')


class LinkTests(unittest.TestCase):
    JOBS = [
        {'job_name': 'load-files', 'definition': {'main': {
            'type': 'PYTHON', 'file': '/opt/jobs/load_files.py'}}},
        {'job_name': 'report', 'definition': {'main': {
            'type': 'PYTHON', 'file': '/opt/jobs/report.py'}}},
        {'job_name': 'daily-transfer', 'definition': {'main': {
            'type': 'TRANSFER', 'param': 'daily-report'}}},
        {'job_name': 'tool', 'definition': {'main': {
            'type': 'EXECUTABLE', 'path': '/usr/local/bin/tool'}}},
        {'job_name': 'sql-job', 'definition': {'main': {
            'type': 'SQL', 'text': 'update t set a = 1'}}},
        {'job_name': 'typed', 'definition': {'main': {
            'type': 'PYTHON', 'code': 'print(1)'}}},
    ]

    def linked(self, entries):
        for entry in entries:
            entry['paths'], entry['params'] = external.references(
                entry['command'], read)
        return {item['command']: item['jobs']
                for item in external.link(entries, self.JOBS)}

    def test_a_command_that_runs_the_script_of_a_job(self):
        got = self.linked(external.read_crontab_entries(CRONTAB, NOW))
        self.assertEqual(got['/usr/bin/python3 /opt/jobs/report.py'],
                         ['report'])

    def test_a_script_that_the_command_runs_is_searched(self):
        got = self.linked(external.read_crontab_entries(CRONTAB, NOW))
        self.assertEqual(got['/opt/rapo/load.sh --full'], ['load-files'])

    def test_the_param_of_a_transfer_job(self):
        got = self.linked(external.read_crontab_entries(CRONTAB, NOW))
        self.assertEqual(got['/opt/rapo/transfer.sh'], ['daily-transfer'])
        direct = [{'source': 'crontab', 'command':
                   'python file-transfer.py /p/daily-report.param'}]
        self.assertEqual(self.linked(direct)[direct[0]['command']],
                         ['daily-transfer'])

    def test_an_entry_that_runs_nothing_of_rapo_has_no_job(self):
        got = self.linked(external.read_crontab_entries(CRONTAB, NOW))
        self.assertEqual(got['/opt/start.sh'], [])

    def test_windows_paths_match_with_any_slash_and_case(self):
        jobs = [{'job_name': 'win', 'definition': {'main': {
            'type': 'BATCH', 'file': 'D:/Jobs/Load.bat'}}}]
        entry = [{'source': 'task_scheduler',
                  'command': '"cmd.exe" /c "d:\\jobs\\load.bat" now'}]
        entry[0]['paths'], entry[0]['params'] = external.references(
            entry[0]['command'], read)
        found = external.link(entry, jobs)
        self.assertEqual(found[0]['jobs'], ['win'] if os.name == 'nt' else [])

    def test_the_jobs_of_the_lists(self):
        entries = external.read_crontab_entries(CRONTAB, NOW)
        for entry in entries:
            entry['paths'], entry['params'] = external.references(
                entry['command'], read)
        found = external.of_jobs(entries, self.JOBS)
        self.assertEqual(sorted(found), ['daily-transfer', 'load-files',
                                         'report'])
        self.assertEqual(found['report'], [{
            'source': 'crontab', 'schedule': '30 6 * * 1-5', 'name': None}])

    def test_sql_and_typed_code_have_no_marker(self):
        self.assertEqual(external.job_markers(self.JOBS[4]['definition']),
                         (set(), set()))
        self.assertEqual(external.job_markers(self.JOBS[5]['definition']),
                         (set(), set()))


class ScanTests(unittest.TestCase):
    def test_a_scan_is_kept_until_it_is_asked_again(self):
        first = external.scan(lambda: [{'source': 'crontab', 'command': 'a',
                                        'schedule': 'x'}])
        self.assertEqual(len(first['entries']), 1)
        self.assertIsNone(first['error'])
        again = external.kept()
        self.assertEqual(again['entries'][0]['command'], 'a')
        self.assertEqual(again['time'], first['time'])

    def test_a_failure_is_kept_as_an_error_with_no_entries(self):
        def broken():
            raise external.ExternalError('PowerShell was not found')
        found = external.scan(broken)
        self.assertEqual((found['entries'], found['error']),
                         ([], 'PowerShell was not found'))


if __name__ == '__main__':
    unittest.main()
