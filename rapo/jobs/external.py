"""Lists what is started outside rapo: the crontab (Linux) and the Windows
Task Scheduler, of the user the server runs as. It only reads.

Every entry is listed, whatever it runs. It is linked to a job of rapo when it
starts what the main task of the job runs: the script, the program, or the
transfer param. The command and the scripts it runs are searched (the scripts
two levels deep, as the crontab scan of the transfers does).
"""

import datetime as dt
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import threading

from ..logger import logger
from ..transfer import crontab

SCRIPT_LIMIT = crontab.SCRIPT_LIMIT
DEPTH = crontab.DEPTH
TIMEOUT = 60
CRONTAB, TASK_SCHEDULER = 'crontab', 'task_scheduler'
WORD = re.compile(r'''"([^"]+)"|'([^']+)'|([^\s'";|&()<>=]+)''')
TRIGGERS = {
    'MSFT_TaskTrigger': 'Trigger', 'MSFT_TaskTimeTrigger': 'Once', 'MSFT_TaskDailyTrigger': 'Daily',
    'MSFT_TaskWeeklyTrigger': 'Weekly', 'MSFT_TaskMonthlyTrigger': 'Monthly',
    'MSFT_TaskMonthlyDOWTrigger': 'Monthly (by weekday)',
    'MSFT_TaskBootTrigger': 'At startup', 'MSFT_TaskLogonTrigger': 'At log on',
    'MSFT_TaskIdleTrigger': 'When idle', 'MSFT_TaskEventTrigger': 'On an event',
    'MSFT_TaskRegistrationTrigger': 'When registered',
    'MSFT_TaskSessionStateChangeTrigger': 'On a session change',
}
WEEKDAYS = ((1, 'Sun'), (2, 'Mon'), (4, 'Tue'), (8, 'Wed'), (16, 'Thu'),
            (32, 'Fri'), (64, 'Sat'))

POWERSHELL = r'''
$ErrorActionPreference = 'Stop'
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$short = $me.Split('\')[-1]
$list = New-Object System.Collections.ArrayList
foreach ($task in Get-ScheduledTask) {
  $user = [string]$task.Principal.UserId
  if (-not $user) { continue }
  if ($user -ne $me -and $user -ne $short) { continue }
  $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction SilentlyContinue
  $actions = @(foreach ($a in $task.Actions) {
    [pscustomobject]@{ Execute = [string]$a.Execute; Arguments = [string]$a.Arguments; Directory = [string]$a.WorkingDirectory }
  })
  $triggers = @(foreach ($t in $task.Triggers) {
    [pscustomobject]@{
      Type = [string]$t.CimClass.CimClassName
      Start = [string]$t.StartBoundary
      Interval = if ($t.Repetition) { [string]$t.Repetition.Interval } else { '' }
      Enabled = [bool]$t.Enabled
      DaysInterval = $t.DaysInterval
      WeeksInterval = $t.WeeksInterval
      DaysOfWeek = $t.DaysOfWeek
    }
  })
  $next = $null; $last = $null; $result = $null
  if ($info) {
    if ($info.NextRunTime) { $next = $info.NextRunTime.ToString('s') }
    if ($info.LastRunTime -and $info.LastRunTime.Year -gt 1999) { $last = $info.LastRunTime.ToString('s') }
    $result = $info.LastTaskResult
  }
  [void]$list.Add([pscustomobject]@{
    Name = $task.TaskName; Path = $task.TaskPath; State = [string]$task.State
    User = $user; Actions = $actions; Triggers = $triggers
    Next = $next; Last = $last; Result = $result
  })
}
ConvertTo-Json -InputObject @($list) -Depth 6 -Compress
'''


class ExternalError(Exception):
    """The scheduler of the system could not be read."""


# What is referenced by a command: the paths and the transfer params in it and
# in the scripts it runs.

def _norm(path):
    return os.path.normcase(os.path.normpath(path.replace('\\', '/')))\
        .replace('\\', '/')


def _words(text):
    for match in WORD.finditer(text):
        yield next(group for group in match.groups() if group)


def references(command, read=crontab._read_script, depth=DEPTH):
    """Get (paths, params): the normalized paths a command mentions, and the
    names of the transfer params, in the command and in the scripts it runs
    (files it names, read up to `depth` levels deep)."""
    paths, params, seen = set(), set(), set()

    def visit(text, level):
        for word in _words(text):
            norm = _norm(word)
            paths.add(norm)
            if word.endswith('.param'):
                params.add(crontab._stem(word))
            if level > 0 and norm not in seen:
                seen.add(norm)
                body = read(word)
                if body is not None:
                    for found in crontab.PARAM_PATTERN.findall(body):
                        params.add(crontab._stem(found))
                    visit(body, level - 1)
    visit(command, depth)
    return paths, params


def job_markers(definition):
    """Get what the main task of a job runs, to be found in a command:
    (paths, params). SQL, and a script typed in the editor, have none."""
    task = (definition or {}).get('main') or {}
    kind = task.get('type')
    if kind == 'TRANSFER' and task.get('param'):
        return set(), {str(task['param'])}
    path = task.get('file') if kind in ('PYTHON', 'PERL', 'SHELL', 'BATCH',
                                        'POWERSHELL') else None
    if kind == 'EXECUTABLE':
        path = task.get('path')
    if kind == 'COMMAND':
        # Only a program given by its path can be told from the rest.
        first = next(_words(task.get('command') or ''), None)
        path = first if first and ('/' in first or '\\' in first) else None
    return ({_norm(path)} if path else set()), set()


# The crontab

def read_crontab_entries(text=None, after=None):
    """Get the entries of the crontab of the user the server runs as, each as
    a dict of the common shape; [] when there is none."""
    if text is None:
        if not sys.platform.startswith('linux'):
            return []
        text = crontab.read_crontab()
        if text is None:
            raise ExternalError('The crontab could not be read.')
    after = after or dt.datetime.now()
    try:
        user = getpass.getuser()
    except Exception:
        user = None
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or crontab.ASSIGNMENT.match(line):
            continue
        if line.startswith('@'):
            head, _, command = line.partition(' ')
            head = head.lower()
            if head != '@reboot' and head not in crontab.MACROS:
                continue
            expression, command = head, command.strip()
        else:
            parts = line.split(None, 5)
            if len(parts) != 6:
                continue
            expression, command = ' '.join(parts[:5]), parts[5].strip()
        next_fire = None
        if expression != '@reboot':
            try:
                next_fire = crontab.Cron(expression).next_fire(after)
            except crontab.CronError:
                next_fire = None
        entries.append({
            'source': CRONTAB, 'name': None, 'state': None,
            'schedule': 'At reboot' if expression == '@reboot' else expression,
            'command': command, 'directory': None, 'user': user,
            'next_fire': next_fire, 'last_run': None, 'last_result': None})
    return entries


# The Windows Task Scheduler

def _duration(text):
    """'PT1H30M' as '1 h 30 min'."""
    match = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?',
                         text or '')
    if not match or not any(match.groups()):
        return text or ''
    days, hours, minutes, seconds = match.groups()
    parts = [f'{value} {unit}' for value, unit in (
        (days, 'd'), (hours, 'h'), (minutes, 'min'), (seconds, 's')) if value]
    return ' '.join(parts)


def describe_trigger(trigger):
    """Say a trigger of the Task Scheduler in words."""
    text = TRIGGERS.get(trigger.get('Type'), trigger.get('Type') or 'Trigger')
    start = str(trigger.get('Start') or '').replace('T', ' ')[:16]
    if trigger.get('Type') == 'MSFT_TaskDailyTrigger' and \
            trigger.get('DaysInterval'):
        text = 'Daily' if trigger['DaysInterval'] == 1 else \
            f'Every {trigger["DaysInterval"]} days'
    if trigger.get('Type') == 'MSFT_TaskWeeklyTrigger':
        mask = trigger.get('DaysOfWeek') or 0
        days = [name for bit, name in WEEKDAYS if mask & bit]
        text = 'Weekly' + (f' on {", ".join(days)}' if days else '')
    if start:
        text += f' from {start}' if trigger.get('Type') == 'MSFT_TaskTimeTrigger' \
            else f' at {start[11:]}' if len(start) > 11 else ''
    if trigger.get('Interval'):
        text += f', repeating every {_duration(trigger["Interval"])}'
    if trigger.get('Enabled') is False:
        text += ' (disabled)'
    return text


def _moment(text):
    try:
        return dt.datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def parse_tasks(text):
    """Get the entries of the JSON the PowerShell script printed."""
    try:
        data = json.loads(text) if text.strip() else []
    except ValueError as error:
        raise ExternalError(f'The Task Scheduler answered with something '
                            f'that is not understood: {error}') from error
    if isinstance(data, dict):
        data = [data]
    entries = []
    for task in data:
        actions = task.get('Actions') or []
        actions = [actions] if isinstance(actions, dict) else actions
        triggers = task.get('Triggers') or []
        triggers = [triggers] if isinstance(triggers, dict) else triggers
        commands = [' '.join(part for part in (
            f'"{item["Execute"]}"' if ' ' in (item.get('Execute') or '')
            else item.get('Execute'), item.get('Arguments')) if part)
            for item in actions]
        directory = next((item['Directory'] for item in actions
                          if item.get('Directory')), None)
        result = task.get('Result')
        entries.append({
            'source': TASK_SCHEDULER,
            'name': (task.get('Path') or '\\') + (task.get('Name') or ''),
            'state': task.get('State'),
            'schedule': '; '.join(describe_trigger(item) for item in triggers)
            or 'No trigger',
            'command': '\n'.join(commands), 'directory': directory,
            'user': task.get('User'),
            'next_fire': _moment(task.get('Next')),
            'last_run': _moment(task.get('Last')),
            'last_result': result if isinstance(result, int) else None})
    return entries


def read_task_scheduler():
    """Get the tasks of the Windows Task Scheduler that run as the user the
    server runs as."""
    shell = shutil.which('powershell') or shutil.which('pwsh')
    if not shell:
        raise ExternalError('PowerShell was not found: the Task Scheduler '
                            'can not be read.')
    try:
        done = subprocess.run(
            [shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
             'Bypass', '-Command', POWERSHELL],
            capture_output=True, text=True, timeout=TIMEOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError) as error:
        raise ExternalError(f'The Task Scheduler could not be read: '
                            f'{error}') from error
    if done.returncode != 0:
        why = (done.stderr or done.stdout or '').strip().splitlines()
        raise ExternalError('The Task Scheduler could not be read: '
                            + (why[0] if why else f'exit {done.returncode}'))
    return parse_tasks(done.stdout)


# The scan, kept until it is asked for again

_lock = threading.Lock()
_scan = {'entries': None, 'time': None, 'error': None, 'running': False}


def scan(read=None):
    """Read the scheduler of this system again, and keep what it found.
    Returns the kept scan."""
    if read is None:
        read = read_task_scheduler if os.name == 'nt' else read_crontab_entries
    try:
        entries, error = read(), None
    except ExternalError as failure:
        entries, error = [], str(failure)
    except Exception as failure:
        logger.error()
        entries, error = [], f'{type(failure).__name__}: {failure}'
    for entry in entries:
        entry['paths'], entry['params'] = references(
            entry['command'] or '')
    with _lock:
        _scan.update(entries=entries, time=dt.datetime.now().replace(
            microsecond=0), error=error, running=False)
    return kept()


def kept():
    """Get the scan as it was kept: {entries, time, error}; entries is None
    when nothing was read yet."""
    with _lock:
        return {key: _scan[key] for key in ('entries', 'time', 'error')}


def scan_in_background():
    """Read the scheduler in a thread when nothing was read yet, so the lists
    know soon what is started outside."""
    with _lock:
        if _scan['entries'] is not None or _scan['running']:
            return
        _scan['running'] = True
    threading.Thread(target=scan, daemon=True,
                     name='rapo-external-scan').start()


def platform():
    """Get what this server can be asked: 'crontab' (Linux), 'task_scheduler'
    (Windows) or None."""
    if os.name == 'nt':
        return TASK_SCHEDULER
    return CRONTAB if sys.platform.startswith('linux') else None


# Linking to the jobs

def link(entries, jobs):
    """Get the entries as the browser gets them, each with `jobs`: the names
    of the jobs of rapo whose main task it starts. `jobs` is a list of
    {'job_name', 'definition' (a dict)}."""
    markers = [(job['job_name'], *job_markers(job.get('definition')))
               for job in jobs]
    result = []
    for entry in entries or []:
        paths, params = entry.get('paths') or set(), entry.get('params') or set()
        names = [name for name, job_paths, job_params in markers
                 if (job_paths & paths) or (job_params & params)]
        shown = {key: value for key, value in entry.items()
                 if key not in ('paths', 'params')}
        shown['jobs'] = names
        result.append(shown)
    return result


def of_jobs(entries, jobs):
    """Get {job name: [what starts it outside rapo]}, for the job lists."""
    found = {}
    for entry in link(entries, jobs):
        for name in entry['jobs']:
            found.setdefault(name, []).append({
                'source': entry['source'], 'schedule': entry['schedule'],
                'name': entry['name']})
    return found
