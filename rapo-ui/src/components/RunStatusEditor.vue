<template>
  <div class="q-gutter-y-sm">
    <div v-for="(target, index) in targets" :key="index" class="target-row">
      <div class="row q-col-gutter-sm items-start">
        <div class="col-12 col-sm-3">
          <q-select v-model="target.type" outlined dense emit-value map-options :options="types" label="Wait for" @update:model-value="typeChanged(target)">
            <template v-slot:prepend><q-icon :name="typeIcon(target.type)" size="xs" /></template>
          </q-select>
        </div>
        <div class="col-12 col-sm-5">
          <q-select
            v-model="target.name"
            outlined
            dense
            use-input
            hide-selected
            fill-input
            input-debounce="0"
            :options="shown[index]"
            :label="`Name of the ${target.type}`"
            :error="missing(target)"
            :error-message="`There is no ${target.type} with this name`"
            @filter="(text, update) => filter(target, index, text, update)"
            @input-value="(text) => typed(target, text)">
            <template v-slot:no-option><q-item><q-item-section class="text-grey">Nothing matches</q-item-section></q-item></template>
          </q-select>
        </div>
        <div class="col-12 col-sm-4">
          <q-select v-model="target.window" outlined dense emit-value map-options :options="windowOptions(target)" label="It must have" />
        </div>
        <div v-if="target.window === 'hours'" class="col-6 col-sm-3">
          <q-input v-model.number="target.hours" outlined dense type="number" min="1" max="720" label="Hours" />
        </div>
        <div v-if="target.window === 'data_date'" class="col-6 col-sm-3">
          <q-input v-model.number="target.days_ago" outlined dense type="number" min="0" max="31" label="Days back" hint="0: today's data, 1: yesterday's" />
        </div>
      </div>
      <q-btn v-if="targets.length > 1" flat round dense size="sm" icon="fas fa-times" color="grey-7" class="remove" @click="remove(index)">
        <q-tooltip>Remove</q-tooltip>
      </q-btn>
    </div>
    <q-btn flat dense no-caps color="primary" icon="fas fa-plus" label="Add another to wait for" @click="add" />
    <div class="text-caption text-grey-7">
      The condition is met when <b>all</b> of these are: the last run that ended counts, it must be <b>Done</b> and fall in the window, and none may be running now.
      The control or transfer is read even when it is off; one that does not exist makes the check an error.
    </div>
  </div>
</template>

<script>
import { api } from "../api";
import { TARGET_TYPES, TARGET_WINDOWS, emptyTarget } from "../utils/job";

// The targets of a condition of kind RUN_STATUS, edited in place (the form of utils/job.js): a control, a transfer, a job
// or a workflow, and the window its last run has to fall in. The names to pick from are read from the server by type.
export default {
  props: {
    // The targets of the condition form, edited in place.
    modelValue: { type: Array, required: true },
    // Whether the condition belongs to a "job" or a "workflow", and its name: they can not wait for themselves.
    ownerType: { type: String, default: "job" },
    ownerName: { type: String, default: "" },
  },
  data() {
    return { names: {}, shown: [], types: TARGET_TYPES };
  },
  computed: {
    targets() {
      return this.modelValue;
    },
  },
  created() {
    this.targets.forEach((target) => this.load(target.type));
  },
  methods: {
    typeIcon(type) {
      return (TARGET_TYPES.find((item) => item.value === type) || {}).icon;
    },
    // A window only a control has is not offered for the others.
    windowOptions(target) {
      return TARGET_WINDOWS.filter((item) => item.value !== "data_date" || target.type === "control");
    },
    async load(type) {
      if (this.names[type] !== undefined) {
        return;
      }
      this.names[type] = null;
      try {
        this.names[type] = (await api("get-dependency-targets", { params: { type }, loadingBar: false })).names;
      } catch {
        this.names[type] = undefined;
      }
    },
    candidates(target) {
      const names = this.names[target.type] || [];
      return this.ownerType === target.type ? names.filter((name) => name !== this.ownerName) : names;
    },
    filter(target, index, text, update) {
      this.load(target.type).then(() => {
        update(() => {
          const needle = text.toLowerCase();
          this.shown[index] = this.candidates(target).filter((name) => name.toLowerCase().includes(needle));
        });
      });
    },
    typed(target, text) {
      // A name typed and not picked is kept: the server tells when it does not exist.
      if (text !== undefined && text !== target.name && text !== "") {
        target.name = text;
      }
    },
    missing(target) {
      const names = this.names[target.type];
      return Boolean(target.name) && Array.isArray(names) && !names.includes(target.name);
    },
    typeChanged(target) {
      this.load(target.type);
      if (target.type !== "control" && target.window === "data_date") {
        target.window = "today";
      }
      target.name = "";
    },
    add() {
      this.targets.push(emptyTarget());
    },
    remove(index) {
      this.targets.splice(index, 1);
      this.shown.splice(index, 1);
    },
  },
};
</script>

<style scoped>
.target-row {
  position: relative;
  padding: 8px 36px 8px 8px;
  border: 1px solid #dfe4e8;
  border-radius: 4px;
}
.remove {
  position: absolute;
  top: 8px;
  right: 4px;
}
</style>
