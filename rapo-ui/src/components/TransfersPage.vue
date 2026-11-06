<template>
  <q-page class="column no-wrap" :style-fn="fillViewportToBottom">
    <h2 class="row items-center text-no-wrap q-gutter-lg q-mb-md">
      <div>Transfers</div>
    </h2>
    <q-card class="fill-card column no-wrap">
      <transfers-panel />
    </q-card>
  </q-page>
</template>

<script>
import TransfersPanel from "./TransfersPanel.vue";
import { fillViewportToBottom } from "../utils/layout";

// The transfers are a tab of the Jobs page. This page is the same panel for a token that may not open Jobs (or when
// the jobs are switched off): the route /transfers sends everyone else to the tab.
export default {
  components: { TransfersPanel },
  methods: { fillViewportToBottom },
};
</script>

<style scoped>
.fill-card {
  flex: 1 1 0;
  min-height: 0;
}
</style>
