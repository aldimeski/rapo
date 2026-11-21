"""Contains the execution of one run of a job.

`execute` runs the parts of a job in order, PRE, CONDITION, MAIN, POST, and
says what happened. It is meant for a process of its own (see `operate`):
the run manager stops a run that is canceled or takes too long by killing
that process and the programs it started (`processes.kill_tree`), so nothing
here counts time or waits for a cancel.

The run reports through `emit(kind, payload)`: `stage` (the part that
starts), `condition` (its value), `log` (a list of (epoch, level, stream,
text) lines). The outcome returned is a dict: `status` (`D` done, `E` error,
`C` canceled because the condition was not met), `stage` (the last part
started), `exit_code` (of MAIN), `condition_value` and `message`.
"""

import datetime as dt
import os
import re
import shutil
import tempfile
import threading
import time
import traceback

from .. import options

from . import catalogue
from . import definition as definitions
from . import dependencies
from . import processes

DONE, ERROR, CANCELED = 'D', 'E', 'C'
FLUSH_SECONDS = 0.5
FLUSH_LINES = 100
# Secrets shorter than this are not hidden in the log: they would blot out
# every letter.
MIN_SECRET = 3
FALSE_TEXTS = ('', '0', 'n', 'false')
ROWS_SHOWN = 20
WATCHDOG_INTERVAL = 2

FAILURES = {'PRE': 'PRE-STEP failed', 'CONDITION': 'CONDITION failed',
            'MAIN': 'MAIN failed', 'POST': 'POST-STEP failed'}


class TaskFailed(Exception):
    """A task that did not succeed; `exit_code` is the one of the program."""

    def __init__(self, message, exit_code=None):
        super().__init__(message)
        self.exit_code = exit_code


class RunLog:
    """The log of a run: what a program writes and what rapo says, with
    the secrets hidden and the amount kept limited."""

    def __init__(self, emit, max_bytes, secrets=()):
        self.emit = emit
        self.max_bytes = max_bytes
        self.secrets = sorted((item for item in secrets
                               if len(item) >= MIN_SECRET),
                              key=len, reverse=True)
        self.size = 0
        self.full = False
        self.lines = []
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._flusher, daemon=True)
        self.thread.start()

    def mask(self, text):
        """Get the text with every secret replaced by ***."""
        for secret in self.secrets:
            text = text.replace(secret, '***')
        return text

    def _add(self, level, stream, text):
        with self.lock:
            self.lines.append((time.time(), level, stream, self.mask(text)))
            full = len(self.lines) >= FLUSH_LINES
        if full:
            self.flush()

    def info(self, text):
        """A line of rapo."""
        self._add('INFO', 'rapo', text)

    def error(self, text):
        """A line of rapo that tells of an error."""
        self._add('ERROR', 'rapo', text)

    def output(self, stream, text):
        """A line a program wrote to `stream` ('out' or 'err'); kept until
        the limit of the run is reached."""
        with self.lock:
            if self.full:
                return
            self.size += len(text.encode('utf-8', errors='replace')) + 1
            reached = self.size > self.max_bytes
            if reached:
                self.full = True
        if reached:
            self._add('WARNING', 'rapo',
                      f'The output of the run reached '
                      f'{self.max_bytes // 1024} KB, the rest is not kept.')
            return
        self._add('INFO', stream, text)

    def flush(self):
        """Hand what was written to the one who listens."""
        with self.lock:
            lines, self.lines = self.lines, []
        if lines:
            self.emit('log', lines)

    def _flusher(self):
        while not self.stopped.wait(FLUSH_SECONDS):
            self.flush()

    def close(self):
        """Hand over the rest and stop."""
        self.stopped.set()
        self.thread.join()
        self.flush()


# The variables

def build_variables(spec, now=None):
    """Get the variables of a run: those of every run and the job's
    parameters (a manual run may give other values in `spec['parameters']`).
    Also returns the parameters apart, as {name: value}, and the secret
    values."""
    now = now or dt.datetime.now()
    variables = {'job_name': spec['job_name'], 'job_id': spec['job_id'],
                 'run_id': spec['run_id'], 'trigger': spec['trigger'],
                 'scheduled_time': spec.get('scheduled_time') or now,
                 'start_time': now}
    parameters, secrets = {}, []
    given = spec.get('parameters') or {}
    for item in spec['definition'].get('parameters') or []:
        value = str(given.get(item['name'], item.get('value', '')))
        parameters[item['name']] = value
        if item.get('secret') and value:
            secrets.append(value)
    variables.update(parameters)
    return variables, parameters, secrets


def run_environment(variables, parameters, task_env):
    """Get the environment of a program: the cleaned one, what tells it
    about the run (`RAPO_JOB_NAME`, `RAPO_JOB_ID`, `RAPO_RUN_ID`,
    `RAPO_TRIGGER`, and `RAPO_PARAM_<NAME>` of every parameter, where a
    secret is better read than put on a command line), then the `env` of
    the task."""
    extra = {'RAPO_JOB_NAME': str(variables['job_name']),
             'RAPO_JOB_ID': str(variables['job_id']),
             'RAPO_RUN_ID': str(variables['run_id']),
             'RAPO_TRIGGER': str(variables['trigger'])}
    for name, value in parameters.items():
        extra[f'RAPO_PARAM_{name.upper()}'] = value
    for name, value in (task_env or {}).items():
        extra[name] = definitions.render(value, variables)
    return processes.clean_environment(extra)


# SQL

def _truthy(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in FALSE_TEXTS
    try:
        return bool(value)
    except (TypeError, ValueError):
        return True


def run_sql(text, variables, log):
    """Run a SQL task: its statements one after another in the rapo
    database, then commit; any error rolls everything back."""
    from ..database import db
    statements = definitions.split_sql(definitions.render(text, variables))
    if not statements:
        raise TaskFailed('The SQL is empty.')
    connection = db.engine.raw_connection()
    try:
        cursor = connection.cursor()
        for number, statement in enumerate(statements, 1):
            word = definitions.first_keyword(statement).upper()
            head = ' '.join(statement.split())[:150]
            log.info(f'SQL {number} of {len(statements)}: {head}')
            try:
                cursor.execute(statement)
                if cursor.description:
                    rows = cursor.fetchmany(ROWS_SHOWN)
                    for row in rows:
                        log.output('out', ' | '.join(
                            '' if item is None else str(item)
                            for item in row))
                    log.info(f'{len(rows)} row(s) shown'
                             if len(rows) < ROWS_SHOWN else
                             f'the first {ROWS_SHOWN} rows shown')
                elif word in ('INSERT', 'UPDATE', 'DELETE', 'MERGE'):
                    log.info(f'{cursor.rowcount} row(s) {word.lower()}d'
                             if word != 'MERGE' else
                             f'{cursor.rowcount} row(s) merged')
            except Exception as error:
                connection.rollback()
                raise TaskFailed(f'SQL {number} of {len(statements)} '
                                 f'({word}): {error}')
        connection.commit()
        log.info('Committed')
    finally:
        connection.close()


def query_value(text, variables):
    """Run the query of a condition, get the first value of its first row
    (None without a row)."""
    from ..database import db
    statement = definitions.split_sql(definitions.render(text, variables))[0]
    connection = db.engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(statement)
        row = cursor.fetchone()
        connection.rollback()
        return row[0] if row else None
    except Exception as error:
        raise TaskFailed(f'The query failed: {error}')
    finally:
        connection.close()


# TRANSFER

def run_transfer(task, variables, log):
    """Run a transfer param, kept as a file or in the database, with the
    arguments of the task set over its parameters."""
    from ..transfer import engine, store
    name = task['param'].strip()
    try:
        text, place = store.read(name)
    except store.StoreError as error:
        raise TaskFailed(f'Transfer param {name}: {error}')
    arguments = task.get('arguments') or []
    for argument in arguments:
        key, _, value = argument.partition(':')
        text = store.set_parameter(text, key.strip(), definitions.render(
            value.strip(), variables))
    log.info(f'Transfer {name} ({"file" if place == store.FILE else "database"}'
             f'), {len(arguments)} argument(s)')
    result = engine.execute(name, text, log_limit=int(
        options.get('JOBS', 'log_max_lines')))
    lines = list(result.log)
    if result.db_log:
        lines = [item[2] for item in result.db_log]
    for line in lines:
        log.output('out', str(line))
    log.info(f'Transfer {name}: {result.transferred} transferred, '
             f'{result.failed} failed')
    if result.status != 'D':
        raise TaskFailed(result.message or f'Transfer {name} failed.')


# Programs

def _inside_allowed(path, ctx, label):
    if not os.path.isabs(path):
        raise TaskFailed(f'{label} {path} is not an absolute path.')
    if not definitions.inside(path, ctx.get('allowed_dirs') or []):
        raise TaskFailed(f'{label} {path} is not in a folder of '
                         '[JOBS] allowed_dirs.')
    return path


def _script(task, kind, variables, workdir, ctx, counter):
    """Get the path of the script of a task: its file, or its code written
    to a file of the run's folder."""
    if 'file' in task:
        path = definitions.render(task['file'], variables)
        _inside_allowed(path, ctx, 'The file')
        if not os.path.isfile(path):
            raise TaskFailed(f'The file {path} does not exist.')
        return path, os.path.dirname(path)
    suffix = {'PYTHON': '.py', 'PERL': '.pl', 'SHELL': '.sh',
              'BATCH': '.cmd', 'POWERSHELL': '.ps1'}[kind]
    path = os.path.join(workdir, f'task{next(counter)}{suffix}')
    code = definitions.render(task['code'], variables)
    encoding = {'POWERSHELL': 'utf-8-sig',
                'BATCH': 'oem' if processes.WINDOWS else 'utf-8'}.get(
        kind, 'utf-8')
    newline = '\r\n' if kind in ('BATCH', 'POWERSHELL') and \
        processes.WINDOWS else '\n'
    with open(path, 'w', encoding=encoding, errors='replace',
              newline=newline) as file:
        file.write(code if code.endswith('\n') else code + '\n')
    return path, workdir


def _interpreter(task, kind, ctx):
    path = task.get('interpreter')
    if not path:
        name = task.get('shell')
        if name:
            path = shutil.which(name)
        path = path or ctx['interpreters'].get(kind)
    if not path:
        raise TaskFailed(f'No interpreter of {kind} was found.')
    return path


def program_command(task, variables, workdir, ctx, counter):
    """Get (command, shell, cwd, environment extras) of a program task."""
    kind = task['type']
    arguments = [definitions.render(item, variables)
                 for item in task.get('args') or []]
    cwd = task.get('cwd')
    extras = {}
    if kind == 'EXECUTABLE':
        path = _inside_allowed(definitions.render(task['path'], variables),
                               ctx, 'The program')
        command, default_cwd = [path] + arguments, os.path.dirname(path)
        shell = False
    elif kind == 'COMMAND':
        command = definitions.render(task['command'], variables)
        default_cwd, shell = workdir, True
    else:
        script, default_cwd = _script(task, kind, variables, workdir, ctx,
                                      counter)
        interpreter = _interpreter(task, kind, ctx)
        shell = False
        if kind == 'PYTHON':
            command = [interpreter, '-u', script] + arguments
            extras = {'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
        elif kind == 'BATCH':
            command = [interpreter, '/d', '/c', 'call', script] + arguments
        elif kind == 'POWERSHELL':
            command = [interpreter, '-NoProfile', '-NonInteractive',
                       '-ExecutionPolicy', 'Bypass', '-File',
                       script] + arguments
        else:
            command = [interpreter, script] + arguments
    cwd = definitions.render(cwd, variables) if cwd else default_cwd
    if not os.path.isdir(cwd):
        raise TaskFailed(f'The working folder {cwd} does not exist.')
    return command, shell, cwd, extras


def run_program(task, variables, parameters, log, workdir, ctx, counter):
    """Run a program task, get its exit code."""
    command, shell, cwd, extras = program_command(task, variables, workdir,
                                                  ctx, counter)
    environment = run_environment(variables, parameters, task.get('env'))
    environment.update(extras)
    shown = command if isinstance(command, str) else ' '.join(command)
    log.info(f'{task["type"]}: {shown}')
    try:
        code = processes.run(command, log, shell=shell, cwd=cwd,
                             environment=environment)
    except OSError as error:
        raise TaskFailed(f'The program could not be started: {error}')
    log.info(f'Exit code {code}')
    return code


# Tasks and conditions

def run_task(task, variables, parameters, log, workdir, ctx, counter):
    """Run a task that has to succeed. Returns the exit code of a program
    (0 for SQL and TRANSFER); raises TaskFailed."""
    kind = task['type']
    if kind == 'SQL':
        run_sql(task['text'], variables, log)
        return 0
    if kind == 'TRANSFER':
        run_transfer(task, variables, log)
        return 0
    code = run_program(task, variables, parameters, log, workdir, ctx,
                       counter)
    if code != 0:
        raise TaskFailed(f'The exit code is {code}.', exit_code=code)
    return code


def _expand(text, variables, now):
    from ..transfer import engine
    return engine.expand_date_tokens(definitions.render(text, variables),
                                     now)


def evaluate_condition(condition, variables, parameters, log, workdir, ctx,
                      counter, now=None, owner=None):
    """Get (met, value) of the condition; `value` is a short text for the
    run (for RUN_STATUS, why it is not met). `owner` is ('job' or 'workflow',
    name). Raises TaskFailed when it can not be evaluated."""
    now = now or dt.datetime.now()
    kind = condition['kind']
    if kind == 'RUN_STATUS':
        try:
            met, why = dependencies.check(condition, owner, now)
        except dependencies.DependencyError as error:
            raise TaskFailed(str(error)) from error
        return met, 'met' if met else why
    if kind == 'SQL':
        value = query_value(condition['text'], variables)
        return _truthy(value), '' if value is None else str(value)
    if kind == 'EXIT_CODE':
        code = run_program(condition['task'], variables, parameters, log,
                           workdir, ctx, counter)
        return code == 0, str(code)
    if kind == 'FILE_EXISTS':
        path = _expand(condition['path'], variables, now)
        found = os.path.exists(path)
        return found == condition.get('exists', True), (
            'exists' if found else 'missing')
    directory = _expand(condition['directory'], variables, now)
    if not os.path.isdir(directory):
        raise TaskFailed(f'The folder {directory} does not exist.')
    mask = condition.get('mask')
    pattern = re.compile(_expand(mask, variables, now)) if mask else None
    count = sum(1 for entry in os.scandir(directory)
                if entry.is_file() and (pattern is None
                                        or pattern.search(entry.name)))
    low, high = condition.get('min'), condition.get('max')
    met = (low is None or count >= low) and (high is None or count <= high)
    return met, str(count)


# The run

def _counter():
    number = 0
    while True:
        number += 1
        yield number


def execute(spec, emit, ctx=None, now=None):
    """Run the parts of a job and return the outcome (never raises).

    `spec` holds `job_name`, `job_id`, `run_id`, `trigger`,
    `scheduled_time`, the normalized `definition`, optional `parameters`
    (values for this run) and `max_output_kb`. `ctx` is what the definition
    is checked against on this server (`catalogue.context()`).
    """
    ctx = ctx or catalogue.context()
    outcome = {'status': ERROR, 'stage': None, 'exit_code': None,
               'condition_value': None, 'message': None}
    max_kb = spec.get('max_output_kb') or options.get('JOBS',
                                                      'max_output_kb')
    try:
        variables, parameters, secrets = build_variables(spec, now)
    except (KeyError, TypeError) as error:
        outcome['message'] = f'The run is not described well: {error}'
        return outcome
    # The secrets of a workflow that runs the job are hidden too.
    log = RunLog(emit, int(max_kb) * 1024,
                 list(secrets) + list(spec.get('extra_secrets') or []))
    workdir = tempfile.mkdtemp(prefix='rapo-job-')
    counter = _counter()
    stage = None
    try:
        log.info(f'Job {spec["job_name"]}, run {spec["run_id"]} '
                 f'({spec["trigger"]})')
        problems = definitions.validate(spec['definition'], ctx,
                                        check_paths=True)
        if problems:
            raise TaskFailed('The job is not fit to run on this server: '
                             + ' '.join(problems))
        for stage in ('PRE', 'CONDITION', 'MAIN', 'POST'):
            part = spec['definition'].get(stage.lower())
            if not part:
                continue
            outcome['stage'] = stage
            emit('stage', stage)
            log.info(f'{stage}')
            if stage == 'CONDITION':
                met, value = evaluate_condition(
                    part, variables, parameters, log, workdir, ctx, counter,
                    now, owner=('job', spec['job_name']))
                outcome['condition_value'] = value[:100]
                emit('condition', value[:100])
                log.info(f'Condition {part["kind"]}: {value}; '
                         f'{"met" if met else "not met"}')
                if not met:
                    outcome.update(
                        status=CANCELED,
                        message=f'The condition was not met: {value}.'
                        if part['kind'] == 'RUN_STATUS' else
                        'The condition was not met.')
                    return outcome
                continue
            code = run_task(part, variables, parameters, log, workdir, ctx,
                            counter)
            if stage == 'MAIN':
                outcome['exit_code'] = code
        outcome['status'] = DONE
    except TaskFailed as error:
        if stage == 'MAIN' and error.exit_code is not None:
            outcome['exit_code'] = error.exit_code
        label = FAILURES.get(stage, 'The run failed')
        outcome['message'] = f'{label}: {error}'
        log.error(outcome['message'])
    except Exception as error:
        outcome['message'] = (f'{FAILURES.get(stage, "The run failed")}: '
                              f'{type(error).__name__}: {error}')
        log.error(outcome['message'])
        for line in traceback.format_exc().splitlines():
            log.error(line)
    finally:
        log.close()
        shutil.rmtree(workdir, ignore_errors=True)
    return outcome


def _watch(parent_pid):
    """Exit the process at once when the server process is gone."""
    while True:
        if os.getppid() != parent_pid:
            os._exit(1)
        time.sleep(WATCHDOG_INTERVAL)


def operate(connection, spec, parent_pid):
    """Perform one run in a process of its own: what `execute` emits and
    its outcome go through `connection` as (kind, payload)."""
    threading.Thread(name='Watchdog', target=_watch, args=(parent_pid,),
                     daemon=True).start()
    lock = threading.Lock()

    def emit(kind, payload):
        with lock:
            connection.send((kind, payload))
    try:
        outcome = execute(spec, emit)
    except BaseException as error:
        outcome = {'status': ERROR, 'stage': None, 'exit_code': None,
                   'condition_value': None,
                   'message': f'{type(error).__name__}: {error}'}
    try:
        emit('outcome', outcome)
    finally:
        connection.close()
