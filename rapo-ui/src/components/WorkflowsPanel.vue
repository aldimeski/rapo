<template>
  <div class="column no-wrap fill-panel">
    <div class="row items-center q-pa-sm q-gutter-sm">
      <q-input v-model="search" dense outlined clearable debounce="200" placeholder="Search workflow, description or job" class="col-4">
        <template v-slot:prepend><q-icon name="fas fa-search" /></template>
      </q-input>
      <q-chip v-for="chip in chips" :key="chip.key" clickable dense :class="{ 'chip-selected': filter === chip.key }" :aria-pressed="String(filter === chip.key)" @click="filter = chip.key">
        {{ chip.label }} <span class="q-ml-xs">({{ chip.count }})</span>
      </q-chip>
      <q-space />
      <q-btn flat dense no-caps icon="fas fa-history" label="All runs" @click="openRuns(null)">
        <q-tooltip>The runs of every workflow, latest first</q-tooltip>
      </q-btn>
      <q-btn v-if="$can('jobs', 'edit')" color="teal" no-caps icon="fas fa-plus" label="New workflow" @click="$router.push({ name: 'edit-workflow', params: { name: 'new' } })" />
    </div>
    <q-separator />

    <load-error-notice v-if="error" :message="`The workflows could not be loaded: ${error}`" @retry="$emit('reload')" />
    <q-markup-table v-else flat dense class="col scroll jobs-table">
      <thead>
        <tr class="bg-blue-grey-2">
          <th class="text-left">Workflow</th>
          <th class="text-left" title="The jobs it runs, one after another">Steps</th>
          <th class="text-left">Schedule</th>
          <th class="text-left">Next start</th>
          <th class="text-left">Last run</th>
          <th></th>
        </tr>
      </thead>
      <tbody v-if="!workflows">
        <skeleton-rows :rows="4" :columns="['text', 'text', 'text', 'text', 'text', null]" />
      </tbody>
      <tbody v-else-if="!rows.length">
        <tr>
          <td colspan="6" class="text-grey-7 text-center">{{ workflows.length ? "No workflow matches" : "No workflows yet" }}</td>
        </tr>
      </tbody>
      <tbody v-else>
        <tr v-for="row in rows" :key="row.workflow_id" :class="{ 'text-grey-6': row.status === 'N' }">
          <td>
            <router-link :to="{ name: 'edit-workflow', params: { name: row.workflow_name } }" class="job-link text-weight-bold" :class="row.status === 'N' ? 'text-grey-7' : 'text-blue-grey-9'">{{ row.workflow_name }}</router-link>
            <q-chip v-if="row.status === 'N'" dense color="grey-6" text-color="white" class="q-ml-sm">Off</q-chip>
            <div v-if="row.description" class="text-grey-7 text-caption">{{ row.description }}</div>
          </td>
          <td class="steps-cell">
            <span v-if="!row.steps.length" class="text-deep-orange-9"><q-icon name="fas fa-exclamation-circle" class="q-mr-xs" />No steps: add a job</span>
            <template v-else>
              <span v-for="(job, index) in row.steps" :key="job" class="text-no-wrap">
                <q-icon v-if="index" name="fas fa-chevron-right" size="9px" color="blue-grey-4" class="q-mx-xs" /><span class="step-name">{{ job }}</span>
              </span>
            </template>
            <div v-if="row.waits_for && row.waits_for.length" class="text-grey-7 text-caption">
              <q-icon name="fas fa-hourglass-half" size="10px" class="q-mr-xs" />Waits for {{ row.waits_for.map((target) => `${target.type} ${target.name}`).join(", ") }}
              <q-tooltip><div v-for="target in row.waits_for" :key="target.type + target.name">{{ targetText(target) }}</div></q-tooltip>
            </div>
          </td>
          <td class="schedule-cell">
            <template v-if="row.schedule_config">
              <q-icon name="fas fa-clock" size="xs" :color="row.status === 'N' ? 'grey-5' : 'teal'" class="q-mr-xs" />{{ describe(row.schedule_config) }}
            </template>
            <span v-else class="text-grey-6">Not scheduled</span>
          </td>
          <td class="text-no-wrap"><date-time-text v-if="row.next_fire" :value="row.next_fire" /></td>
          <td class="text-no-wrap">
            <template v-if="row.last_run">
              <a class="cursor-pointer" v-keyboard:button @click="openRuns(row.workflow_name)">
                <q-chip dense clickable>
                  <q-avatar :icon="workflowRunStatus(row.last_run).icon" :color="workflowRunStatus(row.last_run).color" text-color="white" />
                  {{ workflowRunStatus(row.last_run).label }}
                </q-chip>
              </a>
              <date-time-text :value="row.last_run.end_time || row.last_run.start_time || row.last_run.queued_time" />
              <div class="text-caption text-grey-7">
                <template v-if="row.last_run.status !== 'D' && row.last_run.step_no">at step {{ row.last_run.step_no }} of {{ row.last_run.steps_total }}</template>
                <template v-else-if="row.last_run.status === 'D'">{{ row.last_run.steps_total }} step{{ row.last_run.steps_total === 1 ? "" : "s" }}</template>
                <template v-if="row.last_run.status === 'E' && row.last_run.mail_status"> &middot; {{ /^not sent/i.test(row.last_run.mail_status) ? "mail not sent" : "mail sent" }}</template>
              </div>
            </template>
            <span v-else class="text-grey-6">never</span>
            <div v-if="row.unmet" class="text-caption text-amber-9">
              <q-icon name="fas fa-exclamation-triangle" size="10px" class="q-mr-xs" />condition not met at {{ shortTime(row.unmet.last) }}<template v-if="row.unmet.count > 1"> ({{ row.unmet.count }} times since)</template>
            </div>
          </td>
          <td class="text-no-wrap text-right">
            <q-btn v-if="row.running" aria-label="Cancel run" size="sm" color="grey-7" round flat icon="fas fa-times" :disable="!$can('jobs', 'operate')" @click="cancelRun(row)">
              <q-tooltip>Cancel the run: the step that runs is stopped and the rest skipped</q-tooltip>
            </q-btn>
            <q-btn v-else aria-label="Run now" size="sm" color="teal" round flat icon="fas fa-play" :disable="!$can('jobs', 'operate') || !row.steps.length" @click="runNow(row)">
              <q-tooltip>{{ row.steps.length ? "Run now" : "A workflow without steps can not run" }}</q-tooltip>
            </q-btn>
            <q-btn aria-label="More" size="sm" color="grey-7" round flat icon="fas fa-ellipsis-v">
              <q-menu>
                <q-list dense class="text-no-wrap">
                  <q-item clickable v-close-popup @click="$router.push({ name: 'edit-workflow', params: { name: row.workflow_name } })">
                    <q-item-section avatar><q-icon name="fas fa-edit" size="xs" /></q-item-section>
                    <q-item-section>{{ $can("jobs", "edit") ? "Edit workflow" : "Open workflow" }}</q-item-section>
                  </q-item>
                  <q-item clickable v-close-popup :disable="row.running || !row.steps.length || !$can('jobs', 'operate')" @click="openRunDialog(row)">
                    <q-item-section avatar><q-icon name="fas fa-sliders-h" size="xs" /></q-item-section>
                    <q-item-section>Run with parameters...</q-item-section>
                  </q-item>
                  <q-item clickable v-close-popup @click="openRuns(row.workflow_name)">
                    <q-item-section avatar><q-icon name="fas fa-history" size="xs" /></q-item-section>
                    <q-item-section>Run history</q-item-section>
                  </q-item>
                  <q-item clickable v-close-popup :disable="!$can('jobs', 'operate')" @click="setStatus(row)">
                    <q-item-section avatar><q-icon :name="row.status === 'Y' ? 'fas fa-pause' : 'fas fa-play'" size="xs" /></q-item-section>
                    <q-item-section>{{ row.status === "Y" ? "Switch off" : "Switch on" }}</q-item-section>
                  </q-item>
                  <template v-if="$can('jobs', 'edit')">
                    <q-separator />
                    <q-item clickable v-close-popup class="text-deep-orange" :disable="row.running" @click="remove(row)">
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

    <workflow-runs-dialog v-model="runsOpen" :name="runsName" @changed="$emit('reload')" />
    <job-run-dialog v-model="runDialogOpen" kind="workflow" :name="runDialogName" @started="$emit('reload')" />
  </div>
</template>

<script>
import DateTimeText from "./DateTimeText.vue";
import JobRunDialog from "./JobRunDialog.vue";
import LoadErrorNotice from "./LoadErrorNotice.vue";
import SkeletonRows from "./SkeletonRows.vue";
import WorkflowRunsDialog from "./WorkflowRunsDialog.vue";
import { api, notifyError } from "../api";
import { workflowRunStatus } from "../constants";
import { toDateTimeString } from "../utils/format";
import { targetText } from "../utils/job";
import { describeSchedule } from "../utils/schedule";

// The list of the workflows, the second tab of the Jobs page. The page reads them (get-workflows) and hands them over;
// what is done to one is done here, and the page is told to read them again.
export default {
  components: { DateTimeText, JobRunDialog, LoadErrorNotice, SkeletonRows, WorkflowRunsDialog },
  props: {
    // The workflows (get-workflows), or null while they load.
    workflows: { type: Array, default: null },
    error: { type: String, default: null },
  },
  emits: ["reload"],
  data() {
    return { search: "", filter: "all", runsOpen: false, runsName: null, runDialogOpen: false, runDialogName: null };
  },
  computed: {
    allRows() {
      return this.workflows || [];
    },
    chips() {
      const count = (test) => this.allRows.filter(test).length;
      return [
        { key: "all", label: "All", count: this.allRows.length },
        { key: "on", label: "On", count: count((row) => row.status === "Y") },
        { key: "off", label: "Off", count: count((row) => row.status === "N") },
        { key: "scheduled", label: "Scheduled", count: count((row) => row.status === "Y" && row.schedule_config) },
        { key: "running", label: "Running", count: count((row) => row.running) },
        { key: "failed", label: "Failed", count: count((row) => row.last_run && row.last_run.status === "E") },
      ];
    },
    rows() {
      const needle = (this.search || "").toLowerCase();
      const tests = {
        all: () => true,
        on: (row) => row.status === "Y",
        off: (row) => row.status === "N",
        scheduled: (row) => row.status === "Y" && row.schedule_config,
        running: (row) => row.running,
        failed: (row) => row.last_run && row.last_run.status === "E",
      };
      return this.allRows.filter((row) => tests[this.filter](row) && (!needle || [row.workflow_name, row.description, ...row.steps].some((text) => text && String(text).toLowerCase().includes(needle))));
    },
  },
  methods: {
    // "07:10" for a moment of today, else the date and the time.
    shortTime(value) {
      const text = toDateTimeString(value);
      return text.startsWith(new Date().toLocaleDateString("sv-SE")) ? text.substring(11, 16) : text.substring(0, 16);
    },
    targetText,
    workflowRunStatus,
    describe(config) {
      return describeSchedule(config);
    },
    openRuns(name) {
      this.runsName = name;
      this.runsOpen = true;
    },
    openRunDialog(row) {
      this.runDialogName = row.workflow_name;
      this.runDialogOpen = true;
    },
    async runNow(row) {
      try {
        await api("run-workflow", { method: "POST", params: { name: row.workflow_name } });
        this.$q.notify({ type: "positive", message: `${row.workflow_name} started.` });
        this.$emit("reload");
      } catch (error) {
        notifyError(`Failed to run ${row.workflow_name}.`, error);
      }
    },
    async cancelRun(row) {
      try {
        await api("cancel-workflow-run", { method: "POST", params: { run_id: row.last_run.run_id } });
        this.$emit("reload");
      } catch (error) {
        notifyError(`Failed to cancel ${row.workflow_name}.`, error);
      }
    },
    async setStatus(row) {
      const enable = row.status !== "Y";
      try {
        await api("set-workflow-status", { method: "POST", params: { name: row.workflow_name, enabled: enable ? "true" : "false" } });
        this.$emit("reload");
      } catch (error) {
        notifyError(`Failed to switch ${row.workflow_name} ${enable ? "on" : "off"}.`, error);
      }
    },
    remove(row) {
      this.$q
        .dialog({
          title: "Delete workflow",
          message: `Delete ${row.workflow_name} with its versions? The jobs it runs are not touched. The history of its runs stays until its retention ends.`,
          cancel: true,
          persistent: true,
          ok: { label: "Delete", color: "deep-orange", flat: true },
        })
        .onOk(async () => {
          try {
            await api("delete-workflow", { method: "DELETE", params: { name: row.workflow_name } });
            this.$emit("reload");
          } catch (error) {
            notifyError(`Failed to delete ${row.workflow_name}.`, error);
          }
        });
    },
  },
};
</script>

<style scoped>
.fill-panel {
  flex: 1 1 0;
  min-height: 0;
}
.steps-cell {
  min-width: 260px;
}
.step-name {
  font-family: var(--rapo-font-mono);
  font-size: 12px;
}
.schedule-cell {
  min-width: 200px;
}
.job-link {
  text-decoration: none;
}
.job-link:hover {
  text-decoration: underline;
}
</style>
