# AdSentry

Campaign anomaly detection and log search for a multi-tenant ad platform — the
"something broke at 2am, tell me before the client does" layer that sits between
platform syncs and the account team.

AdSentry watches daily delivery metrics for every campaign in every tenant,
turns statistical and rule-based findings into **stateful incidents**, and gives
you a log search that answers *why* without standing up an ELK cluster.

```bash
make install && make run     # http://127.0.0.1:8030
```

Two seeded tenants, six campaigns, a realistic incident feed, and 260 searchable
log events. No external services, no Docker required to look at it.

---

## Why this exists

A media buyer's morning is triage: which of the 400 campaigns in my book are
quietly losing money, and which of those is a *tracking* problem rather than a
*performance* problem? Getting that wrong is expensive in both directions —
alert on noise and the team mutes the channel; miss a dark tracking pixel and
you burn a day of budget with nothing to show the client.

The engine here is deliberately **not** a black box. Two detectors, both pure
functions of a time series, with thresholds you can explain in a stand-up:

| Detector | Answers | Method |
| --- | --- | --- |
| Robust statistics | "Is this number weird for *this* campaign?" | Modified z-score vs. same-weekday baseline |
| Tracking-health rules | "Is this impossible?" | Absolute conditions (zero delivery, dark pixel, click loss) |

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
 platform sync ───▶ │  POST /api/v1/metrics/batch                  │
 (at-least-once)    │    upsert on (account, campaign, day)        │
                    └───────────────────┬──────────────────────────┘
                                        │
                    ┌───────────────────▼──────────────────────────┐
                    │  detection.py                                │
                    │    robust_z()   rules()                      │
                    │    → Finding[]  (pure, no I/O, no clock)     │
                    └───────────────────┬──────────────────────────┘
                                        │
                    ┌───────────────────▼──────────────────────────┐
                    │  incidents.py                                │
                    │    open / bump occurrences / auto-resolve    │
                    │    one open row per (campaign, kind)         │
                    └───────────────────┬──────────────────────────┘
                                        │
   dashboard ◀────── GET /incidents ────┘

 log shipper ──▶ POST /logs/batch ──▶ log_tokens (inverted index)
                                           │
              GET /logs?q=postback+timeout ─┘  indexed join + HAVING count
```

Everything is tenant-scoped by `account_id` taken from an authenticated header,
never from the request body. The demo resolves `X-Account-Id`; `ADSENTRY_DEMO=0`
requires a per-tenant `X-Api-Key` and the dashboard demo switcher is hidden.

---

## Detection design

### 1. Robust z-score, not mean/stddev

```
z = (x - median) / (1.4826 × MAD)
```

Mean and standard deviation have a fatal property for this use case: one
Black-Friday spike inflates σ enough to hide the next two weeks of real
anomalies. Median and MAD do not move when a minority of points are extreme.
The 1.4826 constant puts MAD on the same scale as σ for normally distributed
data, and **3.5** is the Iglewicz–Hoaglin cut-off for flagging outliers in
small samples.

Three guards keep the alerts actionable:

- **Same-weekday baseline.** With ≥3 same-weekday points, compare Sunday to
  Sundays. Ad delivery has a strong weekly shape; comparing Sunday to Tuesday
  produces a "drop" every weekend.
- **Minimum sample size.** No score below 5 baseline points. A z-score from
  three observations is a random number generator with a confidence interval.
- **Materiality floor.** Require ≥25% relative change *as well as* z ≥ 3.5, so a
  very low-variance series cannot alert on a 2% wobble.
- **Volume floors.** Impressions/clicks need ≥100 impressions and ≥20 clicks
  before their ratios are scored; 0 clicks from 3 impressions is not a signal.

When MAD is zero (a perfectly flat history) the scale falls back to 5% of the
median level rather than dividing by zero — otherwise every trivial deviation
scores infinity.

### 2. Tracking-health rules

Statistics cannot see a broken pixel: a campaign losing 100% of its conversions
has *no variance* to compare against. These are absolute conditions:

| Kind | Fires when | Usually means |
| --- | --- | --- |
| `NO_DATA` | Active campaign, no metric row today | Sync job died, or the campaign was archived upstream |
| `SPEND_WITHOUT_DELIVERY` | Spend > 0, impressions = 0 | Billing without serving — the worst kind of bug |
| `TRACKING_DARK` | Sustained clicks, zero conversions, prior conversions | Pixel/postback broken, not a performance problem |
| `TOTAL_CLICK_LOSS` | Impressions with literally zero clicks | Broken landing page, rejected creative, bot filter change |
| `CONVERSION_COLLAPSE` | Conversions down >80% vs. baseline | Offer/checkout break, or attribution window changed |
| `CREATIVE_FATIGUE` | CTR declining across consecutive days | Normal wear — worth a creative refresh, not a page |
| `<METRIC>_SPIKE` / `_DROP` | Robust z-score breach | Spend, clicks, conversions or CTR moved abnormally |

Each finding carries `observed`, `baseline`, `z`, a human message and a detail
string. The dashboard shows the number, not just a red dot, because "clicks
dropped 61% vs. the last four Mondays" is a diagnosis and "anomaly detected" is
not.

### 3. Incidents are state, not events

One open incident per `(campaign, kind)`. Re-detection bumps `occurrences` and
`last_seen_day` instead of inserting a row. When a detector stops firing, the
incident is auto-resolved with the day it was last seen, so the feed means
"currently broken" and history stays queryable.

This is enforced in two places on purpose: the service collapses findings
before writing, and the SQL Server schema has a partial unique index on open
rows (`ux_incident_open_campaign_kind`). Application bugs should not be able to
create an alert storm.

Root-cause suppression matters too: when impressions are zero, AdSentry does
not also report a CTR drop, a click drop, and a spend anomaly. One incident,
one cause. The dashboard sorts critical-first, and the demo seeds one
`TRACKING_DARK` and one `SPEND_WITHOUT_DELIVERY` so the distinction is visible
without editing data.

---

## Log search without Elasticsearch

Ops questions are token questions: *"show me errors from the postback worker in
the last hour"*. A `LIKE '%postback%'` scan over 20M rows is a table scan with
extra steps.

AdSentry keeps an inverted index in the same database:

```sql
-- one row per (event, token), written at ingest
SELECT e.* FROM log_events e
JOIN log_tokens t ON t.event_id = e.id
WHERE t.account_id = :account
  AND t.token IN ('postback', 'timeout')
GROUP BY e.id
HAVING COUNT(DISTINCT t.token) = 2      -- AND semantics across terms
ORDER BY e.ts DESC
LIMIT 50;
```

The covering index `(account_id, token, event_id)` means the search seeks
straight to the matching postings and never reads the event table until the
final fetch. Structured filters (`level`, `source`, `campaign`, time window) are
applied as indexed predicates alongside.

`GET /logs/stats` reports event and token counts so you can see index growth per
tenant, and the search response carries its own `took_ms` so a slow query is
visible in the UI rather than only in a dashboard nobody opens.

Ingest is idempotent on `(account_id, event_uid)`: log shippers retry, and a
retry must not duplicate a postback error into the feed.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness; no database access |
| `GET` | `/readyz` | Readiness; checks the DB round-trip |
| `GET` | `/metrics` | Prometheus text exposition (request counts, latency) |
| `POST` | `/api/v1/campaigns` | Create a campaign |
| `GET` | `/api/v1/campaigns` | Health cards: today vs. baseline, open incident count, 14-day sparkline |
| `GET` | `/api/v1/campaigns/{id}/series` | Daily series for the drawer chart |
| `POST` | `/api/v1/metrics` | Upsert one day (idempotent) |
| `POST` | `/api/v1/metrics/batch` | Upsert up to 5,000 days |
| `POST` | `/api/v1/incidents/evaluate` | Run detection for the tenant (or one campaign) |
| `GET` | `/api/v1/incidents` | Feed: open by default, critical-first |
| `POST` | `/api/v1/incidents/{id}/resolve` | Resolve (idempotent — resolving twice is not an error) |
| `POST` | `/api/v1/logs` | Ingest one event, indexed on write |
| `POST` | `/api/v1/logs/batch` | Ingest up to 1,000 events |
| `GET` | `/api/v1/logs` | Token search + level filter, newest first |
| `GET` | `/api/v1/logs/stats` | Event/token counts, level breakdown |
| `GET` | `/api/v1/demo/accounts` | Demo-only tenant switcher |

Interactive docs at `/docs`.

```bash
# Ingest a day
curl -s -X POST http://127.0.0.1:8030/api/v1/metrics \
  -H "X-Account-Id: 1" -H "Content-Type: application/json" \
  -d '{"campaign_external_id":"cmp-1001","day":"2026-10-17",
       "impressions":48210,"clicks":934,"conversions":41,"spend_cents":38600}'

# Evaluate and inspect
curl -s -X POST http://127.0.0.1:8030/api/v1/incidents/evaluate \
  -H "X-Account-Id: 1"
curl -s -H "X-Account-Id: 1" "http://127.0.0.1:8030/api/v1/incidents?severity=critical"

# Token search
curl -s -H "X-Account-Id: 1" \
  "http://127.0.0.1:8030/api/v1/logs?q=postback%20timeout&level=ERROR&limit=20"
```

---

## Dashboard

Single-page dashboard, no build step: plain ES modules, server-served, ~600
lines of hand-written JS.

- **Campaign health grid** — today vs. baseline with a 14-day inline sparkline,
  colour-coded by z-score direction; click through to the day-by-day series.
- **Incident feed** — critical first, grouped by campaign, with occurrence count
  and the actual observed/baseline values; resolve inline.
- **Log explorer** — query bar with token AND semantics, level filter, and the
  server-reported query time; each result expands to its structured fields.
- **"Run detection"** button — calls `evaluate` and refreshes the feed, so the
  demo shows detect → triage → resolve without restarting anything.

---

## Tests

`make test` — **39 tests**, all passing on Python 3.11 and 3.12.

| File | Covers |
| --- | --- |
| `tests/test_detection.py` | Robust z math (median/MAD, MAD=0 fallback, min-sample guard), every rule kind, spike vs. drop direction, weekday-aware baseline, no duplicate findings |
| `tests/test_api.py` | Metric upsert idempotency, incident open/bump/resolve lifecycle, evaluation endpoint, log ingest + search semantics, pagination, validation errors |
| `tests/test_tenant_isolation.py` | Campaigns, metrics, incidents and logs invisible across tenants; foreign IDs 404 not 403; token search never crosses accounts |
| `tests/test_detection_bdd.py` | Gherkin scenarios in `tests/features/detection.feature` — "sustained click loss raises an incident", "an ongoing issue does not open a second incident", "a healthy campaign stays quiet" |

The BDD layer exists because "did we page someone for nothing?" is a product
question, not a unit question. Scenarios are written the way an account manager
would describe the failure, and the step definitions drive the real service.

```gherkin
Scenario: An ongoing problem does not page twice
  Given a campaign with sustained clicks and no conversions
  When detection runs on two consecutive days
  Then exactly one incident is open
  And its occurrence count is 2
```

---

## Benchmarks

`make bench` (Windows 11, Python 3.12, SQLite, single process — the numbers are
about algorithmic shape, not absolute throughput; SQL Server with the indexes in
`migrations/001_init.sql` is the tuned target):

| Scenario | Result |
| --- | --- |
| Evaluate 1,000 campaigns × 26 days (26k metric rows) | **246 ms** p50 |
| Per-campaign detection | ~0.25 ms |
| Token search, 20k events / 120k tokens, no filter | **7.3 ms** |
| Token AND search (2 terms) over the same corpus | **72.9 ms** |

The detector is O(days) per campaign with a bounded baseline window, so cost
scales linearly with the number of active campaigns — 10k campaigns is ~2.5 s of
a scheduled job, which is the right shape for a nightly plus hourly incremental
run. Search cost is driven by posting-list size, which is why `level` and time
range are applied *before* the join in the query planner.

---

## Design decisions

**Why SQL for the inverted index instead of shipping to ELK?**
A second cluster is a second thing to page about. For ≤10M events per tenant the
`(account, token, event_id)` covering index gives sub-100ms queries with zero
operational surface. The trade-off (no stemming, no phrase queries, no
relevance ranking) is written down instead of hidden; `migrations/001_init.sql`
documents the partitioning/columnstore path for when volume outgrows it.

**Why incidents as rows, not alerts as a stream?**
A stream of anomaly events is a log. State has to be derived at read time, which
means every dashboard, digest email and on-call integration re-implements
dedupe. Modelling incidents as mutable state with `first_seen`/`last_seen`/
`occurrences` gives one answer, computed once.

**Why money in integer minor units?**
`FLOAT` cannot represent 0.10 and SQL Server's `MONEY` silently rounds to four
decimals. Every spend value is `BIGINT` paise/cents; formatting happens at the
edge.

**Why both statistics and rules?**
They fail in opposite directions. Statistics catch "this campaign is behaving
unlike itself"; rules catch "this campaign cannot possibly be healthy" even when
the history is too short or too flat to score. A production alerting system
needs both, and each has to be explainable to a non-engineer.

**Why does evaluation never run on read?**
`GET /campaigns` returns a stored health snapshot; detection runs on ingest or
via `POST /incidents/evaluate`. Read paths stay fast and predictable, and the
same evaluation can be replayed over historical data without touching request
latency.

**Tenant isolation is a query-shape property.** Every repository function takes
`account_id` as its first argument and every statement filters on it, including
`UPDATE`/`DELETE`. `tests/test_tenant_isolation.py` asserts cross-tenant IDs
return 404 rather than 403, so an attacker cannot distinguish "exists in another
tenant" from "does not exist" — and a missing `WHERE` fails a test instead of a
customer.

---

## Limitations

- **Detection is daily-granularity.** Intraday pacing failures are the next
  iteration; the detector is already a pure function of a series, so an hourly
  series drops in without changing the incident layer.
- **No seasonality model beyond weekday.** Holidays and promo calendars need an
  explicit exclusion list; a robust baseline reduces but does not remove the
  false positives they cause.
- **Search has no stemming or synonyms.** `postback` does not match
  `postbacks`. Acceptable for ops queries; would need a real analyzer for
  free-text client search.
- **Auth is header-based for the demo.** `X-Account-Id` is replaced by API-key
  hashing (`ADSENTRY_DEMO=0`) and belongs behind the platform's gateway JWT in
  production.

## Layout

```
src/adsentry/
  detection.py     # robust z + rules, pure functions
  incidents.py     # open/bump/auto-resolve lifecycle
  logsearch.py     # tokenizer, index writes, indexed query builder
  api/             # campaigns, metrics, incidents, logs, system
  schemas.py       # pydantic request/response contracts
  seed.py          # two tenants, six campaigns, planted failures
tests/             # 39 tests incl. Gherkin scenarios
web/               # hand-written ES module dashboard
migrations/        # SQL Server 2022 DDL
```
