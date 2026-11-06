<template>
  <q-page>
    <div v-if="!ready">
      <h2 class="row items-center q-mb-lg">
        <q-skeleton type="QChip" width="90px" height="50px" class="q-mr-md" />
        <q-skeleton type="text" width="300px" height="60px" />
      </h2>
      <load-error-notice v-if="loadError" :message="`The workflow could not be loaded: ${loadError}`" @retry="reloadAfterError" />
      <editor-skeleton v-else :rows="2" />
    </div>
    <div v-else>
      <h2 class="row items-center q-gutter-x-lg q-mb-lg">
        <div>{{ !editorWritable ? "Workflow" : isNew ? "New workflow" : "Edit workflow" }}</div>
        <div class="row items-center no-wrap text-grey-7 page-subject">
          <q-chip size="lg">
            <q-avatar icon="fas fa-stream" color="indigo-5" text-color="white" class="type-avatar" />
            {{ form.name || "New workflow" }}
          </q-chip>
          <span class="q-ml-xs">{{ form.def.steps.length }} step{{ form.def.steps.length === 1 ? "" : "s" }}<template v-if="!isNew"> &middot; version {{ version }}</template></span>
        </div>
      </h2>

      <q-card>
        <q-tabs v-model="tab" class="text-white bg-blue-grey-7" active-color="light-blue-1" indicator-color="light-blue-1" align="left" inline-label narrow-indicator no-caps>
          <q-tab name="general" label="General" icon="fas fa-window-maximize" />
          <q-tab name="steps" :label="`Steps (${form.def.steps.length})`" icon="fas fa-list-ol" />
          <q-tab name="condition" label="Condition" :icon="form.def.condition.on ? 'fas fa-check-circle' : 'fas fa-circle-notch'" />
          <q-tab name="schedule" label="Schedule" :icon="form.scheduleOn ? 'fas fa-clock' : 'fas fa-circle-notch'" />
          <q-tab v-if="!isNew" name="versions" :label="`Versions (${version})`" icon="fas fa-history" />
        </q-tabs>

        <q-separator />

        <q-banner v-if="!editorWritable" dense class="bg-blue-grey-1 text-blue-grey-10">
          <template #avatar><q-icon name="fas fa-eye" size="16px" /></template>
          Read only: your token can view workflows but not change them.
        </q-banner>
        <q-banner v-if="!isNew && !form.def.steps.length" dense class="bg-red-1 text-red-10">
          <template #avatar><q-icon name="fas fa-exclamation-circle" size="16px" color="red" /></template>
          This workflow has no steps (its jobs were deleted) and is switched off. Add a job on the Steps tab; a workflow without steps can not be saved or run.
        </q-banner>

        <q-banner v-if="checkResult" dense :class="checkResult.ok ? 'bg-green-1 text-green-10' : 'bg-red-1 text-red-10'">
          <template #avatar><q-icon :name="checkResult.ok ? 'fas fa-check-circle' : 'fas fa-exclamation-circle'" size="16px" :color="checkResult.ok ? 'green' : 'red'" /></template>
          <div v-if="checkResult.ok">Fit to be saved: the steps, the jobs, the condition and the parameters were checked.</div>
          <div v-else>
            <div v-for="problem in checkResult.problems" :key="problem">{{ problem }}</div>
          </div>
          <div v-for="problem in checkResult.missing || []" :key="problem" class="text-amber-10"><q-icon name="fas fa-exclamation-triangle" size="12px" class="q-mr-xs" />{{ problem }} It can still be saved, but the run would end as an error.</div>
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
                  :hint="isNew ? 'Letters, digits, dots, hyphens and underscores; it can not be changed later' : 'The name of a workflow is not changed'" />
                <q-input v-model="form.description" outlined label="Description" maxlength="500" />
                <q-input v-model="form.notify_email" outlined label="E-mail on failure" maxlength="500" :hint="mailHint" />
                <q-toggle v-model="form.status" true-value="Y" false-value="N" label="On" />
                <div class="text-caption text-grey-7 q-mt-none">An off workflow is not started by its schedule; it can still be run by hand.</div>

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
                    A parameter of the workflow overrides the parameter of the same name of each job that has one, for that run only. A job that has no such parameter is not given it. A secret is never shown
                    again, and is hidden in the logs. A run started by hand may give other values.
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

            <!-- Steps -->
            <q-tab-panel name="steps">
              <div class="q-ma-lg q-gutter-y-md panel-width">
                <div class="text-caption text-grey-7">
                  The jobs run one after another: when a step ends, the next one starts. If a step fails the workflow stops, the later steps are skipped, and one e-mail is sent for the workflow (the mail of the job is not
                  used). If the condition of a step is not met the workflow stops too, as a warning, without a mail. A job can be in a workflow once, and keeps its own schedule.
                </div>

                <div v-if="!form.def.steps.length" class="text-grey-7">No steps yet: add a job below.</div>
                <q-list v-else bordered separator class="rounded-borders">
                  <q-item v-for="(job, index) in form.def.steps" :key="job" :class="{ 'bg-red-1': repeated[job] }">
                    <q-item-section avatar>
                      <q-avatar size="26px" color="blue-grey-6" text-color="white" class="step-number">{{ index + 1 }}</q-avatar>
                    </q-item-section>
                    <q-item-section>
                      <q-item-label>
                        <q-icon v-if="known[job]" :name="jobType(known[job].job_type).icon" :color="jobType(known[job].job_type).color" size="xs" class="q-mr-xs" />
                        <span class="text-weight-bold">{{ job }}</span>
                        <q-chip v-if="known[job] && known[job].status === 'N'" dense size="sm" color="grey-6" text-color="white" class="q-ml-sm">Off<q-tooltip>The job is switched off: its own schedule does not start it, but it still runs as a step</q-tooltip></q-chip>
                        <q-chip v-if="!known[job]" dense size="sm" color="deep-orange" text-color="white" class="q-ml-sm">No such job</q-chip>
                      </q-item-label>
                      <q-item-label v-if="known[job] && known[job].description" caption>{{ known[job].description }}</q-item-label>
                      <q-item-label v-if="repeated[job]" caption class="text-red-10">Repeated at {{ repeatedText(job) }}: a job can be in a workflow once</q-item-label>
                    </q-item-section>
                    <q-item-section v-if="editorWritable" side>
                      <div class="row no-wrap items-center">
                        <q-btn aria-label="Move up" flat round dense size="sm" icon="fas fa-arrow-up" :disable="index === 0" @click="move(index, -1)" />
                        <q-btn aria-label="Move down" flat round dense size="sm" icon="fas fa-arrow-down" :disable="index === form.def.steps.length - 1" @click="move(index, 1)" />
                        <q-btn aria-label="Remove the step" flat round dense size="sm" icon="fas fa-minus" color="primary" @click="form.def.steps.splice(index, 1)" />
                      </div>
                    </q-item-section>
                  </q-item>
                </q-list>

                <q-select
                  v-if="editorWritable"
                  v-model="picked"
                  outlined
                  dense
                  use-input
                  input-debounce="0"
                  emit-value
                  map-options
                  :options="jobOptions"
                  label="Add a job"
                  hint="The job is added at the end; the arrows move it"
                  class="add-select"
                  @filter="filterJobs"
                  @update:model-value="addJob">
                  <template v-slot:no-option>
                    <q-item><q-item-section class="text-grey">{{ jobs.length ? "Every job is a step already, or none matches" : "There are no jobs: make one on the Jobs tab" }}</q-item-section></q-item>
                  </template>
                  <template v-slot:option="{ itemProps, opt }">
                    <q-item v-bind="itemProps">
                      <q-item-section avatar><q-icon :name="jobType(opt.type).icon" :color="jobType(opt.type).color" size="xs" /></q-item-section>
                      <q-item-section>
                        <q-item-label>{{ opt.label }}</q-item-label>
                        <q-item-label v-if="opt.caption" caption>{{ opt.caption }}</q-item-label>
                      </q-item-section>
                    </q-item>
                  </template>
                </q-select>
              </div>
            </q-tab-panel>

            <!-- Condition -->
            <q-tab-panel name="condition">
              <div class="q-ma-lg q-gutter-y-md panel-width">
                <q-toggle v-model="form.def.condition.on" label="Start the workflow only when a condition is met" />
                <div class="text-caption text-grey-7">
                  It is checked once, before the first step. When it is not met the run ends as canceled, shown as a warning, with the reason in its message; no step runs and no mail is sent.
                  The history can hide such runs, and the list says how many there were since the last real run.
                </div>
                <condition-editor v-if="form.def.condition.on" v-model="form.def.condition" :kinds="conditionKinds" :owner-type="'workflow'" :owner-name="form.name" />
              </div>
            </q-tab-panel>

            <!-- Schedule -->
            <q-tab-panel name="schedule">
              <div class="q-ma-lg q-gutter-y-md">
                <q-toggle v-model="form.scheduleOn" label="Scheduled" />
                <div class="text-caption text-grey-7">The job scheduler starts the workflow at these times, on the server that holds its lease. The jobs in it keep their own schedules.</div>
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
                <q-tooltip anchor="top left" self="bottom left" :offset="[0, 5]">Save and return to the workflows</q-tooltip>
              </q-btn>
              <q-btn label="Apply" color="primary" outline :disable="!dirty || saving" @click="persist('stay')">
                <q-tooltip anchor="top left" self="bottom left" :offset="[0, 5]">Save and keep editing (Ctrl+S)</q-tooltip>
              </q-btn>
            </template>
            <q-btn label="Check" color="primary" flat icon="fas fa-clipboard-check" :loading="checking" @click="check">
              <q-tooltip anchor="top left" self="bottom left" :offset="[0, 5]">Check the steps and the jobs without saving</q-tooltip>
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
import ConditionEditor from "./ConditionEditor.vue";
import EditorSkeleton from "./EditorSkeleton.vue";
import LoadErrorNotice from "./LoadErrorNotice.vue";
import ScheduleEditBox from "./ScheduleEditBox.vue";
import { api, notifyError } from "../api";
import { jobType } from "../constants";
import editorGuard from "../mixins/editorGuard";
import { toDateTimeString } from "../utils/format";
import { MASK, OVERLAPS, WORKFLOW_CONDITION_KINDS, scheduleToUnits } from "../utils/job";
import { defaultSchedule, describeSchedule, parseSchedule, scheduleUnits } from "../utils/schedule";
import { definitionToForm, emptyWorkflowForm, formProblems, formToDefinition, repeatedJobs } from "../utils/workflow";

const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const LIST = { name: "jobs", query: { tab: "workflows" } };

function emptyForm() {
  return { name: "", description: "", notify_email: "", status: "Y", def: emptyWorkflowForm(), scheduleOn: false, schedule: defaultSchedule() };
}

// The editor of one workflow: the jobs it runs in order, its parameters, its schedule and its versions. Like the other
// editors it keeps what was loaded to know what changed, asks before leaving unsaved changes, and Ctrl+S applies.
export default {
  mixins: [editorGuard],
  components: { ConditionEditor, EditorSkeleton, LoadErrorNotice, ScheduleEditBox },
  props: ["name"],
  data() {
    return {
      ready: false,
      loadError: null,
      tab: "general",
      catalogue: null,
      // The jobs there are (get-jobs), to pick the steps from and to tell of a job that is off or gone.
      jobs: [],
      jobOptions: [],
      picked: null,
      form: emptyForm(),
      savedJson: "",
      version: 0,
      versions: null,
      versionsError: null,
      pickedVersion: null,
      saving: false,
      checking: false,
      checkResult: null,
      shown: {},
      overlaps: OVERLAPS,
      conditionKinds: WORKFLOW_CONDITION_KINDS,
    };
  },
  computed: {
    // For mixins/editorGuard.
    editorWritable() {
      return this.$can("jobs", "edit");
    },
    editorNoun() {
      return "Workflow";
    },
    dirty() {
      return JSON.stringify(this.form) !== this.savedJson;
    },
    isNew() {
      return this.name === "new";
    },
    known() {
      return Object.fromEntries(this.jobs.map((job) => [job.job_name, job]));
    },
    repeated() {
      return repeatedJobs(this.form.def.steps);
    },
    mailHint() {
      const catalogue = this.catalogue;
      if (!catalogue) {
        return "Addresses separated by a comma or a semicolon";
      }
      if (!catalogue.email_enabled) {
        return "Failure mail is off in rapo.ini ([EMAIL] enabled): a failed workflow is only marked Error";
      }
      return catalogue.default_email
        ? "Empty: the default address of rapo.ini ([JOBS] default_email). The addresses of the jobs are not used in a workflow"
        : "Empty: nobody is told, there is no default address in rapo.ini ([JOBS] default_email). The addresses of the jobs are not used in a workflow";
    },
    timeoutHint() {
      const seconds = this.catalogue ? this.catalogue.timeout : null;
      return seconds ? `For the whole workflow. Empty: the default of ${seconds} s ([JOBS] timeout); 0: no limit` : "For the whole workflow. Empty: the default of rapo.ini ([JOBS] timeout); 0: no limit";
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
    repeatedText(job) {
      const numbers = this.repeated[job] || [];
      return numbers.length === 2 ? `steps ${numbers[0]} and ${numbers[1]}` : `steps ${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
    },
    async initialLoad() {
      try {
        const [catalogue, jobs] = await Promise.all([api("get-job-catalogue", { loadingBar: false }), api("get-jobs", { loadingBar: false })]);
        this.catalogue = catalogue;
        this.jobs = jobs.jobs;
        if (this.isNew) {
          this.form = emptyForm();
          this.savedJson = JSON.stringify(this.form);
          this.ready = true;
          return;
        }
        this.loadWorkflow(await api("get-workflow", { params: { name: this.name } }));
        this.ready = true;
      } catch (error) {
        if (error.status === 404 && !this.isNew) {
          this.$q.notify({ type: "negative", message: `There is no workflow ${this.name}.` });
          this.$router.push(LIST);
          return;
        }
        this.loadError = error.message;
        notifyError("Failed to load the workflow.", error);
      }
    },
    reloadAfterError() {
      this.loadError = null;
      return this.initialLoad();
    },
    loadWorkflow(workflow) {
      const config = workflow.schedule_config;
      this.form = {
        name: workflow.workflow_name,
        description: workflow.description || "",
        notify_email: workflow.notify_email || "",
        status: workflow.status,
        def: definitionToForm(workflow.definition),
        scheduleOn: Boolean(config) && Boolean(scheduleUnits(config)),
        schedule: config && scheduleUnits(config) ? parseSchedule(config) : defaultSchedule(),
      };
      this.version = workflow.version;
      this.shown = {};
      this.savedJson = JSON.stringify(this.form);
    },
    // The jobs that are not steps yet, matching what is typed.
    filterJobs(value, update) {
      update(() => {
        const needle = (value || "").toLowerCase();
        this.jobOptions = this.jobs
          .filter((job) => !this.form.def.steps.includes(job.job_name) && (!needle || job.job_name.toLowerCase().includes(needle) || (job.description || "").toLowerCase().includes(needle)))
          .map((job) => ({ label: job.job_name, value: job.job_name, type: job.job_type, caption: job.description }));
      });
    },
    addJob(name) {
      if (name && !this.form.def.steps.includes(name)) {
        this.form.def.steps.push(name);
      }
      this.$nextTick(() => {
        this.picked = null;
      });
    },
    move(index, by) {
      const steps = this.form.def.steps;
      const to = index + by;
      if (to < 0 || to >= steps.length) {
        return;
      }
      steps.splice(to, 0, steps.splice(index, 1)[0]);
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
        return complain("A workflow name has 1-128 letters, digits, dots, hyphens or underscores, and starts with a letter or a digit.", "general");
      }
      const problems = formProblems(this.form.def);
      if (problems.length) {
        return complain(problems[0], problems[0].startsWith("Parameter") ? "general" : "steps");
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
        this.$q.notify({ color: "grey-7", message: "No changes" });
        if (mode === "close") {
          this.$router.push(LIST);
        }
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
        saved = await api("save-workflow", { method: "POST", body: { ...this.payload(), create: this.isNew } });
      } catch (error) {
        // Stay on the page so unsaved edits are not lost; the server's word names what is wrong.
        this.saving = false;
        this.checkResult = { ok: false, problems: [error.message] };
        notifyError("The workflow was not saved.", error);
        return false;
      }
      const name = saved.workflow_name;
      this.saving = false;
      if (mode === "close" || mode === "leave") {
        this.savedJson = JSON.stringify(this.form);
        this.$q.notify({ type: "positive", message: `Workflow ${name} was saved successfully.` });
        if (mode === "close") {
          this.$router.push(LIST);
        }
        return true;
      }
      const wasNew = this.isNew;
      this.loadWorkflow(saved);
      this.checkResult = null;
      this.versions = null;
      this.$q.notify({ type: "positive", message: `Workflow ${name} was saved.` });
      if (wasNew) {
        await this.$router.replace({ name: "edit-workflow", params: { name } });
      }
      return true;
    },
    // The workflow as last read.
    async reloadSaved() {
      this.loadWorkflow(await api("get-workflow", { params: { name: this.form.name } }));
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
        const answer = await api("check-workflow", { method: "POST", body: { name: this.form.name, definition: formToDefinition(this.form.def) }, loadingBar: false });
        this.checkResult = { ok: answer.ok, problems: answer.problems, missing: answer.missing };
      } catch (error) {
        notifyError("The workflow could not be checked.", error);
      } finally {
        this.checking = false;
      }
    },
    cancel() {
      // The route guard asks first when there are unsaved changes.
      this.$router.push(LIST);
    },
    async loadVersions() {
      this.versionsError = null;
      try {
        this.versions = await api("get-workflow-versions", { params: { name: this.form.name }, loadingBar: false });
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
      this.tab = "steps";
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
.add-select {
  max-width: 520px;
}
.step-number {
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
