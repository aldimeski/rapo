<template>
  <div class="q-gutter-y-md">
    <q-select v-model="condition.kind" outlined dense emit-value map-options :options="kinds" label="Kind of condition" class="type-select">
      <template v-slot:option="{ itemProps, opt }">
        <q-item v-bind="itemProps">
          <q-item-section>
            <q-item-label>{{ opt.label }}</q-item-label>
            <q-item-label caption>{{ opt.caption }}</q-item-label>
          </q-item-section>
        </q-item>
      </template>
    </q-select>

    <template v-if="condition.kind === 'SQL'">
      <code-box v-model="condition.text" label="Query" :template-vars="variables" />
      <div class="text-caption text-grey-7">One SELECT (or WITH) query, read only. The condition is met when its first value is not empty and not 0, <code>N</code> or <code>false</code>.</div>
    </template>
    <job-task-editor
      v-else-if="condition.kind === 'EXIT_CODE'"
      v-model="condition.task"
      :catalogue="catalogue"
      :types="exitCodeTypes"
      :parameter-names="parameterNames"
      :job-name="ownerName"
      type-label="Type of the program" />
    <run-status-editor v-else-if="condition.kind === 'RUN_STATUS'" v-model="condition.targets" :owner-type="ownerType" :owner-name="ownerName" />
    <template v-else-if="condition.kind === 'FILE_EXISTS'">
      <q-input v-model="condition.path" outlined dense label="File or folder" input-class="mono" hint="Date tokens like <YYYYMMDD> and {variables} are filled in" />
      <q-btn-toggle
        v-model="condition.exists"
        no-caps
        unelevated
        toggle-color="teal"
        color="white"
        text-color="blue-grey-8"
        :options="[{ label: 'It exists', value: true }, { label: 'It does not exist', value: false }]" />
    </template>
    <template v-else>
      <q-input v-model="condition.directory" outlined dense label="Folder" input-class="mono" hint="Date tokens like <YYYYMMDD> and {variables} are filled in" />
      <q-input v-model="condition.mask" outlined dense label="File mask" input-class="mono" hint="A regular expression matched against the file names; empty: every file" />
      <div class="row q-col-gutter-md">
        <div class="col-3"><q-input v-model.number="condition.min" outlined dense type="number" min="0" label="At least" clearable /></div>
        <div class="col-3"><q-input v-model.number="condition.max" outlined dense type="number" min="0" label="At most" clearable /></div>
      </div>
    </template>
  </div>
</template>

<script>
import CodeBox from "./CodeBox.vue";
import JobTaskEditor from "./JobTaskEditor.vue";
import RunStatusEditor from "./RunStatusEditor.vue";
import { EXIT_CODE_TYPES } from "../utils/job";

// The condition of a job or of a workflow: its kind and the settings of the kind, edited in place (the form of
// utils/job.js). The same editor serves both; a workflow offers fewer kinds.
export default {
  components: { CodeBox, JobTaskEditor, RunStatusEditor },
  props: {
    modelValue: { type: Object, required: true },
    // The kinds to pick from (CONDITION_KINDS or WORKFLOW_CONDITION_KINDS of utils/job.js).
    kinds: { type: Array, required: true },
    catalogue: { type: Object, default: null },
    // The variables a query may use, and the names of the parameters.
    variables: { type: Array, default: () => [] },
    parameterNames: { type: Array, default: () => [] },
    // What the condition belongs to: it can not wait for itself.
    ownerType: { type: String, default: "job" },
    ownerName: { type: String, default: "" },
  },
  data() {
    return { exitCodeTypes: EXIT_CODE_TYPES };
  },
  computed: {
    condition() {
      return this.modelValue;
    },
  },
};
</script>
