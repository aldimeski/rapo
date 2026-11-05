<template>
  <q-page>
    <div v-if="!ready">
      <h2 class="row items-center q-mb-lg">
        <q-skeleton type="QChip" width="90px" height="50px" class="q-mr-md" />
        <q-skeleton type="text" width="300px" height="60px" />
      </h2>
      <load-error-notice v-if="loadError" :message="`The job could not be loaded: ${loadError}`" @retry="reloadAfterError" />
      <editor-skeleton v-else :rows="2" />
    </div>
    <div v-else>
      <h2 class="row items-center q-gutter-x-lg q-mb-lg">
        <div>{{ !editorWritable ? "Job" : isNew ? "New job" : "Edit job" }}</div>
        <div class="row items-center no-wrap text-grey-7 page-subject">
          <q-chip size="lg">
            <q-avatar :icon="jobType(form.def.main.type).icon" :color="jobType(form.def.main.type).color" text-color="white" class="type-avatar" />
            {{ form.name || "New job" }}
          </q-chip>
          <span class="q-ml-xs">{{ jobType(form.def.main.type).label }}<template v-if="!isNew"> &middot; version {{ version }}</template></span>
        </div>
      </h2>

      <q-card>
        <q-tabs v-model="tab" class="text-white bg-blue-grey-7" active-color="light-blue-1" indicator-color="light-blue-1" align="left" inline-label narrow-indicator no-caps>
          <q-tab name="general" label="General" icon="fas fa-window-maximize" />
          <q-tab name="pre" label="Pre" :icon="form.def.pre.on ? 'fas fa-check-circle' : 'fas fa-circle-notch'" />
          <q-tab name="condition" label="Condition" :icon="form.def.condition.on ? 'fas fa-check-circle' : 'fas fa-circle-notch'" />
          <q-tab name="main" label="Main" icon="fas fa-play-circle" />
          <q-tab name="post" label="Post" :icon="form.def.post.on ? 'fas fa-check-circle' : 'fas fa-circle-notch'" />
          <q-tab name="schedule" label="Schedule" :icon="form.scheduleOn ? 'fas fa-clock' : 'fas fa-circle-notch'" />
          <q-tab v-if="!isNew" name="versions" :label="`Versions (${version})`" icon="fas fa-history" />
        </q-tabs>

        <q-separator />

        <q-banner v-if="!editorWritable" dense class="bg-blue-grey-1 text-blue-grey-10">
          <template #avatar><q-icon name="fas fa-eye" size="16px" /></template>
          Read only: your token can view jobs but not change them.
        </q-banner>
        <q-banner v-else-if="usesSql && !canFreeSql" dense class="bg-amber-1 text-amber-10">
          <template #avatar><q-icon name="fas fa-exclamation-triangle" size="16px" color="amber-8" /></template>
          This job has SQL in it, which runs as the schema owner: saving it needs Free SQL, which your token does not have.
        </q-banner>

        <q-banner v-if="checkResult" dense :class="checkResult.ok ? 'bg-green-1 text-green-10' : 'bg-red-1 text-red-10'">
          <template #avatar><q-icon :name="checkResult.ok ? 'fas fa-check-circle' : 'fas fa-exclamation-circle'" size="16px" :color="checkResult.ok ? 'green' : 'red'" /></template>
          <div v-if="checkResult.ok">Fit to run on this server: the types, interpreters, files, SQL, condition and parameters were checked.</div>
          <div v-else>
            <div v-for="problem in checkResult.problems" :key="problem">{{ problem }}</div>
          </div>
          <template #action><q-btn flat dense no-caps label="Dismiss" @click="checkResult = null" /></template>
        </q-banner>

        <q-form v-readonly-guard @submit="editorWritable && persist('stay')" @reset="cancel" :class="{ 'editor-readonly': !editorWritable }">
          <q-tab-panels v-model="tab" animated keep-alive>
            <!-- General -->
            <q-tab-panel name="general">
              <div class="q-ma-lg q-gutter-y-md panel-width">
                <q-input
                  v-model="form.name"
                  outlined
                  label="Name"
                  maxlength="128"
                  :readonly="!isNew"
                  :hint="isNew ? 'Letters, digits, dots, hyphens and underscores; it can not be changed later' : 'The name of a job is not changed'" />
                <q-input v-model="form.description" outlined label="Description" maxlength="500" />
                <q-input v-model="form.notify_email" outlined label="E-mail on failure" maxlength="500" :hint="mailHint" />
                <q-toggle v-model="form.status" true-value="Y" false-value="N" label="On" />
                <div class="text-caption text-grey-7 q-mt-none">An off job is not started by its schedule; it can still be run by hand.</div>

                <div class="row q-col-gutter-md">
                  <div class="col-4">
                    <q-input v-model.number="form.def.timeout" outlined type="number" min="0" label="Timeout (seconds)" :hint="timeoutHint" clearable />
                  </div>
                  <div class="col-8">
                    <q-select v-model="form.def.overlap" outlined emit-value map-options :options="overlaps" label="A start while it is still running" :hint="overlapCaption">
                      <template v-slot:option="{ itemProps, opt }">
                        <q-item v-bind="itemProps">
                          <q-item-section>
                            <q-item-label>{{ opt.label }}</q-item-label>
                            <q-item-label caption>{{ opt.caption }}</q-item-label>
                          </q-item-section>
                        </q-item>
                      </template>
                    </q-select>
                  </div>
                </div>

                <div>
                  <div class="text-subtitle2 q-mb-xs">Parameters</div>
                  <div class="text-caption text-grey-7 q-mb-sm">
                    Values the tasks use as <code>{name}</code> and the programs read as <code>RAPO_PARAM_NAME</code>. A secret is never shown again, and is hidden in the log. A run started by hand may give other
                    values.
                  </div>
                  <div v-for="(parameter, index) in form.def.parameters" :key="index" class="row items-start q-col-gutter-sm q-mb-xs">
                    <div class="col-3"><q-input v-model="parameter.name" outlined dense label="Name" /></div>
                    <div class="col">
                      <q-input v-model="parameter.value" outlined dense label="Value" :type="parameter.secret && !shown[index] ? 'password' : 'text'">
                        <template v-if="parameter.secret" v-slot:append>
                          <q-icon :name="shown[index] ? 'fas fa-eye-slash' : 'fas fa-eye'" class="cursor-pointer" size="xs" @click="shown[index] = !shown[index]" />
                        </template>
                      </q-input>
                    </div>
                    <div class="col-auto q-pt-sm"><q-toggle v-model="parameter.secret" label="Secret" @update:model-value="secretChanged(parameter)" /></div>
                    <div class="col-auto q-pt-xs"><q-btn aria-label="Remove parameter" flat round dense icon="fas fa-minus" color="primary" @click="form.def.parameters.splice(index, 1)" /></div>
                  </div>
                  <q-btn flat dense no-caps color="primary" icon="fas fa-plus" label="Add a parameter" @click="form.def.parameters.push({ name: '', value: '', secret: false })" />
                </div>
              </div>
            </q-tab-panel>

            <!-- Pre -->
            <q-tab-panel name="pre">
              <div class="q-ma-lg q-gutter-y-md panel-width">
                <q-toggle v-model="form.def.pre.on" label="Run a task before the condition and the main task" />
                <div class="text-caption text-grey-7">
                  The task prepares what the condition and the main task need. If it fails, the run ends as an error and nothing else runs.
                </div>
                <job-task-editor v-if="form.def.pre.on" v-model="form.def.pre.task" :catalogue="catalogue" :parameter-names="parameterNames" type-label="Type of the pre task" />
              </div>
            </q-tab-panel>

            <!-- Condition -->
            <q-tab-panel name="condition">
              <div class="q-ma-lg q-gutter-y-md panel-width">
                <q-toggle v-model="form.def.condition.on" label="Run the job only when a condition is met" />
                <div class="text-caption text-grey-7">
                  It is checked after the pre task. When it is not met the run ends as canceled, not as an error, and the main and post tasks do not run. An error while checking it is an error.
                </div>
                <template v-if="form.def.condition.on">
                  <q-select v-model="form.def.condition.kind" outlined dense emit-value map-options :options="conditionKinds" label="Kind of condition" class="type-select">
                    <template v-slot:option="{ itemProps, opt }">
                      <q-item v-bind="itemProps">
                        <q-item-section>
                          <q-item-label>{{ opt.label }}</q-item-label>
                          <q-item-label caption>{{ opt.caption }}</q-item-label>
                        </q-item-section>
                      </q-item>
                    </template>
                  </q-select>

                  <template v-if="form.def.condition.kind === 'SQL'">
                    <code-box v-model="form.def.condition.text" label="Query" :template-vars="conditionVariables" />
                    <div class="text-caption text-grey-7">One SELECT (or WITH) query, read only. The condition is met when its first value is not empty and not 0, <code>N</code> or <code>false</code>.</div>
                  </template>
                  <job-task-editor
                    v-else-if="form.def.condition.kind === 'EXIT_CODE'"
                    v-model="form.def.condition.task"
                    :catalogue="catalogue"
                    :types="exitCodeTypes"
                    :parameter-names="parameterNames"
                    type-label="Type of the program" />
                  <template v-else-if="form.def.condition.kind === 'FILE_EXISTS'">
                    <q-input v-model="form.def.condition.path" outlined dense label="File or folder" input-class="mono" hint="Date tokens like <YYYYMMDD> and {variables} are filled in" />
                    <q-btn-toggle
                      v-model="form.def.condition.exists"
                      no-caps
                      unelevated
                      toggle-color="teal"
                      color="white"
                      text-color="blue-grey-8"
                      :options="[{ label: 'It exists', value: true }, { label: 'It does not exist', value: false }]" />
                  </template>
                  <template v-else>
                    <q-input v-model="form.def.condition.directory" outlined dense label="Folder" input-class="mono" hint="Date tokens like <YYYYMMDD> and {variables} are filled in" />
                    <q-input v-model="form.def.condition.mask" outlined dense label="File mask" input-class="mono" hint="A regular expression matched against the file names; empty: every file" />
                    <div class="row q-col-gutter-md">
                      <div class="col-3"><q-input v-model.number="form.def.condition.min" outlined dense type="number" min="0" label="At least" clearable /></div>
                      <div class="col-3"><q-input v-model.number="form.def.condition.max" outlined dense type="number" min="0" label="At most" clearable /></div>
                    </div>
                  </template>
                </template>
              </div>
            </q-tab-panel>

            <!-- Main -->
            <q-tab-panel name="main">
              <div class="q-ma-lg q-gutter-y-md panel-width">
                <div class="text-caption text-grey-7">What the job does. It succeeds when its exit code is 0 (SQL and transfers: when they raise no error); anything on stderr is only logged.</div>
                <job-task-editor v-model="form.def.main" :catalogue="catalogue" :parameter-names="parameterNames" type-label="Type of the job" />
              </div>
            </q-tab-panel>

            <!-- Post -->
            <q-tab-panel name="post">
              <div class="q-ma-lg q-gutter-y-md panel-width">
                <q-toggle v-model="form.def.post.on" label="Run a task after the main task" />
                <div class="text-caption text-grey-7">
                  It runs only when the main task succeeded. If it fails the run ends as an error, but the result of the main task stays as it was.
                </div>
                <job-task-editor v-if="form.def.post.on" v-model="form.def.post.task" :catalogue="catalogue" :parameter-names="parameterNames" type-label="Type of the post task" />
              </div>
            </q-tab-panel>

            <!-- Schedule -->
            <q-tab-panel name="schedule">
              <div class="q-ma-lg q-gutter-y-md">
                <q-toggle v-model="form.scheduleOn" label="Scheduled" />
                <div class="text-caption text-grey-7">The job scheduler starts the job at these times, on the server that holds its lease. It is independent of the scheduler of the controls.</div>
                <template v-if="form.scheduleOn">
                  <schedule-edit-box ref="box" v-model="form.schedule" :cascade="false" />
                  <div class="text-grey-8"><q-icon name="fas fa-clock" color="teal" class="q-mr-xs" />{{ scheduleText }}</div>
                </template>
              </div>
            </q-tab-panel>

            <!-- Versions -->
            <q-tab-panel v-if="!isNew" name="versions">
              <div class="q-ma-lg">
                <div class="text-caption text-grey-7 q-mb-sm">Every save of the definition or the schedule is a version. A version can be loaded into the editor, and then saved as a new one.</div>
                <load-error-notice v-if="versionsError" :message="`The versions could not be loaded: ${versionsError}`" @retry="loadVersions" />
                <div v-else-if="!versions" class="text-center"><q-spinner color="teal" /></div>
                <div v-else class="row q-col-gutter-md">
                  <div class="col-4">
                    <q-markup-table flat dense>
                      <thead>
                        <tr class="bg-blue-grey-2">
                          <th class="text-left">Version</th>
                          <th class="text-left">Saved</th>
                          <th class="text-left">By</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="item in versions" :key="item.version" class="cursor-pointer" :class="{ 'bg-blue-grey-1': pickedVersion && pickedVersion.version === item.version }" @click="pickedVersion = item">
                          <td>{{ item.version }}<q-chip v-if="item.version === version" dense size="sm" color="teal" text-color="white" class="q-ml-sm">saved</q-chip></td>
                          <td class="text-no-wrap">{{ toDateTimeString(item.updated_date) }}</td>
                          <td>{{ item.updated_by || "" }}</td>
                        </tr>
                      </tbody>
                    </q-markup-table>
                  </div>
                  <div class="col-8">
                    <div v-if="!pickedVersion" class="text-grey-7">Pick a version to read it.</div>
                    <template v-else>
                      <div class="row items-center q-mb-xs">
                        <div class="col text-grey-7">Version {{ pickedVersion.version }}<template v-if="pickedVersion.schedule_config"> &middot; {{ describe(pickedVersion.schedule_config) }}</template></div>
                        <q-btn v-if="editorWritable" flat dense no-caps color="primary" icon="fas fa-upload" label="Load into the editor" @click="loadVersion(pickedVersion)" />
                      </div>
                      <pre class="version-json">{{ JSON.stringify(pickedVersion.definition, null, 2) }}</pre>
                    </template>
                  </div>
                </div>
              </div>
            </q-tab-panel>
          </q-tab-panels>

          <div class="editor-actions row items-center q-gutter-sm q-px-lg q-py-sm">
            <template v-if="editorWritable">
              <q-btn label="Save" color="primary" :loading="saving" @click="persist('close')">
                <q-tooltip anchor="top left" self="bottom left" :offset="[0, 5]">Save and return to the jobs</q-tooltip>
              </q-btn>
              <q-btn label="Apply" color="primary" outline :disable="!dirty || saving" @click="persist('stay')">
                <q-tooltip anchor="top left" self="bottom left" :offset="[0, 5]">Save and keep editing (Ctrl+S)</q-tooltip>
              </q-btn>
            </template>
            <q-btn label="Check" color="primary" flat icon="fas fa-clipboard-check" :loading="checking" @click="check">
              <q-tooltip anchor="top left" self="bottom left" :offset="[0, 5]">Check the definition as this server would at a run, without saving it</q-tooltip>
            </q-btn>
            <q-btn :label="editorWritable ? 'Cancel' : 'Close'" type="reset" color="primary" flat />
            <q-space />
            <div v-if="dirty" class="text-orange-9 text-weight-medium row items-center no-wrap">
              <q-icon name="fas fa-circle" size="8px" class="q-mr-sm" />
              Unsaved changes
            </div>
          </div>
        </q-form>
      </q-card>
    </div>
  </q-page>
</template>

<script>
import CodeBox from "./CodeBox.vue";
import EditorSkeleton from "./EditorSkeleton.vue";
import JobTaskEditor from "./JobTaskEditor.vue";
import LoadErrorNotice from "./LoadErrorNotice.vue";
import ScheduleEditBox from "./ScheduleEditBox.vue";
import { api, notifyError } from "../api";
import { jobType } from "../constants";
import editorGuard from "../mixins/editorGuard";
import { toDateTimeString } from "../utils/format";
import { CONDITION_KINDS, EXIT_CODE_TYPES, MASK, OVERLAPS, definitionToForm, emptyJobForm, formProblems, formToDefinition, formUsesSql, scheduleToUnits } from "../utils/job";
import { defaultSchedule, describeSchedule, parseSchedule, scheduleUnits } from "../utils/schedule";

const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

// A new job starts as a Python script when the server has Python, else as the first type it offers.
function emptyForm(catalogue) {
  const available = catalogue ? catalogue.types.filter((item) => item.available && item.type !== "SQL") : [];
  const type = available.some((item) => item.type === "PYTHON") ? "PYTHON" : available.length ? available[0].type : "COMMAND";
  return { name: "", description: "", notify_email: "", status: "Y", def: emptyJobForm(type), scheduleOn: false, schedule: defaultSchedule() };
}

// The editor of one job: its definition in the parts PRE, CONDITION, MAIN and POST, its parameters, its schedule and its
// versions. Like the other editors it keeps what was loaded to know what changed, asks before leaving unsaved
// changes, and Ctrl+S applies.
export default {
  mixins: [editorGuard],
  components: { CodeBox, EditorSkeleton, JobTaskEditor, LoadErrorNotice, ScheduleEditBox },
  // The code boxes of a token that may not edit jobs are read only.
  provide() {
    return { editorReadonly: () => !this.$can("jobs", "edit") };
  },
  props: ["name"],
  data() {
    return {
      // A new job is ready at once; an existing one once it has loaded, so the form never shows empty first.
      ready: false,
      loadError: null,
      tab: "general",
      catalogue: null,
      form: emptyForm(null),
      savedJson: "",
      // The version of the job as saved, and the versions read for the Versions tab.
      version: 0,
      versions: null,
      versionsError: null,
      pickedVersion: null,
      saving: false,
      checking: false,
      checkResult: null,
      // Which secret values are shown in clear.
      shown: {},
      overlaps: OVERLAPS,
      conditionKinds: CONDITION_KINDS,
      exitCodeTypes: EXIT_CODE_TYPES,
    };
  },
  computed: {
    // For mixins/editorGuard.
    editorWritable() {
      return this.$can("jobs", "edit");
    },
    editorNoun() {
      return "Job";
    },
    dirty() {
      return JSON.stringify(this.form) !== this.savedJson;
    },
    isNew() {
      return this.name === "new";
    },
    canFreeSql() {
      return this.$store.getters.canFreeSql;
    },
    usesSql() {
      return formUsesSql(this.form.def);
    },
    parameterNames() {
      return this.form.def.parameters.map((item) => item.name.trim()).filter(Boolean);
    },
    conditionVariables() {
      return [
        { name: "job_name", label: "Job name" },
        { name: "run_id", label: "Run ID" },
        { name: "scheduled_time", format: "%Y-%m-%d %H:%M:%S", label: "The time the run was scheduled for" },
        ...this.parameterNames.map((name) => ({ name, label: "Parameter" })),
      ];
    },
    mailHint() {
      const catalogue = this.catalogue;
      if (!catalogue) {
        return "Addresses separated by a comma or a semicolon";
      }
      if (!catalogue.email_enabled) {
        return "Failure mail is off in rapo.ini ([EMAIL] enabled): a failed job is only marked Error";
      }
      return catalogue.default_email ? "Empty: the default address of rapo.ini ([JOBS] default_email)" : "Empty: nobody is told, there is no default address in rapo.ini ([JOBS] default_email)";
    },
    timeoutHint() {
      const seconds = this.catalogue ? this.catalogue.timeout : null;
      return seconds ? `Empty: the default of ${seconds} s ([JOBS] timeout); 0: no limit` : "Empty: the default of rapo.ini ([JOBS] timeout); 0: no limit";
    },
    overlapCaption() {
      return (OVERLAPS.find((item) => item.value === this.form.def.overlap) || {}).caption || "";
    },
    scheduleText() {
      try {
        return describeSchedule(JSON.stringify(scheduleToUnits(this.form.schedule)));
      } catch (error) {
        return "";
      }
    },
  },
  methods: {
    jobType,
    toDateTimeString,
    describe(config) {
      return describeSchedule(config);
    },
    async initialLoad() {
      try {
        this.catalogue = await api("get-job-catalogue", { loadingBar: false });
        if (this.isNew) {
          this.form = emptyForm(this.catalogue);
          this.savedJson = JSON.stringify(this.form);
          this.ready = true;
          return;
        }
        this.loadJob(await api("get-job", { params: { name: this.name } }));
        this.ready = true;
      } catch (error) {
        if (error.status === 404 && !this.isNew) {
          this.$q.notify({ type: "negative", message: `There is no job ${this.name}.` });
          this.$router.push({ name: "jobs" });
          return;
        }
        this.loadError = error.message;
        notifyError("Failed to load the job.", error);
      }
    },
    reloadAfterError() {
      this.loadError = null;
      return this.initialLoad();
    },
    loadJob(job) {
      const config = job.schedule_config;
      this.form = {
        name: job.job_name,
        description: job.description || "",
        notify_email: job.notify_email || "",
        status: job.status,
        def: definitionToForm(job.definition),
        // A stored schedule the editor can not read starts over from the default, and is off.
        scheduleOn: Boolean(config) && Boolean(scheduleUnits(config)),
        schedule: config && scheduleUnits(config) ? parseSchedule(config) : defaultSchedule(),
      };
      this.version = job.version;
      this.shown = {};
      this.savedJson = JSON.stringify(this.form);
    },
    // A parameter that stops being secret must not keep the hidden value: it was never known here.
    secretChanged(parameter) {
      if (!parameter.secret && parameter.value === MASK) {
        parameter.value = "";
        this.$q.notify({ type: "warning", message: `The hidden value of ${parameter.name || "the parameter"} can not be shown: enter it again.` });
      }
    },
    payload() {
      return {
        name: this.form.name.trim(),
        definition: formToDefinition(this.form.def),
        description: this.form.description,
        notify_email: this.form.notify_email,
        status: this.form.status,
        schedule: this.form.scheduleOn ? scheduleToUnits(this.form.schedule) : null,
      };
    },
    // What can be seen without the server; a complaint switches to the tab it is about.
    validate() {
      const complain = (text, tab) => {
        this.$q.notify({ type: "negative", message: text });
        this.tab = tab;
        return false;
      };
      if (this.isNew && !NAME_PATTERN.test(this.form.name.trim())) {
        return complain("A job name has 1-128 letters, digits, dots, hyphens or underscores, and starts with a letter or a digit.", "general");
      }
      const problems = formProblems(this.form.def);
      if (problems.length) {
        return complain(problems[0], problems[0].startsWith("Parameter") ? "general" : "main");
      }
      if (this.usesSql && !this.canFreeSql) {
        return complain("A job with SQL in it needs Free SQL to be saved.", "main");
      }
      if (this.form.scheduleOn && this.$refs.box) {
        const problem = this.$refs.box.problem();
        if (problem) {
          return complain(problem, "schedule");
        }
      }
      return true;
    },
    // Save ("close": back to the list) or Apply ("stay").
    async persist(mode) {
      if (this.saving || !this.editorWritable) {
        return;
      }
      if (!this.isNew && !this.dirty) {
        this.noChanges(mode, "jobs");
        return;
      }
      if (!this.validate()) {
        return;
      }
      await this.submit(mode);
    },
    // On leaving: the checks of Save.
    async saveBeforeLeave() {
      if (!this.validate()) {
        return false;
      }
      return this.submit("leave");
    },
    // The write; true when saved. "leave": saved on leaving, the route guard goes on to where it was going.
    async submit(mode) {
      this.saving = true;
      let saved;
      try {
        saved = await api("save-job", { method: "POST", body: { ...this.payload(), create: this.isNew } });
      } catch (error) {
        // Stay on the page so unsaved edits are not lost; the server's word names what is wrong.
        this.saving = false;
        this.checkResult = { ok: false, problems: [error.message] };
        notifyError("The job was not saved.", error);
        return false;
      }
      const name = saved.job_name;
      this.saving = false;
      if (mode === "close" || mode === "leave") {
        this.savedJson = JSON.stringify(this.form);
        this.$q.notify({ type: "positive", message: `Job ${name} was saved successfully.` });
        if (mode === "close") {
          this.$router.push({ name: "jobs" });
        }
        return true;
      }
      const wasNew = this.isNew;
      this.loadJob(saved);
      this.checkResult = null;
      this.versions = null;
      this.$q.notify({ type: "positive", message: `Job ${name} was saved.` });
      // A new job moves to its own URL (the form already holds it).
      if (wasNew) {
        await this.$router.replace({ name: "edit-job", params: { name } });
      }
      return true;
    },
    // The server holds no version to conflict with: the job as last read.
    async reloadSaved() {
      this.loadJob(await api("get-job", { params: { name: this.form.name } }));
    },
    async check() {
      if (this.checking) {
        return;
      }
      const problems = formProblems(this.form.def);
      if (problems.length) {
        this.checkResult = { ok: false, problems };
        return;
      }
      this.checking = true;
      try {
        const answer = await api("check-job", { method: "POST", body: { definition: formToDefinition(this.form.def) }, loadingBar: false });
        this.checkResult = { ok: answer.ok, problems: answer.problems };
      } catch (error) {
        notifyError("The definition could not be checked.", error);
      } finally {
        this.checking = false;
      }
    },
    cancel() {
      // The route guard asks first when there are unsaved changes.
      this.$router.push({ name: "jobs" });
    },
    async loadVersions() {
      this.versionsError = null;
      try {
        this.versions = await api("get-job-versions", { params: { name: this.form.name }, loadingBar: false });
        this.pickedVersion = this.versions.find((item) => item.version === this.version) || this.versions[0] || null;
      } catch (error) {
        this.versionsError = error.message;
      }
    },
    // An older version into the form, to be saved as a new one: its secrets stay hidden and keep their saved values.
    loadVersion(item) {
      const config = item.schedule_config;
      this.form.def = definitionToForm(item.definition);
      this.form.scheduleOn = Boolean(config) && Boolean(scheduleUnits(config));
      this.form.schedule = config && scheduleUnits(config) ? parseSchedule(config) : defaultSchedule();
      this.shown = {};
      this.tab = "main";
      this.$q.notify({ type: "info", message: `Version ${item.version} is loaded; Save makes it a new version.` });
    },
  },
  watch: {
    // The versions are read when their tab is first opened, and again after a save.
    tab(tab) {
      if (tab === "versions" && !this.versions) {
        this.loadVersions();
      }
    },
  },
  mounted() {
    return this.initialLoad();
  },
  // Leaving with unsaved changes (Cancel, the side menu, back) asks first: save, discard or keep editing.
  beforeRouteUpdate(to, from, next) {
    this.confirmLeave(next);
  },
  beforeRouteLeave(to, from, next) {
    this.confirmLeave(next);
  },
};
</script>

<style scoped>
/* A chip rounds its avatar with a fixed radius, which is not a circle at size xl. */
.q-chip .type-avatar {
  border-radius: 50%;
}
.panel-width {
  max-width: 1000px;
}
.type-select {
  max-width: 420px;
}
:deep(.mono) {
  font-family: var(--rapo-font-mono);
  font-size: 13px;
}
.version-json {
  margin: 0;
  padding: 8px;
  max-height: 520px;
  overflow: auto;
  font-family: var(--rapo-font-mono);
  font-size: 12px;
  border: 1px solid var(--rapo-panel-border);
  background: var(--rapo-surface-alt);
  color: var(--rapo-header-text);
}
</style>
