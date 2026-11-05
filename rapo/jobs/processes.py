"""Contains what the jobs need to run programs: the environment they get,
reading their output and stopping a process together with its children.
"""

import os
import subprocess
import threading

import psutil

WINDOWS = os.name == 'nt'
# What a program gets of the environment of rapo: the essentials of the OS,
# never the rest (it may hold passwords). Names are compared in upper case.
SAFE_NAMES = {
    'PATH', 'TZ', 'LANG', 'LANGUAGE', 'TERM',
} | ({
    'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP',
    'TMP', 'USERPROFILE', 'USERNAME', 'USERDOMAIN', 'HOMEDRIVE', 'HOMEPATH',
    'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES',
    'PROGRAMFILES(X86)', 'PROGRAMW6432', 'COMMONPROGRAMFILES',
    'ALLUSERSPROFILE', 'COMPUTERNAME', 'OS', 'PROCESSOR_ARCHITECTURE',
    'NUMBER_OF_PROCESSORS', 'PSMODULEPATH',
} if WINDOWS else {
    'HOME', 'USER', 'LOGNAME', 'SHELL', 'TMPDIR',
})
SAFE_PREFIXES = ('LC_',)
# The code page of the console output of Windows programs; elsewhere the
# fallback for output that is not UTF-8.
FALLBACK_ENCODING = 'oem' if WINDOWS else 'latin-1'
GRACE_SECONDS = 5.0


def clean_environment(extra=None):
    """Get the environment a job's program starts with: the essentials of
    the OS of this process, then `extra`."""
    environment = {}
    for key, value in os.environ.items():
        name = key.upper()
        if name in SAFE_NAMES or name.startswith(SAFE_PREFIXES):
            environment[key] = value
    environment.update(extra or {})
    return environment


def decode(raw):
    """Get the text of a line of output: UTF-8, else the code page of the
    OS."""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode(FALLBACK_ENCODING, errors='replace')


def _read(stream, name, log):
    try:
        for raw in iter(stream.readline, b''):
            log.output(name, decode(raw.rstrip(b'\r\n')))
    finally:
        stream.close()


def run(command, log, shell=False, cwd=None, environment=None):
    """Run a program and wait for it: `command` is a list (a command line
    text with `shell`). Its output goes to `log` line by line as it comes.
    Returns the exit code; raises OSError when it can not be started.
    """
    flags = subprocess.CREATE_NO_WINDOW if WINDOWS else 0
    process = subprocess.Popen(
        command, shell=shell, cwd=cwd, env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, creationflags=flags)
    readers = [threading.Thread(target=_read, args=(stream, name, log),
                                daemon=True)
               for stream, name in ((process.stdout, 'out'),
                                    (process.stderr, 'err'))]
    for reader in readers:
        reader.start()
    code = process.wait()
    for reader in readers:
        reader.join()
    return code


def kill_tree(pid, grace=GRACE_SECONDS):
    """Stop a process and every process it started: ask them to end, then
    kill what is still there after `grace` seconds. A process that is
    already gone is not an error."""
    try:
        root = psutil.Process(pid)
        processes = root.children(recursive=True) + [root]
    except psutil.Error:
        return
    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(processes, timeout=grace)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=grace)
