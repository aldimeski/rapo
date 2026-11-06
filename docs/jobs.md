# Jobs

A **job** is something rapo runs besides the controls and the file transfers: a SQL script, a transfer, a program, a
command, or a script in one of several languages. Jobs have a page of their own (*Jobs*, between *Scheduler* and
*Transfers*), a scheduler of their own and an access area of their own (*Jobs*). A job always runs on the server where
rapo runs; running on other machines is not part of it.

The design and the reasons behind the choices are in [jobs-design.md](jobs-design.md); the routes are in the
[API reference](api/README.md#jobs).

## Contents

1. [Switching the jobs on, and who may use them](#switching-the-jobs-on-and-who-may-use-them)
1. [What a job is made of](#what-a-job-is-made-of)
1. [The types of a task](#the-types-of-a-task)
1. [Conditions](#conditions)
1. [Variables and parameters](#variables-and-parameters)
1. [Scheduling and running](#scheduling-and-running)
1. [Logs and the failure mail](#logs-and-the-failure-mail)
1. [Transfers in jobs](#transfers-in-jobs)
1. [Workflows](#workflows)
1. [Dependencies](#dependencies)
1. [Safety](#safety)
1. [rapo.ini `[JOBS]`](#rapoini-jobs)
1. [When a job does not run](#when-a-job-does-not-run)

## Switching the jobs on, and who may use them

The jobs and the workflows are **on by default** (`[JOBS] enabled=True`); with no job defined nothing runs. Set `enabled=False`, in `rapo.ini`
or on the *Configuration* tab of *Instance details* (it applies at once), to switch them off: the *Jobs* page and its
routes are not offered, and the job manager and the job scheduler stop. Jobs and their history stay in the database.

Access is the area **Jobs**, in three levels:

| Level   | Allows                                                                                                   |
|---------|----------------------------------------------------------------------------------------------------------|
| View    | See the jobs and workflows, their runs and logs, the upcoming fires and the job scheduler                |
| Operate | Also run a job or workflow now, cancel a run, switch one on or off, stop and start the job scheduler     |
| Edit    | Also create, change and delete jobs and workflows, and make transfer params for them                     |

A job runs programs as the user rapo runs as, so **Edit** is only in the *Full* token preset: *Viewer* gets View,
*Operator* gets Operate and *Editor* gets nothing. A job with SQL in it also needs **Free SQL**, because the SQL runs as
the schema owner. Tokens made before the jobs existed have no level of this area: edit them on the *Tokens* page.

## What a job is made of

A run goes through up to four parts, in this order:

| Part          | Optional | What it does                                                                                       | If it fails                                  |
|---------------|----------|----------------------------------------------------------------------------------------------------|----------------------------------------------|
| **Pre**       | yes      | A task that prepares what the others need                                                          | The run ends as an **error**; nothing else runs |
| **Condition** | yes      | Decides whether the job runs at all                                                                | Not met: the run is **canceled**, not an error. An error while checking it is an error |
| **Main**      | no       | The work of the job                                                                                | The run ends as an **error**; post does not run |
| **Post**      | yes      | A task after the main task; it runs **only when main succeeded**                                   | The run ends as an **error**, but the result of main stays as it was |

The order is the one of a control (preparation, then prerequisite, then the work, then completion): the condition is
checked after the pre task, so the pre task can prepare what the condition looks at.

A task **succeeds** when its exit code is `0` (a SQL task when it raises no error, a transfer when it moves its files
without a failure). What a program writes to stderr is only logged; it does not make the task fail.

Besides the parts a job has a name, a description, an on/off switch, a schedule, an address for the failure mail, a
**timeout**, **parameters**, and an **overlap** policy:

* **Timeout** (seconds): a run still going after it is stopped and ends as an error. Empty: `[JOBS] timeout` (3600);
  `0`: no limit.
* **Overlap**: what a start does while the job is still running or waiting. *Skip* (the default): a manual start is
  refused, a scheduled one is recorded as a canceled run. *Queue*: it waits for the previous run to end. *Allow*: the runs
  go side by side.

Every save of the definition or the schedule is a **version**; the *Versions* tab shows them and loads an older one into
the editor, to be saved as a new version. The name of a job is not changed after it is created.

## The types of a task

The types are the ones of the OS of the server, and the ones listed in `[JOBS] enabled_types` (empty: all of them). A type
whose interpreter was not found on the server is shown greyed, with the reason.

| Type         | OS      | What it runs                                                                                    |
|--------------|---------|-------------------------------------------------------------------------------------------------|
| `SQL`        | all     | A statement or a script in the rapo database                                                    |
| `TRANSFER`   | all     | A [transfer](transfer.md) param, with arguments                                                 |
| `EXECUTABLE` | all     | A program with arguments, started directly (no shell)                                           |
| `COMMAND`    | all     | A command line run by the shell of the OS (pipes and redirects work)                            |
| `PYTHON`     | all     | A Python script or code                                                                         |
| `PERL`       | all     | A Perl script or code                                                                           |
| `SHELL`      | Linux   | A `sh` or `bash` script or code                                                                 |
| `BATCH`      | Windows | A `.bat` or `.cmd` file or code                                                                 |
| `POWERSHELL` | Windows | A PowerShell script or code (`powershell` or `pwsh`)                                            |

**SQL.** One statement, or a script: plain statements end with `;`, a PL/SQL block with a `/` alone on its line. Only DML
and PL/SQL are allowed (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CALL`, `BEGIN`/`DECLARE`, `COMMIT`, `ROLLBACK`,
`SAVEPOINT`); `CREATE`, `DROP`, `TRUNCATE` and the like are refused. The statements run one after another in one
transaction, in the database rapo works in (never another one), and are committed at the end; an error rolls them all back.
A query shows its first 20 rows in the log. A PL/SQL block is not looked into, which is why saving SQL needs Free SQL.

**Programs and scripts.** A script is a *file* or *code*; code is written to a file of the run, run, and deleted. A `file`
(and the program of an `EXECUTABLE`) must be an absolute path below `[JOBS] allowed_dirs`. A task may also set
**arguments** (one per line), a **working folder** (default: the folder of the program or script, or a folder of the run
for code and commands), and **environment** variables (`NAME=value`, one per line). Python is started with `-u`, so its
output comes line by line; PowerShell with `-NoProfile -NonInteractive -ExecutionPolicy Bypass`; a batch file with
`cmd /d /c call`. `PYTHON` and `PERL` may name another **interpreter**, `SHELL` and `POWERSHELL` a **shell**.

## Conditions

| Kind                | Met when                                                                                           |
|---------------------|----------------------------------------------------------------------------------------------------|
| SQL query           | One `SELECT` (or `WITH`) returns a first value that is not empty and not `0`, `N` or `false`. It is run read only |
| Exit code           | A program, command or script exits with `0`                                                        |
| File exists         | A file or folder exists (or, for *it does not exist*, does not). Date tokens like `<YYYYMMDD>` are filled in |
| Files in a folder   | The number of files of a folder that match a mask (a regular expression) is within `min` and `max`. A missing folder is an error |
| Run status          | Other runs (controls, transfers, jobs, workflows) have ended well: see [Dependencies](#dependencies)  |

A condition that is not met ends the run as *Canceled* with the message *The condition was not met*, and the value it got
is kept with the run.

## Variables and parameters

In the text of a task (SQL, commands, arguments, code, paths, the arguments of a transfer) `{name}` and `{name:format}`
are filled in when the run starts. The variables of every run are:

`{job_name}`, `{job_id}`, `{run_id}`, `{trigger}` (`schedule` or `manual`), `{scheduled_time}` and `{start_time}` (dates:
`{scheduled_time:%Y%m%d}`), and the **parameters** of the job. **Only these names are replaced**: any other braces (a
shell `${HOME}`, awk's `{print $1}`, a Python dict) stay as written, so scripts need no escaping.

A **parameter** has a name, a value and a *secret* switch. Programs also get them in their environment as
`RAPO_PARAM_<NAME>`, with `RAPO_JOB_NAME`, `RAPO_JOB_ID`, `RAPO_RUN_ID` and `RAPO_TRIGGER`; read a secret from there rather
than put it on a command line. The value of a secret is **never sent to the browser** (it shows as `********`; saving the
job with it so keeps the stored one) and is replaced by `***` in the log. A run started by hand (*Run with parameters...*)
may give other values for that run only.

## Scheduling and running

A job is scheduled like a control: daily, weekly, monthly, or with ranges, steps and lists of days, hours, minutes and
seconds (the *Schedule* tab has the same box). There is no cascade; jobs are not chained yet (see below).

The **job scheduler** starts the scheduled jobs. It is independent of the scheduler of the controls: its own lease (one
server schedules, the others stand by and take over), its own *Stop scheduler* and *Start scheduler* button on the *Jobs*
page, which apply to every server, its own indicator in the header. A fire found more than a minute late (the scheduler was
stopped, or the host suspended) is recorded as *missed* and not run, as is a fire that fell into the downtime of the
scheduler, for up to `[JOBS] missed_window_hours`.

Every run is recorded at once, waits in a queue for one of the `[JOBS] parallelism` slots, and runs **in a process of its
own**. A run that is canceled, or goes over its timeout, is stopped by killing its process **with everything it started**.
The slots are read at every start, so a change of `parallelism` applies at once. A run that a crash of the server left
active is finished as an error when the server starts again.

A job is also started by hand: *Run now* (or *Run with parameters...*) on the page. An off job is not scheduled, but can
still be run by hand.

## Logs and the failure mail

The log of a run has what the program wrote to stdout and stderr, and what rapo says (which part started, the exit code, the
SQL statements and the rows they changed, the value of a condition). The *Runs* dialog colours the lines: what the program
wrote to stderr stands out, an error of rapo is red, and the notes of rapo are grey. The two streams are read apart, so
lines of stdout and stderr may come in another order than the program wrote them.

At most `[JOBS] max_output_kb` (1024) of output is kept per run, the rest is dropped with a note; the newest
`[JOBS] log_max_lines` (5000) lines of each job are kept in the database, and runs and events older than
`[JOBS] history_retention_days` (90) are deleted by the maintenance of the job scheduler.

When a run ends as an **error** (any part, a timeout, or a run whose process died) rapo sends **one e-mail** to the address
of the job, or else to `[JOBS] default_email`, with the run, the part it got to, the exit code, the message and the last 40
lines of the log. A run that is canceled by the user, one whose condition was not met, a skipped start and a late or missed
fire are not errors and send nothing. The mail needs `[EMAIL] enabled=True`; when it can not be sent the run is still an
error, and *what became of the mail* is shown with the run (`mail_status`). The *Jobs* page says when the failure mail is
off, or when jobs have no address for it.

## Transfers in jobs

A `TRANSFER` task runs a transfer param, kept as a file in `[TRANSFER] param_dir` or in the database, and may give
**arguments**: `Name:value` lines that replace or add parameters of the param **for this job only**, e.g.
`FileMask:<YYYYMMDD-1d>\.csv`. The param itself is not changed. A job runs a transfer **whether or not** the *Transfers*
tab is switched on (`[TRANSFER] enabled`).

In the editor, **New param...** makes a param for the job in the database (named after the job), and **Edit param...**
changes a param kept there; a param kept in a file is only read. Changing a param says which other jobs run it. A param made
this way is listed on the *Transfers* tab when it is on, without a schedule of its own: the job is what is scheduled. A
param that is also scheduled on the *Transfers* tab would run twice, so use one of the two.

## Workflows

A **workflow** is a named list of jobs run **one after another**: when a step ends, the next one starts. It has its own tab
on the *Jobs* page (*Workflows*), its own editor, and its own schedule, run history and failure mail. There is no
branching and there are no parallel steps.

| Part            | What it is                                                                                                  |
|-----------------|-------------------------------------------------------------------------------------------------------------|
| **Steps**       | The jobs, in order. At least one. **A job can be in a workflow once**: the editor does not offer it again, and a save names a job that is repeated and the steps it is at ("load is repeated, at steps 2 and 5") |
| **Parameters**  | As a job has them. In a step, a parameter of the workflow **overrides** the parameter of the same name of the job, for that run only; a job that has no such parameter is not given it. Secrets are hidden in the logs of the steps too |
| **Schedule, on/off** | As a job: a schedule of the job scheduler, no cascade. An off workflow is not scheduled and can still be run by hand |
| **E-mail**      | One address for the whole workflow; empty: `[JOBS] default_email`                                           |
| **Timeout**     | Seconds for the **whole** workflow (empty: `[JOBS] timeout`; `0`: none). Each step also keeps the timeout of its job |
| **Overlap**     | As a job: *Skip* (a manual start is refused, a scheduled one is recorded as canceled), *Queue* (waits for the previous run of the workflow) or *Allow* |
| **Versions**    | Every save of the definition or the schedule is a version, as for a job                                      |

**How a run goes.** The workflow takes no slot of `[JOBS] parallelism`; each step is an **ordinary run of its job**: it is
queued for a slot, runs in a process of its own, has its own log, and is listed with the runs of the job (as *Workflow*,
with the run of the workflow it belongs to). If the job is already running (by hand, by its own schedule, or as a step of
another workflow) the step **waits for it to end** and then starts, whatever the overlap of the job. A job that is switched
off still runs as a step: only its own schedule is off.

| A step ends as                         | The workflow                                                                                          |
|----------------------------------------|-------------------------------------------------------------------------------------------------------|
| Done                                   | Goes on with the next step; after the last it is **Done**                                              |
| Error (any part, a timeout, a dead process) | **Stops as an error**; the later steps are *Skipped*; one failure mail is sent                    |
| Canceled because its **condition was not met** | **Stops as canceled**, shown as a **warning** (*Stopped*, amber): the message and the log say where and why ("Stopped at step 2 of 4 (job `load-files`): The condition was not met."); the later steps are *Skipped*; no mail |
| Canceled by someone                    | Stops as canceled, as a warning; no mail                                                               |

**Cancel** of a workflow run cancels the step that runs (it is killed with everything it started), skips the rest, and
ends as canceled. A workflow over its timeout does the same and ends as an error.

**The failure mail.** A workflow that ends as an **error** sends **one** e-mail, to its address, or else to
`[JOBS] default_email`. It names the step that failed, lists every step with how it ended, and has the last lines of the
log of the failed step and of the workflow. The **mail of a job is not used** while it runs as a step of a workflow, and the
run of the job says so. A workflow that stops as canceled (a warning) sends nothing. As for a job, *what became of the mail*
is shown with the run, and the mail needs `[EMAIL] enabled=True`.

**The history** of a workflow run lists its steps with their status, duration and message; click a step to read the log of
its run of the job. The log of the workflow itself tells which step starts, how each ends, and where and why it stopped; a
warning is amber, an error red.

**Deleting a job that is in workflows** says which ones and asks for confirmation. The job is then taken out of their steps
(a new version of each), and a workflow left **without steps** is switched off and listed as having none; it can not be run
until a job is added. A job is told in its editor (and in the list) which workflows it is a step of. A job keeps its own
schedule and can still be run by hand.

## Dependencies

The condition **Run status** makes a job (or the start of a workflow) wait for **other runs**: a *control*, a *transfer*,
a *job* or a *workflow*. It has one or more **targets**, each with a **window**, and is met when **every** target is.

| Window                | The target is fine when                                                                             |
|-----------------------|------------------------------------------------------------------------------------------------------|
| Ended today           | its last run that ended, ended today                                                                 |
| Ended in the last N hours | it ended within N hours (1 to 720), counted from the **end** of the run                           |
| Ended since the last successful run | it ended after the last successful run of this job or workflow (or, when there is none, after it was made) |
| Has the data of a day | **controls only**: the last run for the day of its data (`date_from`) `N` days back (0 to 31; 0 is today) |

For a target the **last run that ended counts, and it must be Done**: an earlier good run does not make up for a later
failed one. A target that is **running (or waiting) now is not fine**. A control is *Done* in status `D`; a transfer, a job
or a workflow in status `D` as well. A transfer is read whether or not `[TRANSFER] enabled` is on.

A job whose condition is not met ends as *Canceled*, with the reason in the message: "The condition was not met: the control
DEMO_BILLING has not ended today (last at 2026-10-30 22:52); the job prep is running". A **workflow** may have a condition
too (the *Condition* tab), checked once **before its first step**: it may be Run status, File exists or Files in a folder.
When it is not met no step runs and the run ends as canceled, shown as a **warning** (*Stopped at the start of the
workflow: ...*), without a mail.

* **A target that does not exist is an error**, not "not met" (the failure mail is sent). *Check* in the editor tells it
  before; saving is not refused, as a control may be made later. A job and a workflow can not wait for themselves.
* **The lists show the last real run.** A job that polls (scheduled every few minutes, waiting for a control) would end
  almost every run canceled; the list shows the last run that really ran, with a note *condition not met at 07:10 (12 times
  since)*, and the *Runs* dialog can **hide** such runs. They are recorded all the same, and kept as long as other runs.
* The list says what a job or workflow **waits for** (hover for the windows).
* No new access right: the *Jobs* area covers it. The names to pick from are read with `get-dependency-targets` (View).

## Safety

A job runs programs as the user rapo runs as, and SQL as the schema owner. What limits it:

* the **Jobs: Edit** level, in the *Full* preset only, and **Free SQL** for SQL;
* `[JOBS] allowed_dirs`: a script file or program must be below one of these folders (empty: none is accepted).
  `COMMAND` is a command line and an `interpreter` an absolute path, so neither is limited by it: leave `COMMAND` out of
  `enabled_types` where that is not wanted;
* `[JOBS] enabled_types`: the types a server offers;
* a **cleaned environment**: a program gets the essentials of the OS (`PATH`, `SystemRoot`, `TEMP`, `HOME`, ...) and the
  variables listed above, never the environment of rapo, which may hold passwords;
* secrets are hidden in the log, in the e-mail, and in everything the browser gets.

## rapo.ini `[JOBS]`

| Option                    | Default | Meaning                                                                                     |
|---------------------------|---------|---------------------------------------------------------------------------------------------|
| `enabled`                 | `True`  | Show and run the jobs here; a change from the UI applies at once                            |
| `parallelism`             | `4`     | Jobs executed at once on this server; more wait in a queue                                  |
| `timeout`                 | `3600`  | Seconds after which a run is stopped (a job may set its own); `0`: no limit                 |
| `history_retention_days`  | `90`    | Days the history of runs is kept                                                            |
| `log_max_lines`           | `5000`  | Log lines kept per job                                                                      |
| `max_output_kb`           | `1024`  | Output kept per run                                                                         |
| `enabled_types`           | all     | Job types offered, e.g. `SQL,TRANSFER,PYTHON,SHELL`                                         |
| `allowed_dirs`            | none    | Folders scripts and programs may be run from, comma separated                               |
| `default_email`           | none    | Address of the failure mail of a job that has none of its own                               |
| `python_path`, `perl_path`, `shell_path`, `powershell_path` | found | Interpreters; Python: the one rapo runs in |
| `lease_timeout`, `refresh_interval`, `maintenance_interval`, `event_retention_days`, `missed_window_hours` | 60, 300, 86400, 90, 24 | As the options of `[SCHEDULER]`, for the job scheduler |

See `rapo.ini.example`; the options can be changed on the *Configuration* tab of *Instance details*.

## When a job does not run

* **"The job is not fit to run on this server"** in the run: the definition is checked again at every run. The message names
  what is wrong: a type that is not in `enabled_types` or not of this OS, an interpreter that was not found, a file outside
  `allowed_dirs` or that does not exist. *Check* in the editor finds the same without running the job.
* **Nothing is started on the schedule**: the *Jobs* page shows the state of the job scheduler (*Stopped* from the UI,
  *Standby* when another server schedules, *Off* when `[SCHEDULER] enabled` is off for this server or in development mode,
  where `rapo-server start dev --scheduler` is needed). A job that is off is not scheduled.
* **A run stays *Initiated***: all `parallelism` slots are busy, or an earlier run of the same job is still going and the
  overlap is *Queue*.
* **A scheduled start is *Canceled, skipped***: the overlap is *Skip* and the previous run was still going.
* **No mail**: see `mail_status` of the run: no address, `[EMAIL] enabled` off, or the SMTP server refused.
* **The job tables are missing** (`503`): run `migrations/v0.9.0/upgrade.sql`.

## Not yet

A workflow has no branching, no parallel steps, and no step that goes on after a failure. The jobs that start outside rapo (the crontab, the
Windows Task Scheduler) are not listed, as the transfers started by the crontab are. The `SHELL` type and the paths for a
Linux server are written but have so far been run on Windows only.
