import { kindLabel, shortDay } from '../format.js';

export default {
  name: 'IncidentFeed',
  props: {
    incidents: { type: Array, default: () => [] },
    resolvingId: { type: Number, default: null },
  },
  emits: ['resolve'],
  methods: { kindLabel, shortDay },
  template: `
    <section class="panel" aria-labelledby="incidents-title">
      <div class="panel-head">
        <div class="panel-title" id="incidents-title">Incidents</div>
        <div class="panel-meta">{{ incidents.length }} open</div>
      </div>

      <div v-if="incidents.length" class="alerts">
        <article v-for="incident in incidents" :key="incident.id" class="alert" :class="incident.severity">
          <div class="alert-bar" aria-hidden="true"></div>
          <div class="alert-body">
            <div class="alert-message">{{ incident.message }}</div>
            <div class="alert-meta">
              <span class="incident-kind">{{ kindLabel(incident.kind) }}</span>
              <span aria-hidden="true">·</span>
              <span>{{ shortDay(incident.last_seen_day) }}</span>
              <span v-if="incident.occurrences > 1">· {{ incident.occurrences }} days</span>
              <button
                class="resolve"
                type="button"
                :disabled="resolvingId === incident.id"
                @click="$emit('resolve', incident.id)"
              >
                {{ resolvingId === incident.id ? 'Resolving…' : 'Resolve' }}
              </button>
            </div>
          </div>
        </article>
      </div>

      <div v-else class="empty">
        <strong>Quiet</strong>
        No open incidents. Run detection to re-check every campaign.
      </div>
    </section>
  `,
};
