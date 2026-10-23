"""Contains the catalogue of the parameters of a transfer (.param file).

The UI builds the param editor from it: the name, the kind of value, the
default, whether the parameter is required and what it does (the description
is the one of the parameter table of docs/transfer.md). The engine does not
read it; it knows the parameters from `engine.Config.from_params`.
"""

# Kinds of value: flag (Y/N), choice (one of `choices`), path, paths (comma
# separated), regex, date, number and text.
GROUPS = ['Source', 'SFTP', 'Filter', 'Checks', 'Compression', 'Archive',
          'Log', 'Notification']

# (name, group, kind, default, required, choices, description)
_PARAMETERS = [
    ('InputDir', 'Source', 'path', None, True, None,
     'Source directory (remote path when SFTP)'),
    ('OutputDir', 'Source', 'paths', None, True, None,
     'Target directory/directories (must exist), comma separated'),
    ('KeepInput', 'Source', 'flag', 'Y', False, None,
     'Y copy (default), N move'),

    ('SftpCheck', 'SFTP', 'flag', 'N', False, None,
     'Y SFTP source, N local'),
    ('SftpType', 'SFTP', 'choice', None, False, ['Clear', 'Key'],
     'Password file or private key (SftpCheck:Y)'),
    ('SftpPassFile', 'SFTP', 'path', None, False, None,
     'First line is the password (SftpType:Clear)'),
    ('SftpKey', 'SFTP', 'path', None, False, None,
     'Private key file (SftpType:Key)'),
    ('SftpHost', 'SFTP', 'text', None, False, None,
     'SFTP server (SftpCheck:Y)'),
    ('SftpUser', 'SFTP', 'text', None, False, None,
     'SFTP user (SftpCheck:Y)'),
    ('SftpPort', 'SFTP', 'number', '22', False, None,
     'SFTP port (default 22)'),
    ('SftpStrictHostKey', 'SFTP', 'flag', 'Y', False, None,
     'Y refuse a server whose host key is unknown (default), N accept it'),

    ('FileMask', 'Filter', 'regex', None, True, None,
     'Regular expression matched against the file name, with optional date '
     'tokens such as <YYYYMMDD-5d>'),
    ('FileDateCheck', 'Filter', 'flag', 'N', False, None,
     'Filter by file modification date'),
    ('FileCreationDate', 'Filter', 'date', None, False, None,
     'Date or date&datedate; required if FileDateCheck:Y; may use date '
     'tokens'),
    ('SubDirCheck', 'Filter', 'flag', 'N', False, None,
     'Recurse into sub-directories'),

    ('SizeCheck', 'Checks', 'flag', 'N', False, None,
     'Skip files whose size changes during SizeCheckDelay seconds'),
    ('SizeCheckDelay', 'Checks', 'number', '20', False, None,
     'Seconds to wait before the size is checked again (SizeCheck:Y, '
     'default 20)'),
    ('CtlFileCheck', 'Checks', 'flag', 'N', False, None,
     'Require <name>.<CtlFileExtension> next to the file'),
    ('CtlFileExtension', 'Checks', 'text', None, False, None,
     'Control file extension (CtlFileCheck:Y)'),

    ('GzipInput', 'Compression', 'flag', 'N', False, None,
     'Gzip the file in the target (adds .gz)'),
    ('GzipOutput', 'Compression', 'flag', 'N', False, None,
     'Input is .gz, unpack it into the target'),

    ('ArchiveCheck', 'Archive', 'flag', 'N', False, None,
     'Copy the original file to the archive'),
    ('ArchiveDir', 'Archive', 'path', None, False, None,
     'Archive directory (ArchiveCheck:Y)'),

    ('LogMode', 'Log', 'choice', 'Full', False, ['Full', 'Basic', 'None'],
     'Full = everything, Basic = transfers and errors, None = no log file'),
    ('LogDatabase', 'Log', 'flag', 'N', False, None,
     'Y writes the log to the database (per LogMode) and ignores LogDir and '
     'LogFile; only for a param kept in the database'),
    ('LogDir', 'Log', 'path', None, False, None,
     'Log directory (unless LogMode:None or LogDatabase:Y)'),
    ('LogFile', 'Log', 'text', None, False, None,
     'Log file name (unless LogMode:None or LogDatabase:Y)'),

    ('Notification', 'Notification', 'flag', 'N', False, None,
     'E-mail the errors'),
    ('NotificationSender', 'Notification', 'text', None, False, None,
     'Sender e-mail (Notification:Y)'),
    ('NotificationRe', 'Notification', 'text', None, False, None,
     'Recipient e-mails, comma separated (Notification:Y)'),
    ('NotificationCc', 'Notification', 'text', None, False, None,
     'Copy e-mails, comma separated'),
    ('NotificationMethod', 'Notification', 'choice', 'smtp', False, ['smtp'],
     'How the e-mail is sent: smtp'),
    ('NotificationServer', 'Notification', 'text', None, False, None,
     'SMTP relay host (Notification:Y)'),
    ('NotificationPort', 'Notification', 'number', '25', False, None,
     'SMTP relay port (default 25)'),
]


# Parameters only a param kept in the database can have: the file of a param
# may be run by the filetransfer-py script, which does not know them.
DATABASE_ONLY = {'LogDatabase'}

# Parameters that have no effect while another has a value: name -> (the
# other parameter, its value).
IGNORED_WHEN = {'LogDir': ('LogDatabase', 'Y'), 'LogFile': ('LogDatabase', 'Y')}


def parameters():
    """Get the parameters in the order they are written, as dictionaries.

    `storage` is 'db' for a parameter only a param in the database can have,
    and `ignored_when` is [parameter, value] while which it has no effect.
    """
    return [{'name': name, 'group': group, 'kind': kind, 'default': default,
             'required': required, 'choices': choices,
             'description': description,
             'storage': 'db' if name in DATABASE_ONLY else None,
             'ignored_when': list(IGNORED_WHEN[name])
             if name in IGNORED_WHEN else None}
            for (name, group, kind, default, required, choices,
                 description) in _PARAMETERS]
