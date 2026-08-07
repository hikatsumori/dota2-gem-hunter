-- Dota 2 Steam Market Rare Socket Scanner Database Schema
-- Generated from Alembic migrations for reference

-- Items that can have sockets
CREATE TABLE items (
    id SERIAL PRIMARY KEY,
    market_hash_name VARCHAR(500) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    item_type VARCHAR(50),
    quality VARCHAR(50),
    rarity VARCHAR(50),
    can_have_sockets BOOLEAN NOT NULL DEFAULT FALSE,
    max_sockets INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Individual market listings
CREATE TABLE listings (
    id BIGSERIAL PRIMARY KEY,
    item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
    listing_id VARCHAR(100),
    price_usd NUMERIC(10, 2) NOT NULL,
    price_raw BIGINT NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
    seller_steamid VARCHAR(50),
    listing_url TEXT,
    raw_description TEXT,
    fetched_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP
);

-- Gems found in listings
CREATE TABLE gems (
    id SERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    gem_type VARCHAR(20) NOT NULL,
    gem_name VARCHAR(200) NOT NULL,
    socket_index INTEGER,
    is_rare BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Price history for trend analysis
CREATE TABLE price_history (
    id BIGSERIAL PRIMARY KEY,
    item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
    min_price_usd NUMERIC(10, 2),
    avg_price_usd NUMERIC(10, 2),
    max_price_usd NUMERIC(10, 2),
    listing_count INTEGER,
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Alert configuration
CREATE TABLE alert_rules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    gem_type VARCHAR(20),
    gem_names TEXT[],
    max_price_usd NUMERIC(10, 2),
    min_discount_pct NUMERIC(5, 2),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Sent alerts (deduplication)
CREATE TABLE alerts_sent (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT REFERENCES listings(id) ON DELETE SET NULL,
    rule_id INTEGER REFERENCES alert_rules(id) ON DELETE SET NULL,
    message_hash VARCHAR(64),
    sent_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Scan metrics for health monitoring
CREATE TABLE scan_metrics (
    id BIGSERIAL PRIMARY KEY,
    scan_type VARCHAR(50) NOT NULL,
    listings_scanned INTEGER NOT NULL DEFAULT 0,
    gems_found INTEGER NOT NULL DEFAULT 0,
    alerts_sent INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    duration_seconds NUMERIC(10, 3) NOT NULL DEFAULT 0,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_listings_item_fetched ON listings (item_id, fetched_at);
CREATE INDEX idx_gems_listing ON gems (listing_id);
CREATE INDEX idx_gems_rare ON gems (is_rare, gem_type) WHERE is_rare = TRUE;
CREATE INDEX idx_price_history_item_time ON price_history (item_id, recorded_at);
CREATE INDEX idx_alerts_listing ON alerts_sent (listing_id);
CREATE INDEX idx_scan_metrics_completed_at ON scan_metrics (completed_at);
CREATE INDEX idx_scan_metrics_type_time ON scan_metrics (scan_type, completed_at);