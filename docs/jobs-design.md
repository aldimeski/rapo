# RAJO - jobs: design

Design of the jobs of rapo (RAJO, Revenue Assurance Job Orchestrator), for the requirement in
`documents/RAJO.txt` (v1.0). Status: **draft for review**, nothing of it is implemented. The first release is 0.9.0+fork,
workflows and dependencies are the second phase (their hooks are prepared here).

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

Routes (names as the others, all under `/api`): `get-jobs`, `get-job`, `get-job-catalogue` (types of this server,
interpreters, condition kinds, defaults), `check-job` (validates a definition without saving or running),
`save-job`, `delete-job`, `set-job-status` (on/off), `save-job-schedule`, `run-job` (with `parameters` overrides for
this run), `cancel-job-run`, `get-job-runs`, `get-job-run` (with log), `get-job-versions`, `get-job-upcoming`,
`job-scheduler-status`, `job-scheduler-start`, `job-scheduler-stop`; live events `jobs:changed` and
`jobs-scheduler:changed` through the socket, as `scheduler:changed`.

A new area in `rapo/web/api/scopes.py`: `'jobs': ('Jobs', ('none', 'view', 'operate', 'edit'))` with

| Level | Allows |
|---|---|
| `view` | see jobs, their runs and logs, the upcoming fires |
| `operate` | also run now, cancel a run, switch a job on/off, stop and start the job scheduler |
| `edit` | also create, change, delete jobs (and see/test with *Check*) |

`edit` belongs only to the `full` preset (decided); `viewer` gets `view`, `operator` gets `operate`, `editor` none. Saving
a job whose parts include SQL needs **Free SQL** too (`NEEDS_FREE_SQL`), because the SQL runs as the schema owner.
Every secret parameter is returned masked.

## 7. UI

Menu item **Jobs** between *Scheduler* and *Transfers* (`/jobs`, shown when `jobs_available` of `/api/info` is true and
the token may view the area). The page has:

* a status bar of the job scheduler (state, holder, parallelism and queue, stop/start) and its own indicator in the
  header like the one of the Scheduler;
* the list of jobs: name, type, where it runs (on/off), schedule, next start, last run and its status, filters;
* the editor with the tabs **General** (name, description, notify address, on/off, timeout, overlap, parameters),
  **Pre**, **Condition**, **Main**, **Post**, **Schedule** (the box of the controls), **Versions** and a **Check**
  button;
* the run history of a job (dialog like the Transfers one) with the stage reached, the exit code and the log.

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

Open: the names of the tables and routes (sections 3 and 6).

## 10. Order of the work

1. Schema and options: `[JOBS]`, migration `v0.9.0`, `store.py`, `definition.py` with tests.
2. Executors per type and the run process, with tests on Windows (and Linux when available).
3. Generalised scheduler and job scheduler, run manager, e-mail, retention.
4. API and the `jobs` access area.
5. UI: page, editor, history, status bar, menu.
6. Docs, CHANGELOG, `v0.9.0`; second phase afterwards: workflows and dependencies.
