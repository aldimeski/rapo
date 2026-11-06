# RAJO - jobs: design

Design of the jobs of rapo (RAJO, Revenue Assurance Job Orchestrator), for the requirement in
`documents/RAJO.txt` (v1.0). Status: **implemented in 0.9.0+fork**; the user guide is [jobs.md](jobs.md). Workflows and
dependencies are the second phase (their hooks are prepared here).

## 1. What a job is

A job is a named definition of up to four parts, run in this order by one run:

| Part | Optional | Does | On failure |
|---|---|---|---|
| PRE | yes | a task run before the others | run ends **Error**, nothing else runs |
| CONDITION | yes | decides whether the job runs | false: run ends **Canceled** ("condition not met"); an error while evaluating: **Error** |
| MAIN | no | the work of the job | run ends **Error**; POST does not run |
| POST | yes | a task run after MAIN | run ends **Error**, the result of MAIN stays recorded |

POST runs only when MAIN succeeded. The order is the one of a control (`preparation_sql`, `prerequisite_sql`, work,
`completion_sql`), where the prerequisite is checked after the preparation.

A **task** has a type and its settings. PRE, MAIN and POST may be of different types. Success of a task is exit code `0`
(for SQL: no error, for TRANSFER: the run is `D`one with no failed file); output on stderr is only logged.

### Types

| Type | OS | Settings of the task |
|---|---|---|
| `SQL` | all | `text` (one command, or a script: statements ended by `;`, PL/SQL blocks ended by `/` on its own line). DML and PL/SQL only (a `SELECT` is allowed, DDL such as `CREATE`, `DROP`, `TRUNCATE` is not); the rapo database only. A PL/SQL block is not looked into (`execute immediate` is up to the one who has Free SQL) |
| `TRANSFER` | all | `param` (name; file or database) and `arguments`: `Name:value` lines that replace or add param parameters for this job only |
| `EXECUTABLE` | all | `path`, `args` (list), `cwd`, `env` - started directly, no shell |
| `COMMAND` | all | `command` (a command line run by the OS shell), `cwd`, `env` |
| `PYTHON` | all | `file` or `code`, `args`, `cwd`, `env`, `interpreter` (optional override) |
| `PERL` | all | same as `PYTHON` |
| `SHELL` | Linux | `file` or `code`, `args`, `cwd`, `env`, `shell` (`sh`/`bash`) |
| `BATCH` | Windows | `file` or `code`, `args`, `cwd`, `env` |
| `POWERSHELL` | Windows | `file` or `code`, `args`, `cwd`, `env`, `shell` (`powershell`/`pwsh`) |

The types a server offers are the ones of its OS, cut by `[JOBS] enabled_types`, and each needs its interpreter: the
interpreters are looked up at start (setting, else `PATH`; Python: the interpreter rapo runs in) and a type whose
interpreter is missing is shown as unavailable with the reason. `code` is written to a temporary file in a private
folder, run and deleted; with `file` (or the `path` of an `EXECUTABLE`) the path must be absolute and below `[JOBS] allowed_dirs`.
A `COMMAND` is a command line and an `interpreter` an absolute path, so neither is limited by `allowed_dirs`: leave `COMMAND`
out of `enabled_types` where that is not wanted.

### Condition kinds

| Kind | Settings | Met when |
|---|---|---|
| `SQL` | `text` returning one value | the value is not empty, not `0`, not `N`/`false` (as the prerequisite of a control) |
| `EXIT_CODE` | a task (any type except SQL/TRANSFER) | the exit code is `0` |
| `FILE_EXISTS` | `path` (may use date tokens), `exists`: true/false | the file or folder exists (or not) |
| `FILE_COUNT` | `directory`, `mask` (regex), `min`, `max` | the number of matching files is within the limits |

A condition kind of `EXIT_CODE` is a task of its own, so the `SQL` kind reads the rapo database read-only, as the
prerequisite statement of a control does (it reuses `rapo/core/sqlcheck.py` for the check).

### Variables

`{name}` and `{name:format}` (a `datetime` takes `strftime` codes, e.g. `{scheduled_time:%Y%m%d}`) in the text of a
task and in `arguments`: `job_name`, `job_id`, `run_id`, `trigger`, `scheduled_time`, `start_time` and the job's own
**parameters** (name, value, `secret`). Only these names are replaced; any other braces (a shell `${HOME}`, awk's
`{print}`, a Python dict) stay as written, so scripts need no escaping (`definition.render`). A secret is never written
to a log or shown in the UI.

## 2. Definition format

One row per job in `rapo_job`; the parts are one JSON document in `definition`:

```json
{
  "version": 1,
  "pre":       {"type": "SQL", "text": "update ..."},
  "condition": {"kind": "FILE_EXISTS", "path": "/data/in/<YYYYMMDD>.ok", "exists": true},
  "main":      {"type": "PYTHON", "file": "/opt/rapo/jobs/load.py", "args": ["--day", "{yesterday}"],
                "cwd": "/opt/rapo/jobs", "env": {"MODE": "daily"}},
  "post":      null,
  "parameters": [{"name": "db_user", "value": "x", "secret": false}],
  "timeout": 3600,
  "overlap": "skip"
}
```

`overlap` says what a start does while the previous run is still active: `skip` (default, recorded as Canceled),
`queue` or `allow`. `timeout` is seconds, `0` none (default `[JOBS] timeout`).

The definition is validated when it is saved and by a **Check** call that does not run it: known type for this OS and in
`enabled_types`, interpreter found, path below `allowed_dirs` and existing, SQL statements allowed (no DDL) and
parsable where Oracle can tell, TRANSFER param found and its arguments valid (same checks as *Check* of the Transfers
page), variables known, condition well formed.

## 3. Database

New tables (migration `migrations/v0.9.0/upgrade.sql`, written to be run again; like the transfer tables they are not in
`schema/oracle.sql`):

| Table | Holds |
|---|---|
| `rapo_job` | `job_id`, `job_name` (unique; 1-128 letters, digits, dots, hyphens, underscores, as a param name), `description`, `job_type` (type of MAIN, for lists), `definition` (clob), `schedule_config` (the JSON of a control: `mday`, `wday`, `hour`, `min`, `sec`), `status` (`Y`/`N`), `notify_email`, `created_*`, `updated_*`, `version` |
| `rapo_job_version` | `job_id`, `version`, `definition`, `schedule_config`, `updated_by`, `updated_date`: every save, as control versions |
| `rapo_job_run` | `run_id`, `job_id`, `job_name`, `trigger_type` (`schedule`, `manual`; second phase `workflow`), `scheduled_time`, `queued_time`, `start_time`, `end_time`, `status` (`I`,`S`,`D`,`E`,`C`), `stage` (`PRE`,`CONDITION`,`MAIN`,`POST`), `condition_value`, `exit_code`, `message`, `runner`, `requested_by`, `definition_version`, `log_lines`, `mail_status`, `workflow_run_id` (empty now) |
| `rapo_job_log` | `log_id`, `run_id`, `job_id`, `line_no`, `log_time`, `log_level`, `stream` (`out`, `err`, `rapo`), `message`; newest `[JOBS] log_max_lines` per job |
| `rapo_job_event` | the events of the job scheduler, as `rapo_scheduler_event`: `event_id`, `job_id`, `trigger_type`, `event_type`, `scheduled_time`, `event_time`, `run_id`, `message`, `runner` |

The lease of the job scheduler is a second row of the existing `rapo_scheduler` table (`id = 'RAPO.JOBS'`), so that it
is independent of the row `RAPO.SCHEDULER` of the controls, with the same columns (`status`, `heartbeat`,
`instance_id`, `disabled`).

A job is never deleted while a run of it is running or waiting; its runs and log lines are deleted by the maintenance
after `[JOBS] history_retention_days`.

## 4. Running

* **Lease** (`rapo/core/lease.py`): the lease logic of the scheduler of the controls is a class of its own, used by
  both schedulers: acquire or renew with a heartbeat, take over a silent holder, give up, stop and start for every
  server. Every statement names its row (`RAPO.SCHEDULER` or `RAPO.JOBS`), so the two never touch each other: the
  queries of the controls' scheduler, `reader.read_scheduler_record`, the CLI and the checkpoint clean-up of
  `database.py` named no row before and are bound to `RAPO.SCHEDULER` now.
* **Job scheduler** (`rapo/jobs/scheduler.py`): the same fire, late-fire (more than 60 seconds late: an event
  `MISSED`, not a run), missed-downtime and maintenance logic as `rapo/core/scheduler.py`, on the lease `RAPO.JOBS`,
  `rapo_job_event` and `rapo_job.schedule_config`, with `rapo/core/schedule.py` unchanged. It follows `[JOBS]`
  (`enabled`, `lease_timeout`, `refresh_interval`, `maintenance_interval`, `event_retention_days`,
  `missed_window_hours`) and `RAPO_SCHEDULER`; it has its own stop/start switch (the `disabled` column of its row), and one
  server runs it at a time, not necessarily the one running the scheduler of the controls. Maintenance deletes the
  runs and events older than their retention.
* **Run manager** (`rapo/jobs/manager.py`): after `rapo/transfer/manager.py`. A run is recorded at once (`I`), waits
  in a queue for one of the `[JOBS] parallelism` slots (read at every dispatch, so a reload applies at once), runs in a
  process of its own (`S`) and ends `D`, `E` or `C`. The log is written to `rapo_job_log` as the run reports it. Events:
  `FIRED` when a run is queued, `STARTED` when it gets its process, `MISSED`, `FAILED` (it could not be started) and
  `CANCELED`. `overlap` is applied at the start: `skip` refuses a manual start (409) and records a scheduled one as a
  canceled run; `queue` lets a run wait for the previous run of its job without blocking other jobs; `allow` does not
  care. A run over its `timeout` (the job's, else `[JOBS] timeout`, 0: none) and a run whose process died are errors.
  At start the runs this server left active by a crash are finished as errors.
* **Run process** (`rapo/jobs/executor.py`, `processes.py`): the parts of one run go one after another inside the same
  process (`executor.operate`), which reports `stage`, `condition`, `log` and finally `outcome` through a pipe. The run
  manager stops a canceled run or one over its `timeout` by killing that process **with all the programs it started**
  (`processes.kill_tree`, with `psutil`: terminate, then kill after 5 seconds; works the same on Linux and Windows), so
  the executor itself counts no time and waits for no cancel. A SQL task uses the connection of rapo, so killing the
  process ends its session and Oracle rolls the open transaction back.
  * Programs (`EXECUTABLE`, `COMMAND`, `PYTHON`, `PERL`, `SHELL`, `BATCH`, `POWERSHELL`) are started with `subprocess`
    without a window, stdin closed, stdout and stderr read line by line into the log as they come (UTF-8, else the code
    page of the OS). `code` of a script task is written to a file in a folder of the run, deleted afterwards. Python is
    started with `-u`; PowerShell with `-NoProfile -NonInteractive -ExecutionPolicy Bypass -File`; a batch file with
    `cmd /d /c call`. The working folder is the `cwd` of the task, else the folder of the program or script file, else the
    folder of the run. A `file` or `path` is checked against `allowed_dirs` again after its variables are filled in.
  * The environment is a cleaned one: the essentials of the OS (`PATH`, `SystemRoot`, `TEMP`, `HOME`, `LANG`, ...) and
    nothing else of rapo's own, plus `RAPO_JOB_NAME`, `RAPO_JOB_ID`, `RAPO_RUN_ID`, `RAPO_TRIGGER` and
    `RAPO_PARAM_<NAME>` for every parameter (a secret is better read from there than put on a command line), plus the
    `env` of the task.
  * The log keeps what a program writes to stdout (`out`) and stderr (`err`) and what rapo says (`rapo`), as
    (time, level, stream, text); a secret parameter (3 characters or more) is replaced by `***` in every line, and
    after `[JOBS] max_output_kb` of output the rest is dropped with one note. Output on stderr is only logged.
  * SQL: the statements of the task (`split_sql`) run one after another on one connection; a query shows its first 20
    rows; all are committed at the end, and any error rolls everything back and ends the part. A condition query is
    run read only (rolled back).
  * TRANSFER: the param is read with `rapo.transfer.store.read` (file or database), each argument is set over it with
    `store.set_parameter` (variables filled in), and it runs with `rapo.transfer.engine.execute`; the transfer's own log
    lines go to the job log, a failed file or any error fails the part.
  * A run starts by checking its definition against this server again (`validate`, with the files that must exist), so a
    job whose type left `enabled_types` or whose file left `allowed_dirs` since it was saved is not run.
* **Failure mail** (`rapo/jobs/notify.py`): any error of any part (PRE, CONDITION evaluation, MAIN, POST), a timeout and
  a run whose process died end the run as `E` and send one e-mail with the run, the stage, the exit code, the message
  and the last 40 lines of the log (secrets already hidden) to the address of the job, else to `[JOBS] default_email`,
  through `rapo/core/mailer.py` (`[EMAIL]`). A run canceled by the user or by a stop of the manager, one whose condition
  was not met, a skipped start and a late or missed fire are not errors and send nothing. When no mail can be sent (no
  address, `[EMAIL] enabled` off, no sender, a server that refuses) the run is still `E` and `mail_status` of the run
  says why.
* **Variables** and the secrets are applied in the run process, so a secret never travels to the browser.

## 5. rapo.ini

```ini
[JOBS]
# Show and run the jobs on this server (like [TRANSFER] enabled; applies at once from the UI).
enabled=True
# Jobs executed at once on this server; more wait in a queue.
parallelism=4
timeout=3600
history_retention_days=90
log_max_lines=5000
max_output_kb=1024
# Job types offered; empty: all of the OS.
#enabled_types=SQL,TRANSFER,PYTHON,SHELL
# Folders scripts and programs may be run from (comma separated); empty: refused.
#allowed_dirs=/opt/rapo/jobs
# Where a failure mail goes when the job has no address.
#default_email=ra-alerts@example.com
# Interpreters; empty: found on PATH.
#python_path=
#perl_path=
#shell_path=
#powershell_path=
# Same meaning as the options of [SCHEDULER].
lease_timeout=60
refresh_interval=300
maintenance_interval=86400
event_retention_days=90
missed_window_hours=24
```

`parallelism` is defined in `rapo.ini` (decided); like `[SCHEDULER] control_parallelism` it is read when a run is dispatched,
so a change on the *Configuration* tab or a reload applies at once. The switch and the lists apply on reload, as for
`[TRANSFER] enabled`. With `enabled=False` the *Jobs* page, its routes, the job scheduler and the run
manager are off, as for the transfers. The default is `True` (decided), unlike `[TRANSFER] enabled`.

## 6. API and access

Routes (all under `/api`, described in [docs/api](api/README.md#jobs)): `get-jobs`, `get-job`, `get-job-catalogue`,
`check-job` (validates a definition without saving or running it), `save-job`, `delete-job`, `set-job-status`,
`save-job-schedule`, `run-job` (with `parameters` for that run), `cancel-job-run`, `get-job-runs`, `get-job-run`,
`get-job-versions`, `job-scheduler-status`, `job-scheduler-start`, `job-scheduler-stop`, `job-scheduler-upcoming`,
`job-scheduler-events`; and the live event `jobs:changed` through the socket, to the connections that may view the
area. While `[JOBS] enabled` is off they answer 404, and `/api/info` has `jobs_available`.

A new area in `rapo/web/api/scopes.py`: `'jobs': ('Jobs', ('none', 'view', 'operate', 'edit'))` with

| Level | Allows |
|---|---|
| `view` | see jobs, their runs and logs, the upcoming fires |
| `operate` | also run now, cancel a run, switch a job on/off, stop and start the job scheduler |
| `edit` | also create, change, delete jobs (and see/test with *Check*) |

`edit` belongs only to the `full` preset (decided); `viewer` gets `view`, `operator` gets `operate`, `editor` none. Saving
a job whose definition has SQL in it (a SQL task or a SQL condition) needs **Free SQL** too, because the SQL runs as the
schema owner; it is checked by `save-job` and not by the area, so `jobs: edit` alone saves the jobs without SQL. The
value of a secret parameter is never returned: it is `********`, and a save that sends it back so keeps the stored
one (`definition.mask_secrets`, `restore_secrets`).

## 7. UI

Menu item **Jobs** between *Scheduler* and *Transfers* (`/jobs`, shown when `jobs_available` of `/api/info` is true and
the token may view the area; its own indicator in the header, a cogs button coloured by the state of the job
scheduler). It is built in two steps.

Step 1 (done): reading and running.

* `JobsPage.vue`: a status bar of the job scheduler (`JobSchedulerBar.vue`: its state, the server that schedules, the
  slots of the job manager, the next fire, and stop/start for tokens with *Jobs: Operate*); filter chips (all, on, off,
  scheduled, running, failed) and a search; the list with the name and description, the type, the schedule, the next
  start and the last run (status, the part it got to, the exit code, whether the failure mail went); run now / cancel,
  and a menu: run with parameters, run history, switch on/off, delete (*Jobs: Edit*). A note says when the failure mail
  is off in `rapo.ini` or jobs have no address for it. It follows `jobs:changed` and polls every 10 seconds.
* `JobRunsDialog.vue`: the runs of one job or of all, and the log of the one picked, its lines coloured by what wrote
  them (stderr, an error of rapo, a note of rapo), the message, what became of the failure mail, and cancel.
* `JobRunDialog.vue`: run now with other values of the parameters, for that run only; a secret is empty and keeps its
  saved value unless typed.

Step 2 (done): the editor, `/edit-job/<name>` (`new` for a job that does not exist yet), opened from the name of a job
and by *New job* (*Jobs: Edit*; a token that may only view gets it read only).

* `JobEdit.vue` with the tabs **General** (name, which is not changed later, description, the address of the failure
  mail with a note of what an empty one means on this server, on/off, timeout, overlap, parameters with the secret
  switch), **Pre**, **Condition**, **Main**, **Post**, **Schedule** (the box of the controls, without a cascade) and
  **Versions** (every version with its definition, and *Load into the editor* to save an older one as a new version). A
  tab shows whether its optional part is on.
* `JobTaskEditor.vue`: the type (the types of the OS; one that is not available, or SQL without Free SQL, is listed
  greyed with the reason) and the settings of the type: SQL in the code box with the variables, a transfer param with its
  `Name:value` arguments, a program with arguments, a command line, a script as a file or as code, working folder,
  environment, interpreter or shell. The condition kinds are SQL, the exit code of a program, a file that exists, and a
  number of files in a folder.
* **Check** asks the server (`check-job`) whether the definition is fit to run here, and shows every problem; Save,
  Apply (Ctrl+S) and Cancel as in the other editors, with the same guard against leaving unsaved changes. A save that
  the server refuses shows its words above the tabs and keeps the form. One save is one version, the schedule included
  (`save-job` takes the schedule).
* `utils/job.js` turns a definition into the form (lists as text, one per line) and back.
* A TRANSFER task has **New param...** and **Edit param...** beside the param it runs: the dialog of the Transfers page, in a
  mode of its own (`job-mode`) that uses the routes of the jobs, so it works while the Transfers page is off, keeps a
  new param in the database only, and reads a param kept in a file without changing it. A new param is named after the
  job (`<job>-param`). Changing a param says which other jobs run it. The list of params to pick from comes from
  `get-job-transfer-params`, files and database, with the jobs that run each.

## 8. Code and tests

* New package `rapo/jobs/`: `catalogue.py` (types, kinds, platform detection), `definition.py` (parse, validate),
  `store.py` (tables, runs, logs, versions), `executor.py` (tasks, process tree), `manager.py`, `scheduler.py`
  (built on the generalised `rapo/core/scheduler.py`).
* Changes elsewhere: `rapo/options.py` (`[JOBS]`), `rapo/web/api/app.py`, `scopes.py`, `rapo/transfer/engine.py` (apply
  arguments to a param text), `rapo/core/scheduler.py` (generalised), `rapo-ui/src` (page, editor, menu, router).
* Tests `tests/test_jobs.py` as `tests/test_transfer.py` (SQLite instead of Oracle): definition validation per OS,
  every condition kind, PRE/CONDITION/MAIN/POST ordering and the POST-only-after-success rule, exit codes, timeout kill
  of a process tree, cleaned environment, secret masking, queue and parallelism, overlap, late and missed fires, that the
  job scheduler and the control scheduler hold separate leases, that a TRANSFER job runs while `[TRANSFER] enabled` is
  off, e-mail only for errors.
* Docs: `docs/jobs.md` (user guide, written with the code), release notes in `migrations/v0.9.0/`.

## 9. Decisions on this design and what is open

Decided (customer, 2026-11-05):

1. `[JOBS] enabled` defaults to `True`.
2. The lease of the job scheduler is a second row of `rapo_scheduler` and `Scheduler` is generalised.
3. `viewer` gets `view`, `operator` gets `operate`, `editor` none, `edit` only `full`.
4. SQL runs as the schema owner, so saving a job with an SQL part needs Free SQL.

5. The names of the tables and routes of sections 3 and 6 are as proposed.

Where the built version differs from what was first written here: the lease logic is a class of its own
(`rapo/core/lease.py`) used by both schedulers instead of a generalised `Scheduler`; `get-job-run` answers the log as
`lines` besides `run_log`; `save-job` also takes the schedule, so one save is one version; `[JOBS] parallelism` is read at
every dispatch (a change applies at once); a TRANSFER task has routes of its own for its params
(`get-job-transfer-*`); and `{variables}` are replaced only when they name a known variable, so scripts need no escaping.

## 10. Order of the work (done)

1. Schema and options: `[JOBS]`, migration `v0.9.0`, `store.py`, `definition.py` with tests.
2. Executors per type and the run process, with tests on Windows (and Linux when available).
3. Generalised scheduler and job scheduler, run manager, e-mail, retention.
4. API and the `jobs` access area.
5. UI: page, editor, history, status bar, menu.
6. Docs, CHANGELOG, `v0.9.0`; second phase afterwards: workflows and dependencies.

## 11. Workflows (0.10.0)

Requirement: `documents/RAJO.txt`, second phase. A **workflow** is a named list of jobs run one after another: when a
step ends, the next one starts, and the whole stops at the first step that does not succeed. Dependencies of a job on a
control, a transfer or another job (a condition kind *Run status*) are the next release (0.11.0): a dependency that is not
met cancels the run at once, without waiting, and inside a workflow that stops the workflow.

### 11.1 What a workflow is

| Part | |
|---|---|
| name, description | The name has the rules of a job name; it is not changed after it is created |
| steps | An ordered list of jobs, by name. **At least one; the same job at most once** (the message names the job and its steps: "load-files is at steps 2 and 5"); every job must exist. No branching, no parallel steps |
| parameters | `{name, value, secret}` as a job has them. In a step, a parameter of the workflow overrides the parameter of the job of the same name, for that run only; secrets are hidden in the logs and never sent to the browser |
| schedule, on/off | As a job: the units of a control schedule, no cascade; an off workflow is not scheduled and can still be run by hand |
| `notify_email` | The address of the failure mail; empty: `[JOBS] default_email` |
| `timeout` | Seconds for the whole workflow (empty: `[JOBS] timeout`; 0: none); each step also keeps the timeout of its job |
| `overlap` | As a job: `skip` (a manual start is refused, a scheduled one is recorded as canceled), `queue` (waits for the previous run of the workflow), `allow` |
| versions | Every save of the definition or the schedule is a version, as for a job |

The definition is one JSON document: `{"version": 1, "steps": [{"job": "a"}, {"job": "b"}], "parameters": [], "timeout":
null, "overlap": "skip"}`. A step is an object so that it can get settings later.

A job in a workflow stays a job: it keeps its own schedule and can be run by hand. A job that is switched off is still
run as a step (only its own schedule is off). Deleting a job that is in workflows asks first and names them; on
confirmation the job is removed from their steps (a new version of each), and a workflow left without steps is switched
off and listed as having none.

### 11.2 A run

* A workflow run is recorded at once (`rapo_workflow_run`, status `I` queued, `S` running, `D` done, `E` error, `C`
  canceled) with a row for every step (`rapo_workflow_run_step`), all waiting (`W`). It has a log of its own
  (`rapo_workflow_log`) that tells which step starts, how each ends and, when it stops, where and why.
* It is run by a thread of the server that started it, **not** by a slot: the workflow itself takes no slot. Each step is an
  ordinary run of the job: it is queued in the job manager, waits for one of the `[JOBS] parallelism` slots, runs in a process
  of its own, and is recorded in `rapo_job_run` with `trigger_type = 'workflow'` and `workflow_run_id`. The step runs
  under the overlap `queue` whatever the job's own is: when the job is already running (by hand or by its schedule) the step
  waits for it to end and then starts.
* The **failure mail of the job is not sent** for a step; the workflow sends its own, once.
* How a step ends decides the workflow:

| The step run | The workflow | The later steps |
|---|---|---|
| `D` done | goes on with the next step; after the last it is `D` | |
| `E` error (any part, timeout, dead process) | stops, `E` | `K` skipped |
| `C` condition not met | stops, `C` (shown as a **warning**, amber); the log has a `WARNING` line: "Stopped at step 2 of 4 (job `x`): The condition was not met." | `K` skipped |
| `C` canceled by the user or stopped | stops, `C`, the message says so | `K` skipped |

* **Cancel** of a workflow run cancels the step that runs (it is killed with everything it started), skips the rest and ends
  `C`. A workflow over its `timeout` does the same and ends `E`. A server that stops ends the runs it has as `E`
  ("stopped because the server stopped unexpectedly") at the next start, and their step runs with them.
* **Mail**: one e-mail for a workflow run that ends `E`, to `notify_email`, else `[JOBS] default_email`; it names the step
  that failed, lists every step with its end, and has the last lines of the log. A workflow that stops as `C` (a condition
  not met, a cancel) sends nothing, as for a job. `mail_status` of the run says what became of it.
* **Parameters** given for a run by hand override the parameters of the workflow, which override those of the jobs. Only
  names a job has are given to that job.

### 11.3 Scheduling

The job scheduler fires workflows as it fires jobs: the same lease, late-fire and missed-downtime handling, events in
`rapo_job_event` (a new column `workflow_id`), maintenance that deletes old runs. Its upcoming list and status count them.

### 11.4 Database (migration `v0.10.0`)

`rapo_workflow` (as `rapo_job` with `steps` inside `definition`), `rapo_workflow_version`, `rapo_workflow_run`,
`rapo_workflow_run_step`, `rapo_workflow_log`, and the column `workflow_id` of `rapo_job_event`.

### 11.5 API, access, UI

Routes in the area *Jobs* (View: reads; Operate: run, cancel, switch on and off; Edit: save, delete): `get-workflows`,
`get-workflow`, `check-workflow`, `save-workflow`, `delete-workflow`, `set-workflow-status`, `save-workflow-schedule`,
`run-workflow`, `cancel-workflow-run`, `get-workflow-runs`, `get-workflow-run` (with its steps and its log),
`get-workflow-versions`; `delete-job` answers `409` with `used_by` (the workflows) until it is sent with `confirm`. The
Jobs page gets a second tab, **Workflows**: the list with the last run and the step it stopped at, the run history with a
run's steps (each with its status, its duration and a link to the run of the job and its log) and the log of the workflow,
and an editor with the tabs General (name, description, address, on/off, timeout, overlap, parameters), Steps (the jobs in
order; a note on a job that is off), Schedule and Versions.

### 11.6 Built (0.10.0)

Where the built version is more exact than the text above: the failure mail of a workflow has the last lines of the log of the
failed step as well as of the workflow; a start that has to wait for the previous run (`overlap: queue`) says so in the log;
`delete-job` is refused (`409`) until `confirm`, and `get-jobs` and `get-job` list the workflows of a job (`workflows`);
`job-scheduler-upcoming` lists the fires of the jobs and of the workflows (`kind`); the events have `workflow_id` and
`workflow_name`. The status of a step of a run is `W`, `I`, `S`, `D`, `E`, `C` or `K` (skipped), and the UI shows a workflow
run that ended `C` with a message "Stopped at step ..." as a warning. A run that waits for the previous one (`queue`) is
recorded and shown as waiting (`I`). The code: `rapo/jobs/workflow.py` (definition), `workflow_store.py`, `workflow_manager.py`
(the orchestrator), `notify.py` (the mail), `scheduler.py`, the routes in `rapo/web/api/app.py`, and in the UI the tab
`WorkflowsPanel.vue`, `WorkflowRunsDialog.vue` and `WorkflowEdit.vue`.

### 11.7 Order of the work

1. This design, the migration `v0.10.0`, the definition and the storage, with tests.
2. The orchestrator, the changes of the job manager (a step does not mail, waits for its job), the scheduler.
3. API and access.
4. UI.
5. Docs, CHANGELOG, `v0.10.0`.

## 12. Dependencies (0.11.0)

Requirement: `documents/RAJO.txt`, second phase. A **dependency** is a condition of a job, or of a whole workflow at its
start, that says another run has to have ended well: a control, a transfer, a job or a workflow. It is a new condition kind,
`RUN_STATUS`, and follows the rules of every condition: when it is not met the run is **canceled at once** (no waiting), with
the reason in its message; inside a workflow that cancels the workflow. No schema change: the condition is part of the JSON
definition.

### 12.1 The condition

```json
{"kind": "RUN_STATUS", "targets": [
  {"type": "control",  "name": "DEMO_BILLING", "window": {"kind": "data_date", "days_ago": 1}},
  {"type": "transfer", "name": "daily-report", "window": {"kind": "today"}},
  {"type": "job",      "name": "load",         "window": {"kind": "hours", "hours": 6}},
  {"type": "workflow", "name": "nightly",      "window": {"kind": "since_last_run"}}]}
```

* **Targets**: a list, **all must be met**; at least one; none twice; never the job or the workflow that owns the condition. The
  window is **per target**.
* **Windows** (the end of a run is what is looked at, except in `data_date`):

| Window | A run counts when | Targets |
|---|---|---|
| `today` | it ended since midnight of today (server time) | all |
| `hours` (N, 1-720) | it ended in the last N hours | all |
| `since_last_run` | it ended after the last **successful** run of the job or workflow that owns the condition; when it has none, after the moment it was **created** | all |
| `data_date` (`days_ago`, 0-31) | the day of its `date_from` ("run from") is the day that many days ago: 0 today, 1 yesterday. The window `date_to` is not looked at | **controls only** |

* **Met** when the **last finished** run that counts is **Done**. When the target has a run that is still going (waiting,
  started, running) it is **not met**: it is not over. Not met too when no run counts, or the last one that counts ended in
  an error or was canceled; the message says which. For `data_date` the last finished run for that day is meant, whenever it
  ended. A control is Done in status `D`, a transfer when the whole transfer was done without a failed file, as its status says.
* The reason is in the message of the run: "The condition was not met: control DEMO_BILLING has no run for 2026-11-05 (its last
  run was for 2026-11-04 and is done); transfer daily-report is still running". It is also in the log of the run.
* A target that **does not exist** is an error, not "not met": the run ends as an error (with the failure mail). *Check* tells it
  when the definition is saved (a save is not refused: a control may be made later). A transfer is read whether or not
  `[TRANSFER] enabled` is on; when the transfer tables are missing that is an error that says so.
* The condition is evaluated the same for a run by hand and one by the schedule.

### 12.2 A workflow's condition

A workflow may have a `condition` in its definition, evaluated **at its start**, before the first step: `RUN_STATUS`, `FILE_EXISTS`
or `FILE_COUNT` (the ones that need no process: the workflow is run by a thread of the server). When it is not met no step is
run: the workflow run is **canceled** (a warning, as when a step's condition is not met), all steps are skipped, the log says why,
no mail. A condition that can not be evaluated is an error, with the mail. `since_last_run` there means the last successful run
of the workflow.

### 12.3 What the lists show

A job or a workflow that is polled (scheduled every few minutes, with `since_last_run`) would end almost every run canceled
because its condition was not met. The lists therefore show as the **last run** the last one that really ran, with a note
"condition not met at 07:10 (12 times since)" when there are such runs after it, and the history can hide them (`hide_unmet`).
Such runs are still recorded and still deleted by the retention.

### 12.4 Built (0.11.0)

Where the built version is more exact than the text above: the transfer and job windows read `rapo_transfer_run` and
`rapo_job_run`, the workflow `rapo_workflow_run`, a control `rapo_log` joined by name to `rapo_config`; the reason is told for
every target that is not met, joined by `;`. A run is *unmet* when it is `C` at the stage `CONDITION` (a job) or its message
starts "Stopped at the start of the workflow" (a workflow); a workflow stopped at a **step** by the condition of that job
still counts as a real run. `get-dependency-targets` serves the editor; `check-job` and `check-workflow` take the name of the
owner and answer `missing`. The code: `rapo/jobs/dependencies.py`, `executor.evaluate_condition`, `workflow_manager._condition`,
`store.read_unmet` and `read_waits_for`, and in the UI `ConditionEditor.vue` and `RunStatusEditor.vue`.

### 12.5 Order of the work

1. This design, the definition and its checks (jobs and workflows), the reading of the statuses (`rapo/jobs/dependencies.py`).
2. The run of the job (the reason in the message), the start of the workflow, the lists.
3. API: the names of the targets, `check-job` and `check-workflow` find missing ones, `hide_unmet`.
4. UI: the condition editor shared by the job and the workflow, the Condition tab of a workflow, "waits for" in the lists.
5. Docs, CHANGELOG, `v0.11.0` (no `upgrade.sql`).
