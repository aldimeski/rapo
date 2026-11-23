"""Contains the storage of the groups of file transfers.

A group is a named list of params (`rapo_transfer_group_member`, in the order
they are run) with a schedule of its own (`rapo_transfer_group`). A run of a
group is a row of `rapo_transfer_group_run`, with a row of
`rapo_transfer_group_run_step` for each param; every step is also a run of the
param in `rapo_transfer_run` (its `group_run_id` says which run of the group
started it), so the log of a transfer is seen with the transfer and with the
group.
"""

import datetime as dt
import re

import sqlalchemy as sa

from ..core import schedule as core_schedule
from ..database import db
from ..logger import logger

from . import store as transfers
from .store import StoreError, now, user

NAME_PATTERN = transfers.NAME_PATTERN
EMAIL_PATTERN = re.compile(r'^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$')
# Statuses of a run of a group and of a step.
INITIATED, RUNNING, DONE, ERROR, CANCELED = 'I', 'S', 'D', 'E', 'C'
ACTIVE = (INITIATED, RUNNING)
WAITING = 'W'
# Triggers of a run.
SCHEDULE, MANUAL = 'schedule', 'manual'
# What a group does when it is started while its last run still goes.
SKIP, QUEUE, ALLOW = 'skip', 'queue', 'allow'
OVERLAPS = (SKIP, QUEUE, ALLOW)
# Left unchanged by save_group.
KEEP = object()
# A line of the log of a run that stands for the log of a transfer run.
LOG_MARK = '@@transfer-run:{}@@'
LOG_MARK_PATTERN = re.compile(r'^@@transfer-run:(\d+)@@$')
LOG_INDENT = '    | '

metadata = sa.MetaData()
group_table = sa.Table(
    'rapo_transfer_group', metadata,
    sa.Column('group_id', sa.Integer, primary_key=True),
    sa.Column('group_name', sa.String(128), nullable=False, unique=True),
    sa.Column('description', sa.String(500)),
    sa.Column('schedule_config', sa.String(4000)),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('notify_email', sa.String(500)),
    sa.Column('overlap', sa.String(8), nullable=False),
    sa.Column('created_date', sa.DateTime),
    sa.Column('created_by', sa.String(256)),
    sa.Column('updated_date', sa.DateTime),
    sa.Column('updated_by', sa.String(256)))
member_table = sa.Table(
    'rapo_transfer_group_member', metadata,
    sa.Column('group_id', sa.Integer, primary_key=True),
    sa.Column('position', sa.Integer, primary_key=True),
    sa.Column('param_name', sa.String(128), nullable=False))
run_table = sa.Table(
    'rapo_transfer_group_run', metadata,
    sa.Column('run_id', sa.Integer, primary_key=True),
    sa.Column('group_id', sa.Integer, nullable=False),
    sa.Column('group_name', sa.String(128), nullable=False),
    sa.Column('trigger_type', sa.String(16), nullable=False),
    sa.Column('scheduled_time', sa.DateTime),
    sa.Column('queued_time', sa.DateTime),
    sa.Column('start_time', sa.DateTime),
    sa.Column('end_time', sa.DateTime),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('step_no', sa.Integer),
    sa.Column('steps_total', sa.Integer),
    sa.Column('message', sa.String(4000)),
    sa.Column('run_log', sa.Text),
    sa.Column('runner', sa.String(255)),
    sa.Column('requested_by', sa.String(256)),
    sa.Column('mail_status', sa.String(200)))
step_table = sa.Table(
    'rapo_transfer_group_run_step', metadata,
    sa.Column('run_id', sa.Integer, primary_key=True),
    sa.Column('step_no', sa.Integer, primary_key=True),
    sa.Column('param_name', sa.String(128), nullable=False),
    sa.Column('transfer_run_id', sa.Integer),
    sa.Column('status', sa.String(1), nullable=False),
    sa.Column('start_time', sa.DateTime),
    sa.Column('end_time', sa.DateTime),
    sa.Column('message', sa.String(4000)))

RUN_COLUMNS = [column for column in run_table.c if column.name != 'run_log']

_warned = set()


def check_name(name):
    """Return the name when it is fit for a group, else raise StoreError."""
    name = str(name or '').strip()
    if not NAME_PATTERN.match(name):
        raise StoreError('A group name has 1-128 letters, digits, dots, '
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
    """Return the schedule JSON, None when the group is not scheduled; raise
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
        raise StoreError('The transfer group tables are missing: run '
                         'migrations/v0.8.7.5/upgrade.sql.', 503)


def _execute(statement, **kwargs):
    try:
        return db.execute(statement, **kwargs)
    except sa.exc.DatabaseError as error:
        _missing(error)
        raise


def _insert_many(table, rows):
    """Insert the rows, one statement each (a group has a few)."""
    for row in rows:
        _execute(table.insert().values(**row))


def _text(value):
    return value.read() if hasattr(value, 'read') else value


# The groups

def _with_members(rows):
    """Add `members` (the names of the params in their order) to rows of
    groups."""
    if not rows:
        return rows
    found = {}
    for row in _execute(sa.select(member_table).order_by(
            member_table.c.group_id, member_table.c.position),
            as_table=True):
        found.setdefault(row['group_id'], []).append(row['param_name'])
    for row in rows:
        row['members'] = found.get(row['group_id'], [])
    return rows


def read_groups():
    """Get every group with its members, by name."""
    rows = _execute(sa.select(group_table).order_by(
        group_table.c.group_name), as_table=True)
    return _with_members(rows)


def read_group(name):
    """Get the group with its members, None when there is none."""
    row = _execute(sa.select(group_table).where(
        group_table.c.group_name == str(name or '')), as_dict=True)
    return _with_members([row])[0] if row else None


def read_group_by_id(group_id):
    """Get the group with its members, None when there is none."""
    row = _execute(sa.select(group_table).where(
        group_table.c.group_id == group_id), as_dict=True)
    return _with_members([row])[0] if row else None


def get_group(name):
    """Get the group, raising StoreError 404 when there is none."""
    row = read_group(check_name(name))
    if not row:
        raise StoreError(f'Group {name} not found.', 404)
    return row


def read_scheduled():
    """Get {group_id: {group_name, config, schedule, enabled}} of the groups
    that have a schedule that can be read; empty when the tables are
    missing."""
    try:
        rows = _execute(sa.select(
            group_table.c.group_id, group_table.c.group_name,
            group_table.c.schedule_config, group_table.c.status).where(
            group_table.c.schedule_config.isnot(None)).order_by(
            group_table.c.group_id), as_table=True)
    except StoreError as error:
        if error.status != 503:
            raise
        if 'tables' not in _warned:
            _warned.add('tables')
            logger.warning(str(error))
        return {}
    found = {}
    for row in rows:
        try:
            units = core_schedule.parse(row['schedule_config'])
        except Exception:
            logger.warning(f'Group {row["group_name"]} has an invalid '
                           'schedule')
            continue
        if units:
            found[row['group_id']] = {
                'group_name': row['group_name'],
                'config': row['schedule_config'], 'schedule': units,
                'enabled': row['status'] == 'Y'}
    return found


def read_membership():
    """Get {param name: [{group_name, group_id, position}]}, by the order of
    the groups; empty when the tables are missing."""
    try:
        rows = _execute(sa.select(
            member_table.c.group_id, member_table.c.position,
            member_table.c.param_name, group_table.c.group_name).where(
            member_table.c.group_id == group_table.c.group_id).order_by(
            group_table.c.group_name, member_table.c.position),
            as_table=True)
    except StoreError as error:
        if error.status != 503:
            raise
        return {}
    found = {}
    for row in rows:
        found.setdefault(row['param_name'], []).append({
            'group_name': row['group_name'], 'group_id': row['group_id'],
            'position': row['position']})
    return found


def check_members(members):
    """Return the names of the params of a group as a list; raise
    StoreError when there is none, when one is given twice or does not
    exist."""
    if not isinstance(members, (list, tuple)) or not members:
        raise StoreError('A group has at least one transfer.')
    names = [transfers.check_name(item) for item in members]
    where = {}
    for number, name in enumerate(names, 1):
        where.setdefault(name, []).append(number)
    repeated = [f'{name} is repeated, at positions '
                f'{", ".join(str(number) for number in numbers)}'
                for name, numbers in where.items() if len(numbers) > 1]
    if repeated:
        raise StoreError('A transfer is in a group once: ' +
                         '; '.join(repeated) + '.', 422)
    missing = [name for name in names if not transfers.exists(name)]
    if missing:
        raise StoreError('There is no transfer ' + ', '.join(missing) + '.',
                         422)
    return names


def _write_members(group_id, names):
    _execute(member_table.delete().where(member_table.c.group_id == group_id))
    if names:
        _insert_many(member_table, [
            {'group_id': group_id, 'position': number, 'param_name': name}
            for number, name in enumerate(names, 1)])


def save_group(name, members=KEEP, description=KEEP, notify_email=KEEP,
               schedule_config=KEEP, status=KEEP, overlap=KEEP,
               create=False):
    """Save a group: create it, or change it. `members` is the list of the
    names of the params in the order they are run (a new group needs it);
    what is not given stays as it was (a new group is on, skips an
    overlapping start and has no description, address or schedule).
    `create` refuses an existing name. Returns the saved group."""
    name = check_name(name)
    old = read_group(name)
    if old and create:
        raise StoreError(f'Group {name} already exists.', 409)
    if not old and members is KEEP:
        raise StoreError('A group has at least one transfer.')
    if members is not KEEP:
        members = check_members(members)
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
    if overlap is not KEEP and overlap not in OVERLAPS:
        raise StoreError(f'overlap is one of {", ".join(OVERLAPS)}.')
    moment = now()
    values = {key: value for key, value in (
        ('description', description), ('notify_email', notify_email),
        ('schedule_config', schedule_config), ('status', status),
        ('overlap', overlap)) if value is not KEEP}
    if old:
        _execute(group_table.update().where(
            group_table.c.group_id == old['group_id']).values(
            updated_date=moment, updated_by=user(), **values))
        group_id = old['group_id']
    else:
        values.setdefault('status', 'Y')
        values.setdefault('overlap', SKIP)
        _execute(group_table.insert().values(
            group_name=name, created_date=moment, created_by=user(),
            updated_date=moment, updated_by=user(), **values))
        group_id = read_group(name)['group_id']
    if members is not KEEP:
        _write_members(group_id, members)
    logger.info(f'Transfer group {name} saved')
    return read_group(name)


def write_schedule(name, schedule_config):
    """Save the schedule of a group (empty: not scheduled)."""
    get_group(name)
    return save_group(name, schedule_config=schedule_config)


def set_status(name, enabled):
    """Switch a group on or off."""
    group = get_group(name)
    _execute(group_table.update().where(
        group_table.c.group_id == group['group_id']).values(
        status='Y' if enabled else 'N', updated_date=now(),
        updated_by=user()))
    logger.info(f'Transfer group {name} switched '
                f'{"on" if enabled else "off"}')


def delete_group(name):
    """Delete a group; its runs stay in the history until the retention
    deletes them. A group with a run waiting or running is refused."""
    group = get_group(name)
    if has_active(group['group_id']):
        raise StoreError(f'Group {name} is running or waiting.', 409)
    _execute(member_table.delete().where(
        member_table.c.group_id == group['group_id']))
    _execute(group_table.delete().where(
        group_table.c.group_id == group['group_id']))
    logger.info(f'Transfer group {name} deleted')


def remove_param(param_name):
    """Take the param out of every group that has it, the others moving up.
    A group left without a transfer is switched off. Returns [{group_name,
    members (left), switched_off}]. A silent no-op while the tables are
    missing."""
    try:
        groups = [group for group in read_groups()
                  if param_name in group['members']]
    except StoreError as error:
        if error.status == 503:
            return []
        raise
    changed = []
    for group in groups:
        left = [name for name in group['members'] if name != param_name]
        _write_members(group['group_id'], left)
        values = {'updated_date': now(), 'updated_by': user()}
        if not left:
            values['status'] = 'N'
        _execute(group_table.update().where(
            group_table.c.group_id == group['group_id']).values(**values))
        changed.append({'group_name': group['group_name'],
                        'members': len(left), 'switched_off': not left})
        logger.info(f'Transfer {param_name} taken out of group '
                    f'{group["group_name"]}')
    return changed


# The runs

def add_run(group, trigger, scheduled_time=None, requested_by=None,
            runner=None):
    """Record an initiated run of the group (a row of read_group) with a
    waiting row for each of its steps, get its ID."""
    members = list(group['members'])
    result = _execute(run_table.insert().values(
        group_id=group['group_id'], group_name=group['group_name'],
        trigger_type=trigger, scheduled_time=scheduled_time,
        queued_time=now(), status=INITIATED, steps_total=len(members),
        runner=runner, requested_by=requested_by))
    run_id = result.inserted_primary_key[0]
    if run_id is None:
        run_id = _execute(sa.select(sa.func.max(run_table.c.run_id)).where(
            run_table.c.group_id == group['group_id']), as_scalar=True)
    run_id = int(run_id)
    if members:
        _insert_many(step_table, [
            {'run_id': run_id, 'step_no': number, 'param_name': name,
             'status': WAITING}
            for number, name in enumerate(members, 1)])
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


def read_steps(run_id):
    """Get the steps of a run in their order, each with the run of the
    transfer it started (`transfer`: status, transferred, failed), if any."""
    steps = _execute(sa.select(step_table).where(
        step_table.c.run_id == run_id).order_by(step_table.c.step_no),
        as_table=True)
    ids = [step['transfer_run_id'] for step in steps
           if step['transfer_run_id']]
    runs = {}
    if ids:
        for row in _execute(sa.select(
                transfers.run_table.c.run_id, transfers.run_table.c.status,
                transfers.run_table.c.transferred,
                transfers.run_table.c.failed,
                transfers.run_table.c.start_time,
                transfers.run_table.c.end_time).where(
                transfers.run_table.c.run_id.in_(ids)), as_table=True):
            runs[row['run_id']] = row
    for step in steps:
        step['transfer'] = runs.get(step['transfer_run_id'])
    return steps


def read_runs(name=None, limit=100):
    """Get the runs of the groups, latest first, without their log."""
    statement = sa.select(*RUN_COLUMNS).order_by(run_table.c.run_id.desc())
    if name:
        statement = statement.where(run_table.c.group_name == name)
    return _execute(statement.limit(limit), as_table=True)


def read_run(run_id):
    """Get a run with its steps (`steps`) and its whole log (`run_log`):
    what the group did, and under each step the log of the run of the
    transfer. None when there is none."""
    row = _execute(sa.select(run_table).where(run_table.c.run_id == run_id),
                   as_dict=True)
    if not row:
        return row
    row['steps'] = read_steps(run_id)
    row['own_log'] = _text(row.get('run_log')) or ''
    row['run_log'] = compose_log(row['own_log'])
    return row


def compose_log(own):
    """Get the log of a run of a group with the log of the transfer run in
    place of each line that stands for it (`LOG_MARK`), indented."""
    lines = []
    for line in own.split('\n'):
        found = LOG_MARK_PATTERN.match(line)
        if not found:
            lines.append(line)
            continue
        try:
            shown = transfers.read_run(int(found.group(1)))
        except StoreError:
            shown = None
        text = (shown or {}).get('run_log') or ''
        lines += [LOG_INDENT + item for item in text.split('\n') if item]
    return '\n'.join(lines)


def has_active(group_id):
    """Tell whether the group has a run waiting or running."""
    return bool(_execute(sa.select(sa.func.count()).select_from(
        run_table).where(run_table.c.group_id == group_id,
                         run_table.c.status.in_(ACTIVE)), as_scalar=True))


def read_last_runs():
    """Get {group name: last run} of every group that ran; empty when the
    tables are missing."""
    inner = sa.select(sa.func.max(run_table.c.run_id)).group_by(
        run_table.c.group_id)
    try:
        rows = _execute(sa.select(*RUN_COLUMNS).where(
            run_table.c.run_id.in_(inner)), as_table=True)
    except StoreError as error:
        if error.status != 503:
            raise
        return {}
    return {row['group_name']: row for row in rows}


def sweep(runner):
    """Finish the runs of this server left active by a crash, and their
    steps. Returns the number of runs."""
    moment = now()
    ids = [row['run_id'] for row in _execute(sa.select(
        run_table.c.run_id).where(run_table.c.runner == runner,
                                  run_table.c.status.in_(ACTIVE)),
        as_table=True)]
    if not ids:
        return 0
    _execute(step_table.update().where(
        step_table.c.run_id.in_(ids),
        step_table.c.status.in_((WAITING, INITIATED, RUNNING))).values(
        status=CANCELED, end_time=moment,
        message='The server stopped unexpectedly.'))
    _execute(run_table.update().where(run_table.c.run_id.in_(ids)).values(
        status=ERROR, end_time=moment,
        message='Group stopped because the server stopped unexpectedly.'))
    return len(ids)


def clean_runs(days):
    """Delete the runs of groups finished more than `days` ago with their
    steps. Returns the number of runs."""
    border = now() - dt.timedelta(days=days)
    old = sa.select(run_table.c.run_id).where(
        run_table.c.queued_time < border, run_table.c.status.notin_(ACTIVE))
    _execute(step_table.delete().where(step_table.c.run_id.in_(old)))
    return _execute(run_table.delete().where(
        run_table.c.queued_time < border,
        run_table.c.status.notin_(ACTIVE))).rowcount
