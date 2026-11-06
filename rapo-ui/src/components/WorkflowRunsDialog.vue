<template>
  <q-dialog v-model="visible" @show="open" @hide="stop">
    <q-card class="column no-wrap" style="width: 1800px; max-width: 95vw; height: 88vh">
      <q-card-section class="row items-center q-py-sm">
        <div class="text-h6">Runs of {{ name || "every workflow" }}</div>
        <q-space />
        <q-btn aria-label="Refresh" flat round dense icon="fas fa-sync" @click="load" />
        <q-btn aria-label="Close" flat round icon="fas fa-times" v-close-popup />
      </q-card-section>
      <q-separator />

      <q-splitter v-model="split" unit="px" :limits="[420, 900]" class="col" style="min-height: 0" separator-class="runs-separator">
        <template #before>
          <q-markup-table flat dense class="runs-table scroll full-height">
            <thead>
              <tr class="bg-blue-grey-2">
                <th class="text-left">Status</th>
                <th v-if="!name" class="text-left">Workflow</th>
                <th class="text-left">Queued</th>
                <th class="text-left">Duration</th>
                <th class="text-left">Trigger</th>
                <th class="text-left" title="The step it got to, of the steps of the workflow">Step</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!runs"><td colspan="6" class="text-center"><q-spinner color="teal" /></td></tr>
              <tr v-else-if="!runs.length"><td colspan="6" class="text-grey-7 text-center">No runs yet</td></tr>
              <tr v-for="run in runs || []" :key="run.run_id" class="cursor-pointer" :class="{ 'bg-blue-grey-1': selected && selected.run_id === run.run_id }" @click="select(run)">
                <td>
                  <q-chip dense>
                    <q-avatar :icon="workflowRunStatus(run).icon" :color="workflowRunStatus(run).color" text-color="white" />
                    {{ workflowRunStatus(run).label }}
                  </q-chip>
                </td>
                <td v-if="!name" class="text-no-wrap">{{ run.workflow_name }}</td>
                <td class="text-no-wrap"><date-time-text :value="run.queued_time" /></td>
                <td class="text-no-wrap">{{ duration(run) }}</td>
                <td class="text-no-wrap">
                  <q-icon :name="trigger(run).icon" color="blue-grey-5" class="q-mr-xs" />{{ trigger(run).label }}
                  <q-tooltip v-if="run.requested_by">by {{ run.requested_by }}</q-tooltip>
                </td>
                <td class="text-no-wrap">{{ run.step_no ? `${run.step_no} of ${run.steps_total}` : "" }}</td>
              </tr>
            </tbody>
          </q-markup-table>
        </template>

        <template #after>
          <div class="column no-wrap q-pa-sm full-height" style="min-width: 0">
            <div v-if="!selected" class="text-grey-7 text-center q-mt-xl">Select a run to see its steps and its log</div>
            <template v-else>
              <div class="row items-center q-mb-xs">
                <div class="col text-grey-7">
                  Run {{ selected.run_id }} on {{ selected.runner }}
                  <template v-if="selected.scheduled_time"> &middot; scheduled for {{ toDateTimeString(selected.scheduled_time) }}</template>
                  &middot; version {{ selected.definition_version }}
                </div>
                <q-btn v-if="active(selected) && $can('jobs', 'operate')" size="sm" color="grey-7" flat no-caps icon="fas fa-times" label="Cancel the run" @click="cancel" />
              </div>
              <pre v-if="selected.message" class="log-message" :class="'log-message--' + messageKind">{{ selected.message }}</pre>
              <div v-if="selected.mail_status" class="text-caption q-mb-xs" :class="mailFailed ? 'text-deep-orange-9' : 'text-grey-7'">
                <q-icon name="fas fa-envelope" class="q-mr-xs" />{{ selected.mail_status }}
              </div>

              <!-- The steps, each with the run of its job: click one to read the log of that run. -->
              <q-markup-table flat dense class="steps-table q-mb-sm">
                <thead>
                  <tr class="bg-blue-grey-2">
                    <th class="text-left" style="width: 40px">#</th>
                    <th class="text-left">Job</th>
                    <th class="text-left">Status</th>
                    <th class="text-left">Duration</th>
                    <th class="text-left">Message</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="step in selected.steps"
                    :key="step.step_no"
                    :class="{ 'cursor-pointer': step.job_run_id, 'bg-blue-grey-1': stepRun && stepRun.run_id === step.job_run_id }"
                    @click="step.job_run_id && openStep(step)">
                    <td>{{ step.step_no }}</td>
                    <td class="text-no-wrap">{{ step.job_name }}</td>
                    <td>
                      <q-chip dense>
                        <q-avatar :icon="runStatus(step.status).icon" :color="runStatus(step.status).color" text-color="white" />
                        {{ runStatus(step.status).label }}
                      </q-chip>
                    </td>
                    <td class="text-no-wrap">{{ stepDuration(step) }}</td>
                    <td class="text-grey-8">{{ step.message }}</td>
                  </tr>
                </tbody>
              </q-markup-table>

              <div class="text-grey-7 text-caption q-mb-xs">
                <template v-if="stepRun">Log of the run {{ stepRun.run_id }} of the job {{ stepRun.job_name }}
                  <a class="cursor-pointer q-ml-sm" v-keyboard:button @click="stepRun = null">Show the log of the workflow</a></template>
                <template v-else>Log of the workflow, {{ selected.log_lines || 0 }} lines</template>
              </div>
              <div class="log-body col scroll">
                <div v-if="!lines.length" class="log-line log-muted">No log lines.</div>
                <div v-for="(line, index) in lines" :key="index" class="log-line" :class="lineClass(line)">
                  <span class="log-time">{{ toTimeString(line.time) }}</span>{{ line.text }}
                </div>
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
import { ACTIVE_RUN_STATUSES, TRIGGER_TYPES, runStatus, workflowRunStatus } from "../constants";
import { toDateTimeString, toMillis, toTimeString } from "../utils/format";

// The runs of one workflow (or of all of them): the steps of the one picked, each with the run of its job and its
// log, and the log of the workflow, which says where it stopped and why.
export default {
  components: { DateTimeText },
  props: {
    modelValue: { type: Boolean, default: false },
    // The workflow, or null for the runs of every workflow.
    name: { type: String, default: null },
    // A run to show first.
    runId: { type: Number, default: null },
  },
  emits: ["update:modelValue", "changed"],
  data() {
    return { runs: null, selected: null, stepRun: null, split: 620 };
  },
  computed: {
    lines() {
      if (this.stepRun) {
        return this.stepRun.lines || [];
      }
      return this.selected && this.selected.lines ? this.selected.lines : [];
    },
    mailFailed() {
      return /^not sent/i.test(this.selected.mail_status || "");
    },
    // An error is red; a workflow that stopped as a warning is amber; the rest is a note.
    messageKind() {
      if (this.selected.status === "E") {
        return "error";
      }
      return workflowRunStatus(this.selected).color === "amber-8" ? "warning" : "soft";
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
    workflowRunStatus,
    toDateTimeString,
    toTimeString,
    active(run) {
      return ACTIVE_RUN_STATUSES.includes(run.status);
    },
    lineClass(line) {
      return { "log-error": line.level === "ERROR", "log-warning": line.level === "WARNING" || line.stream === "err", "log-rapo": line.level === "INFO" && line.stream === "rapo" };
    },
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
    stepDuration(step) {
      return step.start_time ? this.duration(step) : "";
    },
    async open() {
      this.runs = null;
      this.selected = null;
      this.stepRun = null;
      await this.load();
      this.stop();
      this.timer = setInterval(() => this.load(), 3000);
    },
    stop() {
      clearInterval(this.timer);
    },
    async load() {
      try {
        this.runs = await api("get-workflow-runs", { params: { name: this.name, limit: 200 }, loadingBar: false });
        if (this.selected) {
          const run = this.runs.find((item) => item.run_id === this.selected.run_id);
          // The steps and the log of a run still active change.
          if (run && ACTIVE_RUN_STATUSES.includes(this.selected.status)) {
            await this.select(run, true);
          }
        } else if (this.runs.length) {
          await this.select(this.runs.find((item) => item.run_id === this.runId) || this.runs[0]);
        }
        if (this.stepRun && ACTIVE_RUN_STATUSES.includes(this.stepRun.status)) {
          this.stepRun = await api("get-job-run", { params: { run_id: this.stepRun.run_id }, loadingBar: false });
        }
      } catch (error) {
        this.stop();
        notifyError("Failed to load the runs.", error);
      }
    },
    async select(run, keepStep = false) {
      try {
        this.selected = await api("get-workflow-run", { params: { run_id: run.run_id }, loadingBar: false });
        if (!keepStep) {
          this.stepRun = null;
        }
      } catch (error) {
        notifyError("Failed to load the run.", error);
      }
    },
    async openStep(step) {
      try {
        this.stepRun = await api("get-job-run", { params: { run_id: step.job_run_id }, loadingBar: false });
      } catch (error) {
        notifyError("Failed to load the run of the job.", error);
      }
    },
    async cancel() {
      try {
        await api("cancel-workflow-run", { method: "POST", params: { run_id: this.selected.run_id } });
        this.$emit("changed");
        await this.load();
      } catch (error) {
        notifyError("Failed to cancel the run.", error);
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
.log-time {
  margin-right: 8px;
  color: var(--rapo-muted);
}
.log-error {
  color: var(--rapo-crit);
  font-weight: 600;
}
.log-warning {
  color: var(--rapo-warn);
}
.log-rapo {
  color: var(--rapo-muted);
  font-style: italic;
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
}
.log-message--error {
  color: var(--rapo-crit);
  background: var(--rapo-crit-soft);
}
/* A workflow stopped by an unmet condition or a cancel: a warning, not an alarm. */
.log-message--warning {
  color: var(--rapo-warn);
  background: var(--rapo-surface-alt);
}
.log-message--soft {
  color: var(--rapo-header-text);
  background: var(--rapo-surface-alt);
}
.steps-table {
  max-height: 220px;
  overflow: auto;
}
</style>
