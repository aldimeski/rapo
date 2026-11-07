<template>
  <q-page class="column no-wrap" :style-fn="fillViewportToBottom">
    <h2 class="row items-center text-no-wrap q-gutter-lg q-mb-md">
      <div>Jobs</div>
      <div class="col row items-center justify-end">
        <job-scheduler-bar :status="status" :next-fire="nextFire" :clock-offset="clockOffset" :now="now" @changed="refreshStatus" />
      </div>
    </h2>

    <q-banner v-if="catalogue && !catalogue.email_enabled" dense class="bg-blue-grey-1 text-blue-grey-9 q-mb-sm">
      <template v-slot:avatar><q-icon name="fas fa-envelope" color="blue-grey-6" size="xs" /></template>
      Failure mail is off ([EMAIL] enabled in rapo.ini): a job that fails is marked Error, and nobody is told.
    </q-banner>
    <q-banner v-else-if="catalogue && !catalogue.default_email && withoutAddress" dense class="bg-blue-grey-1 text-blue-grey-9 q-mb-sm">
      <template v-slot:avatar><q-icon name="fas fa-envelope" color="blue-grey-6" size="xs" /></template>
      {{ withoutAddress }} job{{ withoutAddress === 1 ? " has" : "s have" }} no address for the failure mail, and [JOBS] default_email is not set in rapo.ini.
    </q-banner>

    <q-card class="fill-card column no-wrap">
      <q-tabs v-model="tab" dense align="left" class="text-blue-grey-8" active-color="teal" indicator-color="teal" no-caps inline-label>
        <q-tab name="jobs" :label="`Jobs${data ? ' (' + data.jobs.length + ')' : ''}`" icon="fas fa-cog" />
        <q-tab name="workflows" :label="`Workflows${workflows ? ' (' + workflows.length + ')' : ''}`" icon="fas fa-stream" />
        <q-tab v-if="transfersTab" name="transfers" label="Transfers" icon="fas fa-exchange-alt" />
        <q-tab name="external" label="External" icon="fas fa-calendar-alt">
          <q-tooltip>What the crontab or the Windows Task Scheduler of the server starts outside rapo</q-tooltip>
        </q-tab>
      </q-tabs>
      <q-separator />
      <workflows-panel v-if="tab === 'workflows'" :workflows="workflows" :error="workflowError" @reload="load()" />
      <transfers-panel v-else-if="tab === 'transfers'" />
      <external-panel v-else-if="tab === 'external'" />
      <template v-else>
      <div class="row items-center q-pa-sm q-gutter-sm">
        <q-input v-model="search" dense outlined clearable debounce="200" placeholder="Search job or description" class="col-4">
          <template v-slot:prepend><q-icon name="fas fa-search" /></template>
        </q-input>
        <q-chip v-for="chip in chips" :key="chip.key" clickable dense :class="{ 'chip-selected': filter === chip.key }" :aria-pressed="String(filter === chip.key)" @click="filter = chip.key">
          {{ chip.label }} <span class="q-ml-xs">({{ chip.count }})</span>
        </q-chip>
        <q-space />
        <q-btn flat dense no-caps icon="fas fa-history" label="All runs" @click="openRuns(null)">
          <q-tooltip>The runs of every job, latest first</q-tooltip>
        </q-btn>
        <q-btn v-if="$can('jobs', 'edit')" color="teal" no-caps icon="fas fa-plus" label="New job" @click="$router.push({ name: 'edit-job', params: { name: 'new' } })" />
      </div>
      <q-separator />

      <load-error-notice v-if="loadError" :message="`The jobs could not be loaded: ${loadError}`" @retry="load()" />
      <q-markup-table v-else flat dense class="col scroll jobs-table">
        <thead>
          <tr class="bg-blue-grey-2">
            <th class="text-left">Job</th>
            <th class="text-left" title="What the main task of the job is">Type</th>
            <th class="text-left">Schedule</th>
            <th class="text-left">Next start</th>
            <th class="text-left">Last run</th>
            <th></th>
          </tr>
        </thead>
        <tbody v-if="!data">
          <skeleton-rows :rows="5" :columns="['text', 'QChip', 'text', 'text', 'text', null]" />
        </tbody>
        <tbody v-else-if="!rows.length">
          <tr>
            <td colspan="6" class="text-grey-7 text-center">{{ data.jobs.length ? "No job matches" : "No jobs yet" }}</td>
          </tr>
        </tbody>
        <tbody v-else>
          <tr v-for="row in rows" :key="row.job_id" :class="{ 'text-grey-6': row.status === 'N' }">
            <td>
              <router-link :to="{ name: 'edit-job', params: { name: row.job_name } }" class="job-link text-weight-bold" :class="row.status === 'N' ? 'text-grey-7' : 'text-blue-grey-9'">{{ row.job_name }}</router-link>
              <q-chip v-if="row.status === 'N'" dense color="grey-6" text-color="white" class="q-ml-sm">Off</q-chip>
              <div v-if="row.description" class="text-grey-7 text-caption">{{ row.description }}</div>
<div v-if="row.waits_for && row.waits_for.length" class="text-grey-7 text-caption">
                <q-icon name="fas fa-hourglass-half" size="10px" class="q-mr-xs" />Waits for {{ row.waits_for.map((target) => `${target.type} ${target.name}`).join(", ") }}
                <q-tooltip><div v-for="target in row.waits_for" :key="target.type + target.name">{{ targetText(target) }}</div></q-tooltip>
              </div>
              <div v-if="row.external && row.external.length" class="text-deep-orange-9 text-caption">
                <q-icon name="fas fa-calendar-alt" size="10px" class="q-mr-xs" />Also started outside rapo ({{ [...new Set(row.external.map((item) => (item.source === "crontab" ? "crontab" : "Task Scheduler")))].join(", ") }})
                <q-tooltip><div v-for="(item, index) in row.external" :key="index">{{ item.name || item.schedule }}<template v-if="item.name"> &middot; {{ item.schedule }}</template></div></q-tooltip>
              </div>
              <div v-if="row.workflows && row.workflows.length" class="text-grey-7 text-caption">
                <q-icon name="fas fa-stream" size="10px" class="q-mr-xs" />In {{ row.workflows.length === 1 ? "workflow" : "workflows" }} {{ row.workflows.join(", ") }}
              </div>
            </td>
            <td>
              <q-chip dense :color="jobType(row.job_type).color" text-color="white" :icon="jobType(row.job_type).icon">{{ jobType(row.job_type).label }}</q-chip>
            </td>
            <td class="schedule-cell">
              <template v-if="row.schedule_config">
                <q-icon name="fas fa-clock" size="xs" :color="row.status === 'N' ? 'grey-5' : 'teal'" class="q-mr-xs" />{{ describe(row.schedule_config) }}
              </template>
              <span v-else class="text-grey-6">Not scheduled</span>
            </td>
            <td class="text-no-wrap">
              <date-time-text v-if="row.next_fire" :value="row.next_fire" />
              <q-tooltip v-if="row.next_fire && schedulerStopped">The job scheduler is stopped: it does not fire until it is started</q-tooltip>
            </td>
            <td class="text-no-wrap">
              <template v-if="row.last_run">
                <a class="cursor-pointer" v-keyboard:button @click="openRuns(row.job_name)">
                  <q-chip dense clickable>
                    <q-avatar :icon="runStatus(row.last_run.status).icon" :color="runStatus(row.last_run.status).color" text-color="white" />
                    {{ runStatus(row.last_run.status).label }}
                  </q-chip>
                </a>
                <date-time-text :value="row.last_run.end_time || row.last_run.start_time || row.last_run.queued_time" />
                <div class="text-caption text-grey-7">
                  <template v-if="stage(row.last_run.stage)">{{ stage(row.last_run.stage) }}</template>
                  <template v-if="row.last_run.exit_code != null && row.last_run.exit_code !== 0"> &middot; exit {{ row.last_run.exit_code }}</template>
                  <template v-if="row.last_run.status === 'E' && row.last_run.mail_status"> &middot; {{ mailNote(row.last_run.mail_status) }}</template>
                </div>
              </template>
              <span v-else class="text-grey-6">never</span>
              <div v-if="row.unmet" class="text-caption text-amber-9">
                <q-icon name="fas fa-exclamation-triangle" size="10px" class="q-mr-xs" />condition not met at {{ shortTime(row.unmet.last) }}<template v-if="row.unmet.count > 1"> ({{ row.unmet.count }} times since)</template>
              </div>
            </td>
            <td class="text-no-wrap text-right">
              <q-btn v-if="row.running" aria-label="Cancel run" size="sm" color="grey-7" round flat icon="fas fa-times" :disable="!$can('jobs', 'operate')" @click="cancelRun(row)">
                <q-tooltip>Cancel the run</q-tooltip>
              </q-btn>
              <q-btn v-else aria-label="Run now" size="sm" color="teal" round flat icon="fas fa-play" :disable="!$can('jobs', 'operate')" @click="runNow(row)">
                <q-tooltip>Run now</q-tooltip>
              </q-btn>
              <q-btn aria-label="More" size="sm" color="grey-7" round flat icon="fas fa-ellipsis-v">
                <q-menu>
                  <q-list dense class="text-no-wrap">
                    <q-item clickable v-close-popup @click="$router.push({ name: 'edit-job', params: { name: row.job_name } })">
                      <q-item-section avatar><q-icon name="fas fa-edit" size="xs" /></q-item-section>
                      <q-item-section>{{ $can("jobs", "edit") ? "Edit job" : "Open job" }}</q-item-section>
                    </q-item>
                    <q-item clickable v-close-popup :disable="row.running || !$can('jobs', 'operate')" @click="openRunDialog(row)">
                      <q-item-section avatar><q-icon name="fas fa-sliders-h" size="xs" /></q-item-section>
                      <q-item-section>Run with parameters...</q-item-section>
                    </q-item>
                    <q-item clickable v-close-popup @click="openRuns(row.job_name)">
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
      </template>
    </q-card>

    <job-runs-dialog v-model="runsOpen" :name="runsName" :run-id="runsRunId" @changed="load()" />
    <job-run-dialog v-model="runDialogOpen" :name="runDialogName" @started="afterStart" />
  </q-page>
</template>

<script>
import { mapActions, mapState } from "vuex";
import DateTimeText from "./DateTimeText.vue";
import ExternalPanel from "./ExternalPanel.vue";
import JobRunDialog from "./JobRunDialog.vue";
import JobRunsDialog from "./JobRunsDialog.vue";
import JobSchedulerBar from "./JobSchedulerBar.vue";
import LoadErrorNotice from "./LoadErrorNotice.vue";
import SkeletonRows from "./SkeletonRows.vue";
import TransfersPanel from "./TransfersPanel.vue";
import WorkflowsPanel from "./WorkflowsPanel.vue";
import { api, notifyError } from "../api";
import { JOB_STAGES, jobType, runStatus } from "../constants";
import { liveRefetch } from "../socket";
import { fillViewportToBottom } from "../utils/layout";
import { describeSchedule } from "../utils/schedule";
import { escapeHtml, toDateTimeString, toMillis } from "../utils/format";
import { targetText } from "../utils/job";

// A run that waits or runs is shown here within this many seconds, whatever the live updates say.
const REFRESH_SECONDS = 10;

export default {
  components: { DateTimeText, ExternalPanel, JobRunDialog, JobRunsDialog, JobSchedulerBar, LoadErrorNotice, SkeletonRows, TransfersPanel, WorkflowsPanel },
  data() {
    return {
      // The tab is in the address (?tab=workflows), so a link and a reload keep it.
      tab: ["workflows", "transfers", "external"].includes(this.$route.query.tab) ? this.$route.query.tab : "jobs",
      data: null,
      // get-workflows, or null while they load; and why they could not be read (the tables of v0.10.0 may be missing).
      workflows: null,
      workflowError: null,
      catalogue: null,
      loadError: null,
      search: "",
      filter: "all",
      now: Date.now(),
      // When the job scheduler status was read: the server clock then tells the offset from the browser's.
      statusRead: Date.now(),
      runsOpen: false,
      runsName: null,
      runsRunId: null,
      runDialogOpen: false,
      runDialogName: null,
    };
  },
  computed: {
    ...mapState({ status: "jobSchedulerStatus" }),
    // The transfers are a tab when the server has them switched on ([TRANSFER] enabled) and the token may see them.
    transfersTab() {
      const info = this.$store.getters.getEnvInfo;
      return Boolean(info && info.transfers_available && this.$can("datasources", "view"));
    },
    allRows() {
      return this.data ? this.data.jobs : [];
    },
    schedulerStopped() {
      return Boolean(this.status && this.status.disabled);
    },
    // The server's clock minus the browser's, so that "in 4 min" is right on a browser with another time.
    clockOffset() {
      return this.status && this.status.server_time ? toMillis(this.status.server_time) - this.statusRead : 0;
    },
    // The job that fires next of those that are on.
    // The job or workflow that fires next of those that are on.
    nextFire() {
      const jobs = this.allRows.filter((row) => row.status === "Y" && row.next_fire).map((row) => ({ job_name: row.job_name, next_fire: row.next_fire }));
      const workflows = (this.workflows || []).filter((row) => row.status === "Y" && row.next_fire).map((row) => ({ job_name: row.workflow_name, next_fire: row.next_fire }));
      return [...jobs, ...workflows].sort((a, b) => toMillis(a.next_fire) - toMillis(b.next_fire))[0] || null;
    },
    // The jobs that are on and have neither an address of their own nor a default one for the failure mail.
    withoutAddress() {
      return this.allRows.filter((row) => row.status === "Y" && !row.notify_email).length;
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
      return this.allRows.filter((row) => tests[this.filter](row) && (!needle || [row.job_name, row.description, row.job_type].some((text) => text && String(text).toLowerCase().includes(needle))));
    },
  },
  watch: {
    tab(value) {
      this.$router.replace({ name: "jobs", query: value === "jobs" ? {} : { tab: value } });
    },
  },
  methods: {
    // "07:10" for a moment of today, else the date and the time.
    shortTime(value) {
      const text = toDateTimeString(value);
      return text.startsWith(new Date().toLocaleDateString("sv-SE")) ? text.substring(11, 16) : text.substring(0, 16);
    },
    targetText,
    ...mapActions(["updateJobSchedulerStatus"]),
    fillViewportToBottom,
    jobType,
    runStatus,
    describe(config) {
      return describeSchedule(config);
    },
    stage(stage) {
      return JOB_STAGES[stage] || "";
    },
    // The first words of what became of the failure mail.
    mailNote(text) {
      return /^not sent/i.test(text) ? "mail not sent" : "mail sent";
    },
    async load() {
      this.loadWorkflows();
      try {
        this.data = await api("get-jobs", { loadingBar: false });
        this.loadError = null;
      } catch (error) {
        if (this.data) {
          notifyError("Failed to refresh the jobs.", error);
        } else {
          this.loadError = error.message;
        }
      }
    },
    async loadWorkflows() {
      try {
        this.workflows = (await api("get-workflows", { loadingBar: false })).workflows;
        this.workflowError = null;
      } catch (error) {
        // The jobs go on without them: the tab says why.
        this.workflowError = error.message;
        if (!this.workflows) {
          this.workflows = [];
        }
      }
    },
    async refreshStatus() {
      try {
        await this.updateJobSchedulerStatus();
        this.statusRead = Date.now();
        await this.load();
      } catch (error) {
        notifyError("Failed to load the job scheduler status.", error);
      }
    },
    openRuns(name, runId = null) {
      this.runsName = name;
      this.runsRunId = runId;
      this.runsOpen = true;
    },
    openRunDialog(row) {
      this.runDialogName = row.job_name;
      this.runDialogOpen = true;
    },
    afterStart() {
      this.load();
    },
    async runNow(row) {
      try {
        await api("run-job", { method: "POST", params: { name: row.job_name } });
        this.$q.notify({ type: "positive", message: `${row.job_name} queued.` });
        await this.load();
      } catch (error) {
        notifyError(`Failed to run ${row.job_name}.`, error);
      }
    },
    async cancelRun(row) {
      try {
        await api("cancel-job-run", { method: "POST", params: { run_id: row.last_run.run_id } });
        await this.load();
      } catch (error) {
        notifyError(`Failed to cancel ${row.job_name}.`, error);
      }
    },
    async setStatus(row) {
      const enable = row.status !== "Y";
      try {
        await api("set-job-status", { method: "POST", params: { name: row.job_name, enabled: enable ? "true" : "false" } });
        await this.load();
      } catch (error) {
        notifyError(`Failed to switch ${row.job_name} ${enable ? "on" : "off"}.`, error);
      }
    },
    // A job that is a step of workflows is taken out of them, which is said and has to be confirmed: a workflow left
    // without steps is switched off.
    remove(row) {
      const used = row.workflows || [];
      const plain = `Delete ${row.job_name} with its versions? The history of its runs stays until its retention ends.`;
      const named = used.map((name) => `<b>${escapeHtml(name)}</b>`).join(", ");
      this.$q
        .dialog({
          title: used.length ? "Delete a job that is in workflows" : "Delete job",
          message: used.length
            ? `${escapeHtml(row.job_name)} is a step of the workflow${used.length === 1 ? "" : "s"} ${named}. Deleting it takes it out of ${used.length === 1 ? "that workflow" : "those workflows"} ` +
              "(a new version of each), and a workflow left without steps is switched off. " +
              "The history of its runs stays until its retention ends."
            : plain,
          html: used.length > 0,
          cancel: true,
          persistent: true,
          ok: { label: used.length ? "Delete and take it out" : "Delete", color: "deep-orange", flat: true },
        })
        .onOk(async () => {
          try {
            const answer = await api("delete-job", { method: "DELETE", params: { name: row.job_name, confirm: used.length ? "true" : null } });
            const off = (answer.workflows || []).filter((item) => item.switched_off).map((item) => item.workflow_name);
            if (off.length) {
              this.$q.notify({ type: "warning", message: `${off.join(", ")} ${off.length === 1 ? "has" : "have"} no steps left and ${off.length === 1 ? "was" : "were"} switched off.` });
            }
            await this.load();
          } catch (error) {
            notifyError(`Failed to delete ${row.job_name}.`, error);
          }
        });
    },
  },
  mounted() {
    // An address with ?tab=transfers while the transfers are off, or the token may not see them: the jobs.
    if (this.tab === "transfers" && !this.transfersTab) {
      this.tab = "jobs";
    }
    this.load();
    this.refreshStatus();
    api("get-job-catalogue", { loadingBar: false })
      .then((answer) => {
        this.catalogue = answer;
      })
      .catch(() => {
        // The notes about the failure mail are left out.
      });
    // The jobs, their runs and the job scheduler push "jobs:changed"; the timer covers a connection that missed one.
    this.stopLive = liveRefetch("jobs:changed", () => this.refreshStatus(), { interval: 2000 });
    this.timer = setInterval(() => {
      this.now = Date.now();
    }, 1000);
    this.poll = setInterval(() => {
      if (!document.hidden) {
        this.refreshStatus();
      }
    }, REFRESH_SECONDS * 1000);
  },
  unmounted() {
    if (this.stopLive) {
      this.stopLive();
    }
    clearInterval(this.timer);
    clearInterval(this.poll);
  },
};
</script>

<style scoped>
.fill-card {
  flex: 1 1 0;
  min-height: 0;
}
.schedule-cell {
  min-width: 240px;
}
.job-link {
  text-decoration: none;
}
.job-link:hover {
  text-decoration: underline;
}
</style>
