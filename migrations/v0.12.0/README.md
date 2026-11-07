# Rapo v0.12.0 Migration Instructions

Upgrades Rapo from v0.11.0 to v0.12.0. Commands run in the application folder.
What the release contains is in the [change log](CHANGELOG.md).

The release adds the *External* tab of the *Jobs* page. There is **no change to the database**, no new option and no new
Python package, so there is no `upgrade.sql`.

1. Update the source in the application folder and reinstall it.
    ```bash
    git fetch
    git checkout v0.12.0
    ./install.sh
    ```
1. Restart the web server, on every host that has one.
    ```bash
    .venv/bin/rapo-server stop && .venv/bin/rapo-server start
    ```
   Reload open browser tabs, so they load the new UI.
1. Nothing else is needed. The tab reads the crontab of the user the server runs as (Linux), or the tasks of the Windows Task
   Scheduler that run as that user, with PowerShell. Tokens of the area *Jobs* with View see it. The commands are shown in
   full: do not keep passwords in them.

To go back to v0.11.0 nothing has to be removed.
