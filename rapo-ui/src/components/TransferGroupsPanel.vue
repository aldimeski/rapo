<template>
  <div class="column no-wrap fill-panel">
    <q-banner v-if="unavailable" dense class="bg-amber-1 text-amber-10 q-ma-sm">
      <template v-slot:avatar><q-icon name="fas fa-exclamation-triangle" color="amber-8" size="xs" /></template>
      The tables of the groups are missing in the database: run <code>migrations/v0.8.7.5/upgrade.sql</code>.
    </q-banner>

    <div class="row items-center q-pa-sm q-gutter-sm">
      <q-input v-model="search" dense outlined clearable debounce="200" placeholder="Search group, transfer or description" class="col-4">
        <template v-slot:prepend><q-icon name="fas fa-search" /></template>
      </q-input>
      <q-space />
      <q-btn flat dense no-caps icon="fas fa-history" label="All runs" @click="openRuns(null)">
        <q-tooltip>The runs of every group, latest first</q-tooltip>
      </q-btn>
      <q-btn v-if="$can('datasources', 'edit')" color="teal" no-caps icon="fas fa-plus" label="New group" :disable="unavailable" @click="openEdit(null)" />
    </div>
    <q-separator />

    <load-error-notice v-if="loadError && !unavailable" :message="`The groups could not be loaded: ${loadError}`" @retry="load()" />
    <q-markup-table v-else flat dense class="col scroll groups-table">
      <thead>
        <tr class="bg-blue-grey-2">
          <th class="text-left">Group</th>
          <th class="text-left" title="In the order they are run. Each shows when it starts by itself and when it is expected to start in the group">Transfers</th>
          <th class="text-left">Schedule</th>
          <th class="text-left">Next start</th>
          <th class="text-left">Last run</th>
          <th></th>
        </tr>
      </thead>
      <tbody v-if="!groups">
        <skeleton-rows :rows="3" :columns="['text', 'text', 'text', 'text', 'text', null]" />
      </tbody>
      <tbody v-else-if="!rows.length">
        <tr>
          <td colspan="6" class="text-grey-7 text-center">{{ groups.length ? "No group matches" : "No groups yet" }}</td>
        </tr>
      </tbody>
      <tbody v-else>
        <tr v-for="row in rows" :key="row.group_id" :class="{ 'text-grey-6': row.status === 'N' }">
          <td>
            <a class="text-weight-bold cursor-pointer" :class="row.status === 'N' ? 'text-grey-7' : 'text-blue-grey-9'" v-keyboard:button @click="openEdit(row)">{{ row.group_name }}</a>
            <q-chip v-if="row.status === 'N'" dense color="grey-6" text-color="white" class="q-ml-sm">Off</q-chip>
            <div v-if="row.description" class="text-grey-7 text-caption">{{ row.description }}</div>
          </td>
          <td class="members-cell">
            <div v-for="member in row.members" :key="member.param_name" class="member-line">
              <span class="text-grey-7 member-number">{{ member.position }}.</span>
              <q-icon v-if="member.running" name="fas fa-sync" class="fa-spin q-mr-xs" size="10px" color="blue-grey-5" />
              <span class="text-weight-medium text-blue-grey-9">{{ member.param_name }}</span>
              <span class="text-caption text-grey-7">
                <template v-if="member.own_next_fire"> &middot; by itself <date-time-text :value="member.own_next_fire" /></template>
                <template v-if="member.group_start && row.status === 'Y'"> &middot; in the group {{ member.position === 1 ? "" : "about " }}<date-time-text :value="member.group_start" /></template>
              </span>
            </div>
          </td>
          <td class="schedule-cell">
            <template v-if="row.scheduled">
              <q-icon name="fas fa-clock" size="xs" :color="row.status === 'N' ? 'grey-5' : 'teal'" class="q-mr-xs" />{{ describe(row.schedule_config) }}
            </template>
            <span v-else class="text-grey-6">Not scheduled</span>
            <div class="text-grey-7 text-caption">{{ overlapText(row.overlap) }}</div>
          </td>
          <td class="text-no-wrap">
            <date-time-text v-if="row.next_fire && row.status === 'Y'" :value="row.next_fire" />
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
              <div v-if="row.last_run.steps_total" class="text-caption text-grey-7">step {{ row.last_run.step_no || 0 }} of {{ row.last_run.steps_total }}</div>
            </template>
            <span v-else class="text-grey-6">never</span>
          </td>
          <td class="text-no-wrap text-right">
            <q-btn v-if="row.running && row.last_run" aria-label="Cancel run" size="sm" color="grey-7" round flat icon="fas fa-times" :disable="!$can('files', 'operate')" @click="cancelRun(row)">
              <q-tooltip>Cancel the run: the transfer that goes is canceled and the next ones do not start</q-tooltip>
            </q-btn>
            <q-btn v-else aria-label="Run now" size="sm" color="teal" round flat icon="fas fa-play" :disable="!$can('files', 'operate') || !row.members.length" @click="runNow(row)">
              <q-tooltip>Run the group now</q-tooltip>
            </q-btn>
            <q-btn aria-label="More" size="sm" color="grey-7" round flat icon="fas fa-ellipsis-v">
              <q-menu>
                <q-list dense class="text-no-wrap">
                  <q-item clickable v-close-popup @click="openEdit(row)">
                    <q-item-section avatar><q-icon name="fas fa-edit" size="xs" /></q-item-section>
                    <q-item-section>{{ $can("datasources", "edit") ? "Edit group" : "Open group" }}</q-item-section>
                  </q-item>
                  <q-item clickable v-close-popup @click="openRuns(row)">
                    <q-item-section avatar><q-icon name="fas fa-history" size="xs" /></q-item-section>
                    <q-item-section>Run history</q-item-section>
                  </q-item>
                  <template v-if="$can('datasources', 'edit')">
                    <q-item clickable v-close-popup @click="setStatus(row)">
                      <q-item-section avatar><q-icon name="fas fa-power-off" size="xs" /></q-item-section>
                      <q-item-section>{{ row.status === "Y" ? "Switch off" : "Switch on" }}</q-item-section>
                    </q-item>
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

    <transfer-group-edit-dialog v-model="editOpen" :group="editGroup" :transfers="transferNames" @saved="saved" />
    <transfer-group-runs-dialog v-model="runsOpen" :name="runsName" @changed="load()" />
  </div>
</template>

<script>
import DateTimeText from "./DateTimeText.vue";
import LoadErrorNotice from "./LoadErrorNotice.vue";
import SkeletonRows from "./SkeletonRows.vue";
import TransferGroupEditDialog from "./TransferGroupEditDialog.vue";
import TransferGroupRunsDialog from "./TransferGroupRunsDialog.vue";
import { api, notifyError } from "../api";
import { runStatus } from "../constants";
import { describeSchedule } from "../utils/schedule";
import { escapeHtml } from "../utils/format";

const REFRESH_SECONDS = 5;
const OVERLAP_TEXT = {
  skip: "A start while it runs is skipped",
  queue: "A start while it runs waits",
  allow: "Starts may overlap",
};

// The groups of transfers: a group runs its transfers one after another, in its order, by a schedule of its own.
export default {
  components: { DateTimeText, LoadErrorNotice, SkeletonRows, TransferGroupEditDialog, TransferGroupRunsDialog },
  props: {
    // The names of the transfers a group can have.
    transferNames: { type: Array, default: () => [] },
  },
  emits: ["changed"],
  data() {
    return { groups: null, loadError: null, unavailable: false, search: "", editOpen: false, editGroup: null, runsOpen: false, runsName: null };
  },
  computed: {
    rows() {
      const needle = (this.search || "").toLowerCase();
      return (this.groups || []).filter(
        (row) => !needle || [row.group_name, row.description, ...row.members.map((member) => member.param_name)].some((text) => text && String(text).toLowerCase().includes(needle))
      );
    },
  },
  methods: {
    runStatus,
    describe(config) {
      return describeSchedule(config);
    },
    overlapText(value) {
      return OVERLAP_TEXT[value] || value;
    },
    async load() {
      try {
        const answer = await api("get-transfer-groups", { loadingBar: false });
        this.groups = answer.groups;
        this.loadError = null;
        this.unavailable = false;
      } catch (error) {
        // 503: the tables are not there (migrations/v0.8.7.5/upgrade.sql has not been run).
        this.unavailable = error.status === 503;
        if (this.unavailable) {
          this.groups = [];
        } else if (this.groups) {
          notifyError("Failed to refresh the groups.", error);
        } else {
          this.loadError = error.message;
        }
      }
    },
    saved() {
      this.load();
      this.$emit("changed");
    },
    openEdit(row) {
      this.editGroup = row;
      this.editOpen = true;
    },
    openRuns(row) {
      this.runsName = row ? row.group_name : null;
      this.runsOpen = true;
    },
    async runNow(row) {
      try {
        await api("run-transfer-group", { method: "POST", params: { name: row.group_name } });
        this.$q.notify({ type: "positive", message: `Group ${row.group_name} started.` });
        await this.load();
        this.$emit("changed");
      } catch (error) {
        notifyError(`Failed to run ${row.group_name}.`, error);
      }
    },
    async cancelRun(row) {
      try {
        await api("cancel-transfer-group-run", { method: "POST", params: { run_id: row.last_run.run_id } });
        await this.load();
        this.$emit("changed");
      } catch (error) {
        notifyError(`Failed to cancel ${row.group_name}.`, error);
      }
    },
    async setStatus(row) {
      try {
        await api("set-transfer-group-status", { method: "POST", params: { name: row.group_name, enabled: row.status === "Y" ? "false" : "true" } });
        await this.load();
        this.$emit("changed");
      } catch (error) {
        notifyError(`Failed to switch ${row.group_name}.`, error);
      }
    },
    remove(row) {
      this.$q
        .dialog({
          title: "Delete group",
          message: `Delete the group <b>${escapeHtml(row.group_name)}</b>? Its transfers stay as they are. The history of its runs stays until its retention ends.`,
          html: true,
          cancel: true,
          persistent: true,
          ok: { label: "Delete", color: "deep-orange", flat: true },
        })
        .onOk(async () => {
          try {
            await api("delete-transfer-group", { method: "DELETE", params: { name: row.group_name } });
            await this.load();
            this.$emit("changed");
          } catch (error) {
            notifyError(`Failed to delete ${row.group_name}.`, error);
          }
        });
    },
  },
  mounted() {
    this.load();
    // The runs end and the schedules move on without the page being touched.
    this.timer = setInterval(() => {
      if (!document.hidden) {
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
.fill-panel {
  flex: 1 1 0;
  min-height: 0;
}
.schedule-cell {
  min-width: 240px;
}
.members-cell {
  min-width: 360px;
}
.member-line {
  white-space: nowrap;
}
.member-number {
  display: inline-block;
  min-width: 22px;
}
</style>
