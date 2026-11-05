"""Contains the storage of the jobs.

A job is a row of `rapo_job` (its definition is one JSON document, its
schedule the `schedule_config` JSON of a control) and every save of it adds a
row to `rapo_job_version`. A run is a row of `rapo_job_run`, its log lines
rows of `rapo_job_log`, and what the job scheduler did (fires, manual runs,
missed fires) rows of `rapo_job_event`.
"""

import datetime as dt
import json
import re

import sqlalchemy as sa

from ..core import schedule as core_schedule
from ..database import db, CLIENT
from ..logger import logger

from . import definition as definitions

NAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
EMAIL_PATTERN = re.compile(r'^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$')
# Statuses of a run.
INITIATED, RUNNING, DONE, ERROR, CANCELED = 'I', 'S', 'D', 'E', 'C'
ACTIVE = (INITIATED, RUNNING)
# Triggers of a run.
SCHEDULE, MANUAL = 'schedule', 'manual'
# Left unchanged by save_job.
KEEP = object()

metadata = sa.MetaData()
job_table = sa.Table(
    'rapo_job', metadata,
    sa.Column('job_id', sa.Integer, primary_key=True),
    sa.Column('job_name', sa.String(128), nullable=False, unique=True),
    sa.Column('description', sa.String(500)),
    sa.Column('job_type', sa.String(16), nullable=False),
    sa.Column('definition', sa.Text, nullable=False),
    sa.Column('schedule_config', sa.String(4000)),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('notify_email', sa.String(500)),
    sa.Column('version', sa.Integer, nullable=False),
    sa.Column('created_date', sa.DateTime),
    sa.Column('created_by', sa.String(256)),
    sa.Column('updated_date', sa.DateTime),
    sa.Column('updated_by', sa.String(256)))
version_table = sa.Table(
    'rapo_job_version', metadata,
    sa.Column('job_id', sa.Integer, primary_key=True),
    sa.Column('version', sa.Integer, primary_key=True),
    sa.Column('definition', sa.Text, nullable=False),
    sa.Column('schedule_config', sa.String(4000)),
    sa.Column('updated_date', sa.DateTime),
    sa.Column('updated_by', sa.String(256)))
run_table = sa.Table(
    'rapo_job_run', metadata,
    sa.Column('run_id', sa.Integer, primary_key=True),
    sa.Column('job_id', sa.Integer, nullable=False),
    sa.Column('job_name', sa.String(128), nullable=False),
    sa.Column('trigger_type', sa.String(16), nullable=False),
    sa.Column('scheduled_time', sa.DateTime),
    sa.Column('queued_time', sa.DateTime),
    sa.Column('start_time', sa.DateTime),
    sa.Column('end_time', sa.DateTime),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('stage', sa.String(10)),
    sa.Column('condition_value', sa.String(100)),
    sa.Column('exit_code', sa.Integer),
    sa.Column('message', sa.String(4000)),
    sa.Column('runner', sa.String(255)),
    sa.Column('requested_by', sa.String(256)),
    sa.Column('definition_version', sa.Integer),
    sa.Column('log_lines', sa.Integer),
    sa.Column('mail_status', sa.String(200)),
    sa.Column('workflow_run_id', sa.Integer))
log_table = sa.Table(
    'rapo_job_log', metadata,
    sa.Column('log_id', sa.Integer, primary_key=True),
    sa.Column('run_id', sa.Integer, nullable=False),
    sa.Column('job_id', sa.Integer, nullable=False),
    sa.Column('line_no', sa.Integer, nullable=False),
    sa.Column('log_time', sa.DateTime),
    sa.Column('log_level', sa.String(8)),
    sa.Column('stream', sa.String(4)),
    sa.Column('message', sa.String(4000)))
event_table = sa.Table(
    'rapo_job_event', metadata,
    sa.Column('event_id', sa.Integer, primary_key=True),
    sa.Column('job_id', sa.Integer),
    sa.Column('trigger_type', sa.String(16)),
    sa.Column('event_type', sa.String(10)),
    sa.Column('scheduled_time', sa.DateTime),
    sa.Column('event_time', sa.DateTime),
    sa.Column('updated', sa.DateTime),
    sa.Column('run_id', sa.Integer),
    sa.Column('message', sa.String(4000)),
    sa.Column('runner', sa.String(255)))

JOB_COLUMNS = [column for column in job_table.c if column.name != 'definition']


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


def check_name(name):
    """Return the name when it is fit for a job, else raise StoreError."""
    name = str(name or '').strip()
    if not NAME_PATTERN.match(name):
        raise StoreError('A job name has 1-128 letters, digits, dots, '
                         'hyphens or underscores, and starts with a letter '
                         'or digit.')
    return name


def check_email(value):
    """Return the addresses (comma or semicolon separated) as a text of
    comma separated ones, None when empty; raise StoreError for a bad one."""
    items = [item.strip() for item in re.split(r'[;,]', value or '')
             if item.strip()]
    for item in items:
        if not EMAIL_PATTERN.match(item):
            raise StoreError(f'"{item}" is not an e-mail address.')
    text = ', '.join(items)
    if len(text) > 500:
        raise StoreError('The e-mail addresses are more than 500 '
                         'characters.')
    return text or None


def check_schedule(schedule_config):
    """Return the schedule JSON, None when the job is not scheduled; raise
    StoreError when it can not be read."""
    if not schedule_config:
        return None
    try:
        core_schedule.parse(schedule_config)
    except (ValueError, TypeError) as error:
        raise StoreError(f'The schedule can not be read: {error}')
    return schedule_config


def _missing(error):
    text = str(error)
    if 'ORA-00942' in text or 'no such table' in text:
        raise StoreError('The job tables are missing: run '
                         'migrations/v0.9.0/upgrade.sql.', 503)


def _execute(statement, **kwargs):
    try:
        return db.execute(statement, **kwargs)
    except sa.exc.DatabaseError as error:
        _missing(error)
        raise


def _text(value):
    return value.read() if hasattr(value, 'read') else value


# The jobs

def read_jobs():
    """Get every job without its definition, by name."""
    statement = sa.select(*JOB_COLUMNS).order_by(job_table.c.job_name)
    return _execute(statement, as_table=True)


def read_job(name):
    """Get the job with its definition (JSON text), None when there is
    none."""
    row = _execute(sa.select(job_table).where(
        job_table.c.job_name == str(name or '')), as_dict=True)
    if row:
        row['definition'] = _text(row['definition'])
    return row


def schedule_json(units):
    """Get the stored JSON of schedule units ({mday, wday, hour, min, sec}),
    None when none is set."""
    if not units or not any(units.values()):
        return None
    return json.dumps({unit: units.get(unit)
                       for unit in core_schedule.UNITS})


def read_job_by_id(job_id):
    """Get the job with its definition (JSON text), None when there is
    none."""
    row = _execute(sa.select(job_table).where(
        job_table.c.job_id == job_id), as_dict=True)
    if row:
        row['definition'] = _text(row['definition'])
    return row


def read_scheduled():
    """Get {job_id: {job_name, schedule}} of the jobs that are on and have
    a schedule that can be read, `schedule` being the parsed units."""
    rows = _execute(sa.select(
        job_table.c.job_id, job_table.c.job_name,
        job_table.c.schedule_config).where(
        job_table.c.status == 'Y',
        job_table.c.schedule_config.isnot(None)).order_by(
        job_table.c.job_id), as_table=True)
    found = {}
    for row in rows:
        try:
            units = core_schedule.parse(row['schedule_config'])
        except Exception:
            logger.warning(f'Job {row["job_name"]} has an invalid schedule')
            continue
        if units:
            found[row['job_id']] = {'job_name': row['job_name'],
                                    'schedule': units}
    return found


def signature():
    """Get what changes when a job or its schedule does: the number of
    jobs, the latest change and the sum of the versions and statuses."""
    row = _execute(sa.select(
        sa.func.count().label('jobs'),
        sa.func.max(job_table.c.updated_date).label('changed'),
        sa.func.sum(job_table.c.version).label('versions'),
        sa.func.sum(sa.case((job_table.c.status == 'Y', 1),
                            else_=0)).label('on')), as_dict=True)
    return tuple(row.values())


def get_job(name):
    """Get the job, raising StoreError 404 when there is none."""
    row = read_job(check_name(name))
    if not row:
        raise StoreError(f'Job {name} not found.', 404)
    return row


def _add_version(job_id, version, definition, schedule_config, moment):
    _execute(version_table.insert().values(
        job_id=job_id, version=version, definition=definition,
        schedule_config=schedule_config, updated_date=moment,
        updated_by=user()))


def save_job(name, definition, description=KEEP, notify_email=KEEP,
             schedule_config=KEEP, status=KEEP, create=False, ctx=None):
    """Save a job: create it, or change it and keep the version before.

    The definition is checked first (`definitions.check`; `ctx` is what it
    is checked against, this server by default). `create` refuses an
    existing name. What is not given stays as it was (a new job has no
    description, address or schedule, and is on). A change of the definition
    or the schedule makes a new version. Returns the saved job.
    """
    name = check_name(name)
    try:
        checked = definitions.check(definition, ctx)
    except definitions.DefinitionError as error:
        raise StoreError(' '.join(error.problems), 422)
    text = definitions.dumps(checked)
    if description is not KEEP:
        description = (description or '').strip() or None
        if description and len(description) > 500:
            raise StoreError('The description is more than 500 characters.')
    if notify_email is not KEEP:
        notify_email = check_email(notify_email)
    if schedule_config is not KEEP:
        schedule_config = check_schedule(schedule_config)
    if status is not KEEP and status not in ('Y', 'N'):
        raise StoreError("status is 'Y' or 'N'.")
    old = read_job(name)
    if old and create:
        raise StoreError(f'Job {name} already exists.', 409)
    moment = now()
    kind = definitions.job_type(checked)
    if old:
        values = {'job_type': kind, 'definition': text,
                  'updated_date': moment, 'updated_by': user()}
        for key, value in (('description', description),
                           ('notify_email', notify_email),
                           ('schedule_config', schedule_config),
                           ('status', status)):
            if value is not KEEP:
                values[key] = value
        changed = (definitions.load(old['definition']) != checked or
                   ('schedule_config' in values and
                    values['schedule_config'] != old['schedule_config']))
        if changed:
            values['version'] = old['version'] + 1
        _execute(job_table.update().where(
            job_table.c.job_id == old['job_id']).values(**values))
        if changed:
            _add_version(old['job_id'], values['version'], text,
                         values.get('schedule_config',
                                    old['schedule_config']), moment)
    else:
        _execute(job_table.insert().values(
            job_name=name, description=None if description is KEEP
            else description, job_type=kind, definition=text,
            schedule_config=None if schedule_config is KEEP
            else schedule_config,
            status='Y' if status is KEEP else status,
            notify_email=None if notify_email is KEEP else notify_email,
            version=1, created_date=moment, created_by=user(),
            updated_date=moment, updated_by=user()))
        row = read_job(name)
        _add_version(row['job_id'], 1, text, row['schedule_config'], moment)
    logger.info(f'Job {name} saved')
    return read_job(name)


def write_schedule(name, schedule_config):
    """Save the schedule of a job (empty: not scheduled); a new version."""
    job = get_job(name)
    return save_job(name, job['definition'], schedule_config=schedule_config)


def set_status(name, enabled):
    """Switch a job on or off. Not a new version."""
    job = get_job(name)
    _execute(job_table.update().where(
        job_table.c.job_id == job['job_id']).values(
        status='Y' if enabled else 'N', updated_date=now(),
        updated_by=user()))
    logger.info(f'Job {name} switched {"on" if enabled else "off"}')


def delete_job(name):
    """Delete a job and its versions. Its runs stay in the history until
    the retention deletes them. A job with a run waiting or running is
    refused."""
    job = get_job(name)
    if has_active(job['job_id']):
        raise StoreError(f'Job {name} is running or waiting.', 409)
    _execute(version_table.delete().where(
        version_table.c.job_id == job['job_id']))
    _execute(job_table.delete().where(job_table.c.job_id == job['job_id']))
    logger.info(f'Job {name} deleted')


def read_versions(job_id):
    """Get the versions of a job, the newest first, with their
    definitions."""
    rows = _execute(sa.select(version_table).where(
        version_table.c.job_id == job_id).order_by(
        version_table.c.version.desc()), as_table=True)
    for row in rows:
        row['definition'] = _text(row['definition'])
    return rows


# The runs

def add_run(job, trigger, scheduled_time=None, requested_by=None,
            runner=None):
    """Record an initiated run of the job (a row of read_job), get its
    ID."""
    result = _execute(run_table.insert().values(
        job_id=job['job_id'], job_name=job['job_name'], trigger_type=trigger,
        scheduled_time=scheduled_time, queued_time=now(), status=INITIATED,
        runner=runner, requested_by=requested_by,
        definition_version=job['version']))
    run_id = result.inserted_primary_key[0]
    if run_id is None:
        run_id = _execute(sa.select(sa.func.max(run_table.c.run_id)).where(
            run_table.c.job_id == job['job_id']), as_scalar=True)
    return int(run_id)


def update_run(run_id, **values):
    """Change a run."""
    for key, size in (('message', 4000), ('mail_status', 200),
                      ('condition_value', 100)):
        if values.get(key):
            values[key] = str(values[key])[:size]
    _execute(run_table.update().where(
        run_table.c.run_id == run_id).values(**values))


def has_active(job_id):
    """Tell whether the job has a run waiting or running."""
    count = _execute(sa.select(sa.func.count()).select_from(run_table).where(
        run_table.c.job_id == job_id, run_table.c.status.in_(ACTIVE)),
        as_scalar=True)
    return bool(count)


def read_runs(job_id=None, limit=100):
    """Get the runs, latest first."""
    statement = sa.select(run_table).order_by(run_table.c.run_id.desc())
    if job_id is not None:
        statement = statement.where(run_table.c.job_id == job_id)
    return _execute(statement.limit(limit), as_table=True)


def read_run(run_id):
    """Get a run with its log (`run_log`, the lines joined), None when there
    is none. `log_trimmed` tells that older lines were deleted to keep the
    newest ones of the job."""
    row = _execute(sa.select(run_table).where(
        run_table.c.run_id == run_id), as_dict=True)
    if not row:
        return row
    lines = [item[0] for item in _execute(
        sa.select(log_table.c.message).where(
            log_table.c.run_id == run_id).order_by(log_table.c.line_no)
    ).fetchall()]
    row['run_log'] = '\n'.join(lines)
    row['log_trimmed'] = bool(row.get('log_lines')) and \
        len(lines) < row['log_lines']
    return row


def read_last_runs():
    """Get {job_id: last run} of every job that ran."""
    inner = sa.select(sa.func.max(run_table.c.run_id)).group_by(
        run_table.c.job_id)
    rows = _execute(sa.select(run_table).where(
        run_table.c.run_id.in_(inner)), as_table=True)
    return {row['job_id']: row for row in rows}


def add_log_lines(run_id, job_id, lines, limit, start=1, trim=True):
    """Write lines of the log of a run, being (epoch, level, stream, text)
    and numbered from `start`, then keep only the newest `limit` lines of
    the job, those of older runs go first (`trim` false leaves that to the
    last call)."""
    if lines:
        rows = [{'run_id': run_id, 'job_id': job_id, 'line_no': number,
                 'log_time': dt.datetime.fromtimestamp(moment).replace(
                     microsecond=0),
                 'log_level': level[:8], 'stream': stream[:4],
                 'message': text[:4000]}
                for number, (moment, level, stream, text)
                in enumerate(lines, start)]
        try:
            with db.engine.begin() as connection:
                connection.execute(log_table.insert(), rows)
        except sa.exc.DatabaseError as error:
            _missing(error)
            raise
    if trim:
        trim_log(job_id, limit)


def trim_log(job_id, limit):
    """Delete all but the newest `limit` log lines of a job."""
    keep = sa.select(log_table.c.log_id).where(
        log_table.c.job_id == job_id).order_by(
        log_table.c.log_id.desc()).limit(limit)
    _execute(log_table.delete().where(
        log_table.c.job_id == job_id, log_table.c.log_id.notin_(keep)))


def sweep(runner):
    """Finish the runs of this server left active by a crash. Returns their
    number."""
    try:
        result = db.execute(run_table.update().where(
            run_table.c.runner == runner,
            run_table.c.status.in_(ACTIVE)).values(
            status=ERROR, end_time=now(),
            message='The job stopped because the server stopped '
                    'unexpectedly.'))
    except sa.exc.DatabaseError as error:
        # The tables are missing: reported where they are used.
        _missing_quiet(error)
        return 0
    return result.rowcount


def _missing_quiet(error):
    text = str(error)
    if not ('ORA-00942' in text or 'no such table' in text):
        raise error


def clean_runs(days):
    """Delete the runs queued more than `days` ago and finished, with their
    log lines. Returns the number of runs."""
    border = now() - dt.timedelta(days=days)
    old = sa.select(run_table.c.run_id).where(
        run_table.c.queued_time < border,
        run_table.c.status.notin_(ACTIVE))
    _execute(log_table.delete().where(log_table.c.run_id.in_(old)))
    result = _execute(run_table.delete().where(
        run_table.c.queued_time < border,
        run_table.c.status.notin_(ACTIVE)))
    return result.rowcount


# The events of the job scheduler

def add_event(job_id, trigger_type, event_type, scheduled_time=None,
              run_id=None, message=None, runner=None):
    """Record what the job scheduler did, get the ID of the event. An event
    moves through types while a request is served (`update_event`)."""
    moment = now()
    result = _execute(event_table.insert().values(
        job_id=job_id, trigger_type=trigger_type, event_type=event_type,
        scheduled_time=scheduled_time, event_time=moment, updated=moment,
        run_id=run_id, message=(message or '')[:4000] or None,
        runner=runner))
    event_id = result.inserted_primary_key[0]
    if event_id is None:
        event_id = _execute(sa.select(sa.func.max(event_table.c.event_id)),
                            as_scalar=True)
    return int(event_id)


def update_event(event_id, **values):
    """Change an event."""
    if values.get('message'):
        values['message'] = str(values['message'])[:4000]
    _execute(event_table.update().where(
        event_table.c.event_id == event_id).values(**values,
                                                   updated=now()))


def read_events(limit=200, job_id=None, event_type=None, trigger_type=None,
                since=None):
    """Get the events, latest first, with the name of their job (empty for
    a job deleted since)."""
    event = event_table
    statement = sa.select(
        event, job_table.c.job_name).select_from(event.outerjoin(
            job_table, event.c.job_id == job_table.c.job_id)).order_by(
        event.c.event_id.desc())
    if job_id is not None:
        statement = statement.where(event.c.job_id == job_id)
    if event_type:
        statement = statement.where(event.c.event_type == event_type)
    if trigger_type:
        statement = statement.where(event.c.trigger_type == trigger_type)
    if since is not None:
        statement = statement.where(event.c.event_time >= since)
    return _execute(statement.limit(limit), as_table=True)


def clean_events(days):
    """Delete the events older than `days`. Returns their number."""
    border = now() - dt.timedelta(days=days)
    result = _execute(event_table.delete().where(
        event_table.c.event_time < border))
    return result.rowcount
