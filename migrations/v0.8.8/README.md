# Rapo v0.8.8 Migration Instructions

Upgrades Rapo from v0.8.7 to v0.8.8. Commands run in the application folder.
What the release contains is in the [change log](CHANGELOG.md).

The release adds the file transfers: the tables `rapo_transfer_param`, `rapo_transfer_schedule` and
`rapo_transfer_run`, the log table `rapo_transfer_log`, and the `[TRANSFER]` section of `rapo.ini`.

1. Update the source in the application folder and reinstall it.
    ```bash
    git fetch
    git checkout v0.8.8
    ./install.sh
    ```
1. Upgrade Rapo's schema (the script can be run again).
    ```bash
    sqlplus <user>/<password>@<database> @migrations/v0.8.8/upgrade.sql
    ```
1. Set `[TRANSFER] param_dir` in `rapo.ini` to the folder of the `.param` files (see `rapo.ini.example`).
   The install adds `paramiko`, which the transfers from an SFTP server use.
1. Restart the web server.
    ```bash
    .venv/bin/rapo-server stop && .venv/bin/rapo-server start
    ```
