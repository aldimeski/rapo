-- Rapo v0.8.7 -> v0.8.8
-- Adds the file transfers: the .param texts kept in the database
-- (rapo_transfer_param), their schedules (rapo_transfer_schedule) and the
-- history of the runs (rapo_transfer_run) and their database logs (rapo_transfer_log).
-- Re-runnable.

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