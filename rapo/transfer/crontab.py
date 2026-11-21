"""Contains the detection of the transfers started from the crontab.

On Linux the crontab of the user the server runs as is read, and every
command in it is checked: a command that runs the transfer script with a
`.param` file starts that param itself, and a script it runs is opened (and
the scripts that one runs, one level deeper) to see whether it starts the
transfer script. The next start of such an entry is computed from its cron
expression.
"""

import datetime as dt
import getpass
import os
import re
import shlex
import subprocess
import sys

from ..logger import logger

MACROS = {
    '@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *',
    '@monthly': '0 0 1 * *', '@weekly': '0 0 * * 0',
    '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@hourly': '0 * * * *',
}
MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep',
          'oct', 'nov', 'dec']
WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']
# Longest horizon searched for the next start (29 February on a Monday).
HORIZON_DAYS = 366*8
# Scripts are read up to this size, and followed this many levels deep.
SCRIPT_LIMIT = 256*1024
DEPTH = 2
PARAM_PATTERN = re.compile(r'''[^\s'";|&()<>]+\.param\b''')
ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\s*=')


class CronError(ValueError):
    """A cron expression that can not be understood."""


def _number(text, names, low):
    text = text.lower()
    if text in names:
        return names.index(text) + low
    if not text.isdigit():
        raise CronError(f'Unknown value {text!r}')
    return int(text)


def _field(text, low, high, names=()):
    """Get the sorted values of one cron field and whether it is `*`."""
    values = set()
    star = text in ('*', '?')
    for part in text.split(','):
        step = 1
        if '/' in part:
            part, step_text = part.split('/', 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f'Bad step in {text!r}')
            step = int(step_text)
        if part == '':
            raise CronError(f'Empty value in {text!r}')
        if part in ('*', '?'):
            first, last = low, high
        elif '-' in part:
            start, end = part.split('-', 1)
            first = _number(start, names, low)
            last = _number(end, names, low)
        else:
            first = _number(part, names, low)
            last = high if step > 1 else first
        if first < low or last > high or first > last:
            raise CronError(f'Value out of range in {text!r}')
        values.update(range(first, last + 1, step))
    return sorted(values), star


class Cron:
    """A cron expression of five fields (or a @macro)."""

    def __init__(self, expression):
        expression = expression.strip()
        self.expression = MACROS.get(expression.lower(), expression)
        fields = self.expression.split()
        if len(fields) != 5:
            raise CronError(f'Expected 5 fields: {expression!r}')
        self.minutes, _ = _field(fields[0], 0, 59)
        self.hours, _ = _field(fields[1], 0, 23)
        self.days, self.any_day = _field(fields[2], 1, 31)
        self.months, _ = _field(fields[3], 1, 12, MONTHS)
        weekdays, self.any_weekday = _field(fields[4], 0, 7, WEEKDAYS)
        self.weekdays = sorted({day % 7 for day in weekdays})

    def day_matches(self, day):
        """Check a date, with the cron rule that a restricted day of month
        and a restricted weekday match when either does."""
        if day.month not in self.months:
            return False
        in_month = day.day in self.days
        in_week = (day.isoweekday() % 7) in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return in_week
        if self.any_weekday:
            return in_month
        return in_month or in_week

    def next_fire(self, after):
        """Get the first start strictly later than the datetime, or None."""
        after = after.replace(second=0, microsecond=0)
        day = after.date()
        last = day + dt.timedelta(days=HORIZON_DAYS)
        while day <= last:
            if self.day_matches(day):
                for hour in self.hours:
                    for minute in self.minutes:
                        moment = dt.datetime(day.year, day.month, day.day,
                                             hour, minute)
                        if moment > after:
                            return moment
            day += dt.timedelta(days=1)
        return None


def parse_crontab(text):
    """Get [(expression, command)] of the entries of a crontab text.

    Comments, blank lines and environment assignments are left out, so are
    @reboot entries, which have no start time.
    """
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or ASSIGNMENT.match(line):
            continue
        if line.startswith('@'):
            head, _, command = line.partition(' ')
            if head.lower() in MACROS:
                entries.append((head.lower(), command.strip()))
            continue
        parts = line.split(None, 5)
        if len(parts) == 6:
            entries.append((' '.join(parts[:5]), parts[5].strip()))
    return entries


def _words(command):
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        return command.split()


def _read_script(path):
    """Get the text of a script, None when it is not a small text file."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > SCRIPT_LIMIT:
            return None
        with open(path, 'rb') as file:
            data = file.read()
    except OSError:
        return None
    if b'\0' in data:
        return None
    return data.decode('utf-8', errors='replace')


def _stem(path):
    name = path.replace('\\', '/').rsplit('/', 1)[-1]
    return name[:-len('.param')] if name.endswith('.param') else name


def find_params(command, script_name, read=_read_script, depth=DEPTH):
    """Find the params a cron command starts.

    Returns
    -------
    found : list of dict
        Per param its `param` name, the `param_path` as written, and the
        `script` that holds the call (None when the command calls the
        transfer script itself).
    """
    words = _words(command)
    found = []
    if any(word.endswith(script_name) for word in words):
        for word in words:
            if word.endswith('.param'):
                found.append({'param': _stem(word), 'param_path': word,
                              'script': None})
        if found:
            return found
    if depth <= 0:
        return found
    extra = [word for word in words if word.endswith('.param')]
    for word in words:
        if word.endswith('.param') or word.startswith('-'):
            continue
        text = read(word)
        if text is None:
            continue
        mentions = script_name in text
        for path in PARAM_PATTERN.findall(text) if mentions else ():
            found.append({'param': _stem(path), 'param_path': path,
                          'script': word})
        if mentions and extra:
            # The script takes the param as its argument.
            for path in extra:
                found.append({'param': _stem(path), 'param_path': path,
                              'script': word})
        if not mentions:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                for inner in find_params(line, script_name, read, depth - 1):
                    # The script the crontab runs, whatever it runs inside.
                    found.append({**inner, 'script': word})
    unique = {}
    for item in found:
        unique.setdefault((item['param'], item['script']), item)
    return list(unique.values())


def read_crontab():
    """Get the text of the crontab of the current user, None if unreadable.
    """
    try:
        done = subprocess.run(['crontab', '-l'], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning(f'Crontab can not be read: {error}')
        return None
    # No crontab for the user is reported as an error.
    if done.returncode != 0:
        return ''
    return done.stdout


def scan(script_name, text=None, after=None, read=_read_script):
    """Get the crontab entries that start transfers.

    Returns
    -------
    entries : list of dict
        Per entry: `param`, `param_path`, `script` (the script that calls
        the transfer script, None when the cron command does), `cron` (the
        expression), `command`, `next_fire` and `user`.
    """
    if text is None:
        if not sys.platform.startswith('linux'):
            return []
        text = read_crontab()
        if text is None:
            return []
    after = after or dt.datetime.now()
    entries = []
    for expression, command in parse_crontab(text):
        found = find_params(command, script_name, read)
        if not found:
            continue
        try:
            next_fire = Cron(expression).next_fire(after)
        except CronError:
            next_fire = None
        for item in found:
            entries.append({**item, 'cron': expression, 'command': command,
                            'next_fire': next_fire, 'user': _user()})
    return entries


def _user():
    try:
        return getpass.getuser()
    except Exception:
        return None
