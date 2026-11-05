<template>
  <div class="q-gutter-y-md">
    <q-select
      v-model="task.type"
      outlined
      dense
      emit-value
      map-options
      option-disable="disable"
      :options="typeOptions"
      :label="typeLabel"
      class="type-select"
      @update:model-value="typeChanged">
      <template v-slot:option="{ itemProps, opt }">
        <q-item v-bind="itemProps">
          <q-item-section avatar><q-icon :name="opt.icon" :color="opt.disable ? 'grey-5' : opt.color" size="xs" /></q-item-section>
          <q-item-section>
            <q-item-label>{{ opt.label }}</q-item-label>
            <q-item-label caption>{{ opt.disable ? opt.reason : opt.description }}</q-item-label>
          </q-item-section>
        </q-item>
      </template>
    </q-select>

    <!-- SQL: a script of statements of DML and PL/SQL, run in the rapo database. -->
    <template v-if="task.type === 'SQL'">
      <code-box v-model="task.text" label="SQL" :template-vars="variableList" :hide-chips="[]" />
      <div class="text-caption text-grey-7">
        One statement or a script: plain statements end with <code>;</code>, a PL/SQL block with <code>/</code> alone on its line. Only DML and PL/SQL
        are allowed (no <code>CREATE</code>, <code>DROP</code>, <code>TRUNCATE</code>). The statements run in one transaction in the rapo database and are
        committed at the end; an error rolls them all back.
      </div>
    </template>

    <!-- TRANSFER: a param, with arguments that replace its parameters for this job only. -->
    <template v-else-if="task.type === 'TRANSFER'">
      <q-select
        v-model="task.param"
        outlined
        dense
        use-input
        hide-selected
        fill-input
        new-value-mode="add-unique"
        input-debounce="0"
        :options="paramOptions"
        label="Transfer param"
        hint="The name of a param kept as a file or in the database"
        @filter="filterParams"
        @input-value="(value) => (task.param = value)" />
      <q-input v-model="task.arguments_text" outlined dense type="textarea" autogrow input-class="mono" label="Arguments" hint="Name:value, one per line, replace parameters of the param for this job only, e.g. FileMask:&lt;YYYYMMDD-1d&gt;\.csv" />
    </template>

    <!-- EXECUTABLE: a program with its arguments, started without a shell. -->
    <template v-else-if="task.type === 'EXECUTABLE'">
      <q-input v-model="task.path" outlined dense label="Program" input-class="mono" :hint="foldersHint" />
      <q-input v-model="task.args_text" outlined dense type="textarea" autogrow input-class="mono" label="Arguments" hint="One per line; {variables} are filled in" />
      <div class="row q-col-gutter-md">
        <div class="col-6"><q-input v-model="task.cwd" outlined dense label="Working folder" input-class="mono" hint="Empty: the folder of the program" /></div>
        <div class="col-6"><q-input v-model="task.env_text" outlined dense type="textarea" autogrow label="Environment" input-class="mono" hint="NAME=value, one per line" /></div>
      </div>
    </template>

    <!-- COMMAND: a command line for the shell of the OS. -->
    <template v-else-if="task.type === 'COMMAND'">
      <q-input v-model="task.command" outlined dense type="textarea" autogrow input-class="mono" label="Command line" hint="Run by the shell of the OS (pipes and redirects work); {variables} are filled in" />
      <div class="row q-col-gutter-md">
        <div class="col-6"><q-input v-model="task.cwd" outlined dense label="Working folder" input-class="mono" hint="Empty: a folder of the run" /></div>
        <div class="col-6"><q-input v-model="task.env_text" outlined dense type="textarea" autogrow label="Environment" input-class="mono" hint="NAME=value, one per line" /></div>
      </div>
    </template>

    <!-- The scripts: a file or the code itself. -->
    <template v-else>
      <q-btn-toggle v-model="task.source" no-caps unelevated toggle-color="teal" color="white" text-color="blue-grey-8" :options="[{ label: 'Code', value: 'code' }, { label: 'File', value: 'file' }]" />
      <q-input v-if="task.source === 'file'" v-model="task.file" outlined dense label="Script file" input-class="mono" :hint="foldersHint" />
      <q-input v-else v-model="task.code" outlined type="textarea" autogrow input-class="mono code-area" :label="`${typeLabelOf(task.type)} code`" hint="Written to a file of the run and run; {variables} are filled in, other braces stay as they are" />
      <div class="row q-col-gutter-md">
        <div class="col-6"><q-input v-model="task.args_text" outlined dense type="textarea" autogrow input-class="mono" label="Arguments" hint="One per line" /></div>
        <div class="col-6"><q-input v-model="task.env_text" outlined dense type="textarea" autogrow input-class="mono" label="Environment" hint="NAME=value, one per line" /></div>
      </div>
      <div class="row q-col-gutter-md">
        <div class="col-6"><q-input v-model="task.cwd" outlined dense label="Working folder" input-class="mono" hint="Empty: the folder of the script, or of the run for code" /></div>
        <div v-if="['PYTHON', 'PERL'].includes(task.type)" class="col-6">
          <q-input v-model="task.interpreter" outlined dense label="Interpreter" input-class="mono" :hint="interpreterHint" />
        </div>
        <div v-else-if="shells.length" class="col-6">
          <q-select v-model="task.shell" outlined dense clearable emit-value :options="shells" label="Shell" hint="Empty: the one of rapo.ini or found on the server" />
        </div>
      </div>
    </template>
  </div>
</template>

<script>
import CodeBox from "./CodeBox.vue";
import { api } from "../api";
import { JOB_TYPES } from "../constants";
import { SHELLS, emptyTask } from "../utils/job";

// One task of a job (its pre or post part, its main task, or the program of a condition): its type and the settings
// of the type. The task is edited in place, in the form of utils/job.js.
export default {
  components: { CodeBox },
  props: {
    // The task form of utils/job.js, edited in place.
    modelValue: { type: Object, required: true },
    // get-job-catalogue: the types of this server and whether each is available.
    catalogue: { type: Object, default: null },
    // The types that may be picked here (all of them when empty).
    types: { type: Array, default: () => [] },
    // The names of the job's parameters, offered as variables.
    parameterNames: { type: Array, default: () => [] },
    typeLabel: { type: String, default: "Type" },
  },
  data() {
    return { params: [], paramOptions: [] };
  },
  computed: {
    task() {
      return this.modelValue;
    },
    typeOptions() {
      const free = this.$store.getters.canFreeSql;
      const known = this.catalogue ? this.catalogue.types : [];
      return known
        .filter((item) => !this.types.length || this.types.includes(item.type))
        .map((item) => {
          const style = JOB_TYPES[item.type] || {};
          const noSql = item.type === "SQL" && !free;
          return {
            value: item.type,
            label: style.label || item.type,
            icon: style.icon,
            color: style.color,
            description: item.description,
            // The type being edited stays pickable, so the form can show it, whatever the server says now.
            disable: (!item.available || noSql) && item.type !== this.task.type,
            reason: noSql ? "SQL needs Free SQL: it runs as the schema owner" : item.reason,
          };
        });
    },
    variableList() {
      return [
        { name: "job_name", label: "Job name" },
        { name: "job_id", label: "Job ID" },
        { name: "run_id", label: "Run ID" },
        { name: "trigger", label: "schedule or manual" },
        { name: "scheduled_time", format: "%Y-%m-%d %H:%M:%S", label: "The time the run was scheduled for" },
        { name: "start_time", format: "%Y-%m-%d %H:%M:%S", label: "The time the run started" },
        ...this.parameterNames.filter(Boolean).map((name) => ({ name, label: "Parameter" })),
      ];
    },
    shells() {
      return SHELLS[this.task.type] || [];
    },
    foldersHint() {
      const folders = this.catalogue && this.catalogue.allowed_dirs;
      return folders && folders.length ? `An absolute path below ${folders.join(", ")}` : "An absolute path; [JOBS] allowed_dirs is not set in rapo.ini, so no file is accepted yet";
    },
    interpreterHint() {
      return this.task.type === "PYTHON" ? "Empty: the Python rapo runs in, or [JOBS] python_path" : "Empty: [JOBS] perl_path, or found on the server";
    },
  },
  methods: {
    typeLabelOf(type) {
      return (JOB_TYPES[type] || {}).label || type;
    },
    // A new type starts from a clean task, keeping what the types share (nothing but the type itself is meaningful).
    typeChanged(type) {
      const fresh = emptyTask(type);
      for (const key of Object.keys(fresh)) {
        if (key !== "type") {
          this.task[key] = fresh[key];
        }
      }
    },
    filterParams(value, update) {
      update(() => {
        const needle = value.toLowerCase();
        this.paramOptions = this.params.filter((name) => name.toLowerCase().includes(needle));
      });
    },
    // The params of the Transfers page, when this token and this server have it; else the name is typed.
    async loadParams() {
      if (!this.$store.getters.getEnvInfo || !this.$store.getters.getEnvInfo.transfers_available || !this.$can("datasources", "view")) {
        return;
      }
      try {
        const answer = await api("get-transfers", { loadingBar: false });
        this.params = answer.params.filter((row) => row.source === "file" || row.source === "db").map((row) => row.param_name);
        this.paramOptions = this.params;
      } catch (error) {
        // The name is typed.
      }
    },
  },
  mounted() {
    this.loadParams();
  },
};
</script>

<style scoped>
.type-select {
  max-width: 420px;
}
:deep(.mono) {
  font-family: var(--rapo-font-mono);
  font-size: 13px;
}
/* A hint under a field needs its own room, or it sits on the next field. */
:deep(.q-field--with-bottom) {
  padding-bottom: 24px;
}
:deep(.code-area) {
  min-height: 200px;
}
</style>
