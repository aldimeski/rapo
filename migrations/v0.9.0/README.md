# Rapo v0.9.0 Migration Instructions

Upgrades Rapo from v0.8.9 to v0.9.0. Commands run in the application folder.
What the release contains is in the [change log](CHANGELOG.md).

The release adds the jobs: the tables `rapo_job`, `rapo_job_version`, `rapo_job_run`, `rapo_job_log` and `rapo_job_event`,
a row `RAPO.JOBS` of `rapo_scheduler`, and the `[JOBS]` section of `rapo.ini`. There is no new Python package.

**Stop every Rapo server that works on the database before step 2, and start none of the old version after it.** The
scheduler of v0.8.9 and older reads and changes `rapo_scheduler` without naming its row, so with the new row `RAPO.JOBS` in
it, it may take the wrong one. The new version names its row.

1. Wait until your Rapo controls are completed or cancel them. Stop the web server, on every host that has one.
    ```bash
    .venv/bin/rapo-server stop
    ```
1. Update the source in the application folder and reinstall it.
    ```bash
    git fetch
    git checkout v0.9.0
    ./install.sh
    ```
1. Upgrade Rapo's schema (the script can be run again).
    ```bash
    sqlplus <user>/<password>@<database> @migrations/v0.9.0/upgrade.sql
    ```
1. Optionally set the `[JOBS]` section of `rapo.ini` (see `rapo.ini.example`). The defaults switch the jobs on, with no job
   to run. Before the first job that runs a script or a program, set `allowed_dirs` (the folders they may be run from; with
   none set, no file is accepted), and consider `enabled_types` (for example without `COMMAND`), `default_email` for the
   failure mail (which needs `[EMAIL] enabled=True`) and `parallelism`.
1. Give the users the new area: the *Jobs* level of their API tokens (*Tokens* page). Tokens made before this release have
   none; *Viewer* and *Operator* presets get View and Operate, *Full* gets Edit, which runs programs and is meant for few.
1. Start the web server.
    ```bash
    .venv/bin/rapo-server start
    ```
   Reload open browser tabs, so they load the new UI.

To go back to v0.8.9, delete the row of the jobs first, or the old scheduler may take it:
```sql
delete from rapo_scheduler where id = 'RAPO.JOBS';
commit;
```
The job tables can stay; `upgrade.sql` finds them again, and the row, when it is run once more.
