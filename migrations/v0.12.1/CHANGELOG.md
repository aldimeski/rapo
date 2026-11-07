# Rapo v0.12.1 Change Log

## Annotation
The database schema of a **new** installation (`schema/oracle.sql`) now has the tables of the jobs and the workflows. There is no
database change for an installation that exists (no `upgrade.sql`) and the application is the same as v0.12.0; the steps are in
the [migration instructions](README.md).

1. **The schema file.** `schema/oracle.sql` had the tables of the file transfers, but not those made by `migrations/v0.9.0/upgrade.sql`
   (`rapo_job`, `rapo_job_version`, `rapo_job_run`, `rapo_job_log`, `rapo_job_event`) and `migrations/v0.10.0/upgrade.sql`
   (`rapo_workflow`, `rapo_workflow_version`, `rapo_workflow_run`, `rapo_workflow_run_step`, `rapo_workflow_log`, and the column
   `workflow_id` of `rapo_job_event`), nor the row `RAPO.JOBS` of `rapo_scheduler`, the lease of the job scheduler. A database
   made from the schema file therefore had no jobs until those two scripts were run by hand. It now has all of them, with the same
   columns, constraints and indexes as the upgrade scripts make.
1. **Release notes.** `migrations/v0.12.1` (this folder) is the pointer to what a new and an upgraded installation do. The releases
   v0.11.0 and v0.12.0 have no `upgrade.sql` because they change no table.

The file was compared with the upgrade scripts, table by table and column by column, and its 28 statements for the jobs and the
workflows were run on an Oracle database under temporary names (all accepted, then dropped).
