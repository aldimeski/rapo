<template>
  <div class="column no-wrap fill-panel">
    <q-banner v-if="data && data.error" dense class="bg-amber-1 text-amber-10 q-ma-sm">
      <template v-slot:avatar><q-icon name="fas fa-exclamation-triangle" color="amber-8" size="xs" /></template>
      {{ data.error }}
    </q-banner>
    <div v-else-if="data && !data.platform" class="text-grey-7 text-caption q-ma-sm">
      <q-icon name="fas fa-info-circle" class="q-mr-xs" />This server has neither a crontab (Linux) nor a Windows Task Scheduler to read.
    </div>
    <div class="text-grey-7 text-caption q-px-sm q-pt-sm">
      <q-icon name="fas fa-eye" class="q-mr-xs" />What is started outside rapo by {{ platformText }}, of the user the server runs as. Rapo only reads it and never changes it.
      <template v-if="data && data.scanned">Read at <date-time-text :value="data.scanned" />.</template>
    </div>

    <div class="row items-center q-pa-sm q-gutter-sm">
      <q-input v-model="search" dense outlined clearable debounce="200" placeholder="Search name, schedule, command or job" class="col-4">
        <template v-slot:prepend><q-icon name="fas fa-search" /></template>
      </q-input>
      <q-chip v-for="chip in chips" :key="chip.key" clickable dense :class="{ 'chip-selected': filter === chip.key }" :aria-pressed="String(filter === chip.key)" @click="filter = chip.key">
        {{ chip.label }} <span class="q-ml-xs">({{ chip.count }})</span>
      </q-chip>
      <q-space />
      <q-btn flat dense no-caps icon="fas fa-sync" label="Rescan" :loading="scanning" @click="load(true)">
        <q-tooltip>Read the {{ platformText }} again</q-tooltip>
      </q-btn>
    </div>
    <q-separator />

    <load-error-notice v-if="loadError" :message="`What is started outside rapo could not be loaded: ${loadError}`" @retry="load(true)" />
    <q-markup-table v-else flat dense class="col scroll external-table">
      <thead>
        <tr class="bg-blue-grey-2">
          <th class="text-left" style="width: 150px">Started by</th>
          <th class="text-left" style="width: 20%" title="The name of the task of the Task Scheduler; a crontab entry has none">Name</th>
          <th class="text-left" style="width: 17%">Schedule</th>
          <th class="text-left" style="width: 170px">Next start</th>
          <th class="text-left" style="width: 26%">Command</th>
          <th class="text-left" style="width: 11%" title="The jobs of rapo whose main task this starts">Job of rapo</th>
          <th class="text-left" style="width: 170px" title="What the Task Scheduler says of the last run (the crontab keeps none)">Last run</th>
        </tr>
      </thead>
      <tbody v-if="!data">
        <skeleton-rows :rows="5" :columns="['QChip', 'text', 'text', 'text', 'text', 'text', 'text']" />
      </tbody>
      <tbody v-else-if="!rows.length">
        <tr>
          <td colspan="7" class="text-grey-7 text-center">{{ data.tasks.length ? "Nothing matches" : data.error ? "Nothing could be read" : "Nothing is scheduled outside rapo" }}</td>
        </tr>
      </tbody>
      <tbody v-else>
        <tr v-for="(row, index) in rows" :key="index">
          <td>
            <q-chip dense :color="row.source === 'crontab' ? 'blue-grey-7' : 'indigo-5'" text-color="white" :icon="row.source === 'crontab' ? 'fas fa-terminal' : 'fas fa-calendar-alt'">
              {{ row.source === "crontab" ? "Crontab" : "Task Scheduler" }}
            </q-chip>
            <div v-if="row.state && row.state !== 'Ready'" class="text-caption" :class="row.state === 'Disabled' ? 'text-grey-7' : 'text-teal-8'">{{ row.state }}</div>
          </td>
          <td class="name-cell">{{ row.name || "" }}<span v-if="!row.name" class="text-grey-6">&mdash;</span></td>
          <td class="schedule-cell">
            <code v-if="row.source === 'crontab'">{{ row.schedule }}</code>
            <template v-else>{{ row.schedule }}</template>
          </td>
          <td class="text-no-wrap"><date-time-text v-if="row.next_fire" :value="row.next_fire" /><span v-else class="text-grey-6">&mdash;</span></td>
          <td class="command-cell">
            <code v-if="row.command">{{ row.command }}</code>
            <span v-else class="text-grey-6">no program (a handler of the system)</span>
            <div v-if="row.directory" class="text-caption text-grey-7">in {{ row.directory }}</div>
            <div v-if="row.user" class="text-caption text-grey-7">as {{ row.user }}</div>
          </td>
          <td>
            <router-link v-for="name in row.jobs" :key="name" :to="{ name: 'edit-job', params: { name } }" class="job-link text-weight-bold text-blue-grey-9 q-mr-sm">
              <q-icon name="fas fa-link" size="10px" class="q-mr-xs" />{{ name }}
            </router-link>
            <span v-if="!row.jobs.length" class="text-grey-6">&mdash;</span>
          </td>
          <td class="text-no-wrap">
            <date-time-text v-if="row.last_run" :value="row.last_run" />
            <div v-if="row.last_run && row.last_result != null" class="text-caption" :class="row.last_result === 0 ? 'text-grey-7' : 'text-deep-orange-9'">result {{ row.last_result }}</div>
            <span v-if="!row.last_run" class="text-grey-6">&mdash;</span>
          </td>
        </tr>
      </tbody>
    </q-markup-table>
  </div>
</template>

<script>
import DateTimeText from "./DateTimeText.vue";
import LoadErrorNotice from "./LoadErrorNotice.vue";
import SkeletonRows from "./SkeletonRows.vue";
import { api, notifyError } from "../api";

// The scan is read the first time the tab opens after the page was loaded, and then only when asked (Rescan).
let readThisPage = false;

// What the crontab (Linux) or the Task Scheduler (Windows) of the server starts outside rapo, read only; the entries
// that start what the main task of a job runs are linked to the job.
export default {
  components: { DateTimeText, LoadErrorNotice, SkeletonRows },
  data() {
    return { data: null, loadError: null, scanning: false, search: "", filter: "all" };
  },
  computed: {
    allRows() {
      return this.data ? this.data.tasks : [];
    },
    platformText() {
      return { crontab: "the crontab", task_scheduler: "the Windows Task Scheduler" }[this.data && this.data.platform] || "the crontab or the Windows Task Scheduler";
    },
    chips() {
      const count = (test) => this.allRows.filter(test).length;
      return [
        { key: "all", label: "All", count: this.allRows.length },
        { key: "crontab", label: "Crontab", count: count((row) => row.source === "crontab") },
        { key: "task_scheduler", label: "Task Scheduler", count: count((row) => row.source === "task_scheduler") },
        { key: "jobs", label: "Starts a job", count: count((row) => row.jobs.length) },
      ];
    },
    rows() {
      const needle = (this.search || "").toLowerCase();
      const tests = {
        all: () => true,
        crontab: (row) => row.source === "crontab",
        task_scheduler: (row) => row.source === "task_scheduler",
        jobs: (row) => row.jobs.length,
      };
      return this.allRows.filter((row) => tests[this.filter](row) && (!needle || [row.name, row.schedule, row.command, ...row.jobs].some((text) => text && String(text).toLowerCase().includes(needle))));
    },
  },
  methods: {
    async load(rescan = false) {
      this.scanning = rescan;
      try {
        this.data = await api("get-external-tasks", { params: { rescan: rescan ? "true" : null }, loadingBar: rescan });
        this.loadError = null;
        readThisPage = true;
      } catch (error) {
        if (this.data) {
          notifyError("Failed to read what is started outside rapo.", error);
        } else {
          this.loadError = error.message;
        }
      } finally {
        this.scanning = false;
      }
    },
  },
  mounted() {
    this.load(!readThisPage);
  },
};
</script>

<style scoped>
.fill-panel {
  flex: 1 1 0;
  min-height: 0;
}
/* Long names and commands wrap inside their column instead of running over the next one. */
.external-table :deep(table) {
  table-layout: fixed;
  width: 100%;
}
.external-table :deep(td) {
  white-space: normal;
  overflow-wrap: anywhere;
  vertical-align: top;
}
.external-table :deep(td.text-no-wrap) {
  white-space: nowrap;
}
.command-cell {
  white-space: pre-wrap;
}
code {
  font-family: var(--rapo-font-mono);
  font-size: 12px;
}
</style>
