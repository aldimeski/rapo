<template>
  <q-dialog v-model="visible" persistent @show="open">
    <q-card style="width: 760px; max-width: 95vw">
      <q-card-section class="row items-center q-py-sm">
        <div class="text-h6">Schedule {{ row ? row.param_name : "" }}</div>
        <q-space />
        <q-btn aria-label="Close" flat round icon="fas fa-times" @click="close" />
      </q-card-section>
      <q-separator />

      <q-card-section class="q-gutter-y-md">
        <div class="text-grey-8">
          The transfer is started by the rapo scheduler and performed by rapo itself; the <code>file-transfer.py</code> script is not used. The schedule stays with the param when it is moved
          between a file and the database.
        </div>

        <q-banner v-if="row && row.crontab && row.crontab.length" dense class="bg-amber-1 text-amber-10">
          <template v-slot:avatar><q-icon name="fas fa-exclamation-triangle" color="amber-8" size="xs" /></template>
          This param is also started from the crontab:
          <div v-for="entry in row.crontab" :key="entry.cron + entry.command" class="q-ml-md">
            <code>{{ entry.cron }}</code> <span v-if="entry.script">via {{ entry.script }}</span><span v-else>directly</span>
          </div>
          Remove the crontab entry once rapo starts it, or the transfer runs twice.
        </q-banner>

        <q-toggle v-model="enabled" label="Scheduled in rapo" />
        <schedule-edit-box v-if="shown" ref="box" v-model="schedule" :cascade="false" :class="{ 'disabled-box': !enabled }" />
        <div v-if="problem" class="text-deep-orange-9"><q-icon name="fas fa-exclamation-circle" class="q-mr-xs" /> {{ problem }}</div>
      </q-card-section>

      <q-separator />
      <q-card-actions align="right">
        <q-btn v-if="hasSchedule" flat no-caps color="deep-orange" label="Remove schedule" icon="fas fa-trash" :loading="saving" @click="remove" />
        <q-space />
        <q-btn flat no-caps label="Cancel" @click="close" />
        <q-btn color="teal" no-caps label="Save" icon="fas fa-save" :loading="saving" @click="save" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script>
import ScheduleEditBox from "./ScheduleEditBox.vue";
import { api } from "../api";
import { defaultSchedule, parseSchedule, scheduleUnits } from "../utils/schedule";

// Sets the schedule the rapo scheduler starts a transfer by (the units of a control schedule, without a cascade).
export default {
  components: { ScheduleEditBox },
  props: {
    modelValue: { type: Boolean, default: false },
    row: { type: Object, default: null },
  },
  emits: ["update:modelValue", "saved"],
  data() {
    return { schedule: defaultSchedule(), enabled: true, saving: false, problem: null, shown: false };
  },
  computed: {
    visible: {
      get() {
        return this.modelValue;
      },
      set(value) {
        this.$emit("update:modelValue", value);
      },
    },
    hasSchedule() {
      return !!(this.row && this.row.rapo && this.row.rapo.config);
    },
  },
  methods: {
    open() {
      this.problem = null;
      this.shown = false;
      const config = this.row && this.row.rapo && this.row.rapo.config;
      this.enabled = this.row && this.row.rapo ? this.row.rapo.enabled : true;
      // A stored schedule the editor can not read starts over from the default.
      this.schedule = config && scheduleUnits(config) ? parseSchedule(config) : defaultSchedule();
      this.$nextTick(() => (this.shown = true));
    },
    units() {
      const schedule = this.schedule;
      const days = (value) => (value && value.length !== 0 ? String(value) : null);
      const time = (value) => (value != null ? String(value) : null);
      return { mday: days(schedule.mday), wday: days(schedule.wday), hour: time(schedule.hour), min: time(schedule.min), sec: time(schedule.sec) };
    },
    async send(schedule) {
      this.saving = true;
      this.problem = null;
      try {
        await api("save-transfer-schedule", { method: "POST", body: { name: this.row.param_name, schedule, enabled: this.enabled } });
        this.$emit("saved");
        this.visible = false;
      } catch (error) {
        this.problem = error.message;
      } finally {
        this.saving = false;
      }
    },
    save() {
      const problem = this.$refs.box && this.$refs.box.problem();
      if (problem) {
        this.problem = problem;
        return;
      }
      return this.send(this.units());
    },
    remove() {
      return this.send(null);
    },
    close() {
      this.visible = false;
    },
  },
};
</script>

<style scoped>
.disabled-box {
  opacity: 0.5;
}
</style>
