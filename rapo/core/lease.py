"""Contains the lease: the right of one server to run a scheduler.

`rapo_scheduler` has a row for every scheduler: `RAPO.SCHEDULER` for the
controls, `RAPO.JOBS` for the jobs. A server that wants to run one holds a
lease on its row, renewed every few seconds with a heartbeat; the others
stay on standby and take the row over when the heartbeat is older than the
timeout. Whether the scheduler is stopped (from the UI, for every server) is
the `disabled` flag of the row.

Every statement names its row, so the schedulers never touch each other's.
"""

import datetime as dt
import getpass
import os
import platform

from ..database import db
from ..logger import logger

SCHEDULER_ROW = 'RAPO.SCHEDULER'
JOBS_ROW = 'RAPO.JOBS'

RENEWED, LOST, ACQUIRED = 'renewed', 'lost', 'acquired'


def pid_exists(pid):
    """Tell whether a process of this host is alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Windows answers a pid that is gone with an OSError of its own.
        import psutil
        return psutil.pid_exists(pid)
    return True


class Lease:
    """Represents the lease of one scheduler row.

    Attributes
    ----------
    row_id : str
        The `id` of the row of `rapo_scheduler`.
    instance_id : str
        What tells this holder from every other, also of the same server.
    leader : bool
        Whether this server holds the lease.
    """

    def __init__(self, row_id, instance_id, timeout, table=None,
                 standalone=False):
        """`timeout` is a function giving the seconds of silence after which
        a holder is taken over; `table` the Table of `rapo_scheduler`
        (default: the one of the database); `standalone` accepts the row of
        a standalone rapo-scheduler of an older version, which has no
        instance."""
        self.row_id = row_id
        self.instance_id = instance_id
        self.timeout = timeout
        self._table = table
        self.standalone = standalone
        self.server = platform.node()
        self.username = getpass.getuser()
        self.pid = os.getpid()
        self.leader = False
        self.warned = False

    @property
    def table(self):
        """The Table of `rapo_scheduler`."""
        return self._table if self._table is not None \
            else db.tables.scheduler

    def read(self):
        """Get the row as a dict, None when there is none."""
        table = self.table
        return db.execute(table.select().where(table.c.id == self.row_id),
                          as_dict=True)

    def alive(self, record):
        """Tell whether the holder in the record is still sending
        heartbeats."""
        heartbeat = (record or {}).get('heartbeat')
        return (record.get('status') == 'Y' and heartbeat is not None
                and (dt.datetime.now() - heartbeat).total_seconds()
                < self.timeout())

    def renew_or_acquire(self):
        """Renew the lease when this server holds it, else take it when it
        is free. Returns `(RENEWED,)`, `(LOST,)` (it was held and is not any
        more, or the scheduler was stopped: call `release`),
        `(ACQUIRED, since)` (`since` is the last moment the scheduler is
        known to have run, None for a first start) or None (it is held by
        another server, or stopped)."""
        table = self.table
        now = dt.datetime.now()
        if self.leader:
            update = (table.update()
                      .values(heartbeat=now)
                      .where(table.c.id == self.row_id,
                             table.c.instance_id == self.instance_id,
                             table.c.status == 'Y',
                             table.c.disabled == 'N'))
            if db.execute(update).rowcount == 1:
                return (RENEWED,)
            return (LOST,)

        record = self.read()
        if not record or record['disabled'] == 'Y':
            return None
        if record['status'] == 'Y' and record['instance_id'] is None \
                and self.standalone:
            # Row left by a standalone rapo-scheduler of an older version.
            pid = record['pid']
            if record['server'] == self.server and pid and \
                    not pid_exists(int(pid)):
                logger.info('Taking over stale standalone scheduler lease')
            else:
                if not self.warned:
                    logger.warning('Standalone rapo-scheduler seems to be '
                                   f'running at {record["server"]} PID '
                                   f'{pid}, stop it to start this one')
                self.warned = True
                return None
        elif (record['status'] == 'Y'
              and record['instance_id'] != self.instance_id
              and record['heartbeat'] is not None
              and (now - record['heartbeat']).total_seconds()
              < self.timeout()
              and not (record['server'] == self.server and record['pid']
                       and not pid_exists(int(record['pid'])))):
            # Holder is alive, or on another host and its lease is fresh.
            return None

        conditions = [table.c.id == self.row_id, table.c.disabled == 'N']
        for column in ('instance_id', 'heartbeat', 'status'):
            value = record[column]
            column = table.c[column]
            conditions.append(column.is_(None) if value is None
                              else column == value)
        update = (table.update()
                  .values(server=self.server, username=self.username,
                          pid=self.pid, instance_id=self.instance_id,
                          heartbeat=now, start_date=now, stop_date=None,
                          status='Y')
                  .where(*conditions))
        if db.execute(update).rowcount != 1:
            return None
        self.leader = True
        since = max([date for date in (record['heartbeat'],
                                       record['stop_date'])
                     if date is not None], default=None)
        return (ACQUIRED, since)

    def release(self):
        """Give the lease up, so that another server takes it at once."""
        self.leader = False
        table = self.table
        now = dt.datetime.now()
        update = (table.update()
                  .values(stop_date=now, heartbeat=now, status='N')
                  .where(table.c.id == self.row_id,
                         table.c.instance_id == self.instance_id))
        try:
            db.execute(update)
        except Exception:
            logger.error()

    def set_disabled(self, disabled):
        """Stop the scheduler for every server, or let it run again.

        Starting resumes from now: the marks the next holder measures its
        downtime from are moved, so that it is true on whichever server
        takes the lease."""
        table = self.table
        values = {'disabled': 'Y' if disabled else 'N'}
        if not disabled:
            now = dt.datetime.now()
            values.update(heartbeat=now, stop_date=now)
        db.execute(table.update().where(table.c.id == self.row_id)
                   .values(**values))
