# Rapo v0.8.9 Change Log

## Annotation
The file transfers get a switch, `[TRANSFER] enabled`, which is **off by default**, so an existing installation that
uses them must set it. Saving an option of `rapo.ini` from the UI no longer breaks the next save on Windows, and the
KPI tab of the control editor has its `+` next to the last KPI again. There is no change to Rapo's own schema. The
upgrade steps are in the [migration instructions](README.md).

1. **Switch for the file transfers.** `[TRANSFER] enabled` (default `False`) shows and runs the transfers. While it is
   off the *Transfers* menu item and page are hidden (a direct link goes to the first page the token may open), every
   transfer route answers `404`, the transfer manager does not start, the scheduler fires nothing and cleans nothing
   for transfers, and the crontab is not scanned. Params, schedules and runs in the database and the `.param` files
   stay as they are. It can be changed on the *Configuration* tab of *Instance details* and applies at once: switching
   on starts the transfer manager, switching off stops it and cancels the transfers that wait or run. `GET /api/info`
   has the new `transfers_available`. See [docs/transfer.md](../../docs/transfer.md#switching-the-transfers-on).
1. **`rapo.ini` keeps its line endings when saved from the UI.** On Windows the first change made on the *Configuration*
   tab turned a file with LF line endings into CRLF, and the checksum taken when reading no longer matched the one
   taken when saving, so every later change failed with *rapo.ini changed on disk meanwhile*. The file is now read and
   written as it is.
1. **KPI tab of the control editor.** The `+` button beside the `-` of the last KPI row is back, and the *Add KPI*
   button of a control without KPIs with it; the *Add KPI* button next to the KPI tabs stays, so a KPI can be added
   from any of them.
