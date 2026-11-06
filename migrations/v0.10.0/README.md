# Rapo v0.10.0 Migration Instructions

Upgrades Rapo from v0.9.0 to v0.10.0. Commands run in the application folder.
What the release contains is in the [change log](CHANGELOG.md).

The release adds the workflows: the tables `rapo_workflow`, `rapo_workflow_version`, `rapo_workflow_run`,
`rapo_workflow_run_step` and `rapo_workflow_log`, and the column `workflow_id` of `rapo_job_event`. There is no new option, no
new Python package, and no change to the rows of `rapo_scheduler`.

1. Update the source in the application folder and reinstall it.
    ```bash
    git fetch
    git checkout v0.10.0
    ./install.sh
    ```
1. Upgrade Rapo's schema (the script can be run again). Do it before the new version starts: without the tables the
   workflows are not listed or scheduled, the job scheduler logs that they are missing, and the *Workflows* tab says so;
   the jobs themselves are not affected.
    ```bash
    sqlplus <user>/<password>@<database> @migrations/v0.10.0/upgrade.sql
    ```
1. Restart the web server, on every host that has one. A server of v0.9.0 that keeps running does not run workflows; they
   are fired by the server of v0.10.0 that holds the lease of the job scheduler.
    ```bash
    .venv/bin/rapo-server stop && .venv/bin/rapo-server start
    ```
   Reload open browser tabs, so they load the new UI.
1. Tokens of the area *Jobs* need no change: View sees the workflows, Operate runs and cancels them, Edit makes, changes and
   deletes them. Set `[JOBS] default_email` if workflows without an address of their own should still tell somebody when
   they fail (it needs `[EMAIL] enabled=True`).

To go back to v0.9.0 nothing has to be removed: the tables and the column stay, and the older version does not use them.
Its *Jobs* page then does not show the workflows, and a job that is in a workflow can be deleted without being asked.
