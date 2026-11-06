# Rapo v0.10.0 Change Log

## Annotation
**Workflows** for the jobs: a named list of jobs run one after another, stopping at the first step that does not succeed.
New tables `rapo_workflow`, `rapo_workflow_version`, `rapo_workflow_run`, `rapo_workflow_run_step`, `rapo_workflow_log` and a
column `workflow_id` of `rapo_job_event` (`migrations/v0.10.0/upgrade.sql`). There is no new option and no new Python package;
the workflows are on with the jobs (`[JOBS] enabled`) and use the same access area, *Jobs*. The upgrade steps are in the
[migration instructions](README.md).

1. **Workflows.** A workflow has steps (jobs, in order; at least one; a job at most once, and a save that repeats one names it
   and its steps), parameters (a parameter of the workflow overrides that of the same name of the job for that run), a timeout
   for the whole run, an overlap policy (skip, queue, allow), a schedule, an address for the failure mail, an on/off switch and
   versions. It always runs on the server where rapo runs. See [docs/jobs.md](../../docs/jobs.md#workflows) and
   [docs/jobs-design.md](../../docs/jobs-design.md), section 11.
1. **How a run ends.** A step that is done goes on to the next. **An error** stops the workflow as an error: the later steps are
   *Skipped* and one failure mail is sent, to the address of the workflow or else `[JOBS] default_email`, with the steps and the
   output of the step that failed; **the mail of the job is not used** for a step. **A condition that is not met**, or a cancel,
   stops it as canceled, shown as a **warning** (*Stopped*): the message and the log say at which step and why, and no mail is
   sent. Cancel of a run cancels the step that runs; a workflow over its timeout is an error.
1. **Steps are ordinary job runs.** The workflow takes no slot of `[JOBS] parallelism`; each step is queued for a slot, runs in a
   process of its own, has its own log, and is listed with the runs of the job (trigger *Workflow*). A step waits for a run of its
   job that is still going, whatever the overlap of the job. A job that is switched off still runs as a step. The secrets of the
   workflow are hidden in the logs of the steps.
1. **Jobs page.** A *Workflows* tab: the list (steps, schedule, next start, last run and the step it stopped at), run now or with
   parameters, cancel, switch on and off, and the run history, which lists the steps of a run with their status and message,
   the log of the run of the job of a step, and the log of the workflow. The editor has the tabs General, Steps (add, move and
   remove jobs; a job that is off or gone is told), Schedule and Versions, and a *Check*. A job says in the list and in its
   editor which workflows it is a step of.
1. **Deleting a job that is in workflows** says which ones and asks for confirmation (`delete-job` answers `409` until it is
   given `confirm`). The job is taken out of their steps, a new version of each, and a workflow left without steps is switched
   off and shown as having none.
1. **The job scheduler fires workflows too**, with the same lease, handling of late and missed fires and maintenance; its status
   counts them, and `job-scheduler-upcoming` and `-events` include them.
1. **API.** `get-workflows`, `get-workflow`, `check-workflow`, `save-workflow`, `delete-workflow`, `set-workflow-status`,
   `save-workflow-schedule`, `run-workflow`, `cancel-workflow-run`, `get-workflow-runs`, `get-workflow-run`,
   `get-workflow-versions`, all in the area *Jobs* ([docs/api](../../docs/api/README.md#workflows)); `get-jobs` and `get-job`
   list the `workflows` of a job.

Not in this release: dependencies of a job on a control, a transfer or another job (the next release), branching, parallel
steps and a step that goes on after a failure.
