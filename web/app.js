import { createApp, computed, onMounted, onUnmounted, ref } from './vendor/vue.esm-browser.prod.js';
import { api, session } from './api.js';
import { count, money, percent, todayISO } from './format.js';
import CampaignHealth from './components/CampaignHealth.js';
import IncidentFeed from './components/IncidentFeed.js';
import LogExplorer from './components/LogExplorer.js';

const App = {
  components: { CampaignHealth, IncidentFeed, LogExplorer },

  setup() {
    const accounts = ref([]);
    const accountId = ref(null);
    const asOf = ref(todayISO());

    const campaigns = ref([]);
    const incidents = ref([]);
    const logStats = ref({ events: 0, tokens: 0, by_level: {} });
    const logResult = ref(null);

    const query = ref('');
    const level = ref('');
    const error = ref('');
    const loading = ref(true);
    const searching = ref(false);
    const evaluating = ref(false);
    const resolvingId = ref(null);
    const toast = ref('');

    let toastTimer = null;

    const criticalCount = computed(
      () => incidents.value.filter((incident) => incident.severity === 'critical').length
    );

    const spendToday = computed(() =>
      campaigns.value.reduce((total, row) => total + row.spend_cents, 0)
    );

    function notify(message) {
      toast.value = message;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => (toast.value = ''), 3200);
    }

    async function runSearch() {
      if (!accountId.value) return;
      searching.value = true;
      try {
        logResult.value = await api.logs({ q: query.value, level: level.value });
      } catch (err) {
        error.value = err.message;
      } finally {
        searching.value = false;
      }
    }

    async function load() {
      if (!accountId.value) return;
      loading.value = true;
      session.accountId = accountId.value;

      try {
        const [health, open, stats] = await Promise.all([
          api.campaigns(asOf.value),
          api.incidents(),
          api.logStats(),
        ]);
        campaigns.value = health;
        incidents.value = open;
        logStats.value = stats;
        error.value = '';
        await runSearch();
      } catch (err) {
        error.value = err.message;
      } finally {
        loading.value = false;
      }
    }

    async function evaluate() {
      evaluating.value = true;
      try {
        const result = await api.evaluate(asOf.value);
        await load();
        notify(
          result.opened > 0
            ? `${result.opened} new incident${result.opened === 1 ? '' : 's'}`
            : 'Detection clean — no new incidents'
        );
      } catch (err) {
        error.value = err.message;
      } finally {
        evaluating.value = false;
      }
    }

    async function resolveIncident(incidentId) {
      resolvingId.value = incidentId;
      try {
        await api.resolve(incidentId);
        incidents.value = incidents.value.filter((incident) => incident.id !== incidentId);
        notify('Incident resolved');
      } catch (err) {
        notify(err.message);
      } finally {
        resolvingId.value = null;
      }
    }

    async function boot() {
      try {
        accounts.value = await api.accounts();
        if (!accounts.value.length) {
          error.value =
            'Demo tenant switching is disabled. Set ADSENTRY_DEMO=1 for the standalone demo.';
          return;
        }
        accountId.value = accounts.value[0].id;
        session.accountId = accountId.value;
        await load();
      } catch (err) {
        error.value = err.message;
      } finally {
        loading.value = false;
      }
    }

    onMounted(boot);
    onUnmounted(() => clearTimeout(toastTimer));

    return {
      accounts,
      accountId,
      asOf,
      campaigns,
      incidents,
      logStats,
      logResult,
      query,
      level,
      error,
      loading,
      searching,
      evaluating,
      resolvingId,
      toast,
      criticalCount,
      spendToday,
      count,
      money,
      percent,
      load,
      evaluate,
      runSearch,
      resolveIncident,
    };
  },

  template: `
    <div :class="{ loading }">
      <header class="topbar">
        <div class="brand">
          <div class="brand-mark">AS</div>
          <div>
            <div class="brand-name">AdSentry</div>
            <div class="brand-sub">Delivery anomalies &amp; log intelligence</div>
          </div>
        </div>

        <div class="controls">
          <label class="field">
            <span>Account</span>
            <select v-model.number="accountId" @change="load" aria-label="Account">
              <option v-for="account in accounts" :key="account.id" :value="account.id">
                {{ account.name }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>As of</span>
            <input type="date" v-model="asOf" @change="load" aria-label="As of date" />
          </label>
          <button class="primary" type="button" :disabled="evaluating" @click="evaluate">
            {{ evaluating ? 'Scanning…' : 'Run detection' }}
          </button>
        </div>
      </header>

      <main>
        <div v-if="error" class="banner">
          <span>{{ error }}</span>
          <button type="button" @click="load">Retry</button>
        </div>

        <section class="kpis" aria-label="Monitoring summary">
          <div class="kpi">
            <div class="kpi-label">Campaigns monitored</div>
            <div class="kpi-value">{{ campaigns.length }}</div>
            <div class="kpi-foot">{{ money(spendToday) }} spent today</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Open incidents</div>
            <div class="kpi-value">{{ incidents.length }}</div>
            <div class="kpi-foot" :class="criticalCount ? 'is-danger' : 'is-ok'">
              {{ criticalCount ? criticalCount + ' critical' : 'nothing critical' }}
            </div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Log events indexed</div>
            <div class="kpi-value">{{ logStats.events.toLocaleString('en-IN') }}</div>
            <div class="kpi-foot">{{ logStats.tokens.toLocaleString('en-IN') }} tokens</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Detection window</div>
            <div class="kpi-value">14 days</div>
            <div class="kpi-foot">robust z ≥ 3.5, weekday aware</div>
          </div>
        </section>

        <div class="grid">
          <campaign-health :campaigns="campaigns" :loading="loading" />
          <incident-feed
            :incidents="incidents"
            :resolving-id="resolvingId"
            @resolve="resolveIncident"
          />
        </div>

        <div style="margin-top: 16px">
          <log-explorer
            :result="logResult"
            :query="query"
            :level="level"
            :loading="searching"
            @update:query="query = $event"
            @update:level="level = $event"
            @search="runSearch"
          />
        </div>
      </main>

      <footer class="app-footer">
        <span class="footer-brand">AdSentry</span>
        <span class="footer-rule"></span>
        <span>{{ campaigns.length }} campaigns monitored</span>
        <span>{{ incidents.length }} open incidents</span>
        <span>{{ logResult ? logResult.total : 0 }} log events</span>
        <span class="footer-gap"></span>
        <span class="footer-live" :class="{ 'is-down': error }">
          <i></i>{{ error ? 'API unreachable' : 'API connected' }}
        </span>
        <span class="footer-mono">as of {{ asOf }}</span>
      </footer>

      <div v-if="toast" class="toast" role="status">{{ toast }}</div>
    </div>
  `,
};

createApp(App).mount('#app');
