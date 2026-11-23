-- Rapo v0.8.7 -> v0.8.7.5
-- One script for everything after v0.8.7, run in this order:
--   1. the file transfers: rapo_transfer_param, rapo_transfer_schedule,
--      rapo_transfer_run, rapo_transfer_log;
--   2. the jobs (RAJO): rapo_job, rapo_job_version, rapo_job_run, rapo_job_log,
--      rapo_job_event and the lease row RAPO.JOBS of rapo_scheduler;
--   3. the workflows: rapo_workflow, rapo_workflow_version, rapo_workflow_run,
--      rapo_workflow_run_step, rapo_workflow_log and rapo_job_event.workflow_id;
--   4. the groups of transfers: rapo_transfer_group, rapo_transfer_group_member,
--      rapo_transfer_group_run, rapo_transfer_group_run_step and
--      rapo_transfer_run.group_run_id.
-- Re-runnable: every part creates only what is missing.

-- 1. File transfers

declare
  v_count number;
begin
  select count(*) into v_count from user_tables
   where table_name = 'RAPO_TRANSFER_PARAM';
  if v_count = 0 then
    execute immediate q'[
      create table rapo_transfer_param (
        param_name   varchar2(128 char) not null,
        param_text   clob not null,
        description  varchar2(500 char),
        created_date date default sysdate not null,
        created_by   varchar2(256 char),
        updated_date date default sysdate not null,
        updated_by   varchar2(256 char),
        constraint rapo_transfer_param_pk primary key (param_name)
      )]';
  end if;

  select count(*) into v_count from user_tables
   where table_name = 'RAPO_TRANSFER_SCHEDULE';
  if v_count = 0 then
    execute immediate q'[
      create table rapo_transfer_schedule (
        param_name      varchar2(128 char) not null,
        schedule_config varchar2(4000 char),
        status          varchar2(1 char) default 'Y' not null,
        updated_date    date default sysdate not null,
        updated_by      varchar2(256 char),
        constraint rapo_transfer_schedule_pk primary key (param_name),
        constraint rapo_transfer_schedule_ck check (status in ('Y', 'N'))
      )]';
  end if;

  select count(*) into v_count from user_sequences
   where sequence_name = 'RAPO_TRANSFER_RUN_SEQ';
  if v_count = 0 then
    execute immediate 'create sequence rapo_transfer_run_seq increment by 1 start with 1 nocache';
  end if;

  select count(*) into v_count from user_tables
   where table_name = 'RAPO_TRANSFER_RUN';
  if v_count = 0 then
    execute immediate q'[
      create table rapo_transfer_run (
        run_id         number default rapo_transfer_run_seq.nextval not null,
        param_name     varchar2(128 char) not null,
        trigger_type   varchar2(16 char) not null,
        scheduled_time date,
        queued_time    date default sysdate not null,
        start_time     date,
        end_time       date,
        status         varchar2(1 char) default 'I' not null,
        transferred    number(*, 0),
        failed         number(*, 0),
        message        varchar2(4000 char),
        run_log        clob,
        runner         varchar2(255 char),
        requested_by   varchar2(256 char),
        constraint rapo_transfer_run_pk primary key (run_id)
      )]';
    execute immediate 'create index rapo_transfer_run_param_ix on rapo_transfer_run (param_name, queued_time)';
    execute immediate 'create index rapo_transfer_run_time_ix on rapo_transfer_run (queued_time)';
  end if;

  select count(*) into v_count from user_tab_columns
   where table_name = 'RAPO_TRANSFER_RUN' and column_name = 'LOG_LINES';
  if v_count = 0 then
    execute immediate 'alter table rapo_transfer_run add (log_lines number(*, 0))';
  end if;

  select count(*) into v_count from user_sequences
   where sequence_name = 'RAPO_TRANSFER_LOG_SEQ';
  if v_count = 0 then
    execute immediate 'create sequence rapo_transfer_log_seq increment by 1 start with 1 nocache';
  end if;

  select count(*) into v_count from user_tables
   where table_name = 'RAPO_TRANSFER_LOG';
  if v_count = 0 then
    execute immediate q'[
      create table rapo_transfer_log (
        log_id     number default rapo_transfer_log_seq.nextval not null,
        run_id     number(*, 0) not null,
        param_name varchar2(128 char) not null,
        line_no    number(*, 0) not null,
        log_time   date,
        log_level  varchar2(8 char),
        message    varchar2(4000 char),
        constraint rapo_transfer_log_pk primary key (log_id)
      )]';
    execute immediate 'create index rapo_transfer_log_run_ix on rapo_transfer_log (run_id, line_no)';
    execute immediate 'create index rapo_transfer_log_param_ix on rapo_transfer_log (param_name, log_id)';
  end if;
end;
/

-- 2. Jobs

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
  make('sequence', 'rapo_job_seq',
       'create sequence rapo_job_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_job', q'[
    create table rapo_job (
      job_id          number(*, 0) default rapo_job_seq.nextval not null,
      job_name        varchar2(128 char) not null,
      description     varchar2(500 char),
      job_type        varchar2(16 char) not null,
      definition      clob not null,
      schedule_config varchar2(4000 char),
      status          varchar2(1 char) default 'Y' not null,
      notify_email    varchar2(500 char),
      version         number(*, 0) default 1 not null,
      created_date    date default sysdate not null,
      created_by      varchar2(256 char),
      updated_date    date default sysdate not null,
      updated_by      varchar2(256 char),
      constraint rapo_job_pk primary key (job_id),
      constraint rapo_job_name_uk unique (job_name),
      constraint rapo_job_status_ck check (status in ('Y', 'N'))
    )]');

  make('table', 'rapo_job_version', q'[
    create table rapo_job_version (
      job_id          number(*, 0) not null,
      version         number(*, 0) not null,
      definition      clob not null,
      schedule_config varchar2(4000 char),
      updated_date    date default sysdate not null,
      updated_by      varchar2(256 char),
      constraint rapo_job_version_pk primary key (job_id, version)
    )]');

  make('sequence', 'rapo_job_run_seq',
       'create sequence rapo_job_run_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_job_run', q'[
    create table rapo_job_run (
      run_id           number(*, 0) default rapo_job_run_seq.nextval not null,
      job_id           number(*, 0) not null,
      job_name         varchar2(128 char) not null,
      trigger_type     varchar2(16 char) not null,
      scheduled_time   date,
      queued_time      date default sysdate not null,
      start_time       date,
      end_time         date,
      status           varchar2(1 char) default 'I' not null,
      stage            varchar2(10 char),
      condition_value  varchar2(100 char),
      exit_code        number(*, 0),
      message          varchar2(4000 char),
      runner           varchar2(255 char),
      requested_by     varchar2(256 char),
      definition_version number(*, 0),
      log_lines        number(*, 0),
      mail_status      varchar2(200 char),
      workflow_run_id  number(*, 0),
      constraint rapo_job_run_pk primary key (run_id),
      constraint rapo_job_run_status_ck
        check (status in ('I', 'S', 'D', 'E', 'C'))
    )]');
  make('index', 'rapo_job_run_job_ix',
       'create index rapo_job_run_job_ix on rapo_job_run (job_id, queued_time)');
  make('index', 'rapo_job_run_time_ix',
       'create index rapo_job_run_time_ix on rapo_job_run (queued_time)');

  make('sequence', 'rapo_job_log_seq',
       'create sequence rapo_job_log_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_job_log', q'[
    create table rapo_job_log (
      log_id    number(*, 0) default rapo_job_log_seq.nextval not null,
      run_id    number(*, 0) not null,
      job_id    number(*, 0) not null,
      line_no   number(*, 0) not null,
      log_time  date,
      log_level varchar2(8 char),
      stream    varchar2(4 char),
      message   varchar2(4000 char),
      constraint rapo_job_log_pk primary key (log_id)
    )]');
  make('index', 'rapo_job_log_run_ix',
       'create index rapo_job_log_run_ix on rapo_job_log (run_id, line_no)');
  make('index', 'rapo_job_log_job_ix',
       'create index rapo_job_log_job_ix on rapo_job_log (job_id, log_id)');

  make('sequence', 'rapo_job_event_seq',
       'create sequence rapo_job_event_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_job_event', q'[
    create table rapo_job_event (
      event_id       number(*, 0) default rapo_job_event_seq.nextval not null,
      job_id         number(*, 0),
      trigger_type   varchar2(16 char),
      event_type     varchar2(10 char),
      scheduled_time date,
      event_time     date,
      updated        date,
      run_id         number(*, 0),
      message        varchar2(4000 char),
      runner         varchar2(255 char),
      constraint rapo_job_event_pk primary key (event_id)
    )]');
  make('index', 'rapo_job_event_job_ix',
       'create index rapo_job_event_job_ix on rapo_job_event (job_id, event_time)');
  make('index', 'rapo_job_event_time_ix',
       'create index rapo_job_event_time_ix on rapo_job_event (event_time)');
  make('index', 'rapo_job_event_upd_ix',
       'create index rapo_job_event_upd_ix on rapo_job_event (updated)');

  -- The lease of the job scheduler: a row of its own, so it is independent of
  -- the lease of the scheduler of the controls (RAPO.SCHEDULER).
  select count(*) into v_count from rapo_scheduler where id = 'RAPO.JOBS';
  if v_count = 0 then
    insert into rapo_scheduler (id, status) values ('RAPO.JOBS', 'N');
    commit;
  end if;
end;
/

-- 3. Workflows

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

-- 4. Groups of transfers

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
  make('sequence', 'rapo_transfer_group_seq',
       'create sequence rapo_transfer_group_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_transfer_group', q'[
    create table rapo_transfer_group (
      group_id        number(*, 0) default rapo_transfer_group_seq.nextval not null,
      group_name      varchar2(128 char) not null,
      description     varchar2(500 char),
      schedule_config varchar2(4000 char),
      status          varchar2(1 char) default 'Y' not null,
      notify_email    varchar2(500 char),
      overlap         varchar2(8 char) default 'skip' not null,
      created_date    date default sysdate not null,
      created_by      varchar2(256 char),
      updated_date    date default sysdate not null,
      updated_by      varchar2(256 char),
      constraint rapo_transfer_group_pk primary key (group_id),
      constraint rapo_transfer_group_name_uq unique (group_name),
      constraint rapo_transfer_group_ck check (status in ('Y', 'N')),
      constraint rapo_transfer_group_ov_ck check (overlap in ('skip', 'queue', 'allow'))
    )]');
  make('table', 'rapo_transfer_group_member', q'[
    create table rapo_transfer_group_member (
      group_id   number(*, 0) not null,
      position   number(*, 0) not null,
      param_name varchar2(128 char) not null,
      constraint rapo_transfer_group_member_pk primary key (group_id, position)
    )]');
  make('sequence', 'rapo_transfer_group_run_seq',
       'create sequence rapo_transfer_group_run_seq increment by 1 start with 1 nocache');
  make('table', 'rapo_transfer_group_run', q'[
    create table rapo_transfer_group_run (
      run_id         number(*, 0) default rapo_transfer_group_run_seq.nextval not null,
      group_id       number(*, 0) not null,
      group_name     varchar2(128 char) not null,
      trigger_type   varchar2(16 char) not null,
      scheduled_time date,
      queued_time    date default sysdate not null,
      start_time     date,
      end_time       date,
      status         varchar2(1 char) default 'I' not null,
      step_no        number(*, 0),
      steps_total    number(*, 0),
      message        varchar2(4000 char),
      run_log        clob,
      runner         varchar2(255 char),
      requested_by   varchar2(256 char),
      mail_status    varchar2(200 char),
      constraint rapo_transfer_group_run_pk primary key (run_id)
    )]');
  make('index', 'rapo_transfer_group_run_ix',
       'create index rapo_transfer_group_run_ix on rapo_transfer_group_run (group_id, queued_time)');
  make('index', 'rapo_transfer_group_run_time_ix',
       'create index rapo_transfer_group_run_time_ix on rapo_transfer_group_run (queued_time)');
  make('table', 'rapo_transfer_group_run_step', q'[
    create table rapo_transfer_group_run_step (
      run_id          number(*, 0) not null,
      step_no         number(*, 0) not null,
      param_name      varchar2(128 char) not null,
      transfer_run_id number(*, 0),
      status          varchar2(1 char) default 'W' not null,
      start_time      date,
      end_time        date,
      message         varchar2(4000 char),
      constraint rapo_transfer_group_step_pk primary key (run_id, step_no),
      constraint rapo_transfer_group_step_ck check (status in ('W', 'I', 'S', 'D', 'E', 'C'))
    )]');

  -- A transfer run started by a group says which run of the group.
  select count(*) into v_count from user_tab_columns
   where table_name = 'RAPO_TRANSFER_RUN' and column_name = 'GROUP_RUN_ID';
  if v_count = 0 then
    execute immediate 'alter table rapo_transfer_run add (group_run_id number(*, 0))';
  end if;
end;
/
