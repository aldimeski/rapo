-- Rapo v0.8.9 -> v0.9.0
-- Adds the jobs (RAJO): the job definitions (rapo_job) and their versions
-- (rapo_job_version), the history of the runs (rapo_job_run) with their log
-- lines (rapo_job_log), the events of the job scheduler (rapo_job_event), and
-- the lease row of the job scheduler in rapo_scheduler.
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
