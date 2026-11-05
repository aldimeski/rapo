# Rapo v0.9.0 Change Log

## Annotation
The **jobs** (RAJO, Revenue Assurance Job Orchestrator): SQL scripts, transfers, programs and scripts that rapo runs on
their own schedule, beside the controls and the file transfers. New tables `rapo_job`, `rapo_job_version`, `rapo_job_run`,
`rapo_job_log`, `rapo_job_event` and a row `RAPO.JOBS` of `rapo_scheduler` (`migrations/v0.9.0/upgrade.sql`); new section
`[JOBS]` of `rapo.ini`; a new access area *Jobs*. The jobs are on by default and nothing runs until a job is defined. The
upgrade steps are in the [migration instructions](README.md); **all servers on the database must be stopped for them**.

1. **Jobs.** A job has up to four parts, run in this order: *pre*, *condition*, *main* and *post* (post only when main
   succeeded). Types: `SQL`, `TRANSFER`, `EXECUTABLE`, `COMMAND`, `PYTHON`, `PERL` on every OS, `SHELL` on Linux, `BATCH` and
   `POWERSHELL` on Windows. Conditions: a SQL query, an exit code, a file that exists, the number of files in a folder. A job
   has parameters (with secrets), a timeout, an overlap policy (skip, queue, allow) and versions. It always runs on the
   server where rapo runs. See [docs/jobs.md](../../docs/jobs.md) and [docs/jobs-design.md](../../docs/jobs-design.md).
1. **A job scheduler of its own.** It is independent of the scheduler of the controls: its own lease (the row `RAPO.JOBS`),
   stop and start switch, options and events (`rapo_job_event`), with the same handling of late and missed fires. Every run
   is queued for one of `[JOBS] parallelism` slots and performed in a process of its own; a canceled run or one over its
   timeout is stopped with everything it started.
1. **Failure mail.** An error in any part, a timeout or a run whose process died sends one e-mail to the address of the
   job, else to `[JOBS] default_email`, and marks the run as an error; a cancel, an unmet condition and a skipped start send
   nothing.
1. **Jobs page and editor.** *Jobs* between *Scheduler* and *Transfers*, with its own indicator in the header: the list
   (filters, next start, last run), run now or with parameters, cancel, switch on and off, the run history with the log
   (lines coloured by what wrote them) and the status of the job scheduler. The editor has the tabs General, Pre, Condition,
   Main, Post, Schedule and Versions, and a *Check* that asks the server whether the definition is fit to run.
1. **Transfer params from a job.** A `TRANSFER` task has *New param...* and *Edit param...*: a param is made in the
   database, or changed there, from the editor of the job, also while `[TRANSFER] enabled` is off; a param kept in a file
   is only read.
1. **Access.** The area *Jobs* (View, Operate, Edit): *Viewer* gets View, *Operator* Operate, *Editor* nothing, *Full* Edit.
   A job with SQL in it needs Free SQL. Tokens made before this release have no level of the area. Routes: `get-jobs`,
   `get-job`, `get-job-catalogue`, `check-job`, `save-job`, `delete-job`, `set-job-status`, `save-job-schedule`, `run-job`,
   `cancel-job-run`, `get-job-runs`, `get-job-run`, `get-job-versions`, `job-scheduler-status`, `-start`, `-stop`,
   `-upcoming`, `-events`, and `get-job-transfer-params`, `-catalogue`, `get-job-transfer`, `validate-job-transfer`,
   `save-job-transfer` ([docs/api](../../docs/api/README.md#jobs)); the live event `jobs:changed`; `jobs_available` in
   `GET /api/info`.
1. **The scheduler lease is bound to its row.** `rapo_scheduler` has a row for each scheduler (`RAPO.SCHEDULER`,
   `RAPO.JOBS`), but its statements named no row: the lease, the stop and start switch, `read_scheduler_record`, the CLI
   stop and the checkpoint clean-up would have read or changed the other row too. The lease logic is now a class
   (`rapo/core/lease.py`) used by both schedulers, and every statement names its row. **An older version must not run
   against a database that has the row `RAPO.JOBS`** (see the migration instructions).
1. **`rapo-server start dev --scheduler` starts the scheduler.** It never did: the command line passed "not given", which a
   development server turned into "off". A development server started with the flag now runs the scheduler of the controls
   and of the jobs.

Not in this release: workflows (jobs one after another, stopping at the first failure) and dependencies on controls and
transfers, which are the next step. The `SHELL` type and the paths for a Linux server have so far been run on Windows only.
