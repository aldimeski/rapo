# Rapo v0.12.1 Migration Instructions

Upgrades Rapo from v0.12.0 to v0.12.1. Commands run in the application folder.
What the release contains is in the [change log](CHANGELOG.md).

The release fixes the schema file for **new** installations. There is **no change to the database** of an installation that
exists, no new option and no new Python package, so there is no `upgrade.sql`. The application is the same as v0.12.0.

1. Update the source in the application folder and reinstall it.
    ```bash
    git fetch
    git checkout v0.12.1
    ./install.sh
    ```
1. Restart the web server (only to show the new version number).
    ```bash
    .venv/bin/rapo-server stop && .venv/bin/rapo-server start
    ```

**An installation that was upgraded** through the releases has the job and workflow tables already, from
`migrations/v0.9.0/upgrade.sql` and `migrations/v0.10.0/upgrade.sql`. If one of them was skipped (the *Jobs* page says the tables are
missing), run it now; both can be run again.

**A new installation** deploys `schema/oracle.sql` once and then needs none of the `upgrade.sql` of the releases: it has the tables of
the file transfers, the jobs and the workflows, and the row `RAPO.JOBS` of the lease of the job scheduler.
