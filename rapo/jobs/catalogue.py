"""Contains what a job can be: its types, condition kinds and the OS the
server runs on.

A job type is offered when the OS of the server has it and `[JOBS]
enabled_types` (when set) lists it; it can run when its interpreter exists.
"""

import os
import shutil
import sys

from .. import options

LINUX = 'linux'
WINDOWS = 'windows'

# Kinds of task: sql (the rapo database), transfer (a transfer param),
# program (a program started with arguments, no shell), command (a command
# line for the OS shell), script (a script file or inline code).
TYPES = {
    'SQL': {'os': None, 'kind': 'sql',
            'description': 'SQL command or script (DML and PL/SQL) run in '
                           'the rapo database'},
    'TRANSFER': {'os': None, 'kind': 'transfer',
                 'description': 'A file transfer param, kept as a file or '
                                'in the database'},
    'EXECUTABLE': {'os': None, 'kind': 'program',
                   'description': 'A program with its arguments, started '
                                  'without a shell'},
    'COMMAND': {'os': None, 'kind': 'command',
                'description': 'A command line run by the shell of the OS'},
    'PYTHON': {'os': None, 'kind': 'script',
               'description': 'A Python script or code'},
    'PERL': {'os': None, 'kind': 'script',
             'description': 'A Perl script or code'},
    'SHELL': {'os': LINUX, 'kind': 'script',
              'description': 'A shell (sh or bash) script or code'},
    'BATCH': {'os': WINDOWS, 'kind': 'script',
              'description': 'A batch (.bat or .cmd) file or code'},
    'POWERSHELL': {'os': WINDOWS, 'kind': 'script',
                   'description': 'A PowerShell script or code'},
}

CONDITION_KINDS = {
    'SQL': 'A query returning one value; met when it is not empty and not 0',
    'EXIT_CODE': 'A program, command or script; met when it exits with 0',
    'FILE_EXISTS': 'A file or folder exists (or does not)',
    'FILE_COUNT': 'The number of files of a folder matching a mask is '
                  'within limits',
    'RUN_STATUS': 'Another run (a control, a transfer, a job or a workflow) '
                  'has ended well',
}
# The kinds a workflow may have at its start: the ones that need no process,
# as the workflow is run by a thread of the server.
WORKFLOW_CONDITION_KINDS = ('RUN_STATUS', 'FILE_EXISTS', 'FILE_COUNT')

# What a RUN_STATUS condition can look at, and the windows of a target.
TARGET_TYPES = ('control', 'transfer', 'job', 'workflow')
TARGET_WINDOWS = {
    'today': 'It ended today',
    'hours': 'It ended in the last N hours',
    'since_last_run': 'It ended after the last successful run of this job '
                      'or workflow',
    'data_date': 'Its data is of a day (a control only)',
}
# Types that may be the task of an EXIT_CODE condition.
EXIT_CODE_TYPES = ('EXECUTABLE', 'COMMAND', 'PYTHON', 'PERL', 'SHELL',
                   'BATCH', 'POWERSHELL')

OVERLAPS = ('skip', 'queue', 'allow')


def platform():
    """Get the OS family of this server: 'windows' or 'linux' (any other
    POSIX system counts as Linux)."""
    return WINDOWS if os.name == 'nt' else LINUX


def os_types(system=None):
    """Get the job types the OS has, in the order of TYPES."""
    system = system or platform()
    return [name for name, item in TYPES.items()
            if item['os'] in (None, system)]


def enabled_types():
    """Get the types listed in [JOBS] enabled_types, None when it is empty
    (every type of the OS)."""
    value = options.get('JOBS', 'enabled_types')
    if not value:
        return None
    return [name.strip().upper() for name in str(value).split(',')
            if name.strip()]


def offered_types(system=None, enabled=None):
    """Get the types this server offers: those of the OS, cut by
    [JOBS] enabled_types."""
    types = os_types(system)
    enabled = enabled_types() if enabled is None else enabled
    if enabled is None:
        return types
    return [name for name in types if name in enabled]


def allowed_dirs():
    """Get the absolute folders of [JOBS] allowed_dirs."""
    value = options.get('JOBS', 'allowed_dirs')
    if not value:
        return []
    return [os.path.abspath(item.strip()) for item in str(value).split(',')
            if item.strip()]


def _which(*names):
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _configured(name, *fallbacks):
    path = options.get('JOBS', name)
    if path:
        return str(path) if os.path.isfile(str(path)) else None
    return _which(*fallbacks)


def interpreters(system=None):
    """Get {type: path or None} of the interpreters of the types that need
    one; None where the interpreter is not found. Types without one (SQL,
    TRANSFER, EXECUTABLE) are not listed."""
    system = system or platform()
    found = {
        'PYTHON': _configured('python_path') if options.get(
            'JOBS', 'python_path') else sys.executable,
        'PERL': _configured('perl_path', 'perl'),
    }
    if system == WINDOWS:
        found['COMMAND'] = os.environ.get('COMSPEC') or _which('cmd')
        found['BATCH'] = found['COMMAND']
        found['POWERSHELL'] = _configured('powershell_path', 'pwsh',
                                          'powershell')
    else:
        found['COMMAND'] = _which('sh')
        found['SHELL'] = _configured('shell_path', 'bash', 'sh')
    return found


def context(system=None):
    """Get what a definition is checked against on this server."""
    system = system or platform()
    return {'platform': system,
            'types': offered_types(system),
            'os_types': os_types(system),
            'allowed_dirs': allowed_dirs(),
            'interpreters': interpreters(system)}


def describe(system=None):
    """Get the catalogue for the editor: every type of the OS with whether
    it is available here and why not."""
    ctx = context(system)
    types = []
    for name in ctx['os_types']:
        item = TYPES[name]
        reason = None
        if name not in ctx['types']:
            reason = 'Not listed in [JOBS] enabled_types'
        elif name in ctx['interpreters'] and not ctx['interpreters'][name]:
            reason = 'The interpreter was not found'
        types.append({'type': name, 'kind': item['kind'],
                      'description': item['description'],
                      'available': reason is None, 'reason': reason})
    return {'platform': ctx['platform'], 'types': types,
            'condition_kinds': [{'kind': key, 'description': text}
                                for key, text in CONDITION_KINDS.items()],
            'workflow_condition_kinds': list(WORKFLOW_CONDITION_KINDS),
            'target_types': list(TARGET_TYPES),
            'windows': [{'kind': key, 'description': text}
                        for key, text in TARGET_WINDOWS.items()],
            'overlaps': list(OVERLAPS),
            'allowed_dirs': ctx['allowed_dirs'],
            'timeout': options.get('JOBS', 'timeout')}
