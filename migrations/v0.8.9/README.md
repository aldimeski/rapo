# Rapo v0.8.9 Migration Instructions

Upgrades Rapo from v0.8.8 to v0.8.9. Commands run in the application folder.
What the release contains is in the [change log](CHANGELOG.md).

There is **no database schema change** and no new Python package.

The file transfers are now **off by default**. If you use them (v0.8.8), step 3 is required, otherwise the
*Transfers* page disappears and the transfers scheduled in rapo stop running.

1. Update the source in the application folder and reinstall it.
    ```bash
    git fetch
    git checkout v0.8.9
    ./install.sh
    ```
1. Restart the web server.
    ```bash
    .venv/bin/rapo-server stop && .venv/bin/rapo-server start
    ```
1. To keep the file transfers, set `enabled=True` in the `[TRANSFER]` section of `rapo.ini` (see `rapo.ini.example`),
   or switch it on in the *Configuration* tab of *Instance details*, which applies at once.
   Reload open browser tabs, so they load the new UI.
