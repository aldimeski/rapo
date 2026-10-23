<template>
  <q-page class="column no-wrap" :style-fn="fillViewportToBottom">
    <h2 class="row items-center text-no-wrap q-gutter-lg q-mb-md">
      <div>Transfers</div>
      <div class="col row items-center justify-end q-gutter-sm text-body2 text-grey-8">
        <span v-if="data" :title="schedulerState(data.scheduler.state).description">
          <q-icon name="fas fa-clock" :color="schedulerState(data.scheduler.state).color" class="q-mr-xs" />
          Scheduler: {{ schedulerState(data.scheduler.state).label }}
        </span>
        <span v-if="data" :title="`${data.manager.running.length} running, ${data.manager.queued.length} queued; at most ${data.manager.capacity} at once`">
          <q-icon name="fas fa-sync" :class="{ 'fa-spin': data.manager.running.length }" color="blue-grey-5" class="q-mr-xs" />
          {{ data.manager.running.length }} running<template v-if="data.manager.queued.length">, {{ data.manager.queued.length }} queued</template>
        </span>
      </div>
    </h2>

    <q-banner v-if="data && !data.param_dir" dense class="bg-amber-1 text-amber-10 q-mb-sm">
      <template v-slot:avatar><q-icon name="fas fa-exclamation-triangle" color="amber-8" size="xs" /></template>
      <code>[TRANSFER] param_dir</code> is not set in rapo.ini: params can be kept in the database only.
    </q-banner>
    <div v-if="data && !data.crontab_supported" class="text-grey-7 text-caption q-mb-sm">
      <q-icon name="fas fa-info-circle" class="q-mr-xs" />This server is not Linux: the crontab is not read, so transfers are started by the rapo scheduler or by hand only.
    </div>
    <q-banner v-if="data && !data.database_available" dense class="bg-amber-1 text-amber-10 q-mb-sm">
      <template v-slot:avatar><q-icon name="fas fa-exclamation-triangle" color="amber-8" size="xs" /></template>
      The transfer tables are missing in the database: run <code>migrations/v0.8.8/upgrade.sql</code>. Params in the folder still run, but their schedule and history
      can not be kept.
    </q-banner>

    <q-card class="fill-card column no-wrap">
      <div class="row items-center q-pa-sm q-gutter-sm">
        <q-input v-model="search" dense outlined clearable debounce="200" placeholder="Search param, script or description" class="col-4">
          <template v-slot:prepend><q-icon name="fas fa-search" /></template>
        </q-input>
        <q-chip v-for="chip in chips" :key="chip.key" clickable dense :class="{ 'chip-selected': filter === chip.key }" :aria-pressed="String(filter === chip.key)" @click="filter = chip.key">
          {{ chip.label }} <span class="q-ml-xs">({{ chip.count }})</span>
        </q-chip>
        <q-space />
        <q-btn v-if="data && data.crontab_scan && data.crontab_supported" flat dense no-caps icon="fas fa-sync" label="Rescan crontab" :loading="rescanning" @click="load(true)">
          <q-tooltip>Read the crontab of the server user again and look into the scripts it runs</q-tooltip>
        </q-btn>
        <q-btn v-if="$can('datasources', 'edit')" color="teal" no-caps icon="fas fa-plus" label="New transfer" @click="openEdit(null)" />
      </div>
      <q-separator />

      <load-error-notice v-if="loadError" :message="`The transfers could not be loaded: ${loadError}`" @retry="load()" />
      <q-markup-table v-else flat dense class="col scroll transfers-table">
        <thead>
          <tr class="bg-blue-grey-2">
            <th class="text-left">Param</th>
            <th class="text-left" title="Where the param is kept: a .param file in the folder of rapo.ini [TRANSFER] param_dir, or a row of the database">Kept in</th>
            <th class="text-left" title="What starts the transfer: the scheduler of rapo, or the crontab of the server user">Started by</th>
            <th class="text-left">Schedule</th>
            <th class="text-left" title="The earliest next start, whoever starts it">Next start</th>
            <th class="text-left">Last run</th>
            <th></th>
          </tr>
        </thead>
        <tbody v-if="!data">
          <skeleton-rows :rows="5" :columns="['text', 'QChip', 'QChip', 'text', 'text', 'text', null]" />
        </tbody>
        <tbody v-else-if="!rows.length">
          <tr>
            <td colspan="7" class="text-grey-7 text-center">{{ data.params.length ? "No transfer matches" : "No transfers yet" }}</td>
          </tr>
        </tbody>
        <tbody v-else>
          <tr v-for="row in rows" :key="row.param_name">
            <td>
              <span class="text-weight-bold text-blue-grey-9">{{ row.param_name }}</span>
              <div v-if="row.description" class="text-grey-7 text-caption">{{ row.description }}</div>
            </td>
            <td>
              <q-chip dense :color="source(row).color" text-color="white" :icon="source(row).icon">
                {{ source(row).label }}
                <q-tooltip>{{ source(row).title }}</q-tooltip>
              </q-chip>
            </td>
            <td>
              <q-chip v-for="chip in startedBy(row)" :key="chip.key" dense :color="chip.color" text-color="white" :icon="chip.icon">
                {{ chip.label }}
                <q-tooltip>{{ chip.title }}</q-tooltip>
              </q-chip>
              <span v-if="!startedBy(row).length" class="text-grey-6">&mdash;</span>
            </td>
            <td class="schedule-cell">
              <div v-if="row.rapo && row.rapo.scheduled" :class="{ 'text-grey-6': !row.rapo.enabled }">
                <q-icon name="fas fa-clock" size="xs" color="teal" class="q-mr-xs" />
                {{ describe(row.rapo.config) }}<span v-if="!row.rapo.enabled"> (off)</span>
                <span class="text-grey-7 text-caption"> &middot; by the rapo scheduler</span>
              </div>
              <div v-for="entry in row.crontab" :key="entry.cron + entry.command + entry.param_path" class="text-no-wrap">
                <q-icon name="fas fa-terminal" size="xs" color="blue-grey" class="q-mr-xs" />
                <code>{{ entry.cron }}</code>
                <span class="text-grey-7 text-caption">
                  &middot; <template v-if="entry.script">via {{ baseName(entry.script) }}</template><template v-else>calls the script directly</template
                  ><template v-if="entry.user"> &middot; {{ entry.user }}</template>
                </span>
                <q-tooltip anchor="bottom left" self="top left">{{ entry.command }}</q-tooltip>
              </div>
              <span v-if="!startedBy(row).length" class="text-grey-6">Not scheduled</span>
            </td>
            <td class="text-no-wrap">
              <template v-if="nextStart(row)">
                <date-time-text :value="nextStart(row).when" />
                <div class="text-caption text-grey-7">{{ nextStart(row).by }}</div>
              </template>
            </td>
            <td class="text-no-wrap">
              <template v-if="row.last_run">
                <a class="cursor-pointer" v-keyboard:button @click="openRuns(row)">
                  <q-chip dense clickable>
                    <q-avatar :icon="runStatus(row.last_run.status).icon" :color="runStatus(row.last_run.status).color" text-color="white" />
                    {{ runStatus(row.last_run.status).label }}
                  </q-chip>
                </a>
                <date-time-text :value="row.last_run.end_time || row.last_run.start_time || row.last_run.queued_time" />
                <div v-if="row.last_run.transferred != null" class="text-caption text-grey-7">
                  {{ row.last_run.transferred }} transferred<template v-if="row.last_run.failed"> &middot; {{ row.last_run.failed }} failed</template>
                </div>
              </template>
              <span v-else class="text-grey-6">never</span>
            </td>
            <td class="text-no-wrap text-right">
              <q-btn
                v-if="active(row)"
                aria-label="Cancel run"
                size="sm"
                color="grey-7"
                round
                flat
                icon="fas fa-times"
                :disable="!$can('files', 'operate')"
                @click="cancelRun(row)">
                <q-tooltip>Cancel the run</q-tooltip>
              </q-btn>
              <q-btn v-else-if="row.source !== 'external'" aria-label="Run now" size="sm" color="teal" round flat icon="fas fa-play" :disable="!$can('files', 'operate') || row.source === 'both'" @click="runNow(row)">
                <q-tooltip>Run now</q-tooltip>
              </q-btn>
              <q-btn aria-label="More" size="sm" color="grey-7" round flat icon="fas fa-ellipsis-v" :disable="row.source === 'external'">
                <q-menu>
                  <q-list dense class="text-no-wrap">
                    <q-item clickable v-close-popup @click="openEdit(row)">
                      <q-item-section avatar><q-icon name="fas fa-edit" size="xs" /></q-item-section>
                      <q-item-section>{{ $can("datasources", "edit") ? "Edit param" : "Open param" }}</q-item-section>
                    </q-item>
                    <q-item v-if="$can('datasources', 'edit')" clickable v-close-popup :disable="row.source === 'both'" @click="openSchedule(row)">
                      <q-item-section avatar><q-icon name="fas fa-clock" size="xs" /></q-item-section>
                      <q-item-section>Schedule in rapo...</q-item-section>
                    </q-item>
                    <q-item clickable v-close-popup @click="openRuns(row)">
                      <q-item-section avatar><q-icon name="fas fa-history" size="xs" /></q-item-section>
                      <q-item-section>Run history</q-item-section>
                    </q-item>
                    <template v-if="$can('datasources', 'edit') && row.source !== 'both'">
                      <q-separator />
                      <q-item clickable v-close-popup :disable="row.source === 'file' ? !data.database_available : !data.param_dir" @click="move(row)">
                        <q-item-section avatar><q-icon :name="row.source === 'file' ? 'fas fa-database' : 'fas fa-file-alt'" size="xs" /></q-item-section>
                        <q-item-section>{{ row.source === "file" ? "Move to the database" : "Move to a file" }}</q-item-section>
                      </q-item>
                    </template>
                    <template v-if="$can('datasources', 'edit')">
                      <q-separator />
                      <q-item clickable v-close-popup class="text-deep-orange" @click="remove(row)">
                        <q-item-section avatar><q-icon name="fas fa-trash" size="xs" /></q-item-section>
                        <q-item-section>Delete</q-item-section>
                      </q-item>
                    </template>
                  </q-list>
                </q-menu>
              </q-btn>
            </td>
          </tr>
        </tbody>
      </q-markup-table>
    </q-card>

    <transfer-edit-dialog v-model="editOpen" :name="editName" :param-dir="data && data.param_dir" :database-available="!data || data.database_available" @saved="load()" />
    <transfer-schedule-dialog v-model="scheduleOpen" :row="scheduleRow" @saved="load()" />
    <transfer-runs-dialog v-model="runsOpen" :name="runsName" />
  </q-page>
</template>

<script>
import DateTimeText from "./DateTimeText.vue";
import LoadErrorNotice from "./LoadErrorNotice.vue";
import SkeletonRows from "./SkeletonRows.vue";
import TransferEditDialog from "./TransferEditDialog.vue";
import TransferRunsDialog from "./TransferRunsDialog.vue";
import TransferScheduleDialog from "./TransferScheduleDialog.vue";
import { api, notifyError } from "../api";
import { ACTIVE_RUN_STATUSES, runStatus, schedulerState } from "../constants";
import { fillViewportToBottom } from "../utils/layout";
import { describeSchedule } from "../utils/schedule";
import { escapeHtml, toMillis } from "../utils/format";

const REFRESH_SECONDS = 5;

// Where a param is kept.
const SOURCES = {
  file: { label: "File", icon: "fas fa-file-alt", color: "blue-grey-6", title: "A .param file in the folder of [TRANSFER] param_dir" },
  db: { label: "Database", icon: "fas fa-database", color: "indigo-5", title: "A row of rapo_transfer_param" },
  both: { label: "File + database", icon: "fas fa-exclamation-triangle", color: "deep-orange", title: "The same name exists as a file and in the database: delete one of them" },
  external: { label: "Outside", icon: "fas fa-question-circle", color: "grey", title: "Only the crontab knows this param: it is not in param_dir or the database, so rapo can not edit or run it" },
};

export default {
  components: { DateTimeText, LoadErrorNotice, SkeletonRows, TransferEditDialog, TransferRunsDialog, TransferScheduleDialog },
  data() {
    return {
      data: null,
      loadError: null,
      rescanning: false,
      search: "",
      filter: "all",
      editOpen: false,
      editName: null,
      scheduleOpen: false,
      scheduleRow: null,
      runsOpen: false,
      runsName: null,
    };
  },
  computed: {
    allRows() {
      return this.data ? this.data.params : [];
    },
    chips() {
      const count = (test) => this.allRows.filter(test).length;
      return [
        { key: "all", label: "All", count: this.allRows.length },
        { key: "file", label: "Files", count: count((row) => row.source === "file") },
        { key: "db", label: "Database", count: count((row) => row.source === "db") },
        { key: "rapo", label: "Rapo scheduler", count: count((row) => row.scheduling === "rapo" || row.scheduling === "both") },
        // The crontab exists on Linux only: elsewhere nothing is ever started by it.
        ...(this.data && this.data.crontab_supported ? [{ key: "crontab", label: "Crontab", count: count((row) => row.scheduling === "crontab" || row.scheduling === "both") }] : []),
        { key: "none", label: "Not scheduled", count: count((row) => !row.scheduling) },
      ];
    },
    rows() {
      const needle = (this.search || "").toLowerCase();
      const tests = {
        all: () => true,
        file: (row) => row.source === "file",
        db: (row) => row.source === "db",
        rapo: (row) => row.scheduling === "rapo" || row.scheduling === "both",
        crontab: (row) => row.scheduling === "crontab" || row.scheduling === "both",
        none: (row) => !row.scheduling,
      };
      return this.allRows.filter(
        (row) =>
          tests[this.filter](row) &&
          (!needle ||
            [row.param_name, row.description, ...row.crontab.flatMap((entry) => [entry.script, entry.command, entry.cron])].some((text) => text && String(text).toLowerCase().includes(needle)))
      );
    },
  },
  methods: {
    fillViewportToBottom,
    runStatus,
    schedulerState,
    describe(config) {
      return describeSchedule(config);
    },
    baseName(path) {
      return String(path).split(/[\\/]/).pop();
    },
    source(row) {
      return SOURCES[row.source] || SOURCES.external;
    },
    active(row) {
      return row.running || (row.last_run && ACTIVE_RUN_STATUSES.includes(row.last_run.status));
    },
    // What starts the param: the rapo scheduler, the crontab, both (it would run twice).
    startedBy(row) {
      const chips = [];
      const scheduler = this.data.scheduler;
      if (row.rapo && row.rapo.scheduled) {
        const words = this.describe(row.rapo.config);
        const state = scheduler.state === "running" || scheduler.state === "standby" ? "" : ` The scheduler is ${schedulerState(scheduler.state).label.toLowerCase()}, so it does not fire.`;
        chips.push({
          key: "rapo",
          label: row.rapo.enabled ? "Rapo" : "Rapo (off)",
          icon: "fas fa-clock",
          color: row.rapo.enabled && !state ? "teal" : "grey",
          title: `${words}.${row.rapo.enabled ? "" : " Switched off."}${state}`,
        });
      }
      if (row.crontab.length) {
        const scripts = [...new Set(row.crontab.map((entry) => (entry.script ? this.baseName(entry.script) : "the transfer script")))];
        chips.push({ key: "crontab", label: "Crontab", icon: "fas fa-terminal", color: "blue-grey-7", title: `Started from the crontab through ${scripts.join(", ")}` });
      }
      if (row.scheduling === "both") {
        chips.push({ key: "both", label: "Twice!", icon: "fas fa-exclamation-triangle", color: "deep-orange", title: "Both the rapo scheduler and the crontab start it: remove one of them, or it runs twice" });
      }
      return chips;
    },
    nextStart(row) {
      const starts = [];
      if (row.rapo && row.rapo.enabled && row.rapo.next_fire) {
        starts.push({ when: row.rapo.next_fire, by: "rapo scheduler" });
      }
      row.crontab.filter((entry) => entry.next_fire).forEach((entry) => starts.push({ when: entry.next_fire, by: "crontab" }));
      return starts.sort((a, b) => toMillis(a.when) - toMillis(b.when))[0] || null;
    },
    async load(rescan = false) {
      this.rescanning = rescan;
      try {
        this.data = await api("get-transfers", { params: { rescan: rescan ? "true" : null }, loadingBar: rescan });
        this.loadError = null;
      } catch (error) {
        if (this.data) {
          notifyError("Failed to refresh the transfers.", error);
        } else {
          this.loadError = error.message;
        }
      } finally {
        this.rescanning = false;
      }
    },
    openEdit(row) {
      this.editName = row ? row.param_name : null;
      this.editOpen = true;
    },
    openSchedule(row) {
      this.scheduleRow = row;
      this.scheduleOpen = true;
    },
    openRuns(row) {
      this.runsName = row.param_name;
      this.runsOpen = true;
    },
    async runNow(row) {
      try {
        await api("run-transfer", { method: "POST", params: { name: row.param_name } });
        this.$q.notify({ type: "positive", message: `${row.param_name} queued.` });
        await this.load();
      } catch (error) {
        notifyError(`Failed to run ${row.param_name}.`, error);
      }
    },
    async cancelRun(row) {
      try {
        await api("cancel-transfer", { method: "POST", params: { run_id: row.last_run.run_id } });
        await this.load();
      } catch (error) {
        notifyError(`Failed to cancel ${row.param_name}.`, error);
      }
    },
    // Asks the server what the move changes first: a warning (a param without LogDir and LogFile has no logging as a
    // file) needs the user to confirm it.
    async move(row) {
      const target = row.source === "file" ? "db" : "file";
      let plan;
      try {
        plan = await api("check-move-transfer", { params: { name: row.param_name, target } });
      } catch (error) {
        notifyError(`Failed to check the move of ${row.param_name}.`, error);
        return;
      }
      const warned = plan.warnings.length > 0;
      const list = (items, color) => items.map((item) => `<div class="${color} q-mt-sm">${escapeHtml(item)}</div>`).join("");
      this.$q
        .dialog({
          title: warned ? "Move param: no logging" : "Move param",
          message:
            `Move <b>${escapeHtml(row.param_name)}</b> to ${target === "db" ? "the database" : "a file in param_dir"}? Its schedule stays.` +
            list(plan.warnings, "text-deep-orange-9 text-weight-bold") +
            list(plan.notes, "text-grey-8"),
          html: true,
          cancel: true,
          persistent: true,
          ok: { label: warned ? "Move anyway" : "Move", color: warned ? "deep-orange" : "teal", flat: true },
        })
        .onOk(async () => {
          try {
            await api("move-transfer", { method: "POST", params: { name: row.param_name, target, confirm: warned ? "true" : null } });
            await this.load();
          } catch (error) {
            notifyError(`Failed to move ${row.param_name}.`, error);
          }
        });
    },
    remove(row) {
      this.$q
        .dialog({
          title: "Delete param",
          message: `Delete ${row.param_name}${row.source === "file" ? " (its .param file)" : ""} and its schedule? The history of its runs stays.`,
          cancel: true,
          persistent: true,
          ok: { label: "Delete", color: "deep-orange", flat: true },
        })
        .onOk(async () => {
          try {
            await api("delete-transfer", { method: "DELETE", params: { name: row.param_name } });
            await this.load();
          } catch (error) {
            notifyError(`Failed to delete ${row.param_name}.`, error);
          }
        });
    },
  },
  mounted() {
    this.load();
    // The runs end and the schedules move on without the page being touched.
    this.timer = setInterval(() => {
      if (!document.hidden && !this.rescanning) {
        this.load();
      }
    }, REFRESH_SECONDS * 1000);
  },
  unmounted() {
    clearInterval(this.timer);
  },
};
</script>

<style scoped>
.fill-card {
  flex: 1 1 0;
  min-height: 0;
}
.schedule-cell {
  min-width: 260px;
}
</style>
