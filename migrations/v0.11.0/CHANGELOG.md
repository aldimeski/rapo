# Rapo v0.11.0 Change Log

## Annotation
**Dependencies**: a job, or the start of a workflow, can wait for other runs. A new condition kind *Run status* looks at
controls, transfers, jobs and workflows. There is no database change and no new option (no `upgrade.sql`); the upgrade steps
are in the [migration instructions](README.md).

1. **The condition.** One or more *targets*, each a control, a transfer, a job or a workflow by name, with a *window*: ended
   **today**, ended in the last **N hours** (1 to 720), ended **since the last successful run** of the job or workflow that
   has the condition (or since it was made), or, for a control only, **has the data of a day** (`date_from`, 0 to 31 days
   back). It is met when **every** target is: none of its runs is going or waiting, and its **last run that ended is Done**
   and falls in the window (an earlier good run does not make up for a later failed one). See
   [docs/jobs.md](../../docs/jobs.md#dependencies) and [docs/jobs-design.md](../../docs/jobs-design.md), section 12.
1. **Where it can be.** In the condition of a **job** (as the other kinds) and as the **condition of a workflow**, checked once
   before its first step; a workflow may also use *File exists* and *Files in a folder* there. A job or a workflow can not wait
   for itself, and a transfer is read whether or not `[TRANSFER] enabled` is on.
1. **When it is not met.** A job ends *Canceled* with the reason in its message and log ("The condition was not met: the control
   X has not ended today (last at ...)"); a workflow ends *Canceled* as a **warning** ("Stopped at the start of the workflow:
   ...") and no step runs; no mail is sent. A target that **does not exist**, or that can not be read, is an **error** (with the
   failure mail). *Check* names the targets that do not exist; saving is not refused.
1. **Lists.** The last run shown for a job or workflow is the last one that **really ran**; runs a condition that was not met
   canceled are noted ("condition not met at 07:10 (12 times since)") and can be hidden in the *Runs* dialog. The lists also say
   what a job or workflow **waits for**.
1. **UI.** The condition editor is shared by jobs and workflows (a *Condition* tab is new in the workflow editor), with a
   picker of the names of controls, transfers, jobs and workflows.
1. **API.** `get-dependency-targets` (area *Jobs*, View); `check-job` and `check-workflow` take `name` and answer `missing`;
   `get-jobs` and `get-workflows` add `waits_for` and `unmet` (`get-job` and `get-workflow` add `unmet`); `hide_unmet` on
   `get-job-runs` and `get-workflow-runs` ([docs/api](../../docs/api/README.md#dependencies)).

Not in this release: listing the jobs that start outside rapo (the crontab, the Windows Task Scheduler), branching, parallel
steps and a step that goes on after a failure.
