import { dateTime } from '../format.js';

export default {
  name: 'LogExplorer',
  props: {
    result: { type: Object, default: null },
    query: { type: String, default: '' },
    level: { type: String, default: '' },
    loading: { type: Boolean, default: false },
  },
  emits: ['search', 'update:query', 'update:level'],
  methods: {
    dateTime,
    fieldSummary(fields) {
      const entries = Object.entries(fields || {});
      return entries.length ? `${entries.length} field${entries.length === 1 ? '' : 's'}` : '';
    },
    // Split the message on matched tokens so they can be highlighted without
    // v-html (the log content is user data; it must never be interpreted).
    segments(message) {
      const tokens = (this.result && this.result.tokens) || [];
      if (!tokens.length) return [{ text: message, hit: false }];

      const escaped = tokens.map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
      const pattern = new RegExp(`(${escaped.join('|')})`, 'gi');
      const parts = message.split(pattern);

      return parts
        .filter((part) => part !== '')
        .map((part) => ({
          text: part,
          hit: tokens.some((token) => token.toLowerCase() === part.toLowerCase()),
        }));
    },
  },
  template: `
    <section class="panel" aria-labelledby="logs-title">
      <div class="panel-head">
        <div class="panel-title" id="logs-title">Log explorer</div>
        <div class="panel-meta">inverted index · AND search</div>
      </div>

      <div class="log-toolbar">
        <div class="log-search">
          <input
            :value="query"
            type="search"
            placeholder="search logs — e.g. postback signature"
            aria-label="Search logs"
            @input="$emit('update:query', $event.target.value)"
            @keyup.enter="$emit('search')"
          />
          <button class="primary" type="button" :disabled="loading" @click="$emit('search')">
            {{ loading ? 'Searching…' : 'Search' }}
          </button>
        </div>
        <select
          :value="level"
          aria-label="Log level"
          @change="$emit('update:level', $event.target.value); $emit('search')"
        >
          <option value="">All levels</option>
          <option value="debug">Debug</option>
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="error">Error</option>
          <option value="critical">Critical</option>
        </select>
      </div>

      <div v-if="result" class="log-meta">
        <span>{{ result.total.toLocaleString('en-IN') }} matching events</span>
        <span>{{ result.took_ms }} ms</span>
        <span v-if="result.tokens.length">tokens: {{ result.tokens.join(' + ') }}</span>
        <span v-else>no query — newest first</span>
      </div>

      <div v-if="result && result.events.length" class="log-list">
        <div v-for="event in result.events" :key="event.id" class="log-row">
          <span class="log-time">{{ dateTime(event.ts) }}</span>
          <span class="log-level" :class="event.level">{{ event.level }}</span>
          <span class="log-source" :title="event.source">{{ event.source }}</span>
          <div>
            <div class="log-message">
              <template v-for="(segment, index) in segments(event.message)" :key="index">
                <mark v-if="segment.hit">{{ segment.text }}</mark>
                <span v-else>{{ segment.text }}</span>
              </template>
            </div>
            <details v-if="fieldSummary(event.fields)" class="log-fields">
              <summary>{{ event.campaign_external_id || 'no campaign' }} · {{ fieldSummary(event.fields) }}</summary>
              <pre>{{ JSON.stringify(event.fields, null, 2) }}</pre>
            </details>
          </div>
        </div>
      </div>

      <div v-else-if="result" class="empty">
        <strong>No matching events</strong>
        Try a broader query, or clear the level filter.
      </div>

      <div v-else class="empty">
        <div class="skeleton" style="width: 70%; margin: 0 auto 10px"></div>
        <div class="skeleton" style="width: 45%; margin: 0 auto"></div>
      </div>
    </section>
  `,
};
