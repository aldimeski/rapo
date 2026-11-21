# Rapo v0.8.7.5 Change Log

## Annotation
Everything after v0.8.7 in one release: the **file transfers**, the **jobs** (RAJO, Revenue Assurance Job Orchestrator)
with their own scheduler, **workflows**, **dependencies** (the condition *Run status*) and the list of what the crontab or
the Windows Task Scheduler of the server starts. New tables of the transfers (`rapo_transfer_*`), the jobs (`rapo_job*`)
and the workflows (`rapo_workflow*`) and a row `RAPO.JOBS` of `rapo_scheduler`, all made by one script,
`migrations/v0.8.7.5/upgrade.sql`; new sections `[TRANSFER]` and `[JOBS]` of `rapo.ini`; new requirement `paramiko`; a new
access area *Jobs*. The file transfers are **off by default** (`[TRANSFER] enabled`); the jobs are on, and nothing runs
until a job is defined. The upgrade steps are in the [migration instructions](README.md); **all servers on the database
must be stopped for them**.

This release replaces the releases v0.8.8, v0.8.9, v0.9.0, v0.10.0, v0.11.0 and v0.12.0, which were published for a short
time and are withdrawn. An installation on one of them already has the schema of v0.8.7.5 (see the migration instructions).

### File transfers

1. **File transfers.** The logic of [filetransfer-py](https://github.com/aldimeski/filetransfer-py) is part of rapo
   (`rapo/transfer/`): a transfer copies or moves files from a local directory or an SFTP server into one or more
   target directories (file mask with date tokens, file date, stable size, control file, gzip/gunzip, archive, log,
   e-mail on errors), described by the same `Name:value` params. The `file-transfer.py` script is not used. See
   [docs/transfer.md](../../docs/transfer.md).
1. **Switch for the file transfers.** `[TRANSFER] enabled` (default `False`) shows and runs the transfers. While it is
   off the transfers are hidden in the UI, every transfer route answers `404`, the transfer manager does not start, the
   scheduler fires nothing and cleans nothing for transfers, and the crontab is not scanned for them. Params, schedules
   and runs stay as they are. It can be changed on the *Configuration* tab of *Instance details* and applies at once:
   switching on starts the transfer manager, switching off stops it and cancels the transfers that wait or run.
   `GET /api/info` has `transfers_available`. See [docs/transfer.md](../../docs/transfer.md#switching-the-transfers-on).
1. **Params as files or in the database.** A param is a `<name>.param` file in `[TRANSFER] param_dir` or a row of
   `rapo_transfer_param`; it is created in either place, moved between them, and keeps its schedule.
1. **Scheduled by rapo.** A transfer scheduled in rapo (`rapo_transfer_schedule`) is started by the scheduler holding
   the lease and performed by rapo in its own process, up to `[TRANSFER] parallelism` at once, with its runs and logs
   in `rapo_transfer_run`.
1. **Crontab detection (Linux).** The crontab of the server user is read, and the scripts it runs are opened to
   find the ones that start `file-transfer.py` with a `.param` file. The list shows such a param as started by the
   crontab (through which script, with its cron expression and next start), and flags a param that both rapo and the
   crontab start.
1. **Runs on Windows.** The package imports and the transfers run on Windows; the Linux-only parts are switched off
   there: the crontab scan of the transfers, the owner and group names of the datasource file lists (numbers instead)
   and the load average of the health tab (empty). See [docs/transfer.md](../../docs/transfer.md#windows).
1. **Log in the database.** `LogDatabase:Y` (params kept in the database only) writes the log of a run to
   `rapo_transfer_log` per `LogMode` instead of a file and ignores `LogDir` and `LogFile`; the newest
   `[TRANSFER] log_max_lines` (5000) lines per param are kept. A file param can not have it: it is not offered, refused on
   saving, and dropped when a param moves to a file, which warns and asks to confirm when the param has no `LogDir` and
   `LogFile` (`LogMode` becomes `None`).
1. **Param editor as a table.** Parameter and Value like Instance details, the description of a parameter on hover; a
   new param lists all parameters with `-` to hide one, an existing one lists those with a value and `+` adds the rest;
   comments of the file are kept.
1. **Transfers are a tab of the Jobs page** (`/jobs?tab=transfers`): every param with where it is kept, how it is
   started, the next start, the last run and the run history. The address `/transfers` opens it. A token that may not
   open the *Jobs* page has the page `/transfers` and its menu item instead.
1. **API.** `get-transfers`, `get-transfer`, `get-transfer-catalogue`, `validate-transfer`, `save-transfer`,
   `check-move-transfer`, `move-transfer` (with `confirm`), `delete-transfer`, `save-transfer-schedule`, `run-transfer`,
   `cancel-transfer`, `get-transfer-runs`, `get-transfer-run` (Datasources View/Edit, Files Operate).

### Jobs

1. **Jobs.** SQL scripts, transfers, programs and scripts that rapo runs on their own schedule, beside the controls and
   the file transfers. A job has up to four parts, run in this order: *pre*, *condition*, *main* and *post* (post only
   when main succeeded). Types: `SQL`, `TRANSFER`, `EXECUTABLE`, `COMMAND`, `PYTHON`, `PERL` on every OS, `SHELL` on
   Linux, `BATCH` and `POWERSHELL` on Windows. Conditions: a SQL query, an exit code, a file that exists, the number of
   files in a folder, and *Run status* (see Dependencies). A job has parameters (with secrets), a timeout, an overlap
   policy (skip, queue, allow) and versions. It always runs on the server where rapo runs. See
   [docs/jobs.md](../../docs/jobs.md) and [docs/jobs-design.md](../../docs/jobs-design.md).
1. **A job scheduler of its own.** It is independent of the scheduler of the controls: its own lease (the row
   `RAPO.JOBS`), stop and start switch, options and events (`rapo_job_event`), with the same handling of late and missed
   fires. Every run is queued for one of `[JOBS] parallelism` slots and performed in a process of its own; a canceled run
   or one over its timeout is stopped with everything it started.
1. **Failure mail.** An error in any part, a timeout or a run whose process died sends one e-mail to the address of the
   job, else to `[JOBS] default_email`, and marks the run as an error; a cancel, an unmet condition and a skipped start
   send nothing.
1. **Jobs page and editor.** *Jobs* between *Scheduler* and the rest, with its own indicator in the header and the tabs
   *Jobs*, *Workflows*, *Transfers* and *External*. The list (filters, next start, last run), run now or with parameters,
   cancel, switch on and off, the run history with the log (lines coloured by what wrote them) and the status of the job
   scheduler. The editor has the tabs General, Pre, Condition, Main, Post, Schedule and Versions, and a *Check* that asks
   the server whether the definition is fit to run.
1. **Transfer params from a job.** A `TRANSFER` task has *New param...* and *Edit param...*: a param is made in the
   database, or changed there, from the editor of the job, also while `[TRANSFER] enabled` is off; a param kept in a
   file is only read.
1. **Access.** The area *Jobs* (View, Operate, Edit): *Viewer* gets View, *Operator* Operate, *Editor* nothing, *Full*
   Edit. A job with SQL in it needs Free SQL. Tokens made before this release have no level of the area. Routes:
   `get-jobs`, `get-job`, `get-job-catalogue`, `check-job`, `save-job`, `delete-job`, `set-job-status`,
   `save-job-schedule`, `run-job`, `cancel-job-run`, `get-job-runs`, `get-job-run`, `get-job-versions`,
   `job-scheduler-status`, `-start`, `-stop`, `-upcoming`, `-events`, and `get-job-transfer-params`, `-catalogue`,
   `get-job-transfer`, `validate-job-transfer`, `save-job-transfer` ([docs/api](../../docs/api/README.md#jobs)); the live
   event `jobs:changed`; `jobs_available` in `GET /api/info`.
1. **The scheduler lease is bound to its row.** `rapo_scheduler` has a row for each scheduler (`RAPO.SCHEDULER`,
   `RAPO.JOBS`), but its statements named no row: the lease, the stop and start switch, `read_scheduler_record`, the CLI
   stop and the checkpoint clean-up would have read or changed the other row too. The lease logic is now a class
   (`rapo/core/lease.py`) used by both schedulers, and every statement names its row. **v0.8.7 must not run against a
   database that has the row `RAPO.JOBS`** (see the migration instructions).

### Workflows

1. **Workflows.** A named list of jobs run one after another, stopping at the first step that does not succeed. A
   workflow has steps (jobs, in order; at least one; a job at most once), parameters (a parameter of the workflow
   overrides that of the same name of the job for that run), a condition checked once before the first step, a timeout
   for the whole run, an overlap policy, a schedule, an address for the failure mail, an on/off switch and versions. See
   [docs/jobs.md](../../docs/jobs.md#workflows) and [docs/jobs-design.md](../../docs/jobs-design.md), section 11.
1. **How a run ends.** A step that is done goes on to the next. **An error** stops the workflow as an error: the later
   steps are *Skipped* and one failure mail is sent, to the address of the workflow or else `[JOBS] default_email`, with
   the steps and the output of the step that failed; **the mail of the job is not used** for a step. **A condition that
   is not met**, or a cancel, stops it as canceled, shown as a **warning** (*Stopped*): the message and the log say at
   which step and why, and no mail is sent. A workflow over its timeout is an error.
1. **Steps are ordinary job runs.** The workflow takes no slot of `[JOBS] parallelism`; each step is queued for a slot,
   runs in a process of its own, has its own log, and is listed with the runs of the job (trigger *Workflow*). A step
   waits for a run of its job that is still going. A job that is switched off still runs as a step. The secrets of the
   workflow are hidden in the logs of the steps.
1. **Workflows tab.** The list (steps, schedule, next start, last run and the step it stopped at), run, cancel, switch on
   and off, and the run history with the steps, their logs and the log of the workflow. The editor has the tabs General,
   Steps, Condition, Schedule and Versions, and a *Check*. A job says which workflows it is a step of.
1. **Deleting a job that is in workflows** says which ones and asks for confirmation (`delete-job` answers `409` until
   it is given `confirm`). The job is taken out of their steps, and a workflow left without steps is switched off.
1. **API.** `get-workflows`, `get-workflow`, `check-workflow`, `save-workflow`, `delete-workflow`,
   `set-workflow-status`, `save-workflow-schedule`, `run-workflow`, `cancel-workflow-run`, `get-workflow-runs`,
   `get-workflow-run`, `get-workflow-versions`, all in the area *Jobs* ([docs/api](../../docs/api/README.md#workflows));
   `get-jobs` and `get-job` list the `workflows` of a job; `job-scheduler-upcoming` and `-events` include the workflows.

### Dependencies

1. **The condition *Run status*.** One or more *targets*, each a control, a transfer, a job or a workflow by name, with
   a *window*: ended **today**, ended in the last **N hours** (1 to 720), ended **since the last successful run** of the
   job or workflow that has the condition, or, for a control only, **has the data of a day** (`date_from`, 0 to 31 days
   back). It is met when **every** target is: none of its runs is going or waiting, and its **last run that ended is
   Done** and falls in the window. See [docs/jobs.md](../../docs/jobs.md#dependencies) and
   [docs/jobs-design.md](../../docs/jobs-design.md), section 12.
1. **When it is not met.** A job ends *Canceled* with the reason in its message and log ("The condition was not met:
   the control X has not ended today (last at ...)"); a workflow ends *Canceled* as a **warning** and no step runs; no
   mail is sent. A target that **does not exist**, or that can not be read, is an **error**. *Check* names the targets
   that do not exist.
1. **Lists show the last real run.** Runs that a condition that was not met canceled are noted ("condition not met at
   07:10 (12 times since)") and can be hidden in the *Runs* dialog. The lists also say what a job or workflow **waits
   for**.
1. **API.** `get-dependency-targets` (area *Jobs*, View); `check-job` and `check-workflow` take `name` and answer
   `missing`; `get-jobs` and `get-workflows` add `waits_for` and `unmet`; `hide_unmet` on `get-job-runs` and
   `get-workflow-runs` ([docs/api](../../docs/api/README.md#dependencies)).

### Started outside rapo

1. **The External tab.** Every entry of the crontab of the user the server runs as (Linux), or every task of the
   Windows Task Scheduler that runs as that user: its schedule, next start, the whole command, the folder and user, and
   for the Task Scheduler the state and the result of the last run. **Read only.** It is read when first opened and again
   with *Rescan*. Windows is read with PowerShell (`Get-ScheduledTask`), so a localized Windows works. See
   [docs/jobs.md](../../docs/jobs.md#started-outside-rapo) and [docs/jobs-design.md](../../docs/jobs-design.md),
   section 13.
1. **Linked to jobs.** An entry that starts what the main task of a job runs (its script, program or transfer param)
   names the job, and the job says **Also started outside rapo** in the list and in its editor.
1. **API.** `get-external-tasks` (area *Jobs*, View) and `external` in `get-jobs` and `get-job`
   ([docs/api](../../docs/api/README.md#started-outside-rapo)).

### Other changes

1. **`rapo.ini` keeps its line endings when saved from the UI.** On Windows the first change made on the
   *Configuration* tab turned LF into CRLF, and every later change failed with *rapo.ini changed on disk meanwhile*.
1. **KPI tab of the control editor.** A new *Add KPI* button next to the KPI tabs; the `+` beside the last KPI row stays.
1. **`rapo-server start dev --scheduler` starts the scheduler**, which it never did; a development server started with
   the flag runs the scheduler of the controls and of the jobs.
1. **Schema of a new installation.** `schema/oracle.sql` makes the tables of the transfers, the jobs and the workflows
   and the row `RAPO.JOBS`; a new database needs no `upgrade.sql`.

Not tested yet: the crontab reading on a Linux server (tested on text only) and the `SHELL` type on Linux. Not in this
release: branching, parallel steps, a step that goes on after a failure, and a condition that waits for what starts
outside rapo.
