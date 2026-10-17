export const session = {
  accountId: null,
};

async function request(path, options = {}) {
  const headers = { Accept: 'application/json', ...(options.headers || {}) };
  if (session.accountId !== null) headers['X-Account-Id'] = String(session.accountId);

  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail = payload && (payload.detail || payload.message);
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`);
  }
  return payload;
}

export const api = {
  accounts: () => request('/api/v1/demo/accounts'),

  campaigns: (asOf) => request(`/api/v1/campaigns?as_of=${encodeURIComponent(asOf)}`),

  incidents: () => request('/api/v1/incidents?open=true&limit=50'),

  evaluate: (asOf) =>
    request(`/api/v1/incidents/evaluate?as_of=${encodeURIComponent(asOf)}`, { method: 'POST' }),

  resolve: (id) => request(`/api/v1/incidents/${id}/resolve`, { method: 'POST' }),

  logs: ({ q, level, limit = 60 }) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set('q', q);
    if (level) params.set('level', level);
    return request(`/api/v1/logs?${params.toString()}`);
  },

  logStats: () => request('/api/v1/logs/stats'),
};
