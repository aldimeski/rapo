"""Contains the storage of the file transfers.

A transfer (a param) is kept either as a `<name>.param` file in the folder
of `[TRANSFER] param_dir`, or as a row of `rapo_transfer_param`. Its schedule
is kept in `rapo_transfer_schedule` by the name in both cases, so moving a
param between the folder and the database keeps it. The runs are the rows of
`rapo_transfer_run`.
"""

import datetime as dt
import os
import re

import sqlalchemy as sa

from .. import options
from ..database import db, CLIENT
from ..logger import logger

from . import engine

SUFFIX = '.param'
FILE = 'file'
DATABASE = 'db'
NAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
# Statuses of a run.
INITIATED, RUNNING, DONE, ERROR, CANCELED = 'I', 'S', 'D', 'E', 'C'
ACTIVE = (INITIATED, RUNNING)

metadata = sa.MetaData()
param_table = sa.Table(
    'rapo_transfer_param', metadata,
    sa.Column('param_name', sa.String(128), primary_key=True),
    sa.Column('param_text', sa.Text, nullable=False),
    sa.Column('description', sa.String(500)),
    sa.Column('created_date', sa.DateTime),
    sa.Column('created_by', sa.String(256)),
    sa.Column('updated_date', sa.DateTime),
    sa.Column('updated_by', sa.String(256)))
schedule_table = sa.Table(
    'rapo_transfer_schedule', metadata,
    sa.Column('param_name', sa.String(128), primary_key=True),
    sa.Column('schedule_config', sa.String(4000)),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('updated_date', sa.DateTime),
    sa.Column('updated_by', sa.String(256)))
run_table = sa.Table(
    'rapo_transfer_run', metadata,
    sa.Column('run_id', sa.Integer, primary_key=True),
    sa.Column('param_name', sa.String(128), nullable=False),
    sa.Column('trigger_type', sa.String(16), nullable=False),
    sa.Column('scheduled_time', sa.DateTime),
    sa.Column('queued_time', sa.DateTime),
    sa.Column('start_time', sa.DateTime),
    sa.Column('end_time', sa.DateTime),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('transferred', sa.Integer),
    sa.Column('failed', sa.Integer),
    sa.Column('message', sa.String(4000)),
    sa.Column('run_log', sa.Text),
    sa.Column('runner', sa.String(255)),
    sa.Column('requested_by', sa.String(256)),
    sa.Column('log_lines', sa.Integer))
# The log lines of the runs of params with LogDatabase:Y, one row per line.
log_table = sa.Table(
    'rapo_transfer_log', metadata,
    sa.Column('log_id', sa.Integer, primary_key=True),
    sa.Column('run_id', sa.Integer, nullable=False),
    sa.Column('param_name', sa.String(128), nullable=False),
    sa.Column('line_no', sa.Integer, nullable=False),
    sa.Column('log_time', sa.DateTime),
    sa.Column('log_level', sa.String(8)),
    sa.Column('message', sa.String(4000)))

RUN_COLUMNS = [column for column in run_table.c if column.name != 'run_log']


class StoreError(Exception):
    """A request the storage can not do, with its HTTP status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    """Get the current local time without fractions."""
    return dt.datetime.now().replace(microsecond=0)


def user():
    """Get the API caller recorded with a change."""
    return CLIENT.get() or None


def param_dir():
    """Get the absolute folder of the .param files, None if not set."""
    value = options.get('TRANSFER', 'param_dir')
    if not value:
        return None
    return os.path.abspath(str(value))


def check_name(name):
    """Return the name when it is fit for a param, else raise StoreError."""
    name = str(name or '').strip()
    if name.lower().endswith(SUFFIX):
        name = name[:-len(SUFFIX)]
    if not NAME_PATTERN.match(name):
        raise StoreError('A param name has 1-128 letters, digits, dots, '
                         'hyphens or underscores, and starts with a letter '
                         'or digit.')
    return name


def file_path(name):
    """Get the path of the .param file of the name."""
    folder = param_dir()
    if not folder:
        raise StoreError('Set [TRANSFER] param_dir in rapo.ini to keep '
                         'params as files.')
    return os.path.join(folder, name + SUFFIX)


def _missing(error):
    text = str(error)
    if 'ORA-00942' in text or 'no such table' in text:
        raise StoreError('The file transfer tables are missing: run '
                         'migrations/v0.8.8/upgrade.sql.', 503)


def _execute(statement, **kwargs):
    try:
        return db.execute(statement, **kwargs)
    except sa.exc.DatabaseError as error:
        _missing(error)
        raise


def _text(row):
    value = row['param_text']
    return value.read() if hasattr(value, 'read') else value


# The params

def read_files():
    """Get {name: {modified, size}} of the .param files of the folder."""
    folder = param_dir()
    found = {}
    if not folder or not os.path.isdir(folder):
        return found
    for entry in sorted(os.listdir(folder)):
        path = os.path.join(folder, entry)
        if not entry.endswith(SUFFIX) or not os.path.isfile(path):
            continue
        name = entry[:-len(SUFFIX)]
        info = os.stat(path)
        found[name] = {'modified': dt.datetime.fromtimestamp(
            info.st_mtime).replace(microsecond=0), 'size': info.st_size}
    return found


def read_database():
    """Get {name: {modified, size, description}} of the params in the
    database, None when the tables are missing."""
    statement = sa.select(param_table.c.param_name,
                          param_table.c.description,
                          param_table.c.updated_date,
                          sa.func.length(param_table.c.param_text)
                          .label('size')).order_by(param_table.c.param_name)
    try:
        rows = db.execute(statement, as_table=True)
    except sa.exc.DatabaseError as error:
        _missing_logged(error)
        return None
    return {row['param_name']: {'modified': row['updated_date'],
                                'size': row['size'],
                                'description': row['description']}
            for row in rows}


_warned = set()


def _missing_logged(error):
    text = str(error)
    if not ('ORA-00942' in text or 'no such table' in text):
        raise error
    if 'param' not in _warned:
        _warned.add('param')
        logger.warning('File transfer tables are missing: run '
                       'migrations/v0.8.8/upgrade.sql')


def read(name):
    """Get (text, source) of the param.

    A param present in both places is refused: it would run one of two
    different texts depending on who looks.
    """
    name = check_name(name)
    in_file = False
    folder = param_dir()
    if folder:
        path = os.path.join(folder, name + SUFFIX)
        in_file = os.path.isfile(path)
    statement = sa.select(param_table).where(param_table.c.param_name == name)
    try:
        row = db.execute(statement, as_dict=True)
    except sa.exc.DatabaseError as error:
        _missing_logged(error)
        row = None
    if in_file and row:
        raise StoreError(f'Param {name} exists both as a file and in the '
                         'database: delete one of them.', 409)
    if in_file:
        with open(path, encoding='utf-8-sig') as file:
            return file.read(), FILE
    if row:
        return _text(row), DATABASE
    raise StoreError(f'Param {name} not found.', 404)


def description(name):
    """Get the description of a param kept in the database, else None."""
    statement = sa.select(param_table.c.description).where(
        param_table.c.param_name == name)
    try:
        return db.execute(statement, as_scalar=True)
    except sa.exc.DatabaseError:
        return None


def exists(name):
    """Get where the param is: [] , ['file'], ['db'] or both."""
    places = []
    folder = param_dir()
    if folder and os.path.isfile(os.path.join(folder, name + SUFFIX)):
        places.append(FILE)
    statement = sa.select(sa.func.count()).select_from(param_table).where(
        param_table.c.param_name == name)
    try:
        if db.execute(statement, as_scalar=True):
            places.append(DATABASE)
    except sa.exc.DatabaseError as error:
        _missing_logged(error)
    return places


def validate(text):
    """Check the text of a param, raising StoreError naming the problem."""
    try:
        engine.validate(text)
    except engine.ConfigError as error:
        raise StoreError(str(error))
    except ValueError as error:
        raise StoreError(str(error))


def write(name, text, where, note=None, create=False):
    """Save the text of a param to a file or the database.

    A name taken in the other place is refused; the param of `where` is
    replaced unless `create`, which refuses an existing one.
    """
    name = check_name(name)
    text = (text or '').replace('\r\n', '\n')
    if not text.strip():
        raise StoreError('The param is empty.')
    validate(text)
    if where not in (FILE, DATABASE):
        raise StoreError("Store a param in 'file' or 'db'.")
    if where == FILE and uses_database_log(text):
        raise StoreError('LogDatabase:Y is only for a param kept in the '
                         'database: a file may also be run by the '
                         'filetransfer-py script, which does not know it.')
    places = exists(name)
    other = DATABASE if where == FILE else FILE
    if other in places:
        raise StoreError(f'Param {name} already exists in the '
                         f'{"database" if other == DATABASE else "folder"}: '
                         'move it instead.', 409)
    if create and where in places:
        raise StoreError(f'Param {name} already exists.', 409)
    if where == FILE:
        path = file_path(name)
        if not os.path.isdir(os.path.dirname(path)):
            raise StoreError('The folder of the params does not exist: '
                             f'{os.path.dirname(path)}')
        temporary = path + '.tmp'
        with open(temporary, 'w', encoding='utf-8', newline='\n') as file:
            file.write(text if text.endswith('\n') else text + '\n')
        os.replace(temporary, path)
    else:
        moment = now()
        if DATABASE in places:
            values = {'param_text': text, 'updated_date': moment,
                      'updated_by': user()}
            if note is not None:
                values['description'] = note or None
            _execute(param_table.update().where(
                param_table.c.param_name == name).values(**values))
        else:
            _execute(param_table.insert().values(
                param_name=name, param_text=text, description=note or None,
                created_date=moment, created_by=user(),
                updated_date=moment, updated_by=user()))
    logger.info(f'Transfer param {name} saved to the {where}')
    return name


def delete(name):
    """Delete a param wherever it is, with its schedule."""
    name = check_name(name)
    places = exists(name)
    if not places:
        raise StoreError(f'Param {name} not found.', 404)
    if FILE in places:
        os.remove(file_path(name))
    if DATABASE in places:
        _execute(param_table.delete().where(param_table.c.param_name == name))
    delete_schedule(name)
    logger.info(f'Transfer param {name} deleted')


def parameters(text):
    """Get {name: value} of the text of a param, raising StoreError."""
    try:
        return engine.load_params(text)
    except engine.ConfigError as error:
        raise StoreError(str(error))


def uses_database_log(text):
    """Check whether the param asks for its log in the database."""
    return parameters(text).get('LogDatabase', '').upper() == 'Y'


def drop_parameter(text, name):
    """Get the text without the lines of a parameter, comments kept."""
    kept = [line for line in text.splitlines()
            if line.strip().startswith('#') or not line.strip()
            or line.partition(':')[0].strip() != name]
    return '\n'.join(kept) + '\n'


def set_parameter(text, name, value):
    """Get the text with a parameter set, in its line or at the end."""
    lines, done = [], False
    for line in text.splitlines():
        if (not line.strip().startswith('#') and line.strip()
                and line.partition(':')[0].strip() == name):
            if not done:
                lines.append(f'{name}:{value}')
            done = True
        else:
            lines.append(line)
    if not done:
        lines.append(f'{name}:{value}')
    return '\n'.join(lines) + '\n'


def plan_move(name, target):
    """Get what moving a param to 'file' or 'db' would do.

    Returns
    -------
    plan : dict
        `text` to be stored at the target, `source` where it is now, `note`
        (the description of a database param), `notes` (changes made to the
        text) and `warnings` (what needs a confirmation).

    A param moved from the database to a file loses LogDatabase, which only
    a param in the database can have. When it has no LogDir and LogFile the
    file has no place to write its log to: that is warned of, and on
    confirmation LogMode is set to None, so the param stays valid.
    """
    name = check_name(name)
    if target not in (FILE, DATABASE):
        raise StoreError("Move a param to 'file' or 'db'.")
    places = exists(name)
    if not places:
        raise StoreError(f'Param {name} not found.', 404)
    if len(places) > 1:
        raise StoreError(f'Param {name} exists both as a file and in the '
                         'database: delete one of them.', 409)
    if places[0] == target:
        raise StoreError(f'Param {name} is already in the '
                         f'{"database" if target == DATABASE else "folder"}.')
    text, source = read(name)
    plan = {'text': text, 'source': source, 'notes': [], 'warnings': [],
            'note': description(name) if source == DATABASE else None}
    if target == FILE:
        values = parameters(text)
        if 'LogDatabase' in values:
            plan['text'] = drop_parameter(plan['text'], 'LogDatabase')
            plan['notes'].append('LogDatabase is removed: only a param in '
                                 'the database can log there.')
        if not values.get('LogDir') or not values.get('LogFile'):
            plan['warnings'].append(
                f'{name} has no LogDir and LogFile, so as a file it has no '
                'logging.' + (' LogMode is set to None.'
                              if values.get('LogMode', 'Full').capitalize()
                              != 'None' else ''))
            if values.get('LogMode', 'Full').capitalize() != 'None':
                plan['text'] = set_parameter(plan['text'], 'LogMode', 'None')
        validate(plan['text'])
    return plan


def move(name, target, confirm=False):
    """Move a param to 'file' or 'db', keeping its schedule.

    A move that has warnings (see `plan_move`) needs `confirm`.
    """
    plan = plan_move(name, target)
    if plan['warnings'] and not confirm:
        raise StoreError('Confirmation needed: '
                         + ' '.join(plan['warnings']), 409)
    name = check_name(name)
    text, source, note = plan['text'], plan['source'], plan['note']
    if target == FILE:
        # Written beside the database row first: a failure keeps the row.
        path = file_path(name)
        if not os.path.isdir(os.path.dirname(path)):
            raise StoreError('The folder of the params does not exist: '
                             f'{os.path.dirname(path)}')
        temporary = path + '.tmp'
        with open(temporary, 'w', encoding='utf-8', newline='\n') as file:
            file.write(text if text.endswith('\n') else text + '\n')
        os.replace(temporary, path)
        _execute(param_table.delete().where(param_table.c.param_name == name))
    else:
        moment = now()
        _execute(param_table.insert().values(
            param_name=name, param_text=text, description=note,
            created_date=moment, created_by=user(), updated_date=moment,
            updated_by=user()))
        os.remove(file_path(name))
    logger.info(f'Transfer param {name} moved to the {target}')


# The schedules

def read_schedules():
    """Get {name: {schedule_config, status, updated_date}}, empty when the
    tables are missing."""
    try:
        rows = db.execute(sa.select(schedule_table), as_table=True)
    except sa.exc.DatabaseError as error:
        _missing_logged(error)
        return {}
    return {row['param_name']: row for row in rows}


def write_schedule(name, schedule_config, enabled):
    """Save the schedule of a param ('' / None config: not scheduled)."""
    name = check_name(name)
    if not exists(name):
        raise StoreError(f'Param {name} not found.', 404)
    status = 'Y' if enabled else 'N'
    values = {'schedule_config': schedule_config or None, 'status': status,
              'updated_date': now(), 'updated_by': user()}
    updated = _execute(schedule_table.update().where(
        schedule_table.c.param_name == name).values(**values)).rowcount
    if not updated:
        _execute(schedule_table.insert().values(param_name=name, **values))
    logger.info(f'Transfer {name} schedule saved')


def delete_schedule(name):
    """Forget the schedule of a param."""
    try:
        db.execute(schedule_table.delete().where(
            schedule_table.c.param_name == name))
    except sa.exc.DatabaseError as error:
        _missing_logged(error)


# The runs

def add_run(name, trigger, scheduled_time=None, requested_by=None,
            runner=None):
    """Record an initiated run, get its ID."""
    result = _execute(run_table.insert().values(
        param_name=name, trigger_type=trigger, scheduled_time=scheduled_time,
        queued_time=now(), status=INITIATED, runner=runner,
        requested_by=requested_by))
    run_id = result.inserted_primary_key[0]
    if run_id is None:
        run_id = _execute(sa.select(sa.func.max(run_table.c.run_id)).where(
            run_table.c.param_name == name), as_scalar=True)
    return int(run_id)


def update_run(run_id, **values):
    """Change a run."""
    if 'message' in values and values['message']:
        values['message'] = values['message'][:4000]
    _execute(run_table.update().where(
        run_table.c.run_id == run_id).values(**values))


def read_runs(name=None, limit=100):
    """Get the runs, latest first, without their log."""
    statement = sa.select(*RUN_COLUMNS).order_by(run_table.c.run_id.desc())
    if name:
        statement = statement.where(run_table.c.param_name == name)
    try:
        return db.execute(statement.limit(limit), as_table=True)
    except sa.exc.DatabaseError as error:
        _missing_logged(error)
        return []


def read_run(run_id):
    """Get a run with its log, None when there is none.

    The log of a run of a param with LogDatabase:Y is read from the log
    table; `log_trimmed` tells that older lines were deleted to keep the
    newest ones of the param.
    """
    row = _execute(sa.select(run_table).where(
        run_table.c.run_id == run_id), as_dict=True)
    if not row:
        return row
    if hasattr(row.get('run_log'), 'read'):
        row['run_log'] = row['run_log'].read()
    row['log_trimmed'] = False
    if row.get('log_lines') is not None:
        lines = [item[0] for item in _execute(
            sa.select(log_table.c.message).where(
                log_table.c.run_id == run_id).order_by(log_table.c.line_no)
        ).fetchall()]
        row['run_log'] = '\n'.join(lines)
        row['log_trimmed'] = len(lines) < row['log_lines']
    return row


def add_log_lines(run_id, name, lines, limit):
    """Write the log of a run (epoch, level, line), then keep only the
    newest `limit` lines of the param, those of older runs go first."""
    if lines:
        rows = [{'run_id': run_id, 'param_name': name, 'line_no': number,
                 'log_time': dt.datetime.fromtimestamp(moment).replace(
                     microsecond=0),
                 'log_level': level[:8], 'message': text[:4000]}
                for number, (moment, level, text) in enumerate(lines, 1)]
        try:
            with db.engine.begin() as connection:
                connection.execute(log_table.insert(), rows)
        except sa.exc.DatabaseError as error:
            _missing(error)
            raise
    trim_log(name, limit)


def trim_log(name, limit):
    """Delete all but the newest `limit` log lines of a param."""
    keep = sa.select(log_table.c.log_id).where(
        log_table.c.param_name == name).order_by(
        log_table.c.log_id.desc()).limit(limit)
    _execute(log_table.delete().where(
        log_table.c.param_name == name, log_table.c.log_id.notin_(keep)))


def read_last_runs():
    """Get {name: last run} of every param that ran."""
    inner = sa.select(sa.func.max(run_table.c.run_id)).group_by(
        run_table.c.param_name)
    statement = sa.select(*RUN_COLUMNS).where(run_table.c.run_id.in_(inner))
    try:
        rows = db.execute(statement, as_table=True)
    except sa.exc.DatabaseError as error:
        _missing_logged(error)
        return {}
    return {row['param_name']: row for row in rows}


def clean_runs(days):
    """Delete the runs finished more than `days` ago."""
    border = now() - dt.timedelta(days=days)
    old = sa.select(run_table.c.run_id).where(
        run_table.c.queued_time < border,
        run_table.c.status.notin_(ACTIVE))
    _execute(log_table.delete().where(log_table.c.run_id.in_(old)))
    result = _execute(run_table.delete().where(
        run_table.c.queued_time < border,
        run_table.c.status.notin_(ACTIVE)))
    return result.rowcount
