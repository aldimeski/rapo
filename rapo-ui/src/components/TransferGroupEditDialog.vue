<template>
  <q-dialog v-model="visible" persistent @show="open">
    <q-card class="column no-wrap" style="width: 820px; max-width: 95vw; max-height: 92vh">
      <q-card-section class="row items-center q-py-sm">
        <div class="text-h6">{{ isNew ? "New group of transfers" : `Group ${name}` }}</div>
        <q-space />
        <q-btn aria-label="Close" flat round icon="fas fa-times" @click="close" />
      </q-card-section>
      <q-separator />

      <q-card-section class="col scroll q-gutter-y-md" style="min-height: 0">
        <q-banner v-if="!writable" dense class="bg-blue-grey-1 text-blue-grey-9">
          <template v-slot:avatar><q-icon name="fas fa-eye" size="xs" /></template>
          You may look at the group but not change it.
        </q-banner>
        <q-inner-loading :showing="loading"><q-spinner color="teal" size="32px" /></q-inner-loading>

        <q-input v-model="form.name" outlined dense label="Name" maxlength="128" :readonly="!isNew || !writable" :hint="isNew ? 'Letters, digits, dots, hyphens and underscores' : 'The name of a group is not changed'" />
        <q-input v-model="form.description" outlined dense label="Description" maxlength="500" :readonly="!writable" />

        <div>
          <div class="text-subtitle2">Transfers, in the order they are run</div>
          <div class="text-grey-7 text-caption q-mb-xs">
            The group starts them one after another: the next one starts when the one before has ended. A transfer that fails stops the group. A transfer is in a group once, and may be in other groups too.
          </div>
          <q-list v-if="form.members.length" bordered separator dense class="rounded-borders">
            <q-item v-for="(member, index) in form.members" :key="member">
              <q-item-section side class="text-grey-7 step-number">{{ index + 1 }}.</q-item-section>
              <q-item-section>
                <span class="text-weight-bold text-blue-grey-9">{{ member }}</span>
                <span v-if="!known(member)" class="text-deep-orange-9 text-caption"><q-icon name="fas fa-exclamation-circle" class="q-mr-xs" />There is no such transfer</span>
              </q-item-section>
              <q-item-section v-if="writable" side>
                <div class="row no-wrap">
                  <q-btn aria-label="Move up" flat round dense size="sm" icon="fas fa-arrow-up" :disable="index === 0" @click="move(index, -1)" />
                  <q-btn aria-label="Move down" flat round dense size="sm" icon="fas fa-arrow-down" :disable="index === form.members.length - 1" @click="move(index, 1)" />
                  <q-btn aria-label="Take out" flat round dense size="sm" color="deep-orange" icon="fas fa-times" @click="form.members.splice(index, 1)" />
                </div>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else class="text-deep-orange-9"><q-icon name="fas fa-exclamation-circle" class="q-mr-xs" />No transfers yet: add one.</div>
          <div v-if="writable" class="row items-center q-gutter-sm q-mt-xs">
            <q-select
              v-model="toAdd"
              dense
              outlined
              use-input
              input-debounce="0"
              :options="options"
              label="Add a transfer"
              class="col"
              behavior="menu"
              @filter="filterOptions"
              @update:model-value="add" />
          </div>
        </div>

        <div class="row q-col-gutter-md">
          <div class="col-6">
            <q-select v-model="form.overlap" dense outlined emit-value map-options :options="overlaps" label="When it is started while it still runs" :readonly="!writable" />
            <div class="text-grey-7 text-caption q-mt-xs">{{ overlapCaption }}</div>
          </div>
          <div class="col-6">
            <q-input v-model="form.notify_email" dense outlined label="E-mail on failure" maxlength="500" :readonly="!writable" :hint="mailHint" />
          </div>
        </div>

        <q-toggle v-model="form.status" true-value="Y" false-value="N" label="On" :disable="!writable" />

        <div>
          <q-toggle v-model="form.scheduleOn" label="Scheduled in rapo" :disable="!writable" />
          <schedule-edit-box v-if="shown && form.scheduleOn" ref="box" v-model="form.schedule" :cascade="false" :class="{ 'disabled-box': !writable }" />
          <div v-if="!form.scheduleOn" class="text-grey-7 text-caption">Without a schedule the group is run by hand.</div>
        </div>
        <div v-if="problem" class="text-deep-orange-9"><q-icon name="fas fa-exclamation-circle" class="q-mr-xs" /> {{ problem }}</div>
      </q-card-section>

      <q-separator />
      <q-card-actions align="right">
        <q-btn flat no-caps label="Cancel" @click="close" />
        <q-btn v-if="writable" color="teal" no-caps label="Save" icon="fas fa-save" :loading="saving" @click="save" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script>
import ScheduleEditBox from "./ScheduleEditBox.vue";
import { api } from "../api";
import { defaultSchedule, parseSchedule, scheduleUnits } from "../utils/schedule";

const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const OVERLAPS = [
  { label: "Skip: do not start it", value: "skip", caption: "A start while the group runs is refused (by hand) or recorded as canceled (by schedule)." },
  { label: "Queue: wait for the run before", value: "queue", caption: "The new run waits until the run before has ended, then runs." },
  { label: "Allow: run them together", value: "allow", caption: "Both runs go at once; a transfer that both have still waits for its own run to end." },
];

// Creates a group of transfers or changes one: its transfers in the order they are run, its schedule and how it ends.
export default {
  components: { ScheduleEditBox },
  props: {
    modelValue: { type: Boolean, default: false },
    // The group to change (a row of get-transfer-groups), null for a new one.
    group: { type: Object, default: null },
    // The names of the transfers a group can have.
    transfers: { type: Array, default: () => [] },
    emailEnabled: { type: Boolean, default: true },
  },
  emits: ["update:modelValue", "saved"],
  data() {
    return {
      form: this.emptyForm(),
      toAdd: null,
      filtered: [],
      loading: false,
      saving: false,
      problem: null,
      shown: false,
      overlaps: OVERLAPS,
    };
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
    isNew() {
      return !this.group;
    },
    name() {
      return this.group ? this.group.group_name : "";
    },
    writable() {
      return this.$can("datasources", "edit");
    },
    options() {
      return this.filtered;
    },
    overlapCaption() {
      return (OVERLAPS.find((item) => item.value === this.form.overlap) || {}).caption || "";
    },
    mailHint() {
      return "Empty: the default address of rapo.ini ([JOBS] default_email). One mail for the whole group.";
    },
  },
  methods: {
    emptyForm() {
      return { name: "", description: "", notify_email: "", status: "Y", overlap: "skip", members: [], scheduleOn: false, schedule: defaultSchedule() };
    },
    open() {
      this.problem = null;
      this.shown = false;
      this.toAdd = null;
      const group = this.group;
      if (!group) {
        this.form = this.emptyForm();
      } else {
        const config = group.schedule_config;
        this.form = {
          name: group.group_name,
          description: group.description || "",
          notify_email: group.notify_email || "",
          status: group.status,
          overlap: group.overlap,
          members: group.members.map((member) => member.param_name),
          scheduleOn: Boolean(config) && Boolean(scheduleUnits(config)),
          // A stored schedule the editor can not read starts over from the default.
          schedule: config && scheduleUnits(config) ? parseSchedule(config) : defaultSchedule(),
        };
      }
      this.filterOptions("", (update) => update());
      this.$nextTick(() => (this.shown = true));
    },
    known(name) {
      return this.transfers.includes(name);
    },
    available() {
      return this.transfers.filter((name) => !this.form.members.includes(name));
    },
    filterOptions(text, update) {
      update(() => {
        const needle = (text || "").toLowerCase();
        this.filtered = this.available().filter((name) => !needle || name.toLowerCase().includes(needle));
      });
    },
    add(name) {
      if (name && !this.form.members.includes(name)) {
        this.form.members.push(name);
      }
      this.$nextTick(() => (this.toAdd = null));
    },
    move(index, step) {
      const to = index + step;
      if (to < 0 || to >= this.form.members.length) {
        return;
      }
      const members = this.form.members.slice();
      [members[index], members[to]] = [members[to], members[index]];
      this.form.members = members;
    },
    units() {
      const schedule = this.form.schedule;
      const days = (value) => (value && value.length !== 0 ? String(value) : null);
      const time = (value) => (value != null ? String(value) : null);
      return { mday: days(schedule.mday), wday: days(schedule.wday), hour: time(schedule.hour), min: time(schedule.min), sec: time(schedule.sec) };
    },
    validate() {
      if (this.isNew && !NAME_PATTERN.test(this.form.name.trim())) {
        return "A group name has 1-128 letters, digits, dots, hyphens or underscores, and starts with a letter or a digit.";
      }
      if (!this.form.members.length) {
        return "A group has at least one transfer.";
      }
      const unknown = this.form.members.filter((member) => !this.known(member));
      if (unknown.length) {
        return `There is no transfer ${unknown.join(", ")}: take it out.`;
      }
      if (this.form.scheduleOn && this.$refs.box) {
        return this.$refs.box.problem();
      }
      return null;
    },
    async save() {
      this.problem = this.validate();
      if (this.problem) {
        return;
      }
      this.saving = true;
      try {
        await api("save-transfer-group", {
          method: "POST",
          body: {
            name: this.form.name.trim(),
            members: this.form.members,
            description: this.form.description,
            notify_email: this.form.notify_email,
            status: this.form.status,
            overlap: this.form.overlap,
            schedule: this.form.scheduleOn ? this.units() : null,
            create: this.isNew,
          },
        });
        this.$q.notify({ type: "positive", message: `Group ${this.form.name.trim()} was saved.` });
        this.$emit("saved");
        this.visible = false;
      } catch (error) {
        this.problem = error.message;
      } finally {
        this.saving = false;
      }
    },
    close() {
      this.visible = false;
    },
  },
};
</script>

<style scoped>
.step-number {
  min-width: 28px;
}
.disabled-box {
  opacity: 0.5;
}
</style>
