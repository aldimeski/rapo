# Rapo v0.12.0 Change Log

## Annotation
What the **crontab** (Linux) or the **Windows Task Scheduler** of the server starts is now listed on the Jobs page, with the
jobs of rapo that it also starts. There is no database change and no new option (no `upgrade.sql`); the upgrade steps are in
the [migration instructions](README.md).

1. **The External tab.** Every entry of the crontab of the user the server runs as, or every task of the Windows Task
   Scheduler that runs as that user: its schedule (a task's triggers in words), next start, the whole command, the folder and
   user, and for the Task Scheduler the state and the result of the last run. **Read only**: rapo never changes the crontab or a
   task. The tab is always shown; it is read when first opened and again with *Rescan*. See
   [docs/jobs.md](../../docs/jobs.md#started-outside-rapo) and [docs/jobs-design.md](../../docs/jobs-design.md), section 13.
1. **Linked to jobs.** An entry is linked to a job when it starts what the main task of the job runs: its script or program, or
   its transfer param, found in the command or in the scripts it runs (two levels deep). The job says **Also started outside
   rapo** in the list and in its editor, as a warning that it runs twice when it also has a schedule in rapo.
1. **Windows is read with PowerShell** (`Get-ScheduledTask`), not `schtasks`, so a localized Windows works. Tasks of the system and
   of other users are not listed. **Linux** parsing reuses the crontab reader of the transfers, which works while
   `[TRANSFER] enabled` is off.
1. **API.** `get-external-tasks` (area *Jobs*, View) and `external` in `get-jobs` and `get-job`
   ([docs/api](../../docs/api/README.md#started-outside-rapo)).

The crontab reading has been tested on text only, not yet on a Linux server; the Task Scheduler was run on Windows. Not in this
release: a condition that waits for what starts outside rapo, and changing the crontab or the Task Scheduler from rapo.
