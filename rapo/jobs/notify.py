"""Contains the mail a failed job run sends.

Every error of a run (PRE, CONDITION, MAIN, POST, a timeout, a run that
ended unexpectedly) is reported with one e-mail to the address of the job,
else to `[JOBS] default_email`. A run the user canceled, one whose condition
was not met and one skipped because the job was still running are not
errors and send nothing. The mail needs `[EMAIL] enabled=True`; when it can
not be sent the run is still an error, and says why in `mail_status`.
"""

import datetime as dt
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .. import options
from ..logger import logger

LOG_LINES = 40


def _mailer():
    """The mail module of rapo, imported when the first mail is sent."""
    from ..core import mailer
    return mailer


def recipients(job):
    """Get the list of addresses of a job (a row of the store), those of
    its own or else the default one."""
    mailer = _mailer()
    own = mailer.addresses(job.get('notify_email'))
    return own or mailer.addresses(options.get('JOBS', 'default_email'))


def _when(moment):
    return f'{moment:%Y-%m-%d %H:%M:%S}' if moment else '-'


def build_message(job, run, tail, sender, sender_name=None, reply_to=None,
                  to=()):
    """Get the e-mail of a failed run: `job` and `run` are rows of the
    store, `tail` the last lines of the log."""
    instance = options.get('SCHEDULER', 'instance_name')
    prefix = f'[{instance}] ' if instance else ''
    message = EmailMessage()
    message['From'] = formataddr((sender_name or '', str(sender)))
    message['To'] = ', '.join(to)
    if reply_to:
        message['Reply-To'] = str(reply_to)
    message['Subject'] = f'{prefix}Job {job["job_name"]} failed'
    message['Date'] = dt.datetime.now().astimezone()
    message['Message-ID'] = make_msgid()
    lines = [f'The job {job["job_name"]} did not succeed.', '',
             f'Run:        {run["run_id"]} ({run.get("trigger_type")})',
             f'Scheduled:  {_when(run.get("scheduled_time"))}',
             f'Started:    {_when(run.get("start_time"))}',
             f'Ended:      {_when(run.get("end_time"))}',
             f'Stage:      {run.get("stage") or "-"}']
    if run.get('exit_code') is not None:
        lines.append(f'Exit code:  {run["exit_code"]}')
    if instance:
        lines.append(f'Instance:   {instance}')
    lines += ['', 'What happened:', run.get('message') or '-']
    if tail:
        lines += ['', f'Last {len(tail)} lines of the log:'] + list(tail)
    message.set_content('\n'.join(lines) + '\n')
    return message


def send_failure(job, run, tail=()):
    """Send the mail of a failed run. Never raises. Returns the text for
    `mail_status` of the run: what was done or why not."""
    try:
        mailer = _mailer()
        to = recipients(job)
        if not to:
            return 'Not sent: the job has no address and [JOBS] ' \
                   'default_email is not set.'
        params = mailer.settings()
        if not params.get('enabled'):
            return 'Not sent: [EMAIL] enabled is off in rapo.ini.'
        sender = params.get('sender')
        if not sender:
            return 'Not sent: [EMAIL] sender is not set in rapo.ini.'
        message = build_message(job, run, list(tail)[-LOG_LINES:], sender,
                                params.get('sender_name'),
                                params.get('reply_to'), to)
        refused = mailer.deliver(message, to)
        if refused:
            logger.warning(f'Job {job["job_name"]}: mail refused for '
                           f'{refused}')
            return f'Sent to {len(to) - len(refused)} of {len(to)}, ' \
                   f'refused: {", ".join(refused)}'
        logger.info(f'Job {job["job_name"]}: failure mail sent to '
                    f'{", ".join(to)}')
        return f'Sent to {", ".join(to)}'
    except Exception as error:
        logger.error()
        return f'Not sent: {type(error).__name__}: {error}'
