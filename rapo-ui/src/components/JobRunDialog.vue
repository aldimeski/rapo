<template>
  <q-dialog v-model="visible" @show="load">
    <q-card style="width: 560px; max-width: 95vw">
      <q-card-section class="row items-center q-py-sm">
        <div class="text-h6">Run {{ name }}</div>
        <q-space />
        <q-btn aria-label="Close" flat round dense icon="fas fa-times" v-close-popup />
      </q-card-section>
      <q-separator />

      <q-card-section v-if="!job" class="text-center"><q-spinner color="teal" /></q-card-section>
      <q-card-section v-else-if="!fields.length" class="text-grey-8">This job has no parameters: it runs as it is saved.</q-card-section>
      <q-card-section v-else class="q-gutter-y-sm">
        <div class="text-grey-8">These values are used for this run only; the job keeps its own.</div>
        <q-input
          v-for="field in fields"
          :key="field.name"
          v-model="field.value"
          dense
          outlined
          :label="field.name"
          :type="field.secret && !field.shown ? 'password' : 'text'"
          :placeholder="field.secret ? 'The saved value is used' : ''"
          :hint="field.value !== field.original ? 'Changed for this run' : ''">
          <template v-if="field.secret" v-slot:append>
            <q-icon :name="field.shown ? 'fas fa-eye-slash' : 'fas fa-eye'" class="cursor-pointer" size="xs" @click="field.shown = !field.shown" />
          </template>
        </q-input>
      </q-card-section>

      <q-separator />
      <q-card-actions align="right">
        <q-btn flat no-caps label="Cancel" v-close-popup />
        <q-btn color="teal" no-caps icon="fas fa-play" label="Run" :disable="!job" :loading="running" @click="run" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script>
import { api, notifyError } from "../api";

// Runs a job now with other values of its parameters. A secret is empty here (its value is never sent to the
// browser): left empty it keeps the saved value, typed it replaces it for this run.
export default {
  props: {
    modelValue: { type: Boolean, default: false },
    name: { type: String, default: null },
  },
  emits: ["update:modelValue", "started"],
  data() {
    return { job: null, fields: [], running: false };
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
  },
  methods: {
    async load() {
      this.job = null;
      this.fields = [];
      try {
        this.job = await api("get-job", { params: { name: this.name }, loadingBar: false });
        this.fields = ((this.job.definition && this.job.definition.parameters) || []).map((item) => {
          const field = { name: item.name, secret: Boolean(item.secret), original: item.secret ? "" : item.value || "", shown: false };
          field.value = field.original;
          return field;
        });
      } catch (error) {
        notifyError("Failed to load the job.", error);
        this.visible = false;
      }
    },
    async run() {
      this.running = true;
      try {
        const parameters = {};
        for (const field of this.fields) {
          if (field.value !== field.original) {
            parameters[field.name] = field.value;
          }
        }
        const answer = await api("run-job", { method: "POST", params: { name: this.name }, body: Object.keys(parameters).length ? { parameters } : undefined });
        this.$q.notify({ type: "positive", message: `${this.name} queued.` });
        this.$emit("started", answer.run_id);
        this.visible = false;
      } catch (error) {
        notifyError(`Failed to run ${this.name}.`, error);
      } finally {
        this.running = false;
      }
    },
  },
};
</script>
