-- AdSentry - SQL Server 2022 schema
--
-- The demo builds this schema from SQLAlchemy metadata so `pip install` + run is
-- enough. This file is the equivalent DDL for the deployment target DeltaX runs
-- (MS SQL Server), with the parts that matter in production called out:
--   * the metric fact table is unique on (day, campaign) so late platform
--     restatements upsert instead of double-counting
--   * incidents are stateful rows with a partial unique index guaranteeing at
--     most one *open* incident per (campaign, kind) - the database enforces the
--     "don't page the same person every morning" rule, not application code
--   * log search is an inverted index in plain SQL: log_tokens + HAVING count,
--     which is the ELK idea without a second cluster to operate
--
-- Note on the token table: it is intentionally narrow. One row per (event,
-- token) is a few hundred bytes; 1M events x ~40 tokens is ~40M rows, which SQL
-- Server handles comfortably with the covering index below. If volume grows
-- past that, partition by ts month and move to a columnstore index.

CREATE TABLE accounts (
    id            BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_accounts PRIMARY KEY,
    name          NVARCHAR(120) NOT NULL,
    slug          NVARCHAR(60)  NOT NULL,
    api_key_hash  CHAR(64)      NOT NULL,            -- SHA-256 hex, never the key
    plan          NVARCHAR(30)  NOT NULL CONSTRAINT df_accounts_plan DEFAULT 'growth',
    created_at    DATETIME2(3)  NOT NULL CONSTRAINT df_accounts_created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_accounts_slug UNIQUE (slug)
);

CREATE TABLE campaigns (
    id          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_campaigns PRIMARY KEY,
    account_id  BIGINT        NOT NULL,
    external_id NVARCHAR(80)  NOT NULL,
    name        NVARCHAR(160) NOT NULL,
    channel     NVARCHAR(40)  NOT NULL,
    status      NVARCHAR(20)  NOT NULL CONSTRAINT df_campaigns_status DEFAULT 'active',
    created_at  DATETIME2(3)  NOT NULL CONSTRAINT df_campaigns_created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT fk_campaigns_account FOREIGN KEY (account_id)
        REFERENCES accounts (id) ON DELETE CASCADE,
    CONSTRAINT uq_campaigns_account_external UNIQUE (account_id, external_id),
    CONSTRAINT ck_campaigns_status CHECK (status IN ('active', 'paused', 'ended'))
);

CREATE INDEX ix_campaigns_account_status ON campaigns (account_id, status);

CREATE TABLE metric_days (
    id            BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_metric_days PRIMARY KEY,
    account_id    BIGINT       NOT NULL,
    campaign_id   BIGINT       NOT NULL,
    [day]         DATE         NOT NULL,
    impressions   BIGINT       NOT NULL CONSTRAINT df_metric_impressions DEFAULT 0,
    clicks        BIGINT       NOT NULL CONSTRAINT df_metric_clicks DEFAULT 0,
    conversions   BIGINT       NOT NULL CONSTRAINT df_metric_conversions DEFAULT 0,
    spend_cents   BIGINT       NOT NULL CONSTRAINT df_metric_spend DEFAULT 0,
    CONSTRAINT fk_metric_account FOREIGN KEY (account_id)
        REFERENCES accounts (id) ON DELETE CASCADE,
    CONSTRAINT fk_metric_campaign FOREIGN KEY (campaign_id)
        REFERENCES campaigns (id) ON DELETE CASCADE,
    -- The restatement guard: a re-synced day replaces, never adds.
    CONSTRAINT uq_metric_account_campaign_day UNIQUE (account_id, campaign_id, [day]),
    CONSTRAINT ck_metric_non_negative CHECK (
        impressions >= 0 AND clicks >= 0 AND conversions >= 0 AND spend_cents >= 0
    )
);

-- Covers the baseline read: last N days for one campaign, newest first.
CREATE INDEX ix_metric_campaign_day ON metric_days (campaign_id, [day] DESC) INCLUDE (clicks, spend_cents);
CREATE INDEX ix_metric_account_day ON metric_days (account_id, [day] DESC);

CREATE TABLE incidents (
    id             BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_incidents PRIMARY KEY,
    account_id     BIGINT        NOT NULL,
    campaign_id    BIGINT        NOT NULL,
    kind           NVARCHAR(40)  NOT NULL,
    severity       NVARCHAR(10)  NOT NULL,
    metric         NVARCHAR(20)  NOT NULL,
    first_seen_day DATE          NOT NULL,
    last_seen_day  DATE          NOT NULL,
    occurrences    INT           NOT NULL CONSTRAINT df_incident_occurrences DEFAULT 1,
    message        NVARCHAR(MAX) NOT NULL,
    detail         NVARCHAR(MAX) NULL,
    created_at     DATETIME2(3)  NOT NULL CONSTRAINT df_incident_created DEFAULT SYSUTCDATETIME(),
    resolved_at    DATETIME2(3)  NULL,
    CONSTRAINT fk_incident_account FOREIGN KEY (account_id)
        REFERENCES accounts (id) ON DELETE CASCADE,
    CONSTRAINT fk_incident_campaign FOREIGN KEY (campaign_id)
        REFERENCES campaigns (id) ON DELETE CASCADE,
    CONSTRAINT ck_incident_severity CHECK (severity IN ('warning', 'critical')),
    CONSTRAINT ck_incident_window CHECK (last_seen_day >= first_seen_day)
);

-- The rule that stops alert storms, enforced by the engine: only one open
-- incident may exist per (campaign, kind). A re-detected anomaly bumps
-- occurrences/last_seen_day instead of inserting a second row.
CREATE UNIQUE INDEX ux_incident_open_campaign_kind
    ON incidents (campaign_id, kind)
    WHERE resolved_at IS NULL;

-- The incident feed read path: open first, newest first.
CREATE INDEX ix_incident_feed ON incidents (account_id, resolved_at, last_seen_day DESC);

CREATE TABLE log_events (
    id                   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_log_events PRIMARY KEY,
    account_id           BIGINT        NOT NULL,
    event_uid            NVARCHAR(90)  NOT NULL,   -- de-dupes at-least-once shipping
    ts                   DATETIME2(3)  NOT NULL,
    level                NVARCHAR(10)  NOT NULL,
    source               NVARCHAR(40)  NOT NULL,
    campaign_external_id NVARCHAR(80)  NULL,
    message              NVARCHAR(MAX) NOT NULL,
    fields_json          NVARCHAR(MAX) NULL,        -- OpenSearch would index this per key
    CONSTRAINT fk_log_account FOREIGN KEY (account_id)
        REFERENCES accounts (id) ON DELETE CASCADE,
    CONSTRAINT uq_log_account_event UNIQUE (account_id, event_uid),
    CONSTRAINT ck_log_level CHECK (level IN ('DEBUG', 'INFO', 'WARN', 'ERROR'))
);

CREATE INDEX ix_log_account_time ON log_events (account_id, ts DESC);
CREATE INDEX ix_log_account_level ON log_events (account_id, level, ts DESC);
CREATE INDEX ix_log_campaign ON log_events (campaign_external_id, ts DESC)
    WHERE campaign_external_id IS NOT NULL;

CREATE TABLE log_tokens (
    id         BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_log_tokens PRIMARY KEY,
    account_id BIGINT       NOT NULL,
    event_id   BIGINT       NOT NULL,
    token      NVARCHAR(48) NOT NULL,
    CONSTRAINT fk_token_account FOREIGN KEY (account_id)
        REFERENCES accounts (id) ON DELETE CASCADE,
    CONSTRAINT fk_token_event FOREIGN KEY (event_id)
        REFERENCES log_events (id) ON DELETE CASCADE
);

-- Covering index for the search join: seek to the token(s), read event_id only.
CREATE INDEX ix_token_lookup ON log_tokens (account_id, token, event_id);

-- Retention: logs are operational exhaust, not financial records. Keep 30 days
-- hot (indexed), 90 days in an archive table, then drop. Incident rows are the
-- audit trail and are never deleted.
--
-- Nightly:
--   DELETE TOP (10000) FROM log_events
--   WHERE account_id = @account AND ts < DATEADD(DAY, -30, SYSUTCDATETIME());
-- Batch small to keep lock escalation and log growth predictable.
