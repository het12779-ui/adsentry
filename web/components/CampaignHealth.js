import Sparkline from './Sparkline.js';
import { count, money, percent } from '../format.js';

export default {
  name: 'CampaignHealth',
  components: { Sparkline },
  props: {
    campaigns: { type: Array, default: () => [] },
    loading: { type: Boolean, default: false },
  },
  methods: {
    count,
    money,
    percent,
    status(row) {
      if (row.critical_incidents > 0) return 'critical';
      if (row.open_incidents > 0) return 'warning';
      return 'ok';
    },
    statusLabel(row) {
      const status = this.status(row);
      if (status === 'critical') return `${row.critical_incidents} critical`;
      if (status === 'warning') return `${row.open_incidents} open`;
      return 'Healthy';
    },
  },
  template: `
    <section class="panel" aria-labelledby="campaigns-title">
      <div class="panel-head">
        <div class="panel-title" id="campaigns-title">Delivery health</div>
        <div class="panel-meta">{{ campaigns.length }} campaigns</div>
      </div>

      <table v-if="campaigns.length">
        <thead>
          <tr>
            <th scope="col">Campaign</th>
            <th scope="col">Impressions</th>
            <th scope="col">CTR</th>
            <th scope="col">CVR</th>
            <th scope="col">Spend</th>
            <th scope="col">14-day trend</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in campaigns" :key="row.id">
            <td>
              <div class="campaign-name">{{ row.name }}</div>
              <div class="campaign-id"><span class="chip">{{ row.channel }}</span> {{ row.external_id }}</div>
            </td>
            <td class="num">
              {{ count(row.impressions) }}
              <div class="num-sub">{{ count(row.clicks) }} clicks</div>
            </td>
            <td class="num">{{ percent(row.ctr) }}</td>
            <td class="num">{{ percent(row.cvr) }}</td>
            <td class="num">{{ money(row.spend_cents) }}</td>
            <td><sparkline :values="row.sparkline" /></td>
            <td><span class="state" :class="status(row)">{{ statusLabel(row) }}</span></td>
          </tr>
        </tbody>
      </table>

      <div v-else-if="loading" class="empty">
        <div class="skeleton" style="width: 55%; margin: 0 auto 10px"></div>
        <div class="skeleton" style="width: 35%; margin: 0 auto"></div>
      </div>

      <div v-else class="empty">
        <strong>No campaigns yet</strong>
        Create one with <code>POST /api/v1/campaigns</code>.
      </div>
    </section>
  `,
};
