<template>
  <q-dialog v-model="visible" persistent @show="open">
    <q-card class="column no-wrap" style="width: 900px; max-width: 95vw; max-height: 92vh">
      <q-card-section class="row items-center q-py-sm">
        <div class="text-h6">{{ isNew ? "New transfer" : readonly ? "Transfer " + name : "Edit transfer " + name }}</div>
        <q-chip v-if="!isNew && source" dense class="q-ml-md">{{ source === "file" ? "File" : "Database" }}</q-chip>
        <q-space />
        <q-btn-toggle
          v-model="view"
          dense
          no-caps
          unelevated
          toggle-color="blue-grey-7"
          color="blue-grey-1"
          text-color="blue-grey-8"
          class="q-mr-sm"
          :options="[
            { label: 'Table', value: 'table', icon: 'fas fa-list' },
            { label: 'Text', value: 'text', icon: 'fas fa-align-left' },
          ]"
          @update:model-value="switchView" />
        <q-btn aria-label="Close" flat round icon="fas fa-times" @click="close" />
      </q-card-section>
      <q-separator />

      <q-card-section v-if="loading" class="flex flex-center"><q-spinner color="teal" size="3em" /></q-card-section>
      <q-card-section v-else class="col scroll q-gutter-y-sm">
        <div class="row q-gutter-md">
          <q-input v-model="form.name" class="col" outlined dense label="Param name" :disable="!isNew" :rules="[(val) => /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(val || '') || 'Letters, digits, dots, hyphens, underscores']" lazy-rules />
          <q-btn-toggle
            v-if="isNew"
            v-model="form.where"
            no-caps
            unelevated
            toggle-color="teal"
            :options="[
              { label: 'File in param_dir', value: 'file', icon: 'fas fa-file-alt', disable: !paramDir },
              { label: 'Database', value: 'db', icon: 'fas fa-database', disable: !databaseAvailable },
            ]">
            <template v-slot:default><q-tooltip>Where the param is kept; it can be moved later</q-tooltip></template>
          </q-btn-toggle>
        </div>
        <q-input v-if="form.where === 'db' || source === 'db'" v-model="form.description" outlined dense label="Description" maxlength="500" :readonly="readonly" />

        <transfer-param-table v-if="view === 'table' && catalogue" v-model="form.items" :catalogue="catalogue" :editable="!readonly" :storage="storage" @update:model-value="checked = null" />
        <q-input
          v-else-if="view === 'text'"
          v-model="form.text"
          type="textarea"
          outlined
          spellcheck="false"
          input-class="param-text"
          :readonly="readonly"
          :rows="16"
          label="Param (Name:value, one per line, # starts a comment)"
          @update:model-value="checked = null" />

        <div v-if="checked" class="check-ok q-pa-sm rounded-borders">
          <div><q-icon name="fas fa-check-circle" class="q-mr-xs" /> The param is valid. Date tokens expand now (<strong>{{ checked.reference_time.substring(0, 19).replace("T", " ") }}</strong>) to:</div>
          <div class="q-ml-lg">FileMask: <code>{{ checked.file_mask_expanded }}</code></div>
          <div v-if="checked.file_creation_date_expanded" class="q-ml-lg">FileCreationDate: <code>{{ checked.file_creation_date_expanded }}</code></div>
          <div class="q-ml-lg">{{ checked.sftp ? "SFTP source" : "Local source" }} <code>{{ checked.input_dir }}</code> &rarr; <code>{{ checked.output_dirs.join(", ") }}</code></div>
        </div>
        <div v-if="problem" class="check-problem q-pa-sm rounded-borders"><q-icon name="fas fa-exclamation-circle" class="q-mr-xs" /> {{ problem }}</div>
      </q-card-section>

      <q-separator />
      <q-card-actions align="right">
        <q-btn v-if="view === 'text'" flat no-caps label="Insert example" icon="fas fa-file-code" :disable="readonly" @click="insertExample" />
        <q-space />
        <q-btn flat no-caps label="Check" icon="fas fa-check" :loading="checking" :disable="!currentText()" @click="check" />
        <q-btn flat no-caps label="Close" @click="close" />
        <q-btn v-if="!readonly" color="teal" no-caps label="Save" icon="fas fa-save" :loading="saving" :disable="!canSave" @click="save" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script>
import TransferParamTable from "./TransferParamTable.vue";
import { api, notifyError } from "../api";
import { applyParam, itemsOf, parseParam } from "../utils/transferParam";

const EXAMPLE = `# Source: a local directory, or an SFTP server (SftpCheck:Y with SftpType, SftpHost, SftpUser and SftpPassFile or SftpKey)
InputDir:/data/in
SftpCheck:N
# Target directories (must exist), comma separated
OutputDir:/data/out
# Y copies the file, N moves it
KeepInput:Y
# A regular expression; <YYYYMMDD-1d> is yesterday
FileMask:^report_<YYYYMMDD-1d>\\.csv$
FileDateCheck:N
SubDirCheck:N
SizeCheck:N
CtlFileCheck:N
GzipInput:N
GzipOutput:N
ArchiveCheck:N
LogMode:Basic
LogDir:/data/log
LogFile:report.log
Notification:N
`;

// The parameters the editor offers, read once.
let catalogueCache = null;

// Creates a param or edits one (file or database). A new param lists every parameter with its default; an existing one
// lists those that have a value, and the others are added with "+". The Text view edits the .param text itself. The
// place of an existing param changes with Move.
export default {
  components: { TransferParamTable },
  props: {
    modelValue: { type: Boolean, default: false },
    name: { type: String, default: null },
    paramDir: { type: String, default: null },
    databaseAvailable: { type: Boolean, default: true },
  },
  emits: ["update:modelValue", "saved"],
  data() {
    return {
      loading: false,
      saving: false,
      checking: false,
      source: null,
      checked: null,
      problem: null,
      view: "table",
      catalogue: null,
      // The lines of the text as opened, so that saving from the table keeps its comments.
      lines: [],
      form: { name: "", where: "file", description: "", text: "", items: [] },
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
      return !this.name;
    },
    // Where the param is kept: chosen for a new one, fixed for an existing one.
    storage() {
      return this.isNew ? this.form.where : this.source;
    },
    readonly() {
      return !this.$can("datasources", "edit");
    },
    canSave() {
      return /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(this.form.name || "") && !!this.currentText().trim();
    },
  },
  watch: {
    // A param in a file can not have the parameters only the database offers (LogDatabase): they are lost when the
    // place changes to a file, and come back with their default when it changes to the database.
    "form.where"(where) {
      if (!this.isNew || !this.catalogue) {
        return;
      }
      const only = this.catalogue.filter((item) => item.storage === "db");
      if (where === "file") {
        this.form.items = this.form.items.filter((item) => !only.some((entry) => entry.name === item.name));
      } else {
        const have = new Set(this.form.items.map((item) => item.name));
        this.form.items = [...this.form.items, ...only.filter((entry) => !have.has(entry.name)).map((entry) => ({ name: entry.name, value: entry.default || "" }))];
      }
    },
  },
  methods: {
    // The param text as it would be saved now, from the table or from the text.
    currentText() {
      return this.view === "text" ? this.form.text : applyParam(this.lines, this.form.items);
    },
    switchView(view) {
      this.checked = null;
      if (view === "text") {
        // From the table: its parameters are written into the lines.
        this.form.text = applyParam(this.lines, this.form.items);
      } else {
        this.lines = parseParam(this.form.text);
        this.form.items = itemsOf(this.lines);
      }
    },
    async open() {
      this.checked = null;
      this.problem = null;
      this.source = null;
      this.view = "table";
      this.loading = true;
      try {
        if (!catalogueCache) {
          catalogueCache = (await api("get-transfer-catalogue", { loadingBar: false })).parameters;
        }
        this.catalogue = catalogueCache;
        if (this.isNew) {
          // Every parameter, with its default for the one that has it.
          this.lines = [];
          const where = this.paramDir ? "file" : "db";
          this.form = {
            name: "",
            where,
            description: "",
            text: "",
            // The parameters only a param in the database can have are left out of a file.
            items: this.catalogue.filter((item) => !item.storage || item.storage === where).map((item) => ({ name: item.name, value: item.default || "" })),
          };
          return;
        }
        const param = await api("get-transfer", { params: { name: this.name } });
        this.source = param.source;
        this.lines = parseParam(param.text);
        this.form = { name: param.param_name, where: param.source, description: param.description || "", text: param.text, items: itemsOf(this.lines) };
      } catch (error) {
        notifyError(`Failed to load ${this.name || "the editor"}.`, error);
        this.visible = false;
      } finally {
        this.loading = false;
      }
    },
    insertExample() {
      this.form.text = this.form.text ? this.form.text.replace(/\n*$/, "\n\n") + EXAMPLE : EXAMPLE;
      this.checked = null;
    },
    async check() {
      this.checking = true;
      this.checked = null;
      this.problem = null;
      try {
        this.checked = await api("validate-transfer", { method: "POST", body: { text: this.currentText() } });
      } catch (error) {
        this.problem = error.message;
      } finally {
        this.checking = false;
      }
    },
    async save() {
      this.saving = true;
      this.problem = null;
      try {
        await api("save-transfer", {
          method: "POST",
          body: { name: this.form.name, text: this.currentText(), where: this.isNew ? this.form.where : this.source, description: this.form.description, create: this.isNew },
        });
        this.$q.notify({ type: "positive", message: `${this.form.name} saved.` });
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

<style>
.param-text {
  font-family: var(--rapo-font-mono);
  font-size: 13px;
}
/* The result of Check and the reason a save failed, in the theme tokens (readable in both themes). */
.check-ok {
  background: var(--rapo-added-bg);
  color: var(--rapo-added-fg);
}
.check-problem {
  background: var(--rapo-removed-bg);
  color: var(--rapo-removed-fg);
}
.check-ok code,
.check-problem code {
  color: inherit;
  background: transparent;
}
</style>
