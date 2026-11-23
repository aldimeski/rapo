# Groups of transfers

A **group** is a named list of transfers that are run **one after another**, in the order of the list, by a schedule
of its own. It is the way to say "load the files of A, then B, then C" without guessing how long each takes. The groups
are the *Groups* tab of the *Transfers* tab of the *Jobs* page (`/jobs?tab=transfers&sub=groups`), and they are on and
off together with the transfers (`[TRANSFER] enabled`).

## What a group is

| Setting          | Meaning                                                                                                                  |
|------------------|--------------------------------------------------------------------------------------------------------------------------|
| **Name**         | Letters, digits, dots, hyphens and underscores; not changed after it is created                                         |
| **Transfers**    | The params, in the order they are run. At least one. A transfer is in a group **once**, and may be in any number of groups |
| **Schedule**     | As a transfer has it (daily, weekly, monthly or complex, no cascade); none: the group is run by hand                     |
| **On/off**       | An off group is not scheduled and can still be run by hand                                                               |
| **Overlap**      | What a start does while the group still runs: `skip` (the default), `queue` or `allow` - see below                       |
| **E-mail**       | The address of the mail on failure; empty: `[JOBS] default_email`                                                         |

The **order** is changed in the editor with the arrows next to each transfer, and a transfer is added or taken out there.
A run uses the order and the transfers the group had when the run was started.

## How a group runs

A run of a group is recorded at once (`rapo_transfer_group_run`) and goes through its transfers:

1. The first transfer is started as an ordinary run of that transfer (`rapo_transfer_run`, trigger `group`), which waits
   for a slot like any other and is performed in its own process.
1. When it has ended **Done**, the next one is started, and so on. The run of the group is **Done** when the last one is.
1. A transfer that ends **Error** stops the group: the transfers after it are **not** started (their steps are
   *Canceled*), the run of the group is **Error** and **one mail** is sent for the whole group: it names the step that
   failed, lists every step with how it ended and has the last lines of the log.
1. A transfer or a group that is **canceled** (the *Cancel* button of the list, the history or the group) stops the
   group as *Canceled*: the transfer that goes is canceled and the ones after it do not start. No mail is sent.

A transfer in a group keeps everything of its own: its param, its log files, its `timeout`, its own schedule.

### The same transfer at the same time

A transfer is **never run twice at once** on a server. When it is started while it runs - by hand, by its own schedule, by
the crontab or as a step of another group - the new run **waits** behind the one that goes and starts when that has
ended; waiting runs of one transfer start in the order they were started. A group that reaches a transfer that is busy
waits at that step. The *Run history* of the transfer shows the waiting run as *Queued*.

### When the group is started while it still runs

| `overlap` | A start by hand                       | A start by the schedule                                      |
|-----------|---------------------------------------|--------------------------------------------------------------|
| `skip`    | Refused (`409`)                       | Recorded in the history as *Canceled* ("already running")    |
| `queue`   | The new run waits for the one before  | The same                                                     |
| `allow`   | Both go at once                       | The same                                                     |

With `allow` the two runs of the group still never run the same transfer together: the second one waits at it.
The check is made on the server that is asked; a group started by hand on another server is not seen by this one.

## When things start

The *Groups* tab shows, for every group, its next start and, for each of its transfers, **when it starts by itself** (its
own schedule) and **when it is expected to start in the group**. The first transfer starts with the group. For the others
the time is an **estimate**: the start of the group plus how long the transfers before it took in their last run that ended
Done (nothing for one that never ran). The estimate is not a promise, because a step starts when the one before ends.

The *Transfers* tab lists, for every transfer, the groups it is in (with its place: *2 of 4*) and **all its next starts**,
the earliest first: its own schedule, each group (*group nightly, step 2*) and the crontab (on Linux). A transfer that has
more than one start is not an error: when two of them meet, the second waits for the first.

## Log

* The log of a run of a transfer started by a group is the log of that run: it is read in the *Run history* of the
  transfer, like any other run; the run carries `group_run_id`, the ID of the run of the group.
* The log of a **group** run (*Run history* of the group, or *All runs*) shows what the group did - which step
  started, how it ended - and **under each step the log of that transfer**, indented. The steps are listed above it
  with their status, times and how many files they transferred.

The runs of the groups are kept for `[TRANSFER] history_retention_days`, like the runs of the transfers.

## Changing and deleting

* A group, its transfers and its schedule are changed in the editor; a change counts from the next run.
* Deleting a **group** leaves its transfers as they are; a group that runs or waits is refused.
* Deleting a **transfer** that is in groups asks first; on confirmation the transfer is taken out of them (the others
  move up) and a group left without a transfer is switched off. A transfer that is a step of a group that runs can not be
  deleted.
* Moving a transfer between a file and the database keeps its groups: a group names the param, not its place.

## Tables and API

The tables are `rapo_transfer_group`, `rapo_transfer_group_member` (the transfers with their `position`),
`rapo_transfer_group_run`, `rapo_transfer_group_run_step` and the column `rapo_transfer_run.group_run_id`, made by
`migrations/v0.8.7.5/upgrade.sql` (section 4; the script can be run again) and by `schema/oracle.sql`. The routes
(`get-transfer-groups`, `save-transfer-group`, `run-transfer-group`, ...) are in the
[API reference](api/README.md#groups-of-transfers); they need *Datasources: View* to read, *Datasources: Edit* to
change a group and *Files: Operate* to run or cancel one, as the transfers do.

Code: `rapo/transfer/group_store.py` (storage), `rapo/transfer/groups.py` (the manager: runs, schedule, mail),
`tests/test_transfer_groups.py`.
