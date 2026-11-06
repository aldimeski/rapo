# Rapo v0.11.0 Migration Instructions

Upgrades Rapo from v0.10.0 to v0.11.0. Commands run in the application folder.
What the release contains is in the [change log](CHANGELOG.md).

The release adds the condition *Run status* (dependencies). There is **no change to the database**, no new option and no new
Python package, so there is no `upgrade.sql`.

1. Update the source in the application folder and reinstall it.
    ```bash
    git fetch
    git checkout v0.11.0
    ./install.sh
    ```
1. Restart the web server, on every host that has one. A server of v0.10.0 that keeps running does not know the new
   condition: a job or workflow that has it is refused there, so restart them all.
    ```bash
    .venv/bin/rapo-server stop && .venv/bin/rapo-server start
    ```
   Reload open browser tabs, so they load the new UI.
1. Tokens of the area *Jobs* need no change: View reads the names a condition can wait for, Edit makes the conditions.

To go back to v0.10.0 nothing has to be removed. A job or workflow saved with a condition *Run status* is then not fit to run
(its condition kind is unknown): take the condition out before going back.
