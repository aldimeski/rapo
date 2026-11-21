"""Contains the file transfer engine.

It is the logic of the filetransfer-py tool (a rewrite of file_tran.pl) kept
inside rapo: a transfer copies or moves files from a local directory or an
SFTP server into one or more target directories, with optional file date
filter, stable size check, control file check, gzip/gunzip, archive, logging
and e-mail notification on errors.

A transfer is described by the text of a `.param` file, `Name:value` per
line, `#` starts a comment. `FileMask` and `FileCreationDate` use a small
date language instead of code, e.g. `<YYYYMMDD-5d>` is the date 5 days ago
(see docs/transfer.md).
"""

import calendar
import dataclasses as dc
import datetime as dt
import gzip
import html
import logging
import os
import posixpath
import re
import shutil
import smtplib
import socket
import stat
import tempfile
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

VERSION = '1.2.1'
# Downstream readers must ignore files with this prefix.
TMP_PREFIX = 'tmp_'
RANGE_SEPARATOR = re.compile(r'&date', re.IGNORECASE)


class ConfigError(Exception):
    """Invalid or missing transfer configuration."""


class TransferError(Exception):
    """A transfer step failed."""


# Date mini-language: <YYYYMMDD-5d> <DD-5> <YYYY> ...

_PART = r'(?:YYYY|YY|MM|DD|HH|MI|SS)'
# Block form <FORMAT[+-N unit]>, e.g. <YYYYMMDD-5d>, <YYYY-MM-DD HH-2h>. The
# whole date is shifted once.
_BLOCK_RE = re.compile(
    rf'<\s*({_PART}(?:[-_ :./T]*{_PART})*)\s*'
    r'(?:([+-])\s*(\d+)\s*(y|mo|d|h|mi|s))?\s*>', re.IGNORECASE)
_BLOCK_UNITS = {'y': 'YYYY', 'mo': 'MM', 'd': 'DD', 'h': 'HH', 'mi': 'MI',
                's': 'SS'}
_PART_RE = re.compile(_PART, re.IGNORECASE)
# Single token form <DD-5> shifts the date by 5 days, prints the day part.
_TOKEN_RE = re.compile(
    r'<\s*(YYYY|YY|MM|DD|HH|MI|SS)\s*(?:([+-])\s*(\d+))?\s*>', re.IGNORECASE)
_TOKEN_LIKE_RE = re.compile(r'<\s*(?:YYYY|YY|MM|DD|HH|MI|SS)[^>]*>',
                            re.IGNORECASE)


def shift_months(moment, months):
    """Shift the date by months, the last day of a short month respected."""
    total = moment.year*12 + (moment.month-1) + months
    year, index = divmod(total, 12)
    month = index + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def shift(moment, unit, amount):
    """Shift the whole date by `amount` of `unit`."""
    if unit in ('YYYY', 'YY'):
        return shift_months(moment, amount*12)
    if unit == 'MM':
        return shift_months(moment, amount)
    deltas = {
        'DD': dt.timedelta(days=amount),
        'HH': dt.timedelta(hours=amount),
        'MI': dt.timedelta(minutes=amount),
        'SS': dt.timedelta(seconds=amount),
    }
    return moment + deltas[unit]


def render_part(moved, unit):
    """Get one part (YYYY, MM, ...) of the date as text."""
    if unit == 'YYYY':
        return f'{moved.year:04d}'
    if unit == 'YY':
        return f'{moved.year % 100:02d}'
    part = {'MM': moved.month, 'DD': moved.day, 'HH': moved.hour,
            'MI': moved.minute, 'SS': moved.second}[unit]
    return f'{part:02d}'


def _shifted(now, unit, amount, token):
    try:
        return shift(now, unit, amount)
    except (ValueError, OverflowError) as error:
        raise ConfigError(f"Date token '{token}' is out of range: {error}")


def expand_date_tokens(text, now):
    """Expand the date tokens of the text.

    Block form `<FORMAT-Nu>` shifts the whole date once, then formats it:
    `<YYYYMMDD-5d>`, `<YYYY-MM-DD+1mo>`. Single form `<UNIT-N>` shifts by N
    of that unit and prints only that part: `<DD-5>`.
    """
    def replace_block(match):
        moved = now
        if match.group(2):
            amount = int(match.group(2) + match.group(3))
            moved = _shifted(now, _BLOCK_UNITS[match.group(4).lower()],
                             amount, match.group(0))
        return _PART_RE.sub(
            lambda part: render_part(moved, part.group(0).upper()),
            match.group(1))

    def replace_token(match):
        unit = match.group(1).upper()
        amount = int(match.group(2) + match.group(3)) if match.group(2) else 0
        return render_part(_shifted(now, unit, amount, match.group(0)), unit)

    expanded = _TOKEN_RE.sub(replace_token, _BLOCK_RE.sub(replace_block, text))
    leftover = _TOKEN_LIKE_RE.search(expanded)
    if leftover:
        raise ConfigError(
            f"Invalid date token '{leftover.group(0)}' (expected "
            '<YYYY|YY|MM|DD|HH|MI|SS> with optional +N/-N, or a block like '
            '<YYYYMMDD-5d> with unit y, mo, d, h, mi or s)')
    return expanded


# File date filter (FileDateCheck / FileCreationDate)

_DATE_RE = re.compile(
    r'^(\d{4})(?:-(\d{2})(?:-(\d{2})(?:[ T](\d{2})'
    r'(?:[-:](\d{2})(?:[-:](\d{2}))?)?)?)?)?$')
# Year, month, day, hour, minute, second.
_LEVEL_DEFAULTS = [None, 1, 1, 0, 0, 0]


def parse_date_spec(text):
    """Get (start of the period, level) of a date; level 0 year ... 5 second.
    """
    match = _DATE_RE.match(text.strip())
    if not match:
        raise ConfigError(f"Invalid date '{text}' "
                          '(expected YYYY[-MM[-DD[ HH[-MI[-SS]]]]])')
    parts = [int(group) for group in match.groups() if group is not None]
    values = parts + _LEVEL_DEFAULTS[len(parts):]
    try:
        return dt.datetime(*values), len(parts)-1
    except ValueError as error:
        raise ConfigError(f"Invalid date '{text}': {error}")


def truncate(moment, level):
    """Cut the date to the level (0 year ... 5 second)."""
    kept = [moment.year, moment.month, moment.day, moment.hour,
            moment.minute, moment.second]
    return dt.datetime(*(kept[:level+1] + _LEVEL_DEFAULTS[level+1:]))


@dc.dataclass
class DateFilter:
    """The period of a FileCreationDate."""
    start: dt.datetime
    start_level: int
    end: dt.datetime = None
    end_level: int = 0

    def matches(self, moment):
        """Check whether the moment falls into the period."""
        if self.end is None:
            return truncate(moment, self.start_level) == self.start
        return (truncate(moment, self.start_level) >= self.start
                and truncate(moment, self.end_level) <= self.end)


def build_date_filter(expanded):
    """Get the filter of an expanded FileCreationDate."""
    pieces = RANGE_SEPARATOR.split(expanded)
    if len(pieces) == 1:
        start, level = parse_date_spec(pieces[0])
        return DateFilter(start, level)
    if len(pieces) == 2:
        start, start_level = parse_date_spec(pieces[0])
        end, end_level = parse_date_spec(pieces[1])
        if end < start:
            raise ConfigError('FileCreationDate range ends before it starts:'
                              f" '{expanded}'")
        return DateFilter(start, start_level, end, end_level)
    raise ConfigError('FileCreationDate must hold one date or two dates '
                      f"separated by &date: '{expanded}'")


# Configuration (the text of a .param file)

def load_params(text, label='param'):
    """Get {name: value} of the text of a .param file."""
    params = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip().lstrip('﻿')
        if not line or line.startswith('#'):
            continue
        # Values may contain ':'.
        key, separator, value = line.partition(':')
        if not separator:
            raise ConfigError(f'{label} line {number}: expected key:value')
        params[key.strip()] = value.strip()
    return params


def _text(params, key, default=None):
    value = params.get(key, '')
    if value == '':
        if default is None:
            raise ConfigError(f'Missing parameter {key}')
        return default
    return value


def _flag(params, key, default=None):
    value = _text(params, key, default).upper()
    if value not in ('Y', 'N'):
        raise ConfigError(f'Parameter {key} must be Y or N, '
                          f"got '{params.get(key)}'")
    return value == 'Y'


def _number(params, key, default):
    value = _text(params, key, default)
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"Parameter {key} must be a number, got '{value}'")


def _split_list(value):
    return [item.strip() for item in re.split(r'[,;]', value)
            if item.strip()]


@dc.dataclass
class Config:
    """The settings of one transfer, with the date tokens of the moment."""
    label: str
    input_dir: str
    output_dirs: list
    file_mask_raw: str
    file_mask: object
    file_mask_expanded: str
    keep_input: bool = True
    sftp: bool = False
    sftp_type: str = ''
    sftp_host: str = ''
    sftp_port: int = 22
    sftp_user: str = ''
    sftp_pass_file: str = ''
    sftp_key: str = ''
    sftp_strict_host_key: bool = True
    date_check: bool = False
    date_spec_raw: str = ''
    date_spec_expanded: str = ''
    date_filter: DateFilter = None
    sub_dir_check: bool = False
    size_check: bool = False
    size_check_delay: int = 20
    ctl_check: bool = False
    ctl_extension: str = ''
    gzip_output: bool = False
    gzip_input: bool = False
    archive: bool = False
    archive_dir: str = ''
    log_mode: str = 'Full'
    log_database: bool = False
    log_dir: str = ''
    log_file: str = ''
    notification: bool = False
    notification_sender: str = ''
    notification_to: list = dc.field(default_factory=list)
    notification_cc: list = dc.field(default_factory=list)
    notification_server: str = ''
    notification_port: int = 25

    @classmethod
    def from_params(cls, label, params, now):
        """Get the settings of the parameters, raising ConfigError."""
        mask_raw = _text(params, 'FileMask')
        mask_expanded = expand_date_tokens(mask_raw, now)
        try:
            mask = re.compile(mask_expanded)
        except re.error as error:
            raise ConfigError(f"FileMask '{mask_expanded}' is not a valid "
                              f'regular expression: {error}')

        cfg = cls(
            label=label,
            input_dir=_text(params, 'InputDir'),
            output_dirs=_split_list(_text(params, 'OutputDir')),
            file_mask_raw=mask_raw,
            file_mask=mask,
            file_mask_expanded=mask_expanded,
            keep_input=_flag(params, 'KeepInput', 'Y'),
            sftp=_flag(params, 'SftpCheck', 'N'),
            sub_dir_check=_flag(params, 'SubDirCheck', 'N'),
            size_check=_flag(params, 'SizeCheck', 'N'),
            ctl_check=_flag(params, 'CtlFileCheck', 'N'),
            gzip_output=_flag(params, 'GzipOutput', 'N'),
            gzip_input=_flag(params, 'GzipInput', 'N'),
            archive=_flag(params, 'ArchiveCheck', 'N'),
            date_check=_flag(params, 'FileDateCheck', 'N'),
            notification=_flag(params, 'Notification', 'N'))
        if not cfg.output_dirs:
            raise ConfigError('OutputDir has no directory')
        if cfg.gzip_input and cfg.gzip_output:
            raise ConfigError('GzipInput and GzipOutput cannot both be Y')

        if cfg.sftp:
            cfg.sftp_type = _text(params, 'SftpType').capitalize()
            cfg.sftp_host = _text(params, 'SftpHost')
            cfg.sftp_user = _text(params, 'SftpUser')
            cfg.sftp_port = _number(params, 'SftpPort', '22')
            cfg.sftp_strict_host_key = _flag(params, 'SftpStrictHostKey', 'Y')
            if cfg.sftp_type == 'Clear':
                cfg.sftp_pass_file = _text(params, 'SftpPassFile')
            elif cfg.sftp_type == 'Key':
                cfg.sftp_key = _text(params, 'SftpKey')
            else:
                raise ConfigError('SftpType must be Clear or Key, '
                                  f"got '{cfg.sftp_type}'")

        if cfg.date_check:
            cfg.date_spec_raw = _text(params, 'FileCreationDate')
            cfg.date_spec_expanded = expand_date_tokens(cfg.date_spec_raw,
                                                        now)
            cfg.date_filter = build_date_filter(cfg.date_spec_expanded)

        if cfg.size_check:
            cfg.size_check_delay = _number(params, 'SizeCheckDelay', '20')
        if cfg.ctl_check:
            cfg.ctl_extension = _text(params, 'CtlFileExtension').lstrip('.')
        if cfg.archive:
            cfg.archive_dir = _text(params, 'ArchiveDir')

        cfg.log_mode = _text(params, 'LogMode', 'Full').capitalize()
        if cfg.log_mode not in ('Full', 'Basic', 'None'):
            raise ConfigError('LogMode must be Full, Basic or None, '
                              f"got '{cfg.log_mode}'")
        # LogDatabase:Y writes the log to the database, per LogMode, and
        # LogDir and LogFile are ignored (they need not even be set).
        cfg.log_database = _flag(params, 'LogDatabase', 'N')
        if cfg.log_mode != 'None' and not cfg.log_database:
            cfg.log_dir = _text(params, 'LogDir')
            cfg.log_file = _text(params, 'LogFile')

        if cfg.notification:
            method = _text(params, 'NotificationMethod', 'smtp').lower()
            if method != 'smtp':
                raise ConfigError('NotificationMethod must be smtp, '
                                  f"got '{method}'")
            cfg.notification_sender = _text(params, 'NotificationSender')
            cfg.notification_to = _split_list(_text(params, 'NotificationRe'))
            cfg.notification_cc = _split_list(
                _text(params, 'NotificationCc', ' ').strip())
            cfg.notification_server = _text(params, 'NotificationServer')
            cfg.notification_port = _number(params, 'NotificationPort', '25')
            if not cfg.notification_to:
                raise ConfigError('NotificationRe has no recipient')
        return cfg


def validate(text, now=None, label='param'):
    """Check the text of a .param file without touching any directory.

    Returns
    -------
    config : Config
        The settings, with the date tokens expanded for `now`.

    Raises ConfigError naming the first problem.
    """
    return Config.from_params(label, load_params(text, label),
                              now or dt.datetime.now())


def validate_paths(cfg):
    """Check the local directories once logging is available."""
    directories = list(cfg.output_dirs)
    if cfg.archive:
        directories.append(cfg.archive_dir)
    for directory in directories:
        if not os.path.isdir(directory):
            raise ConfigError(f'Target directory does not exist: {directory}')
        if not os.access(directory, os.W_OK):
            raise ConfigError(f'Target directory is not writable: {directory}')
    if not cfg.sftp:
        if not os.path.isdir(cfg.input_dir):
            raise ConfigError(f'InputDir does not exist: {cfg.input_dir}')
        source = os.path.realpath(cfg.input_dir)
        for directory in cfg.output_dirs:
            if os.path.realpath(directory) == source:
                raise ConfigError(f'OutputDir {directory} is the same as '
                                  'InputDir')


# Logging

class LogFormatter(logging.Formatter):
    """`pid: dd/mm/yy HH:MM:SS [ message ]`, the layout of the Perl tool."""

    def format(self, record):
        message = record.getMessage()
        if record.levelno >= logging.WARNING:
            message = f'{record.levelname}: {message}'
        stamp = self.formatTime(record, '%d/%m/%y %H:%M:%S')
        return f'{record.process}: {stamp} [ {message} ]'


class Collector(logging.Handler):
    """Remembers the errors for the notification and the log for the run.

    Without `database_level` the lines of INFO and above go to `lines`, the
    short log of the run history (`limit` of them). With it, the log goes to
    `records` as (epoch, level name, line) from that level up, the newest
    `database_limit` of them: it is what LogDatabase:Y writes to the database.
    """

    def __init__(self, limit=500, database=False, database_level=None,
                 database_limit=5000):
        super().__init__(level=logging.DEBUG)
        self.setFormatter(LogFormatter())
        self.errors = []
        self.lines = []
        self.limit = limit
        self.cut = 0
        self.database = database
        self.database_level = database_level
        self.database_limit = database_limit
        self.records = []
        self.dropped = 0

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.errors.append(record.getMessage())
        if self.database:
            if (self.database_level is not None
                    and record.levelno >= self.database_level):
                if len(self.records) >= self.database_limit:
                    del self.records[0]
                    self.dropped += 1
                self.records.append((record.created, record.levelname,
                                     self.format(record)))
        elif record.levelno >= logging.INFO:
            if len(self.lines) >= self.limit:
                del self.lines[0]
                self.cut += 1
            self.lines.append(self.format(record))


def setup_logging(cfg, name, database_limit=5000):
    """Get the logger of one transfer run and its collector.

    The log file of the param (LogDir/LogFile) is written as the Perl tool
    wrote it; the collector keeps the run's lines for the history. With
    LogDatabase:Y no file is written: the collector keeps the log per LogMode
    (Full from DEBUG, Basic from INFO, None nothing) for the database.
    """
    log = logging.getLogger(f'rapo.transfer.{name}')
    log.setLevel(logging.DEBUG)
    log.propagate = False
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()

    if cfg.log_mode != 'None' and not cfg.log_database:
        if not os.path.isdir(cfg.log_dir):
            raise ConfigError(f'LogDir does not exist: {cfg.log_dir}')
        handler = logging.FileHandler(
            os.path.join(cfg.log_dir, cfg.log_file), encoding='utf-8')
        handler.setLevel(logging.DEBUG if cfg.log_mode == 'Full'
                         else logging.INFO)
        handler.setFormatter(LogFormatter())
        log.addHandler(handler)

    level = {'Full': logging.DEBUG, 'Basic': logging.INFO}.get(cfg.log_mode)
    collector = Collector(database=cfg.log_database, database_level=level,
                          database_limit=database_limit)
    log.addHandler(collector)
    return log, collector


# Sources: local directory or SFTP server

@dc.dataclass
class FileInfo:
    """A file found in the source."""
    name: str
    path: str
    size: int
    mtime: float


class LocalSource:
    """The files of a local directory."""
    pathmod = os.path

    def list_files(self, root, recursive):
        """Get the files of the directory, of its sub-directories too."""
        walker = os.walk(root) if recursive else [next(os.walk(root))]
        found = []
        for dirpath, dirnames, filenames in walker:
            dirnames.sort()
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                if not os.path.isfile(path):
                    continue
                try:
                    info = os.stat(path)
                except OSError:
                    # Vanished between listing and stat.
                    continue
                found.append(FileInfo(name, path, info.st_size,
                                      info.st_mtime))
        return found

    def stat(self, path):
        """Get (size, modification time) of the file, None if it is gone."""
        try:
            info = os.stat(path)
        except OSError:
            return None
        return info.st_size, info.st_mtime

    def materialize(self, info, staging_dir):
        """Get (local path, whether it is a temporary copy) of the file."""
        return info.path, False

    def remove(self, path):
        """Delete the file."""
        os.unlink(path)

    def close(self):
        """Release the source."""


class SftpSource:
    """The files of a directory of an SFTP server."""
    pathmod = posixpath

    def __init__(self, cfg, log):
        try:
            # Imported here so that local transfers do not need it.
            import paramiko
        except ImportError:
            raise TransferError("SFTP needs the 'paramiko' package "
                                '(pip install paramiko)')
        self._counter = 0
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(
            paramiko.RejectPolicy() if cfg.sftp_strict_host_key
            else paramiko.AutoAddPolicy())
        options = dict(hostname=cfg.sftp_host, port=cfg.sftp_port,
                       username=cfg.sftp_user, timeout=30,
                       allow_agent=False, look_for_keys=False)
        if cfg.sftp_type == 'Clear':
            log.debug('SFTP Clear connection, reading password file %s',
                      cfg.sftp_pass_file)
            options['password'] = self._read_password(cfg.sftp_pass_file, log)
        else:
            if not os.path.isfile(cfg.sftp_key):
                raise TransferError(
                    f'SFTP key file not found: {cfg.sftp_key}')
            log.debug('SFTP Key connection, using key file %s', cfg.sftp_key)
            options['key_filename'] = cfg.sftp_key
        try:
            client.connect(**options)
            self._sftp = client.open_sftp()
        except Exception as error:
            raise TransferError(
                f'Error in SFTP connection to {cfg.sftp_host}: {error}')
        self._client = client
        log.info('SFTP connection established to %s', cfg.sftp_host)

    @staticmethod
    def _read_password(path, log):
        if not os.path.isfile(path):
            raise TransferError(f'SFTP pass file not found: {path}')
        if os.name == 'posix' and os.stat(path).st_mode & 0o077:
            log.warning('SFTP pass file %s is readable by other users; run '
                        'chmod 600 on it', path)
        with open(path, encoding='utf-8') as file:
            lines = [line.rstrip('\r\n') for line in file if line.strip()]
        if not lines:
            raise TransferError(f'SFTP pass file is empty: {path}')
        if len(lines) > 1:
            log.warning('SFTP pass file %s has multiple lines, only the '
                        'first is used', path)
        return lines[0]

    def list_files(self, root, recursive):
        """Get the files of the directory, of its sub-directories too."""
        found = []
        pending = [root]
        while pending:
            directory = pending.pop(0)
            try:
                entries = self._sftp.listdir_attr(directory)
            except IOError as error:
                raise TransferError(
                    f'Cannot list {directory} on SFTP: {error}')
            for entry in sorted(entries, key=lambda item: item.filename):
                # Some servers list them, they are never files.
                if entry.filename in ('.', '..'):
                    continue
                path = posixpath.join(directory, entry.filename)
                mode = entry.st_mode or 0
                size = entry.st_size or 0
                mtime = float(entry.st_mtime or 0)
                # listdir_attr reports the link itself: look at its target.
                if stat.S_ISLNK(mode):
                    try:
                        target = self._sftp.stat(path)
                    except IOError:
                        # Broken link.
                        continue
                    mode = target.st_mode or 0
                    size = target.st_size or 0
                    mtime = float(target.st_mtime or 0)
                    # Links to directories are not followed (could loop).
                    if stat.S_ISDIR(mode):
                        continue
                if stat.S_ISDIR(mode):
                    if recursive:
                        pending.append(path)
                elif stat.S_ISREG(mode):
                    found.append(FileInfo(entry.filename, path, size, mtime))
        return found

    def stat(self, path):
        """Get (size, modification time) of the file, None if it is gone."""
        try:
            info = self._sftp.stat(path)
        except IOError:
            return None
        return info.st_size or 0, float(info.st_mtime or 0)

    def materialize(self, info, staging_dir):
        """Download the file, get (local path, True)."""
        self._counter += 1
        local_path = os.path.join(staging_dir, f'{self._counter}_{info.name}')
        try:
            self._sftp.get(info.path, local_path)
        except Exception as error:
            raise TransferError(
                f"Can't download {info.path} from SFTP: {error}")
        return local_path, True

    def remove(self, path):
        """Delete the file."""
        self._sftp.remove(path)

    def close(self):
        """Close the connection."""
        try:
            self._sftp.close()
            self._client.close()
        except Exception:
            pass


# Transfer

def format_time(epoch):
    """Get the epoch as local date and time."""
    return dt.datetime.fromtimestamp(epoch).strftime('%Y-%m-%d %H:%M:%S')


def deliver(source_path, name, target_dir, log, gzip_input=False,
            gzip_output=False):
    """Write the file to the directory as tmp_<name>, then rename it, so
    that readers never see a partial file.
    """
    final_name = name
    if gzip_input:
        final_name = name + '.gz'
    elif gzip_output:
        if name.endswith('.gz'):
            final_name = name[:-3]
        else:
            log.warning('GzipOutput is Y but %s does not end with .gz, '
                        'copying it unchanged', name)
            gzip_output = False
    tmp_path = os.path.join(target_dir, TMP_PREFIX + final_name)
    final_path = os.path.join(target_dir, final_name)
    try:
        if gzip_input:
            with open(source_path, 'rb') as source, \
                    gzip.open(tmp_path, 'wb') as target:
                shutil.copyfileobj(source, target)
        elif gzip_output:
            with gzip.open(source_path, 'rb') as source, \
                    open(tmp_path, 'wb') as target:
                shutil.copyfileobj(source, target)
        else:
            shutil.copyfile(source_path, tmp_path)
        log.info('File %s copied to %s', name, tmp_path)
        os.replace(tmp_path, final_path)
        log.info('File %s renamed to %s', tmp_path, final_path)
    except (OSError, EOFError) as error:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise TransferError(f"Can't write {name} to {target_dir}: {error}")
    return final_path


def control_file_path(source, info, extension):
    """Get the path of the control file of a file."""
    pm = source.pathmod
    return pm.join(pm.dirname(info.path),
                   pm.splitext(info.name)[0] + '.' + extension)


def select_files(cfg, source, log):
    """Get the files to transfer and the paths of all files found."""
    everything = source.list_files(cfg.input_dir, cfg.sub_dir_check)
    all_paths = {item.path for item in everything}
    log.debug('%d file(s) found in %s', len(everything), cfg.input_dir)

    selected = []
    for item in everything:
        # Control files are never transferred as data files.
        if cfg.ctl_check and (source.pathmod.splitext(item.name)[1]
                              == '.' + cfg.ctl_extension):
            continue
        if not cfg.file_mask.search(item.name):
            continue
        if cfg.date_filter and not cfg.date_filter.matches(
                dt.datetime.fromtimestamp(item.mtime)):
            log.debug('File %s skipped, date %s outside FileCreationDate',
                      item.path, format_time(item.mtime))
            continue
        log.debug('File %s matches', item.path)
        selected.append(item)

    if selected and cfg.size_check:
        log.info('Size check enabled, waiting %d seconds',
                 cfg.size_check_delay)
        time.sleep(cfg.size_check_delay)
        stable = []
        for item in selected:
            current = source.stat(item.path)
            if current and current[0] == item.size:
                stable.append(item)
            else:
                log.info('File %s skipped, size is still changing', item.path)
        selected = stable

    if cfg.ctl_check:
        with_control = []
        for item in selected:
            if control_file_path(source, item, cfg.ctl_extension) in all_paths:
                with_control.append(item)
            else:
                log.info('File %s skipped, control file .%s not found',
                         item.path, cfg.ctl_extension)
        selected = with_control
    return selected, all_paths


def process_file(item, source, cfg, all_paths, staging_dir, log):
    """Transfer one file to every target and, if asked, archive and remove
    it from the source."""
    # Mandatory: record when the file was created/modified BEFORE it is
    # transferred.
    log.info('File %s creation/modification date (before transfer): %s, '
             'size %d bytes -> transferring to %s', item.path,
             format_time(item.mtime), item.size, ', '.join(cfg.output_dirs))
    local_path, temporary = source.materialize(item, staging_dir)
    try:
        for target_dir in cfg.output_dirs:
            deliver(local_path, item.name, target_dir, log, cfg.gzip_input,
                    cfg.gzip_output)
        if cfg.archive:
            # The archive is always the original file.
            deliver(local_path, item.name, cfg.archive_dir, log)
            log.info('File %s archived to %s', item.name, cfg.archive_dir)
    finally:
        if temporary:
            try:
                os.unlink(local_path)
            except OSError:
                pass

    # Only reached when every delivery succeeded.
    if not cfg.keep_input:
        source.remove(item.path)
        log.info('File %s removed from input (KeepInput=N)', item.path)
        if cfg.ctl_check:
            control = control_file_path(source, item, cfg.ctl_extension)
            if control in all_paths:
                source.remove(control)
                log.info('Control file %s removed', control)


def run_transfer(cfg, source, log):
    """Transfer the files, get (number transferred, number failed)."""
    log.info('****** START Transit files ******')
    log.info('InputDir = %s; OutputDir = %s; FileMask = %s', cfg.input_dir,
             ','.join(cfg.output_dirs), cfg.file_mask_expanded)
    if cfg.file_mask_raw != cfg.file_mask_expanded:
        log.info("FileMask '%s' expanded to '%s'", cfg.file_mask_raw,
                 cfg.file_mask_expanded)
    if cfg.date_check:
        log.info("FileCreationDate '%s' expanded to '%s'",
                 cfg.date_spec_raw, cfg.date_spec_expanded)
    log.debug('SizeCheck=%s CtlFileCheck=%s GzipInput=%s GzipOutput=%s '
              'KeepInput=%s ArchiveCheck=%s SubDirCheck=%s', cfg.size_check,
              cfg.ctl_check, cfg.gzip_input, cfg.gzip_output, cfg.keep_input,
              cfg.archive, cfg.sub_dir_check)

    selected, all_paths = select_files(cfg, source, log)
    if not selected:
        log.info('No files to transfer')

    transferred = failed = 0
    with tempfile.TemporaryDirectory(prefix='rapo-transfer-') as staging_dir:
        for item in selected:
            try:
                process_file(item, source, cfg, all_paths, staging_dir, log)
                transferred += 1
            except Exception as error:
                # One bad file must not stop the others.
                failed += 1
                log.error('File %s was not fully processed and is kept in '
                          'input: %s', item.path, error)
    log.info('****** END Transit files: %d transferred, %d failed ******',
             transferred, failed)
    return transferred, failed


# Notification

def describe_run(cfg):
    """Get the lines telling where the transfer ran."""
    if cfg.sftp:
        transfer = (f'SFTP from server {cfg.sftp_host}:{cfg.sftp_port} '
                    f'(user {cfg.sftp_user})')
    else:
        transfer = 'local transfer'
    return [
        f'Ran on server: {socket.getfqdn() or socket.gethostname()}',
        f'Transfer: {transfer}',
        f'Param: {cfg.label}',
        f'Input dir: {cfg.input_dir}; Output dir: '
        f'{", ".join(cfg.output_dirs)}',
    ]


def send_notification(cfg, messages):
    """Send the errors by e-mail, get an error text or None if it was sent."""
    items = '<br>\n'.join(html.escape(message) for message in messages)
    where = '<br>\n'.join(html.escape(line) for line in describe_run(cfg))
    body = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Automatic '
        'File Transfer Notification</title></head><body>Hi,<br>This is an '
        'automatic notification from the rapo file transfer<br><br>'
        f'{where}<br><br>{items}<br><br>Please do not reply if not needed.'
        '<br>Cheers<br>rapo'
        f'<p style="font:7px arial,sans-serif">v.{VERSION}</p></body></html>')
    message = MIMEMultipart('mixed')
    host = socket.getfqdn() or socket.gethostname()
    message['Subject'] = ('Automatic Mail from : File transfer with param '
                          f'{cfg.label} on {host}')
    message['From'] = cfg.notification_sender
    message['To'] = ', '.join(cfg.notification_to)
    if cfg.notification_cc:
        message['Cc'] = ', '.join(cfg.notification_cc)
    message.attach(MIMEText(body, 'html', 'utf-8'))
    try:
        with smtplib.SMTP(cfg.notification_server, cfg.notification_port,
                          timeout=30) as smtp:
            smtp.send_message(message, to_addrs=cfg.notification_to
                              + cfg.notification_cc)
    except (smtplib.SMTPException, OSError) as error:
        return f'Notification e-mail could not be sent: {error}'
    return None


# Execution

@dc.dataclass
class Result:
    """The outcome of one transfer run."""
    status: str = 'D'
    transferred: int = 0
    failed: int = 0
    message: str = None
    log: list = dc.field(default_factory=list)
    # LogDatabase:Y: the log lines to write to the database as (epoch, level,
    # line), possibly none (LogMode:None); None when the log is not kept there.
    db_log: list = None
    # Lines of the log the run produced but did not keep (over the limit).
    db_dropped: int = 0


def execute(name, text, now=None, log_limit=5000):
    """Run one transfer from the text of its .param file.

    Never raises: a problem becomes the status `E` (error) of the result.
    Without errors the status is `D` (done). Failed files make it `E` too,
    as the Perl tool returned 1. `log_limit` is the most lines of the log
    LogDatabase:Y keeps (the newest).
    """
    now = now or dt.datetime.now()
    result = Result()
    try:
        cfg = Config.from_params(name, load_params(text, name), now)
        log, collector = setup_logging(cfg, name, log_limit)
    except (ConfigError, OSError) as error:
        result.status = 'E'
        result.message = f'Configuration error: {error}'
        return result

    source = None
    try:
        validate_paths(cfg)
        source = SftpSource(cfg, log) if cfg.sftp else LocalSource()
        result.transferred, result.failed = run_transfer(cfg, source, log)
    except (ConfigError, TransferError, OSError) as error:
        log.error('%s', error)
    except Exception as error:
        log.exception('Unexpected error')
        collector.errors.append(f'{type(error).__name__}: {error}')
    finally:
        if source is not None:
            source.close()
        for handler in list(log.handlers):
            if not isinstance(handler, Collector):
                handler.close()

    if cfg.log_database:
        result.db_log = list(collector.records)
        result.db_dropped = collector.dropped
    else:
        result.log = list(collector.lines)
        if collector.cut:
            result.log.insert(0,
                              f'[ {collector.cut} earlier lines left out ]')
    if collector.errors:
        result.status = 'E'
        result.message = '\n'.join(collector.errors)[:3900]
        if cfg.notification:
            problem = send_notification(cfg, collector.errors)
            if problem:
                result.message = f'{result.message}\n{problem}'[:3900]
    return result
