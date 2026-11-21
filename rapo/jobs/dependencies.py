"""Dependencies: the condition RUN_STATUS, which looks at how other runs ended.

A condition has targets (a control, a transfer, a job or a workflow), each with
a window. The condition is met when every target is: it has no run going, and
the last run that ended in the window counts as Done. The reason a condition is
not met is told in words, to be put in the message of the run.
"""

import datetime as dt

import sqlalchemy as sa

from ..database import db

from . import store
from . import workflow_store

DONE = 'D'
# The statuses of a run of a control that has not ended.
CONTROL_ACTIVE = ('I', 'W', 'S', 'P', 'F')
ACTIVE = ('I', 'S', 'W')
LIMIT = 100

config_table = sa.table('rapo_config', sa.column('control_id'),
                        sa.column('control_name'))
log_table = sa.table('rapo_log', sa.column('process_id'),
                     sa.column('control_id'),
                     sa.column('start_date', sa.DateTime),
                     sa.column('end_date', sa.DateTime), sa.column('status'),
                     sa.column('date_from', sa.DateTime))


class DependencyError(Exception):
    """A target that can not be looked at."""


def _rows(statement):
    try:
        return db.execute(statement, as_table=True)
    except sa.exc.DatabaseError as error:
        raise DependencyError(f'The runs could not be read: '
                              f'{str(error).splitlines()[0]}') from error


def names(kind):
    """Get the sorted names of everything of the type that can be waited for."""
    if kind == 'control':
        rows = _rows(sa.select(config_table.c.control_name))
        found = {row['control_name'] for row in rows}
    elif kind == 'transfer':
        from ..transfer import store as transfer_store
        found = set(transfer_store.read_files())
        found |= set(transfer_store.read_database() or {})
    elif kind == 'job':
        found = {row['job_name'] for row in store.read_jobs()}
    elif kind == 'workflow':
        found = {row['workflow_name']
                 for row in workflow_store.read_workflows()}
    else:
        raise DependencyError(f'There is no target type {kind}.')
    return sorted(found, key=str.lower)


def missing(condition, known=None):
    """Get the problems of the targets of a condition that do not exist.
    `known` is {type: names} to look in, else the names are read."""
    if not isinstance(condition, dict) or condition.get('kind') != 'RUN_STATUS':
        return []
    known = dict(known or {})
    problems = []
    for target in condition.get('targets') or []:
        kind, name = target.get('type'), target.get('name')
        if kind not in known:
            try:
                known[kind] = set(names(kind))
            except DependencyError as error:
                problems.append(str(error))
                known[kind] = None
        if known[kind] is not None and name not in known[kind]:
            problems.append(f'The {kind} {name} does not exist.')
    return problems


def _run(status, start, end, data_date=None):
    return {'status': status, 'start': start, 'end': end,
            'data_date': data_date}


def runs(kind, name):
    """Get the newest runs of the target, newest first, as dicts of status,
    start, end and (a control only) data_date."""
    if kind == 'control':
        control = _rows(sa.select(config_table.c.control_id).where(
            config_table.c.control_name == name))
        if not control:
            raise DependencyError(f'The control {name} does not exist.')
        rows = _rows(sa.select(log_table).where(
            log_table.c.control_id == control[0]['control_id']).order_by(
            log_table.c.process_id.desc()).limit(LIMIT))
        return [_run(row['status'], row['start_date'], row['end_date'],
                     row['date_from']) for row in rows]
    if kind == 'transfer':
        from ..transfer import store as transfer_store
        table = transfer_store.run_table
        column = table.c.param_name
    elif kind == 'job':
        table, column = store.run_table, store.run_table.c.job_name
    elif kind == 'workflow':
        table = workflow_store.run_table
        column = table.c.workflow_name
    else:
        raise DependencyError(f'There is no target type {kind}.')
    rows = _rows(sa.select(table).where(column == name).order_by(
        table.c.run_id.desc()).limit(LIMIT))
    return [_run(row['status'], row['start_time'], row['end_time'])
            for row in rows]


def since_of(kind, name):
    """Get the moment after which the runs count for `since_last_run` of a
    job or a workflow: the end of its last Done run, else its creation."""
    if kind == 'job':
        run_table, owner_table = store.run_table, store.job_table
        key, column = run_table.c.job_name, owner_table.c.job_name
    else:
        run_table, owner_table = (workflow_store.run_table,
                                  workflow_store.workflow_table)
        key, column = (run_table.c.workflow_name,
                       owner_table.c.workflow_name)
    rows = _rows(sa.select(sa.func.max(run_table.c.end_time).label('moment'))
                 .where(key == name, run_table.c.status == DONE))
    if rows and rows[0]['moment']:
        return rows[0]['moment']
    rows = _rows(sa.select(owner_table.c.created_date).where(column == name))
    return rows[0]['created_date'] if rows else None


def _when(moment):
    return moment.strftime('%Y-%m-%d %H:%M') if moment else 'never'


def _window_text(window):
    kind = window.get('kind')
    if kind == 'hours':
        return f'in the last {window.get("hours")} hours'
    if kind == 'since_last_run':
        return 'since the last successful run'
    if kind == 'data_date':
        return 'for the data of the day'
    return 'today'


def _target(target, now, since):
    """Get '' when the target is met, else why it is not."""
    kind, name = target['type'], target['name']
    window = target.get('window') or {'kind': 'today'}
    label = f'{kind} {name}'
    found = runs(kind, name)
    active = ACTIVE + CONTROL_ACTIVE if kind == 'control' else ACTIVE
    if any(run['status'] in active for run in found):
        return f'the {label} is running'
    finished = [run for run in found if run['end'] or run['start']]
    finished.sort(key=lambda run: run['end'] or run['start'], reverse=True)
    mode = window.get('kind')
    if mode == 'data_date':
        day = (now - dt.timedelta(days=window.get('days_ago', 0))).date()
        finished = [run for run in finished
                    if run['data_date'] and run['data_date'].date() == day]
        if not finished:
            return f'the {label} has no run for the data of {day}'
    elif not finished:
        return f'the {label} has not run {_window_text(window)}'
    last = finished[0]
    moment = last['end'] or last['start']
    if mode == 'today' and moment.date() != now.date():
        return f'the {label} has not ended today (last at {_when(moment)})'
    if mode == 'hours' and moment < now - dt.timedelta(
            hours=window.get('hours', 1)):
        return (f'the {label} has not ended in the last '
                f'{window.get("hours")} hours (last at {_when(moment)})')
    if mode == 'since_last_run' and since and moment <= since:
        return (f'the {label} has not ended since the last successful run '
                f'({_when(since)})')
    if last['status'] != DONE:
        return (f'the last run of the {label} did not end well '
                f'(at {_when(moment)})')
    return ''


def evaluate(condition, now=None, since=None):
    """Tell whether a RUN_STATUS condition is met: (met, why not). `since` is
    the lower bound of the window since_last_run (see `since_of`).
    DependencyError is raised when a target does not exist."""
    now = now or dt.datetime.now()
    reasons = [why for why in (_target(target, now, since)
                               for target in condition.get('targets') or [])
               if why]
    return (not reasons, '; '.join(reasons))


def check(condition, owner=None, now=None):
    """Evaluate a RUN_STATUS condition of `owner` ('job' or 'workflow', name):
    (met, why not). The lower bound of since_last_run is read for the owner."""
    since = None
    if owner and any((target.get('window') or {}).get('kind')
                     == 'since_last_run'
                     for target in condition.get('targets') or []):
        since = since_of(*owner)
    return evaluate(condition, now, since)
