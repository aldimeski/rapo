-- Rapo v0.9.0 -> v0.10.0
-- Adds the workflows: the definitions (rapo_workflow) and their versions
-- (rapo_workflow_version), the history of the runs (rapo_workflow_run) with a
-- row for each step (rapo_workflow_run_step) and a log (rapo_workflow_log),
-- and the column workflow_id of rapo_job_event.
-- Re-runnable.

declare
  v_count number;

  procedure make(p_kind varchar2, p_name varchar2, p_sql varchar2) is
  begin
    if p_kind = 'table' then
      select count(*) into v_count from user_tables
       where table_name = upper(p_name);
    elsif p_kind = 'sequence' then
      select count(*) into v_count from user_sequences
       where sequence_name = upper(p_name);
    else
      select count(*) into v_count from user_indexes
       where index_name = upper(p_name);
    end if;
    if v_count = 0 then
      execute immediate p_sql;
    end if;
  end;
begin
  make('sequence', 'rapo_workflow_seq',
       'create sequence rapo_workflow_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_workflow', q'[
    create table rapo_workflow (
      workflow_id     number(*, 0) default rapo_workflow_seq.nextval not null,
      workflow_name   varchar2(128 char) not null,
      description     varchar2(500 char),
      definition      clob not null,
      schedule_config varchar2(4000 char),
      status          varchar2(1 char) default 'Y' not null,
      notify_email    varchar2(500 char),
      version         number(*, 0) default 1 not null,
      created_date    date default sysdate not null,
      created_by      varchar2(256 char),
      updated_date    date default sysdate not null,
      updated_by      varchar2(256 char),
      constraint rapo_workflow_pk primary key (workflow_id),
      constraint rapo_workflow_name_uk unique (workflow_name),
      constraint rapo_workflow_status_ck check (status in ('Y', 'N'))
    )]');

  make('table', 'rapo_workflow_version', q'[
    create table rapo_workflow_version (
      workflow_id     number(*, 0) not null,
      version         number(*, 0) not null,
      definition      clob not null,
      schedule_config varchar2(4000 char),
      updated_date    date default sysdate not null,
      updated_by      varchar2(256 char),
      constraint rapo_workflow_version_pk primary key (workflow_id, version)
    )]');

  make('sequence', 'rapo_workflow_run_seq',
       'create sequence rapo_workflow_run_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_workflow_run', q'[
    create table rapo_workflow_run (
      run_id             number(*, 0) default rapo_workflow_run_seq.nextval not null,
      workflow_id        number(*, 0) not null,
      workflow_name      varchar2(128 char) not null,
      trigger_type       varchar2(16 char) not null,
      scheduled_time     date,
      queued_time        date default sysdate not null,
      start_time         date,
      end_time           date,
      status             varchar2(1 char) default 'I' not null,
      step_no            number(*, 0),
      steps_total        number(*, 0),
      message            varchar2(4000 char),
      runner             varchar2(255 char),
      requested_by       varchar2(256 char),
      definition_version number(*, 0),
      log_lines          number(*, 0),
      mail_status        varchar2(200 char),
      constraint rapo_workflow_run_pk primary key (run_id),
      constraint rapo_workflow_run_status_ck
        check (status in ('I', 'S', 'D', 'E', 'C'))
    )]');
  make('index', 'rapo_workflow_run_wf_ix',
       'create index rapo_workflow_run_wf_ix on rapo_workflow_run (workflow_id, queued_time)');
  make('index', 'rapo_workflow_run_time_ix',
       'create index rapo_workflow_run_time_ix on rapo_workflow_run (queued_time)');

  make('table', 'rapo_workflow_run_step', q'[
    create table rapo_workflow_run_step (
      run_id      number(*, 0) not null,
      step_no     number(*, 0) not null,
      job_id      number(*, 0),
      job_name    varchar2(128 char) not null,
      job_run_id  number(*, 0),
      status      varchar2(1 char) default 'W' not null,
      start_time  date,
      end_time    date,
      message     varchar2(4000 char),
      constraint rapo_workflow_run_step_pk primary key (run_id, step_no),
      constraint rapo_workflow_step_status_ck
        check (status in ('W', 'I', 'S', 'D', 'E', 'C', 'K'))
    )]');

  make('sequence', 'rapo_workflow_log_seq',
       'create sequence rapo_workflow_log_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_workflow_log', q'[
    create table rapo_workflow_log (
      log_id      number(*, 0) default rapo_workflow_log_seq.nextval not null,
      run_id      number(*, 0) not null,
      workflow_id number(*, 0) not null,
      line_no     number(*, 0) not null,
      log_time    date,
      log_level   varchar2(8 char),
      message     varchar2(4000 char),
      constraint rapo_workflow_log_pk primary key (log_id)
    )]');
  make('index', 'rapo_workflow_log_run_ix',
       'create index rapo_workflow_log_run_ix on rapo_workflow_log (run_id, line_no)');
  make('index', 'rapo_workflow_log_wf_ix',
       'create index rapo_workflow_log_wf_ix on rapo_workflow_log (workflow_id, log_id)');

  -- The events of the job scheduler name the workflow they are about.
  select count(*) into v_count from user_tab_columns
   where table_name = 'RAPO_JOB_EVENT' and column_name = 'WORKFLOW_ID';
  if v_count = 0 then
    execute immediate 'alter table rapo_job_event add (workflow_id number(*, 0))';
  end if;
end;
/
