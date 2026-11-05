"""Contains the definition of a job: reading, checking and rendering it.

A definition is one JSON document with the parts of a job run in this order:
`pre`, `condition`, `main` (required) and `post`, plus the job's own
`parameters`, a `timeout` and the `overlap` policy. See docs/jobs-design.md.
"""

import copy
import json
import os
import re

from ..transfer import catalogue as transfer_catalogue

from . import catalogue

VERSION = 1
PARTS = ('pre', 'condition', 'main', 'post')
KEYS = {'version', 'pre', 'condition', 'main', 'post', 'parameters',
        'timeout', 'overlap'}
# The variables every run has; the job's own parameters come on top.
VARIABLES = ('job_name', 'job_id', 'run_id', 'trigger', 'scheduled_time',
             'start_time')

# What a task of each type may set.
_SCRIPT = {'file', 'code', 'args', 'cwd', 'env'}
SETTINGS = {
    'SQL': {'text'},
    'TRANSFER': {'param', 'arguments'},
    'EXECUTABLE': {'path', 'args', 'cwd', 'env'},
    'COMMAND': {'command', 'cwd', 'env'},
    'PYTHON': _SCRIPT | {'interpreter'},
    'PERL': _SCRIPT | {'interpreter'},
    'SHELL': _SCRIPT | {'shell'},
    'BATCH': _SCRIPT,
    'POWERSHELL': _SCRIPT | {'shell'},
}
SHELLS = {'SHELL': ('sh', 'bash'), 'POWERSHELL': ('powershell', 'pwsh')}

# The statements a SQL task may hold: DML and PL/SQL, no DDL.
SQL_ALLOWED = ('select', 'with', 'insert', 'update', 'delete', 'merge',
               'call', 'begin', 'declare', 'commit', 'rollback',
               'savepoint')
PLSQL = ('begin', 'declare')

NAME_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
PARAM_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_SLASH_LINE = re.compile(r'[ \t]*/[ \t]*(?:\r?\n|$)')
_VARIABLE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}')
_CLOSERS = {'[': ']', '{': '}', '(': ')', '<': '>'}


class DefinitionError(ValueError):
    """A definition that is not valid; `problems` lists what is wrong."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(' '.join(self.problems))


# SQL

def first_keyword(statement):
    """Get the first word of a statement, lower case, after comments and
    parentheses; empty when there is none."""
    text = re.sub(r'/\*.*?\*/', ' ', statement, flags=re.S)
    text = re.sub(r'--[^\n]*', ' ', text)
    match = re.match(r'[\s(]*([A-Za-z_]+)', text)
    return match.group(1).lower() if match else ''


def split_sql(text):
    """Split a SQL script into its statements.

    A plain statement ends at a `;`, a PL/SQL block (BEGIN, DECLARE) and any
    statement also at a `/` alone on its line. Quotes (also `q'[...]'`),
    quoted names and comments are skipped over. A trailing `;` of a plain
    statement is dropped; one that is only comments is left out.
    """
    statements = []
    buffer = []

    def finish():
        statement = ''.join(buffer).strip()
        buffer.clear()
        if first_keyword(statement):
            statements.append(statement)

    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if (index == 0 or text[index - 1] == '\n') and \
                _SLASH_LINE.match(text, index):
            finish()
            index = _SLASH_LINE.match(text, index).end()
            continue
        if text.startswith('--', index):
            end = text.find('\n', index)
            end = length if end < 0 else end
        elif text.startswith('/*', index):
            end = text.find('*/', index + 2)
            end = length if end < 0 else end + 2
        elif char in 'qQ' and text.startswith("'", index + 1) \
                and index + 2 < length \
                and not (index and (text[index - 1].isalnum()
                                    or text[index - 1] == '_')):
            delimiter = text[index + 2]
            closer = _CLOSERS.get(delimiter, delimiter) + "'"
            end = text.find(closer, index + 3)
            end = length if end < 0 else end + 2
        elif char == "'":
            end = index + 1
            while end < length:
                if text[end] == "'":
                    if text.startswith("''", end):
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
        elif char == '"':
            end = text.find('"', index + 1)
            end = length if end < 0 else end + 1
        elif char == ';':
            if first_keyword(''.join(buffer)) in PLSQL:
                buffer.append(char)
            else:
                finish()
            index += 1
            continue
        else:
            end = index + 1
        buffer.append(text[index:end])
        index = end
    finish()
    return statements


def check_sql(text, label, problems, query=False):
    """Check the statements of a SQL task, adding to `problems`. A query
    (a condition) is one SELECT or WITH."""
    statements = split_sql(text or '')
    if not statements:
        problems.append(f'{label}: the SQL is empty.')
        return
    if query and len(statements) != 1:
        problems.append(f'{label}: the condition is one query, not '
                        f'{len(statements)} statements.')
    allowed = ('select', 'with') if query else SQL_ALLOWED
    for number, statement in enumerate(statements, 1):
        word = first_keyword(statement)
        if word not in allowed:
            where = f'statement {number} ' if len(statements) > 1 else ''
            hint = ('a query (SELECT or WITH)' if query else
                    'DML and PL/SQL (SELECT, INSERT, UPDATE, DELETE, MERGE, '
                    'CALL, BEGIN/DECLARE, COMMIT, ROLLBACK, SAVEPOINT)')
            problems.append(f'{label}: {where}starts with '
                            f'{word.upper() or "nothing"}; only {hint} are '
                            'allowed.')


# Variables

def render(text, variables):
    """Put the values of the known `{name}` and `{name:format}` into the
    text. Braces of anything else (a shell `${HOME}`, an awk `{print}`, a
    Python dict) stay as they are, so scripts need no escaping."""
    def replace(match):
        name, spec = match.groups()
        if name not in variables:
            return match.group(0)
        value = variables[name]
        if not spec:
            return str(value)
        try:
            return format(value, spec)
        except (TypeError, ValueError):
            raise DefinitionError(
                [f'Variable {{{name}}}: "{spec}" is not a format of '
                 f'{type(value).__name__}.'])
    return _VARIABLE.sub(replace, text)


# Reading

def load(definition):
    """Get the definition as a dict from JSON text or a dict (a copy)."""
    if isinstance(definition, (str, bytes)):
        try:
            definition = json.loads(definition)
        except ValueError as error:
            raise DefinitionError([f'The definition is not JSON: {error}'])
    if not isinstance(definition, dict):
        raise DefinitionError(['The definition is not a JSON object.'])
    return copy.deepcopy(definition)


def dumps(definition):
    """Get the JSON text kept in the database."""
    return json.dumps(definition, ensure_ascii=False, indent=1)


def _upper_type(task, key='type'):
    if isinstance(task, dict) and isinstance(task.get(key), str):
        task[key] = task[key].strip().upper()


def normalize(definition):
    """Get the definition with its defaults filled in and the type names in
    upper case; `check` is what finds the problems."""
    definition = load(definition)
    definition.setdefault('version', VERSION)
    for part in PARTS:
        definition.setdefault(part, None)
    definition.setdefault('parameters', [])
    definition.setdefault('timeout', None)
    definition.setdefault('overlap', 'skip')
    for part in ('pre', 'main', 'post'):
        _upper_type(definition[part])
    condition = definition['condition']
    if isinstance(condition, dict):
        _upper_type(condition, 'kind')
        _upper_type(condition.get('task'))
    return definition


def job_type(definition):
    """Get the type of the main task, '' when there is none."""
    main = definition.get('main')
    if isinstance(main, dict):
        return main.get('type') or ''
    return ''


# Checking

def inside(path, folders):
    """Tell whether the path is one of the folders or below one."""
    real = os.path.normcase(os.path.realpath(path))
    for folder in folders:
        base = os.path.normcase(os.path.realpath(folder)).rstrip(os.sep)
        if real == base or real.startswith(base + os.sep):
            return True
    return False


def _is_text(value):
    return isinstance(value, str) and bool(value.strip())


def _check_file(path, label, setting, ctx, problems, check_paths):
    if not _is_text(path):
        problems.append(f'{label}: {setting} is not a path.')
        return
    if not os.path.isabs(path):
        problems.append(f'{label}: {setting} must be an absolute path.')
        return
    folders = ctx.get('allowed_dirs') or []
    if not folders:
        problems.append(f'{label}: [JOBS] allowed_dirs is not set in '
                        'rapo.ini, so no script or program file is '
                        'accepted.')
    elif not inside(path, folders):
        problems.append(f'{label}: {setting} is not in a folder of '
                        '[JOBS] allowed_dirs.')
    if check_paths and not os.path.isfile(path):
        problems.append(f'{label}: {path} is not a file.')


def _check_common(task, label, problems):
    args = task.get('args')
    if args is not None and not (isinstance(args, list) and all(
            isinstance(item, str) for item in args)):
        problems.append(f'{label}: args is a list of texts.')
    env = task.get('env')
    if env is not None:
        if not (isinstance(env, dict) and all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in env.items())):
            problems.append(f'{label}: env is names with text values.')
        elif not all(NAME_PATTERN.match(key) for key in env):
            problems.append(f'{label}: a name in env has letters, digits '
                            'and underscores, and does not start with a '
                            'digit.')
    cwd = task.get('cwd')
    if cwd is not None and not _is_text(cwd):
        problems.append(f'{label}: cwd is a path.')


def _check_transfer(task, label, problems):
    name = task.get('param')
    if not (_is_text(name) and PARAM_PATTERN.match(name.strip())):
        problems.append(f'{label}: param is the name of a transfer param '
                        '(1-128 letters, digits, dots, hyphens or '
                        'underscores).')
    arguments = task.get('arguments')
    if arguments is None:
        return
    if not (isinstance(arguments, list) and all(
            isinstance(item, str) for item in arguments)):
        problems.append(f'{label}: arguments is a list of Name:value '
                        'lines.')
        return
    known = {item['name'] for item in transfer_catalogue.parameters()}
    seen = set()
    for item in arguments:
        key, separator, _ = item.partition(':')
        key = key.strip()
        if not separator or not key:
            problems.append(f'{label}: argument "{item}" is not '
                            'Name:value.')
        elif key not in known:
            problems.append(f'{label}: "{key}" is not a parameter of a '
                            'transfer.')
        elif key in seen:
            problems.append(f'{label}: argument {key} is given twice.')
        seen.add(key)


def _check_script(task, kind, label, ctx, problems, check_paths):
    has_file, has_code = 'file' in task, 'code' in task
    if has_file == has_code:
        problems.append(f'{label}: give either file or code.')
    elif has_file:
        _check_file(task['file'], label, 'file', ctx, problems, check_paths)
    elif not _is_text(task['code']):
        problems.append(f'{label}: code is empty.')
    shell = task.get('shell')
    if shell is not None and shell not in SHELLS.get(kind, ()):
        problems.append(f'{label}: shell is one of '
                        f'{", ".join(SHELLS.get(kind, ()))}.')
    interpreter = task.get('interpreter')
    if interpreter is not None and not (
            _is_text(interpreter) and os.path.isabs(interpreter)):
        problems.append(f'{label}: interpreter is an absolute path.')


def _check_task(task, label, ctx, problems, check_paths, condition=False):
    """Check one task and return its type, None when it has none."""
    if not isinstance(task, dict):
        problems.append(f'{label}: is not a task.')
        return None
    kind = task.get('type')
    if kind not in catalogue.TYPES:
        problems.append(f'{label}: type is one of '
                        f'{", ".join(catalogue.TYPES)}.')
        return None
    if kind not in ctx['os_types']:
        problems.append(f'{label}: type {kind} is not available on '
                        f'{ctx["platform"].capitalize()}.')
        return kind
    if kind not in ctx['types']:
        problems.append(f'{label}: type {kind} is not in [JOBS] '
                        'enabled_types.')
        return kind
    if kind in ctx['interpreters'] and not ctx['interpreters'][kind]:
        problems.append(f'{label}: no interpreter of {kind} was found on '
                        'this server.')
    unknown = sorted(set(task) - SETTINGS[kind] - {'type'})
    if unknown:
        problems.append(f'{label}: {kind} has no setting '
                        f'{", ".join(unknown)}.')
    if condition and kind not in catalogue.EXIT_CODE_TYPES:
        problems.append(f'{label}: a condition of kind EXIT_CODE runs a '
                        f'program, command or script, not {kind}.')
    if kind == 'SQL':
        check_sql(task.get('text'), label, problems)
    elif kind == 'TRANSFER':
        _check_transfer(task, label, problems)
    else:
        _check_common(task, label, problems)
        if kind == 'EXECUTABLE':
            _check_file(task.get('path'), label, 'path', ctx, problems,
                        check_paths)
        elif kind == 'COMMAND':
            if not _is_text(task.get('command')):
                problems.append(f'{label}: command is empty.')
        else:
            _check_script(task, kind, label, ctx, problems, check_paths)
    return kind


def _check_file_count(condition, label, problems):
    if not _is_text(condition.get('directory')):
        problems.append(f'{label}: directory is empty.')
    mask = condition.get('mask')
    if mask is not None:
        try:
            re.compile(mask)
        except (re.error, TypeError):
            problems.append(f'{label}: mask is not a regular expression.')
    limits = {}
    for key in ('min', 'max'):
        value = condition.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) \
                or value < 0:
            problems.append(f'{label}: {key} is a whole number, 0 or more.')
        else:
            limits[key] = value
    if 'min' not in limits and 'max' not in limits:
        problems.append(f'{label}: give min or max.')
    elif len(limits) == 2 and limits['min'] > limits['max']:
        problems.append(f'{label}: min is more than max.')


def _check_condition(condition, ctx, problems, check_paths):
    label = 'Condition'
    if not isinstance(condition, dict):
        problems.append(f'{label}: is not an object.')
        return
    kind = condition.get('kind')
    if kind not in catalogue.CONDITION_KINDS:
        problems.append(f'{label}: kind is one of '
                        f'{", ".join(catalogue.CONDITION_KINDS)}.')
        return
    allowed = {'SQL': {'text'}, 'EXIT_CODE': {'task'},
               'FILE_EXISTS': {'path', 'exists'},
               'FILE_COUNT': {'directory', 'mask', 'min', 'max'}}[kind]
    unknown = sorted(set(condition) - allowed - {'kind'})
    if unknown:
        problems.append(f'{label}: {kind} has no setting '
                        f'{", ".join(unknown)}.')
    if kind == 'SQL':
        if 'SQL' not in ctx['types']:
            problems.append(f'{label}: type SQL is not available '
                            '([JOBS] enabled_types).')
        check_sql(condition.get('text'), label, problems, query=True)
    elif kind == 'EXIT_CODE':
        _check_task(condition.get('task'), f'{label} task', ctx, problems,
                    check_paths, condition=True)
    elif kind == 'FILE_EXISTS':
        if not _is_text(condition.get('path')):
            problems.append(f'{label}: path is empty.')
        if not isinstance(condition.get('exists', True), bool):
            problems.append(f'{label}: exists is true or false.')
    else:
        _check_file_count(condition, label, problems)


def _check_parameters(parameters, problems):
    if not isinstance(parameters, list):
        problems.append('parameters is a list.')
        return
    seen = set()
    for number, item in enumerate(parameters, 1):
        label = f'Parameter {number}'
        if not isinstance(item, dict):
            problems.append(f'{label}: is not an object.')
            continue
        unknown = sorted(set(item) - {'name', 'value', 'secret'})
        if unknown:
            problems.append(f'{label}: has no setting {", ".join(unknown)}.')
        name = item.get('name')
        if not (isinstance(name, str) and NAME_PATTERN.match(name)):
            problems.append(f'{label}: name has letters, digits and '
                            'underscores, and does not start with a digit.')
            continue
        if name in VARIABLES:
            problems.append(f'{label}: {name} is a variable of every run.')
        elif name in seen:
            problems.append(f'{label}: {name} is given twice.')
        seen.add(name)
        if not isinstance(item.get('value', ''), str):
            problems.append(f'{label}: value is text.')
        if not isinstance(item.get('secret', False), bool):
            problems.append(f'{label}: secret is true or false.')


def validate(definition, ctx=None, check_paths=False):
    """Get the list of problems of a normalized definition, empty when it
    is fit to be saved on this server.

    `ctx` is what `catalogue.context()` gives (the OS, the types offered,
    the interpreters, `allowed_dirs`); with `check_paths` a script or
    program file must exist too.
    """
    ctx = ctx or catalogue.context()
    problems = []
    unknown = sorted(set(definition) - KEYS)
    if unknown:
        problems.append(f'The definition has no setting '
                        f'{", ".join(unknown)}.')
    if definition.get('version') != VERSION:
        problems.append(f'version is {VERSION}.')
    if not definition.get('main'):
        problems.append('A job needs a main task.')
    for part, label in (('pre', 'Pre'), ('main', 'Main'), ('post', 'Post')):
        if definition.get(part) is not None:
            _check_task(definition[part], label, ctx, problems, check_paths)
    if definition.get('condition') is not None:
        _check_condition(definition['condition'], ctx, problems,
                         check_paths)
    _check_parameters(definition.get('parameters'), problems)
    timeout = definition.get('timeout')
    if timeout is not None and (isinstance(timeout, bool)
                                or not isinstance(timeout, int)
                                or timeout < 0):
        problems.append('timeout is a number of seconds, 0 for none.')
    if definition.get('overlap') not in catalogue.OVERLAPS:
        problems.append(f'overlap is one of {", ".join(catalogue.OVERLAPS)}.')
    return problems


def check(definition, ctx=None, check_paths=False):
    """Get the normalized definition, raising DefinitionError with every
    problem found."""
    definition = normalize(definition)
    problems = validate(definition, ctx, check_paths)
    if problems:
        raise DefinitionError(problems)
    return definition
