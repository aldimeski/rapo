# Rapo v0.8.7.5 Migration Instructions

Upgrades Rapo from v0.8.7 to v0.8.7.5. Commands run in the application folder.
What the release contains is in the [change log](CHANGELOG.md).

The release adds the file transfers, the jobs and the workflows: the tables `rapo_transfer_param`,
`rapo_transfer_schedule`, `rapo_transfer_run`, `rapo_transfer_log`, `rapo_job`, `rapo_job_version`, `rapo_job_run`,
`rapo_job_log`, `rapo_job_event`, `rapo_workflow`, `rapo_workflow_version`, `rapo_workflow_run`,
`rapo_workflow_run_step`, `rapo_workflow_log`, the tables of the groups of transfers (`rapo_transfer_group`,
`rapo_transfer_group_member`, `rapo_transfer_group_run`, `rapo_transfer_group_run_step`, and the column
`rapo_transfer_run.group_run_id`), a row `RAPO.JOBS` of `rapo_scheduler`, the sections `[TRANSFER]` and
`[JOBS]` of `rapo.ini`, and the Python package `paramiko`.

**Stop every Rapo server that works on the database before step 3, and start none of v0.8.7 after it.** The scheduler
of v0.8.7 reads and changes `rapo_scheduler` without naming its row, so with the new row `RAPO.JOBS` in it, it may take
the wrong one. The new version names its row.

1. Wait until your Rapo controls are completed or cancel them. Stop the web server, on every host that has one.
    ```bash
    .venv/bin/rapo-server stop
    ```
1. Update the source in the application folder and reinstall it. The install adds `paramiko`, which the transfers from
   an SFTP server use.
    ```bash
    git fetch --tags --force
    git checkout v0.8.7.5
    ./install.sh
    ```
1. Upgrade Rapo's schema (the script can be run again).
    ```bash
    sqlplus <user>/<password>@<database> @migrations/v0.8.7.5/upgrade.sql
    ```
1. Set the new sections of `rapo.ini` (see `rapo.ini.example`):
    - `[TRANSFER]`: the file transfers are **off** (`enabled=False`). To use them, set `enabled=True` and `param_dir`,
      the folder of the `.param` files. They can also be switched on later on the *Configuration* tab of *Instance
      details*.
    - `[JOBS]`: the defaults switch the jobs on, with no job to run. Before the first job that runs a script or a
      program, set `allowed_dirs` (the folders they may be run from; with none set, no file is accepted), and consider
      `enabled_types` (for example without `COMMAND`), `default_email` for the failure mail of jobs and workflows (which
      needs `[EMAIL] enabled=True`) and `parallelism`.
1. Give the users the new area: the *Jobs* level of their API tokens (*Tokens* page). Tokens made before this release have
   none; *Viewer* and *Operator* presets get View and Operate, *Full* gets Edit, which runs programs and is meant for few.
1. Start the web server, on every host that has one.
    ```bash
    .venv/bin/rapo-server start
    ```
   Reload open browser tabs, so they load the new UI.

The *External* tab of the *Jobs* page reads the crontab of the user the server runs as (Linux), or the tasks of the Windows
Task Scheduler that run as that user, with PowerShell. The commands are shown in full: do not keep passwords in them.

## From v0.8.8 to v0.12.0

These releases were withdrawn and are part of v0.8.7.5. An installation on any of them runs `upgrade.sql` once more:
it makes only what is missing, which for v0.10.0, v0.11.0 and v0.12.0 is the groups of transfers (section 4; the Transfers
tab does not load without the column `rapo_transfer_run.group_run_id`). Take the new source, upgrade the schema and restart:
```bash
git fetch --tags --force --prune-tags
git checkout v0.8.7.5
./install.sh
sqlplus <user>/<password>@<database> @migrations/v0.8.7.5/upgrade.sql
.venv/bin/rapo-server stop && .venv/bin/rapo-server start
```
On v0.8.8 the file transfers had no switch and were always on: to keep them, set `[TRANSFER] enabled=True`.

## Going back to v0.8.7

Delete the row of the jobs first, or the old scheduler may take it:
```sql
delete from rapo_scheduler where id = 'RAPO.JOBS';
commit;
```
The other tables can stay; v0.8.7 does not use them, and `upgrade.sql` finds them again, and the row, when it is run once
more.
