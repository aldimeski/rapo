# File transfers

Rapo carries the logic of the [filetransfer-py](https://github.com/aldimeski/filetransfer-py) tool, which copies or moves
files from a local directory or an SFTP server into one or more target directories. A transfer is described by a
**param**, the same `Name:value` text as a `.param` file of that tool, and it is performed by rapo itself:
the `file-transfer.py` script is not used for a transfer scheduled in rapo.

The *Transfers* page of the UI (`/transfers`) lists every param in one place, whether it is a file or kept in the
database, how it is started and when it starts next.

## Where a param is kept

| Place    | What it is                                                                                          |
|----------|-----------------------------------------------------------------------------------------------------|
| File     | `<name>.param` in the folder of `[TRANSFER] param_dir` (rapo.ini)                                   |
| Database | a row of `rapo_transfer_param` (`param_text` holds the param, `description` is a note)              |
| Outside  | a param only the crontab knows (not in `param_dir`, not in the database): listed, but not editable  |

A name is a file *or* a database row, never both: a param found in both places is flagged and refused until one of
them is deleted. A new param is created in the place chosen in the *New transfer* dialog, and *Move to the database* /
*Move to a file* in the row menu changes it later; the schedule stays with the name.

Names have 1-128 letters, digits, dots, hyphens and underscores, and start with a letter or a digit. Kebab-case
(`daily-report`) is the convention.

A param is checked when it is saved (the *Check* button does it without saving, and shows what the date tokens expand
to now). `FileMask` and `FileCreationDate` use the date language of filetransfer-py, e.g. `<YYYYMMDD-5d>` is the date
5 days ago (block form, `y`, `mo`, `d`, `h`, `mi`, `s`) and `<DD-5>` the day part of it.

| Parameter | Value | Description |
|---|---|---|
| `InputDir` | path | Source directory (remote path when SFTP) |
| `OutputDir` | path[,path...] | Target directory/directories (must exist) |
| `KeepInput` | Y/N | Y copy (default), N move |
| `SftpCheck` | Y/N | Y SFTP source, N local |
| `SftpType` | Clear/Key | Password file or private key |
| `SftpPassFile` | path | First line is the password (Clear) |
| `SftpKey` | path | Private key file (Key) |
| `SftpHost`, `SftpUser` | text | SFTP server and user |
| `FileMask` | regex with optional date tokens | Matched against the file name |
| `FileDateCheck` | Y/N | Filter by file modification date |
| `FileCreationDate` | date or `date&datedate` | Required if `FileDateCheck:Y`; may use date tokens |
| `SubDirCheck` | Y/N | Recurse into sub-directories |
| `SizeCheck` | Y/N | Skip files whose size changes during `SizeCheckDelay` seconds |
| `CtlFileCheck` | Y/N | Require `<name>.<CtlFileExtension>` next to the file |
| `CtlFileExtension` | text | Control file extension |
| `GzipInput` | Y/N | Gzip the file in the target (adds `.gz`) |
| `GzipOutput` | Y/N | Input is `.gz`, unpack it into the target |
| `ArchiveCheck`, `ArchiveDir` | Y/N, path | Copy the original file to the archive |
| `LogMode` | Full/Basic/None | Full = everything, Basic = transfers and errors, None = no log file |
| `LogDatabase` | Y/N | Y writes the log to the database (per `LogMode`) and ignores `LogDir` and `LogFile`; only for a param kept in the database |
| `LogDir`, `LogFile` | path, name | Log location (required unless `LogMode:None` or `LogDatabase:Y`) |
| `Notification` | Y/N | E-mail the errors |
| `NotificationSender`, `NotificationRe`, `NotificationCc` | e-mail(s) | Comma separated lists |
| `NotificationMethod`, `NotificationServer` | `smtp`, host | SMTP relay |

Optional: `SftpPort`, `SftpStrictHostKey`, `SizeCheckDelay`, `NotificationPort`. A file is written to its target as
`tmp_<name>` and renamed when complete, so downstream readers must ignore the `tmp_` prefix.

## The param editor

*New transfer* and *Edit param* show the parameters as a table of **Parameter** and **Value**, like the Configuration
tab of Instance details. Hover a parameter name to read what it does (the descriptions of the table above).

* A new param lists every parameter with its default. `-` hides a parameter, so it is not written to the param;
  `InputDir`, `OutputDir` and `FileMask` (marked `*`) are required and have no `-`.
* An existing param lists only the parameters that have a value. *Add a parameter* at the bottom lists the ones that
  are not shown, with their descriptions, and adds the chosen one with its default.
* Saving from the table changes only the lines of the parameters you changed, adds new ones at the end and drops the
  hidden ones; comments and blank lines stay. A parameter rapo does not know (a typo) is listed under *Not known to
  rapo* so it can be removed.
* *Text* shows the `.param` text itself, for what the table does not cover.
* Without *Datasources: Edit* the table is read only.
## Logs

Every run keeps its log for the *Run history* dialog, and the param decides where the full log goes:

| Param | The log | In Run history |
|---|---|---|
| `LogMode:Full`/`Basic` with `LogDir` and `LogFile` | appended to that file: all lines for `Full`, `INFO` and above for `Basic`; `None` writes nothing | the INFO lines of the run (not the debug ones), at most 500, the older cut with a note |
| `LogDatabase:Y` (database params only) | rows of `rapo_transfer_log`, per `LogMode`: `Full` from `DEBUG`, `Basic` from `INFO`, `None` nothing. No file is written, `LogDir` and `LogFile` are ignored (they are shown in another color in the editor) | the whole database log of the run |

A line reads `pid: dd/mm/yy HH:MM:SS [ message ]`, with `ERROR:` or `WARNING:` before the message from the warnings up.
A run lists its start and end, each file with its date and size before the transfer, the copy to `tmp_<name>`, the
rename, the archive or the removal from the input, and ends with `END Transit files: N transferred, M failed`; with
`Full` it also lists the files found and why a file was skipped. The error messages of a failed run are also in the
red message above the log.

The database log keeps the newest `[TRANSFER] log_max_lines` (5000) lines **per param**, across its runs: when it grows
past that, the oldest lines (of the oldest runs first) are deleted, and the run history says so for a run that lost
lines. The lines of a run are deleted with the run after `history_retention_days`.

`LogDatabase:Y` is refused for a param kept as a file (a file may also be run by the filetransfer-py script, which does
not know it), so the parameter is not offered for a new file param and is dropped when the place is changed to a file.
Moving a param from the database to a file removes `LogDatabase`; if the param has no `LogDir` and `LogFile` it would have
no logging, so rapo warns and asks to confirm, and then sets `LogMode:None`.

## Running

A run, scheduled or started with *Run now*, is recorded at once in `rapo_transfer_run` (status `I` initiated, `S`
running, `D` done, `E` error, `C` canceled), waits in a queue for one of the `[TRANSFER] parallelism` slots of the server and
is performed in its own process. The same param never runs twice at once on a server. A run ends in `E` when
any error was logged (a failed file, a missing directory, a bad param) and `[TRANSFER] timeout` stops one that takes
too long. The log lines of a run (and the errors) are kept with the run and shown in *Run history*; the `LogDir`/`LogFile`
of the param are written as before. `[TRANSFER] history_retention_days` bounds the history, and the scheduler's
maintenance deletes older runs.

## Scheduling

There are two ways to start a transfer, and the list shows which one a param uses:

* **Rapo** - *Schedule in rapo...* in the row menu sets the schedule (daily, weekly, monthly or complex, as for a control,
  without cascade) and switches it on or off. The scheduler of the server holding the lease starts the run, with the
  same lease, late-fire tolerance (a fire found more than a minute late is recorded as canceled) and stop/start
  switch as the controls. Nothing else is needed: **do not** also start the param from the crontab.
* **Crontab** (Linux) - rapo reads the crontab of the user the server runs as (`crontab -l`) and checks every command:
  one that calls `file-transfer.py` with a `.param` file starts that param directly; a script it runs is opened
  (and the scripts it runs, one level deeper) to see whether it calls `file-transfer.py` with a `.param` file. The
  param then shows *Crontab*, the cron expression, the script (*via nightly.sh* or *calls the script directly*), the
  user and the next start computed from the expression. The scan is reused for `[TRANSFER] crontab_scan_interval`
  seconds and *Rescan crontab* reads it again. Rapo only reads the crontab, it never changes it.

The *Started by* column shows `Rapo`, `Crontab`, or `Twice!` when both start the same param, which would run it twice.
The next start shown is the earliest of the two. The scan sees the crontab of this server only, and params are matched
by their file name: a script that builds the param path from a variable is matched on the part before `.param`.

## Windows

The transfers run on Windows too, with the same params (use Windows paths such as `C:\data\in`, and `InputDir` of
an SFTP server stays a Unix path). What is Linux only is switched off when rapo sees another system:

* the crontab scan (`crontab_scan`, `crontab_scan_interval`, `script_name` are ignored), so no param is ever shown as
  started by the crontab, *Rescan crontab* and the *Crontab* filter are hidden and the page says so;
* the owner and group of the files in the datasource lists show as numbers, and the load average of the health
  tab is empty, because Windows has neither.

Start the server with `rapo-server start` (the `install.sh` and `rapoctl.sh` scripts are bash and are not used).
Use the Task Scheduler of Windows only for what rapo can not schedule; a transfer scheduled in rapo needs nothing
else. A transfer is not run twice at once on a server on any system.

## Options

`[TRANSFER]` in `rapo.ini` (see `rapo.ini.example`): `param_dir`, `parallelism`, `timeout`, `history_retention_days`,
`log_max_lines`, `crontab_scan`, `crontab_scan_interval`, `script_name`.

## Access

The page needs *Datasources: View*; creating, changing, moving, deleting and scheduling needs *Datasources: Edit*; running
and canceling needs *Files: Operate*. Anything that writes a param can make the server write files wherever it may,
and read any SFTP key or password file it names, so give *Datasources: Edit* with the same care as the access to the
server.

## Upgrade

Run `migrations/v0.8.8/upgrade.sql` to create `rapo_transfer_param`, `rapo_transfer_schedule`, `rapo_transfer_run` and `rapo_transfer_log`.
Without them the page works for the params in the folder, without schedules or history.
