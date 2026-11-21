<template>
  <q-dialog v-model="visible" @show="open" @hide="stop">
    <q-card class="column no-wrap" style="width: 1800px; max-width: 95vw; height: 88vh">
      <q-card-section class="row items-center q-py-sm">
        <div class="text-h6">Runs of {{ name }}</div>
        <q-space />
        <q-btn aria-label="Refresh" flat round dense icon="fas fa-sync" @click="load" />
        <q-btn aria-label="Close" flat round icon="fas fa-times" v-close-popup />
      </q-card-section>
      <q-separator />

      <!-- The runs take a fixed width, the log all the rest; the divider can be dragged. -->
      <q-splitter v-model="split" unit="px" :limits="[380, 900]" class="col" style="min-height: 0" separator-class="runs-separator">
        <template #before>
          <q-markup-table flat dense class="runs-table scroll full-height">
            <thead>
              <tr class="bg-blue-grey-2">
                <th class="text-left">Status</th>
                <th class="text-left">Queued</th>
                <th class="text-left">Duration</th>
                <th class="text-left">Trigger</th>
                <th class="text-right">Files</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!runs"><td colspan="5" class="text-center"><q-spinner color="teal" /></td></tr>
              <tr v-else-if="!runs.length"><td colspan="5" class="text-grey-7 text-center">No runs yet</td></tr>
              <tr v-for="run in runs || []" :key="run.run_id" class="cursor-pointer" :class="{ 'bg-blue-grey-1': selected && selected.run_id === run.run_id }" @click="select(run)">
                <td>
                  <q-chip dense>
                    <q-avatar :icon="runStatus(run.status).icon" :color="runStatus(run.status).color" text-color="white" />
                    {{ runStatus(run.status).label }}
                  </q-chip>
                </td>
                <td class="text-no-wrap"><date-time-text :value="run.queued_time" /></td>
                <td class="text-no-wrap">{{ duration(run) }}</td>
                <td class="text-no-wrap">
                  <q-icon :name="trigger(run).icon" color="blue-grey-5" class="q-mr-xs" />{{ trigger(run).label }}
                  <q-tooltip v-if="run.requested_by">by {{ run.requested_by }}</q-tooltip>
                </td>
                <td class="text-right">
                  <template v-if="run.transferred != null">{{ run.transferred }}<span v-if="run.failed" class="text-deep-orange"> / {{ run.failed }} failed</span></template>
                </td>
              </tr>
            </tbody>
          </q-markup-table>
        </template>

        <template #after>
          <div class="column no-wrap q-pa-sm full-height" style="min-width: 0">
            <div v-if="!selected" class="text-grey-7 text-center q-mt-xl">Select a run to read its log</div>
            <template v-else>
              <div class="text-grey-7 q-mb-xs">
                Run {{ selected.run_id }} on {{ selected.runner }}<template v-if="selected.scheduled_time"> &middot; scheduled for {{ toDateTimeString(selected.scheduled_time) }}</template>
              </div>
              <pre v-if="selected.message" class="log-message">{{ selected.message }}</pre>
              <div v-if="selected.log_lines != null" class="text-grey-7 text-caption q-mb-xs">
                <q-icon name="fas fa-database" class="q-mr-xs" />Log kept in the database ({{ selected.log_lines }} lines logged)<span v-if="selected.log_trimmed" class="text-deep-orange-9">
                  &middot; its oldest lines were left out: only the newest lines of each param are kept (rapo.ini [TRANSFER] log_max_lines)</span>
              </div>
              <div class="log-body col scroll">
                <div v-if="!logLines.length" class="log-line log-muted">No log lines.</div>
                <div v-for="(line, index) in logLines" :key="index" class="log-line" :class="lineClass(line)">{{ line }}</div>
              </div>
            </template>
          </div>
        </template>
      </q-splitter>
    </q-card>
  </q-dialog>
</template>

<script>
import DateTimeText from "./DateTimeText.vue";
import { api, notifyError } from "../api";
import { ACTIVE_RUN_STATUSES, TRIGGER_TYPES, runStatus } from "../constants";
import { toDateTimeString, toMillis } from "../utils/format";

// The runs of one transfer and the log of the one picked.
export default {
  components: { DateTimeText },
  props: {
    modelValue: { type: Boolean, default: false },
    name: { type: String, default: null },
  },
  emits: ["update:modelValue"],
  data() {
    return { runs: null, selected: null, split: 560 };
  },
  computed: {
    logLines() {
      return this.selected && this.selected.run_log ? this.selected.run_log.split("\n") : [];
    },
    visible: {
      get() {
        return this.modelValue;
      },
      set(value) {
        this.$emit("update:modelValue", value);
      },
    },
  },
  methods: {
    runStatus,
    // ERROR: and WARNING: stand before the message of those lines.
    lineClass(line) {
      return { "log-error": line.includes("[ ERROR:"), "log-warning": line.includes("[ WARNING:") };
    },
    toDateTimeString,
    trigger(run) {
      return TRIGGER_TYPES[String(run.trigger_type).toUpperCase()] || { label: run.trigger_type, icon: "fas fa-question-circle" };
    },
    duration(run) {
      if (!run.start_time) {
        return "";
      }
      const seconds = Math.max(0, Math.round(((run.end_time ? toMillis(run.end_time) : Date.now()) - toMillis(run.start_time)) / 1000));
      return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
    },
    async open() {
      this.runs = null;
      this.selected = null;
      await this.load();
      this.stop();
      this.timer = setInterval(() => this.load(), 3000);
    },
    stop() {
      clearInterval(this.timer);
    },
    async load() {
      try {
        this.runs = await api("get-transfer-runs", { params: { name: this.name, limit: 200 }, loadingBar: false });
        if (this.selected) {
          const run = this.runs.find((item) => item.run_id === this.selected.run_id);
          // The log of a run still active grows.
          if (run && ACTIVE_RUN_STATUSES.includes(this.selected.status)) {
            await this.select(run);
          }
        } else if (this.runs.length) {
          await this.select(this.runs[0]);
        }
      } catch (error) {
        this.stop();
        notifyError("Failed to load the runs.", error);
      }
    },
    async select(run) {
      try {
        this.selected = await api("get-transfer-run", { params: { run_id: run.run_id }, loadingBar: false });
      } catch (error) {
        notifyError("Failed to load the run.", error);
      }
    },
  },
  unmounted() {
    this.stop();
  },
};
</script>

<style scoped>
.log-body {
  min-height: 0;
  padding: 4px 0;
  border: 1px solid var(--rapo-panel-border);
  background: var(--rapo-surface-alt);
  color: var(--rapo-header-text);
}
.log-line {
  padding: 0 8px;
  font-family: var(--rapo-font-mono);
  font-size: 12px;
  line-height: 18px;
  white-space: pre-wrap;
  word-break: break-word;
}
.log-line:hover {
  background: var(--rapo-row-hover);
}
.log-error {
  color: var(--rapo-crit);
  font-weight: 600;
}
.log-warning {
  color: var(--rapo-warn);
}
.log-muted {
  color: var(--rapo-muted);
}
.log-message {
  margin: 0 0 8px;
  padding: 6px 8px;
  font-family: var(--rapo-font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--rapo-crit);
  background: var(--rapo-crit-soft);
}
</style>