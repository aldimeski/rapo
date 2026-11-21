<template>
  <!-- The job scheduler at a glance, on the title row of the Jobs page: its state, the server that schedules, its
       heartbeat, the slots of the job manager and the next fire. It is independent of the scheduler of the controls. -->
  <div v-if="status" class="row items-center status-bar">
    <q-chip class="status-chip">
      <q-avatar :color="state.color" text-color="white">
        <span class="state-dot" :class="{ 'state-dot--live': status.state === 'running' }" />
      </q-avatar>
      <span class="text-weight-bold">{{ state.label }}</span>
      <q-tooltip anchor="bottom middle" self="top middle" :offset="[0, 8]">{{ state.description }}</q-tooltip>
    </q-chip>

    <q-chip class="status-chip">
      <q-avatar icon="fas fa-server" :color="holder.alive ? 'blue-grey-6' : 'grey-5'" text-color="white" />
      <span class="text-weight-bold ellipsis chip-name">{{ holder.server || "No server" }}</span>
      <span v-if="holder.alive && holder.start_date" class="text-grey-7 q-ml-xs">&middot; up {{ age(holder.start_date) }}</span>
      <span v-else-if="holder.stop_date" class="text-grey-7 q-ml-xs">&middot; stopped {{ age(holder.stop_date) }} ago</span>
      <q-tooltip anchor="bottom middle" self="top middle" :offset="[0, 8]">
        <div v-if="holder.server">Scheduling server {{ holder.server }}</div>
        <div>{{ status.leader ? "This server schedules the jobs" : holder.alive ? "Another server schedules; this one takes over if it stops" : "No server holds the lease" }}</div>
        <div v-if="holder.pid">PID {{ holder.pid }}<template v-if="holder.username">, user {{ holder.username }}</template></div>
        <div v-if="holder.heartbeat">Last heartbeat {{ toTimeString(holder.heartbeat) }}; another server takes over after {{ status.lease_timeout }} s without one</div>
      </q-tooltip>
    </q-chip>

    <q-chip class="status-chip">
      <q-avatar color="transparent">
        <q-circular-progress :value="slotShare" size="24px" :thickness="0.32" :color="slotsFull ? 'deep-orange' : 'teal'" track-color="blue-grey-2" />
      </q-avatar>
      <span class="text-weight-bold">{{ manager.running.length }}/{{ manager.capacity }}</span>
      <span v-if="manager.queued.length" class="text-grey-7 q-ml-xs">+{{ manager.queued.length }}</span>
      <q-tooltip anchor="bottom middle" self="top middle" :offset="[0, 8]">
        {{ manager.running.length }} of {{ manager.capacity }} slots running, {{ manager.queued.length }} queued on this server (rapo.ini [JOBS] parallelism)
      </q-tooltip>
    </q-chip>

    <q-chip v-if="nextFire" class="status-chip">
      <q-avatar icon="fas fa-clock" :color="status.disabled ? 'grey-5' : 'teal'" text-color="white" />
      <span class="text-weight-bold ellipsis chip-name" :class="{ 'text-grey-6': status.disabled }">{{ nextFire.job_name }}</span>
      <span class="text-grey-7 q-ml-xs">in {{ until(nextFire.next_fire) }}</span>
      <q-tooltip anchor="bottom middle" self="top middle" :offset="[0, 8]">
        Next fire: {{ nextFire.job_name }} at {{ toDateTimeString(nextFire.next_fire) }}{{ status.disabled ? ", not run while the job scheduler is stopped" : "" }}
      </q-tooltip>
    </q-chip>

    <!-- The tooltips sit on a wrapper: a disabled button gets no hover, so its reason would never show. -->
    <span v-if="$can('jobs', 'operate')" class="inline-block q-ml-xs">
      <q-btn
        :color="status.disabled ? 'green' : 'deep-orange'"
        :icon="status.disabled ? 'fas fa-play' : 'fas fa-stop'"
        :label="status.disabled ? 'Start scheduler' : 'Stop scheduler'"
        :disable="status.state === 'off' || busy"
        :loading="busy"
        rounded
        no-caps
        unelevated
        padding="0 12px"
        class="toggle-button"
        @click="toggle" />
      <q-tooltip v-if="status.state === 'off'">The job scheduler is disabled for this server in rapo.ini or in development mode.</q-tooltip>
      <q-tooltip v-else>{{ status.disabled ? "Scheduled jobs run again from now on" : "No scheduled job runs on any server until started again" }}</q-tooltip>
    </span>
  </div>
  <div v-else class="row items-center q-gutter-sm">
    <q-skeleton v-for="index in 4" :key="index" type="QChip" width="110px" />
  </div>
</template>

<script>
import { api, notifyError } from "../api";
import { jobSchedulerState } from "../constants";
import { durationText, toDateTimeString, toMillis, toTimeString } from "../utils/format";

export default {
  name: "JobSchedulerBar",
  props: {
    // job-scheduler-status, or null while it loads.
    status: { type: Object, default: null },
    // The job that fires next ({job_name, next_fire}), or null.
    nextFire: { type: Object, default: null },
    // Server clock minus browser clock (ms), and the browser's now, ticking.
    clockOffset: { type: Number, default: 0 },
    now: { type: Number, required: true },
  },
  emits: ["changed"],
  data() {
    return { busy: false };
  },
  computed: {
    state() {
      return jobSchedulerState(this.status.state);
    },
    holder() {
      return this.status.holder || {};
    },
    manager() {
      return this.status.manager || { running: [], queued: [], capacity: 0 };
    },
    slotShare() {
      return this.manager.capacity ? Math.min(100, (100 * this.manager.running.length) / this.manager.capacity) : 0;
    },
    slotsFull() {
      return this.manager.capacity > 0 && this.manager.running.length >= this.manager.capacity;
    },
  },
  methods: {
    toDateTimeString,
    toTimeString,
    // Seconds from a naive server datetime to now (or from now to it, ahead), on the server's clock.
    seconds(value, ahead = false) {
      const delta = (toMillis(value) - (this.now + this.clockOffset)) / 1000;
      return Math.max(0, Math.round(ahead ? delta : -delta));
    },
    age(value) {
      return durationText(this.seconds(value));
    },
    until(value) {
      return durationText(this.seconds(value, true));
    },
    toggle() {
      const stopping = !this.status.disabled;
      this.$q
        .dialog({
          title: stopping ? "Stop the job scheduler" : "Start the job scheduler",
          message: stopping
            ? "Scheduled jobs will not run on any server until the job scheduler is started again. Running and queued runs continue, and jobs can still be run by hand. The scheduler of the controls is not affected."
            : "Scheduled jobs will run again from now on.",
          cancel: true,
          persistent: true,
        })
        .onOk(() => this.submit(stopping));
    },
    async submit(stopping) {
      this.busy = true;
      try {
        await api(stopping ? "job-scheduler-stop" : "job-scheduler-start", { method: "POST" });
        this.$q.notify({ type: "positive", message: stopping ? "Job scheduler stopped" : "Job scheduler started" });
      } catch (error) {
        notifyError(stopping ? "Failed to stop the job scheduler." : "Failed to start the job scheduler.", error);
      } finally {
        this.busy = false;
      }
      this.$emit("changed");
    },
  },
};
</script>

<style scoped>
.status-bar {
  flex-wrap: wrap;
  justify-content: flex-end;
  /* A little lower than centered on the title's tall line. */
  position: relative;
  top: 4px;
  font-size: 14px;
  letter-spacing: normal;
  line-height: normal;
}
.status-chip {
  margin: 2px 3px;
}
/* Long server and job names are cut; the tooltips name them in full. */
.chip-name {
  max-width: 170px;
}
.state-dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: currentColor;
}
.state-dot--live {
  animation: state-pulse 1.8s ease-out infinite;
}
@keyframes state-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.9);
  }
  70% {
    box-shadow: 0 0 0 7px rgba(255, 255, 255, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(255, 255, 255, 0);
  }
}
/* As tall as the chips beside it. */
.toggle-button {
  height: 32px;
  min-height: 32px;
  font-size: 14px;
}
.toggle-button :deep(.q-icon) {
  font-size: 12px;
}
@media (prefers-reduced-motion: reduce) {
  .state-dot--live {
    animation: none;
  }
}
</style>
