<template>
  <div class="param-table">
    <table class="param-grid">
      <colgroup>
        <col style="width: 230px" />
        <col />
        <col v-if="editable" style="width: 40px" />
      </colgroup>
      <thead>
        <tr>
          <th title="Name of the parameter in the .param file">Parameter</th>
          <th title="The value of the parameter">Value</th>
          <th v-if="editable"></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="(row, index) in rows" :key="row.name">
          <tr v-if="index === 0 || rows[index - 1].meta.group !== row.meta.group" class="param-group">
            <td :colspan="editable ? 3 : 2">{{ row.meta.group }}</td>
          </tr>
          <tr :class="{ 'param-ignored': row.ignored }">
            <td class="param-name">
              <span>{{ row.name }}</span>
              <span v-if="row.meta.required" class="param-required">*</span>
              <q-tooltip anchor="top middle" self="bottom middle" :offset="[0, 6]" max-width="360px">
                <div>{{ row.meta.description }}</div>
                <div v-if="row.ignored" class="text-orange-4">Ignored while {{ row.meta.ignored_when[0] }} is {{ row.meta.ignored_when[1] }}</div>
                <div v-if="row.meta.default" class="text-grey-5">Default: {{ row.meta.default }}</div>
                <div v-if="row.meta.required" class="text-grey-5">Required</div>
              </q-tooltip>
            </td>
            <td class="param-value">
              <div class="row items-center no-wrap">
              <template v-if="!editable">
                <span class="text-mono">{{ row.value }}</span>
              </template>
              <q-btn-toggle
                v-else-if="row.meta.kind === 'flag'"
                :model-value="row.value"
                dense
                no-caps
                unelevated
                toggle-color="teal"
                color="blue-grey-1"
                text-color="blue-grey-8"
                :options="[
                  { label: 'Y', value: 'Y' },
                  { label: 'N', value: 'N' },
                ]"
                @update:model-value="set(row, $event)" />
              <q-select
                v-else-if="row.meta.kind === 'choice'"
                class="col"
                :model-value="row.value"
                dense
                outlined
                options-dense
                :options="row.meta.choices"
                @update:model-value="set(row, $event)" />
              <q-input
                v-else
                class="col"
                :model-value="row.value"
                dense
                outlined
                spellcheck="false"
                :type="row.meta.kind === 'number' ? 'number' : 'text'"
                :input-class="row.meta.kind === 'number' ? 'number-cell' : 'text-mono'"
                :placeholder="row.meta.default || (row.meta.required ? 'required' : '')"
                @update:model-value="set(row, $event)" />
              <span v-if="row.ignored" class="param-ignored-tag col-auto">ignored</span>
              </div>
            </td>
            <td v-if="editable" class="text-center">
              <q-btn v-if="!row.meta.required" :aria-label="`Hide ${row.name}`" flat round dense size="sm" color="grey-7" icon="fas fa-minus" @click="remove(row)">
                <q-tooltip>Hide {{ row.name }} (it is not written to the param)</q-tooltip>
              </q-btn>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="editable ? 3 : 2" class="text-grey-7 text-center">No parameter has a value</td>
        </tr>
      </tbody>
    </table>

    <div v-if="editable" class="q-mt-xs">
      <q-btn flat dense no-caps size="sm" color="teal" icon="fas fa-plus" :label="hidden.length ? `Add a parameter (${hidden.length})` : 'All parameters are shown'" :disable="!hidden.length">
        <q-menu v-if="hidden.length" anchor="top left" self="bottom left" style="max-height: 50vh">
          <q-list dense style="min-width: 320px">
            <template v-for="(item, index) in hidden" :key="item.name">
              <q-item-label v-if="index === 0 || hidden[index - 1].group !== item.group" header class="q-py-xs">{{ item.group }}</q-item-label>
              <q-item clickable v-close-popup @click="add(item)">
                <q-item-section>
                  <q-item-label class="text-mono">{{ item.name }}</q-item-label>
                  <q-item-label caption>{{ item.description }}</q-item-label>
                </q-item-section>
              </q-item>
            </template>
          </q-list>
        </q-menu>
      </q-btn>
    </div>
  </div>
</template>

<script>
// The parameters of a transfer as a table of Parameter and Value, the way the Configuration tab of Instance details
// lists the options of rapo.ini. The description of a parameter shows above its name. modelValue is the shown
// parameters [{name, value}]; the ones of the catalogue that are not in it wait behind "Add a parameter", and "-"
// hides one again. A parameter the catalogue does not know (a typo, a newer tool) is shown too, so it can be removed.
export default {
  props: {
    modelValue: { type: Array, required: true },
    // [{name, group, kind, default, required, choices, description}] in the order they are written.
    catalogue: { type: Array, required: true },
    editable: { type: Boolean, default: true },
    // Where the param is kept, "file" or "db": a parameter only a param in the database can have is left out of a file.
    storage: { type: String, default: null },
  },
  emits: ["update:modelValue"],
  computed: {
    known() {
      return new Map(this.catalogue.map((item, index) => [item.name, { ...item, index }]));
    },
    rows() {
      const values = new Map(this.modelValue.map((item) => [item.name, item.value]));
      return this.modelValue
        .map((item) => ({ ...item, meta: this.known.get(item.name) || { group: "Not known to rapo", description: "Not a parameter rapo reads: a typo or a removed one", index: 999 } }))
        .filter((row) => this.fits(row.meta))
        .map((row) => ({ ...row, ignored: !!row.meta.ignored_when && values.get(row.meta.ignored_when[0]) === row.meta.ignored_when[1] }))
        .sort((a, b) => a.meta.index - b.meta.index);
    },
    hidden() {
      const shown = new Set(this.modelValue.map((item) => item.name));
      return this.catalogue.filter((item) => !shown.has(item.name) && this.fits(item));
    },
  },
  methods: {
    fits(meta) {
      return !meta.storage || !this.storage || meta.storage === this.storage;
    },
    set(row, value) {
      this.$emit(
        "update:modelValue",
        this.modelValue.map((item) => (item.name === row.name ? { ...item, value: value == null ? "" : String(value) } : item))
      );
    },
    remove(row) {
      this.$emit(
        "update:modelValue",
        this.modelValue.filter((item) => item.name !== row.name)
      );
    },
    add(item) {
      this.$emit("update:modelValue", [...this.modelValue, { name: item.name, value: item.default || "" }]);
    },
  },
};
</script>

<style scoped>
/* Colors are the theme tokens of styles/app.sass, so the dark theme follows without rules of its own. */
.param-grid {
  width: 100%;
  border-collapse: collapse;
}
.param-grid th {
  text-align: left;
  font-weight: 600;
  color: var(--rapo-strong);
  padding: 4px 8px;
  border-bottom: 1px solid var(--rapo-header);
}
.param-grid td {
  padding: 3px 8px;
  border-bottom: 1px solid var(--rapo-grid);
  vertical-align: middle;
}
.param-group td {
  background: var(--rapo-grid);
  color: var(--rapo-strong);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 2px 8px;
}
.param-name {
  font-family: var(--rapo-font-mono);
  font-size: 13px;
  color: var(--rapo-header-text);
  cursor: help;
}
/* A parameter that has no effect (LogDir while LogDatabase is Y). */
.param-ignored .param-name,
.param-ignored .param-value,
.param-ignored .param-value :deep(input) {
  color: var(--rapo-warn);
  font-style: italic;
}
.param-ignored td {
  background: var(--rapo-warn-soft);
}
.param-ignored-tag {
  margin-left: 8px;
  padding: 0 6px;
  font-size: 10px;
  font-style: normal;
  line-height: 16px;
  text-transform: uppercase;
  border: 1px solid var(--rapo-warn);
  border-radius: 3px;
  color: var(--rapo-warn);
}
.param-required {
  color: var(--rapo-crit);
  margin-left: 2px;
}
</style>