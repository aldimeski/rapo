# Rapo v0.8.8 Change Log

## Annotation
New tables `rapo_transfer_param`, `rapo_transfer_schedule`, `rapo_transfer_run` and `rapo_transfer_log`
(`migrations/v0.8.8/upgrade.sql`); new section `[TRANSFER]` of `rapo.ini`. The upgrade steps are in the
[migration instructions](README.md).

1. **File transfers.** The logic of [filetransfer-py](https://github.com/aldimeski/filetransfer-py) is part of rapo
   (`rapo/transfer/`): a transfer copies or moves files from a local directory or an SFTP server into one or more
   target directories (file mask with date tokens, file date, stable size, control file, gzip/gunzip, archive, log,
   e-mail on errors), described by the same `Name:value` params. See [docs/transfer.md](../../docs/transfer.md).
1. **Params as files or in the database.** A param is a `<name>.param` file in `[TRANSFER] param_dir` or a row of
   `rapo_transfer_param`; the *Transfers* page creates one in either place, moves it between them and keeps its
   schedule.
1. **Scheduled by rapo.** A transfer scheduled in rapo (`rapo_transfer_schedule`) is started by the scheduler holding
   the lease and performed by rapo in its own process, up to `[TRANSFER] parallelism` at once, with its runs and logs
   in `rapo_transfer_run`. The `file-transfer.py` script is not used.
1. **Crontab detection (Linux).** The crontab of the server user is read, and the scripts it runs are opened to
   find the ones that start `file-transfer.py` with a `.param` file. The list shows such a param as started by the
   crontab (through which script, with its cron expression and next start), and flags a param that both rapo and the
   crontab start.
1. **Runs on Windows.** The package imports and the transfers run on Windows; the Linux-only parts are switched off
   there: the crontab scan and its UI, the owner and group names of the datasource file lists (numbers instead)
   and the load average of the health tab (empty). See [docs/transfer.md](../../docs/transfer.md#windows).
1. **Log in the database.** `LogDatabase:Y` (params kept in the database only) writes the log of a run to `rapo_transfer_log` per `LogMode` instead of a file and ignores `LogDir` and `LogFile`; the newest `[TRANSFER] log_max_lines` (5000) lines per param are kept. A file param can not have it: it is not offered, refused on saving, and dropped when a param moves to a file, which warns and asks to confirm when the param has no `LogDir` and `LogFile` (`LogMode` becomes `None`). `check-move-transfer`, `confirm` of `move-transfer`.
1. **Param editor as a table.** Parameter and Value like Instance details, the description of a parameter on hover; a new param lists all parameters with `-` to hide one, an existing one lists those with a value and `+` adds the rest; comments of the file are kept (`get-transfer-catalogue`).
1. **Transfers page and API.** `/transfers` lists every param with where it is kept, how it is started, the next start,
   the last run and the run history; `get-transfers`, `get-transfer`, `validate-transfer`, `save-transfer`,
   `move-transfer`, `delete-transfer`, `save-transfer-schedule`, `run-transfer`, `cancel-transfer`,
   `get-transfer-runs`, `get-transfer-run` (Datasources view/edit, Files operate scopes).
