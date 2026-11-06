"""Contains the storage of the workflows.

A workflow is a row of `rapo_workflow` (its definition is one JSON document,
its schedule the `schedule_config` JSON of a control) and every save of it adds
a row to `rapo_workflow_version`. A run is a row of `rapo_workflow_run`, with
a row for each of its steps (`rapo_workflow_run_step`) and a log
(`rapo_workflow_log`). The runs of the jobs that are its steps are in
`rapo_job_run`, which names the workflow run.
"""

import datetime as dt
import json

import sqlalchemy as sa

from ..database import db
from ..logger import logger

from . import definition as definitions
from . import store
from . import workflow as workflows
from .store import (ACTIVE, CANCELED, DONE, ERROR, INITIATED, KEEP, RUNNING,
                    StoreError, now, user)

# Statuses of a step of a run, besides those of a run: waiting for its turn,
# and skipped because an earlier step stopped the workflow.
WAITING, SKIPPED = 'W', 'K'
SCHEDULE, MANUAL = store.SCHEDULE, store.MANUAL

metadata = store.metadata
workflow_table = sa.Table(
    'rapo_workflow', metadata,
    sa.Column('workflow_id', sa.Integer, primary_key=True),
    sa.Column('workflow_name', sa.String(128), nullable=False, unique=True),
    sa.Column('description', sa.String(500)),
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
    'rapo_workflow_version', metadata,
    sa.Column('workflow_id', sa.Integer, primary_key=True),
    sa.Column('version', sa.Integer, primary_key=True),
    sa.Column('definition', sa.Text, nullable=False),
    sa.Column('schedule_config', sa.String(4000)),
    sa.Column('updated_date', sa.DateTime),
    sa.Column('updated_by', sa.String(256)))
run_table = sa.Table(
    'rapo_workflow_run', metadata,
    sa.Column('run_id', sa.Integer, primary_key=True),
    sa.Column('workflow_id', sa.Integer, nullable=False),
    sa.Column('workflow_name', sa.String(128), nullable=False),
    sa.Column('trigger_type', sa.String(16), nullable=False),
    sa.Column('scheduled_time', sa.DateTime),
    sa.Column('queued_time', sa.DateTime),
    sa.Column('start_time', sa.DateTime),
    sa.Column('end_time', sa.DateTime),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('step_no', sa.Integer),
    sa.Column('steps_total', sa.Integer),
    sa.Column('message', sa.String(4000)),
    sa.Column('runner', sa.String(255)),
    sa.Column('requested_by', sa.String(256)),
    sa.Column('definition_version', sa.Integer),
    sa.Column('log_lines', sa.Integer),
    sa.Column('mail_status', sa.String(200)))
step_table = sa.Table(
    'rapo_workflow_run_step', metadata,
    sa.Column('run_id', sa.Integer, primary_key=True),
    sa.Column('step_no', sa.Integer, primary_key=True),
    sa.Column('job_id', sa.Integer),
    sa.Column('job_name', sa.String(128), nullable=False),
    sa.Column('job_run_id', sa.Integer),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('start_time', sa.DateTime),
    sa.Column('end_time', sa.DateTime),
    sa.Column('message', sa.String(4000)))
log_table = sa.Table(
    'rapo_workflow_log', metadata,
    sa.Column('log_id', sa.Integer, primary_key=True),
    sa.Column('run_id', sa.Integer, nullable=False),
    sa.Column('workflow_id', sa.Integer, nullable=False),
    sa.Column('line_no', sa.Integer, nullable=False),
    sa.Column('log_time', sa.DateTime),
    sa.Column('log_level', sa.String(8)),
    sa.Column('message', sa.String(4000)))

WORKFLOW_COLUMNS = [column for column in workflow_table.c
                    if column.name != 'definition']

_text = store._text


def _execute(statement, **kwargs):
    """Run a statement; tables that are missing are told as the ones of the
    workflows, whose script is another one than the jobs'."""
    try:
        return store._execute(statement, **kwargs)
    except StoreError as error:
        if error.status == 503 and 'tables are missing' in str(error):
            raise StoreError('The workflow tables are missing: run '
                             'migrations/v0.10.0/upgrade.sql.', 503)
        raise


# The workflows

def read_workflows():
    """Get every workflow without its definition, by name, each with the
    names of its jobs (`steps`)."""
    rows = _execute(sa.select(workflow_table).order_by(
        workflow_table.c.workflow_name), as_table=True)
    for row in rows:
        try:
            steps = workflows.job_names(json.loads(_text(row['definition'])))
        except ValueError:
            steps = []
        row['steps'] = steps
        del row['definition']
    return rows


def read_workflow(name):
    """Get the workflow with its definition (JSON text), None when there is
    none."""
    row = _execute(sa.select(workflow_table).where(
        workflow_table.c.workflow_name == str(name or '')), as_dict=True)
    if row:
        row['definition'] = _text(row['definition'])
    return row


def read_workflow_by_id(workflow_id):
    """Get the workflow with its definition (JSON text), None when there is
    none."""
    row = _execute(sa.select(workflow_table).where(
        workflow_table.c.workflow_id == workflow_id), as_dict=True)
    if row:
        row['definition'] = _text(row['definition'])
    return row


def get_workflow(name):
    """Get the workflow, raising StoreError 404 when there is none."""
    row = read_workflow(store.check_name(name))
    if not row:
        raise StoreError(f'Workflow {name} not found.', 404)
    return row


def read_scheduled():
    """Get {workflow_id: {workflow_name, schedule}} of the workflows that
    are on and have a schedule that can be read."""
    rows = _execute(sa.select(
        workflow_table.c.workflow_id, workflow_table.c.workflow_name,
        workflow_table.c.schedule_config).where(
        workflow_table.c.status == 'Y',
        workflow_table.c.schedule_config.isnot(None)).order_by(
        workflow_table.c.workflow_id), as_table=True)
    found = {}
    for row in rows:
        try:
            units = store.core_schedule.parse(row['schedule_config'])
        except Exception:
            logger.warning(f'Workflow {row["workflow_name"]} has an '
                           'invalid schedule')
            continue
        if units:
            found[row['workflow_id']] = {
                'workflow_name': row['workflow_name'], 'schedule': units}
    return found


def signature():
    """Get what changes when a workflow or its schedule does."""
    row = _execute(sa.select(
        sa.func.count().label('workflows'),
        sa.func.max(workflow_table.c.updated_date).label('changed'),
        sa.func.sum(workflow_table.c.version).label('versions'),
        sa.func.sum(sa.case((workflow_table.c.status == 'Y', 1),
                            else_=0)).label('on')), as_dict=True)
    return tuple(row.values())


def known_jobs():
    """Get the set of the names of the jobs that exist."""
    return {row['job_name'] for row in store.read_jobs()}


def _add_version(workflow_id, version, definition, schedule_config, moment):
    _execute(version_table.insert().values(
        workflow_id=workflow_id, version=version, definition=definition,
        schedule_config=schedule_config, updated_date=moment,
        updated_by=user()))


def save_workflow(name, definition, description=KEEP, notify_email=KEEP,
                  schedule_config=KEEP, status=KEEP, create=False,
                  jobs=None):
    """Save a workflow: create it, or change it and keep the version before.

    The definition is checked first (`workflows.check`; `jobs` is the set of
    the names of the jobs that exist, by default read from the database).
    What is not given stays; a change of the definition or the schedule makes
    a new version. Returns the saved workflow.
    """
    name = store.check_name(name)
    try:
        checked = workflows.check(
            definition, known_jobs() if jobs is None else jobs)
    except definitions.DefinitionError as error:
        raise StoreError(' '.join(error.problems), 422)
    text = definitions.dumps(checked)
    if description is not KEEP:
        description = (description or '').strip() or None
        if description and len(description) > 500:
            raise StoreError('The description is more than 500 characters.')
    if notify_email is not KEEP:
        notify_email = store.check_email(notify_email)
    if schedule_config is not KEEP:
        schedule_config = store.check_schedule(schedule_config)
    if status is not KEEP and status not in ('Y', 'N'):
        raise StoreError("status is 'Y' or 'N'.")
    old = read_workflow(name)
    if old and create:
        raise StoreError(f'Workflow {name} already exists.', 409)
    moment = now()
    if old:
        values = {'definition': text, 'updated_date': moment,
                  'updated_by': user()}
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
        _execute(workflow_table.update().where(
            workflow_table.c.workflow_id == old['workflow_id']).values(
            **values))
        if changed:
            _add_version(old['workflow_id'], values['version'], text,
                         values.get('schedule_config',
                                    old['schedule_config']), moment)
    else:
        _execute(workflow_table.insert().values(
            workflow_name=name,
            description=None if description is KEEP else description,
            definition=text,
            schedule_config=None if schedule_config is KEEP
            else schedule_config,
            status='Y' if status is KEEP else status,
            notify_email=None if notify_email is KEEP else notify_email,
            version=1, created_date=moment, created_by=user(),
            updated_date=moment, updated_by=user()))
        row = read_workflow(name)
        _add_version(row['workflow_id'], 1, text, row['schedule_config'],
                     moment)
    logger.info(f'Workflow {name} saved')
    return read_workflow(name)


def write_schedule(name, schedule_config):
    """Save the schedule of a workflow (empty: not scheduled); a new
    version."""
    row = get_workflow(name)
    return save_workflow(name, row['definition'],
                         schedule_config=schedule_config)


def set_status(name, enabled):
    """Switch a workflow on or off. Not a new version."""
    row = get_workflow(name)
    _execute(workflow_table.update().where(
        workflow_table.c.workflow_id == row['workflow_id']).values(
        status='Y' if enabled else 'N', updated_date=now(),
        updated_by=user()))
    logger.info(f'Workflow {name} switched {"on" if enabled else "off"}')


def has_active(workflow_id):
    """Tell whether the workflow has a run waiting or running."""
    count = _execute(sa.select(sa.func.count()).select_from(run_table).where(
        run_table.c.workflow_id == workflow_id,
        run_table.c.status.in_(ACTIVE)), as_scalar=True)
    return bool(count)


def delete_workflow(name):
    """Delete a workflow and its versions. Its runs stay in the history until
    the retention deletes them. A workflow with a run waiting or running is
    refused."""
    row = get_workflow(name)
    if has_active(row['workflow_id']):
        raise StoreError(f'Workflow {name} is running or waiting.', 409)
    _execute(version_table.delete().where(
        version_table.c.workflow_id == row['workflow_id']))
    _execute(workflow_table.delete().where(
        workflow_table.c.workflow_id == row['workflow_id']))
    logger.info(f'Workflow {name} deleted')


def read_versions(workflow_id):
    """Get the versions of a workflow, the newest first, with their
    definitions."""
    rows = _execute(sa.select(version_table).where(
        version_table.c.workflow_id == workflow_id).order_by(
        version_table.c.version.desc()), as_table=True)
    for row in rows:
        row['definition'] = _text(row['definition'])
    return rows


# The jobs of the workflows

def workflows_using(job_name):
    """Get the names of the workflows that have the job as a step."""
    found = []
    for row in _execute(sa.select(
            workflow_table.c.workflow_name,
            workflow_table.c.definition).order_by(
            workflow_table.c.workflow_name), as_table=True):
        try:
            names = workflows.job_names(json.loads(_text(row['definition'])))
        except ValueError:
            continue
        if job_name in names:
            found.append(row['workflow_name'])
    return found


def remove_job(job_name):
    """Take the job out of the steps of every workflow that has it, a new
    version of each. A workflow left without steps is switched off. Returns
    [{workflow_name, steps (left), switched_off}]; the definition is
    written as it is: it may be one that can not be saved as it would be
    (no steps), which is the one thing that is not refused here."""
    moment = now()
    changed = []
    for row in _execute(sa.select(workflow_table).order_by(
            workflow_table.c.workflow_name), as_table=True):
        try:
            definition = json.loads(_text(row['definition']))
        except ValueError:
            continue
        definition, found = workflows.remove_job(definition, job_name)
        if not found:
            continue
        text = definitions.dumps(definition)
        version = row['version'] + 1
        empty = not definition['steps']
        values = {'definition': text, 'version': version,
                  'updated_date': moment, 'updated_by': user()}
        if empty:
            values['status'] = 'N'
        _execute(workflow_table.update().where(
            workflow_table.c.workflow_id == row['workflow_id']).values(
            **values))
        _add_version(row['workflow_id'], version, text,
                     row['schedule_config'], moment)
        changed.append({'workflow_name': row['workflow_name'],
                        'steps': len(definition['steps']),
                        'switched_off': empty})
        logger.info(f'Job {job_name} taken out of workflow '
                    f'{row["workflow_name"]}')
    return changed


# The runs

def add_run(workflow, trigger, scheduled_time=None, requested_by=None,
            runner=None):
    """Record an initiated run of the workflow (a row of read_workflow) with
    a waiting row for each of its steps, get its ID."""
    steps = workflows.job_names(json.loads(workflow['definition']))
    result = _execute(run_table.insert().values(
        workflow_id=workflow['workflow_id'],
        workflow_name=workflow['workflow_name'], trigger_type=trigger,
        scheduled_time=scheduled_time, queued_time=now(), status=INITIATED,
        step_no=None, steps_total=len(steps), runner=runner,
        requested_by=requested_by,
        definition_version=workflow['version']))
    run_id = result.inserted_primary_key[0]
    if run_id is None:
        run_id = _execute(sa.select(sa.func.max(run_table.c.run_id)).where(
            run_table.c.workflow_id == workflow['workflow_id']),
            as_scalar=True)
    run_id = int(run_id)
    jobs = {row['job_name']: row['job_id'] for row in store.read_jobs()}
    for number, name in enumerate(steps, 1):
        _execute(step_table.insert().values(
            run_id=run_id, step_no=number, job_id=jobs.get(name),
            job_name=name, status=WAITING))
    return run_id


def update_run(run_id, **values):
    """Change a run."""
    for key, size in (('message', 4000), ('mail_status', 200)):
        if values.get(key):
            values[key] = str(values[key])[:size]
    _execute(run_table.update().where(
        run_table.c.run_id == run_id).values(**values))


def update_step(run_id, step_no, **values):
    """Change a step of a run."""
    if values.get('message'):
        values['message'] = str(values['message'])[:4000]
    _execute(step_table.update().where(
        step_table.c.run_id == run_id,
        step_table.c.step_no == step_no).values(**values))


def skip_steps(run_id, first, message):
    """Mark the steps of a run from `first` on that have not started as
    skipped."""
    _execute(step_table.update().where(
        step_table.c.run_id == run_id, step_table.c.step_no >= first,
        step_table.c.status == WAITING).values(
        status=SKIPPED, message=message[:4000]))


def read_steps(run_id):
    """Get the steps of a run in order."""
    return _execute(sa.select(step_table).where(
        step_table.c.run_id == run_id).order_by(step_table.c.step_no),
        as_table=True)


def read_runs(workflow_id=None, limit=100):
    """Get the runs, latest first."""
    statement = sa.select(run_table).order_by(run_table.c.run_id.desc())
    if workflow_id is not None:
        statement = statement.where(run_table.c.workflow_id == workflow_id)
    return _execute(statement.limit(limit), as_table=True)


def read_run(run_id):
    """Get a run with its steps (`steps`) and its log (`lines`, each with its
    time, level and text, and `run_log`, their text joined), None when there
    is none."""
    row = _execute(sa.select(run_table).where(
        run_table.c.run_id == run_id), as_dict=True)
    if not row:
        return row
    row['steps'] = read_steps(run_id)
    found = _execute(
        sa.select(log_table.c.log_time, log_table.c.log_level,
                  log_table.c.message).where(
            log_table.c.run_id == run_id).order_by(log_table.c.line_no)
    ).fetchall()
    row['lines'] = [{'time': item[0], 'level': item[1], 'text': item[2]}
                    for item in found]
    row['run_log'] = '\n'.join(item['text'] for item in row['lines'])
    return row


def read_last_runs():
    """Get {workflow_id: last run} of every workflow that ran."""
    inner = sa.select(sa.func.max(run_table.c.run_id)).group_by(
        run_table.c.workflow_id)
    rows = _execute(sa.select(run_table).where(
        run_table.c.run_id.in_(inner)), as_table=True)
    return {row['workflow_id']: row for row in rows}


def add_log_line(run_id, workflow_id, number, level, text, limit=None):
    """Write one line of the log of a run, numbered from 1."""
    _execute(log_table.insert().values(
        run_id=run_id, workflow_id=workflow_id, line_no=number,
        log_time=dt.datetime.now().replace(microsecond=0),
        log_level=level[:8], message=str(text)[:4000]))


def trim_log(workflow_id, limit):
    """Delete all but the newest `limit` log lines of a workflow."""
    keep = sa.select(log_table.c.log_id).where(
        log_table.c.workflow_id == workflow_id).order_by(
        log_table.c.log_id.desc()).limit(limit)
    _execute(log_table.delete().where(
        log_table.c.workflow_id == workflow_id,
        log_table.c.log_id.notin_(keep)))


def sweep(runner):
    """Finish the runs of this server left active by a crash, with their
    steps that were not over. Returns their number."""
    try:
        runs = _execute(sa.select(run_table.c.run_id).where(
            run_table.c.runner == runner,
            run_table.c.status.in_(ACTIVE)), as_table=True)
    except StoreError:
        return 0
    message = 'The workflow stopped because the server stopped unexpectedly.'
    for row in runs:
        _execute(step_table.update().where(
            step_table.c.run_id == row['run_id'],
            step_table.c.status.in_((WAITING, INITIATED, RUNNING))).values(
            status=SKIPPED, message=message))
        update_run(row['run_id'], status=ERROR, end_time=now(),
                   message=message)
    return len(runs)


def clean_runs(days):
    """Delete the runs queued more than `days` ago and finished, with their
    steps and log lines. Returns the number of runs."""
    border = now() - dt.timedelta(days=days)
    old = sa.select(run_table.c.run_id).where(
        run_table.c.queued_time < border,
        run_table.c.status.notin_(ACTIVE))
    _execute(log_table.delete().where(log_table.c.run_id.in_(old)))
    _execute(step_table.delete().where(step_table.c.run_id.in_(old)))
    result = _execute(run_table.delete().where(
        run_table.c.queued_time < border,
        run_table.c.status.notin_(ACTIVE)))
    return result.rowcount
