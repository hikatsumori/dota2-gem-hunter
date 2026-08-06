# Dota 2 Steam Market Rare Socket Scanner — Design Spec

**Date:** 2026-08-06  
**Status:** Approved for implementation

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    GITHUB ACTIONS (Cron)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │ Fetch       │→ │ Parse &     │→ │ Analyze &   │            │
│  │ Listings    │  │ Detect Gems │  │ Alert       │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
│         │               │               │                       │
│         ▼               ▼               ▼                       │
│  ┌─────────────────────────────────────────────┐              │
│  │         SUPABASE POSTGRESQL (Free Tier)     │              │
│  │  Listings • Gems • Price History • Alerts   │              │
│  └─────────────────────────────────────────────┘              │
│                              │                                │
│                              ▼                                │
│                    ┌───────────────┐                          │
│                    │ Discord       │                          │
│                    │ Webhook       │                          │
│                    └───────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

**Key decisions:**
- **GitHub Actions cron** (every 5 min) — free, reliable for light scans (~1 scan/min)
- **Supabase PostgreSQL** (free tier: 500MB) — persistent storage, survives workflow restarts
- **Steam WebAPI** + **HTML parsing fallback** — API for listings, HTML for gem/socket details
- **Discord webhook** — simple, reliable notifications
- **Hardcoded alert rules** in config file — simple, version-controlled

---

## 2. Project Structure

```
dota2-socket-scanner/
├── .github/
│   └── workflows/
│       └── scanner.yml           # GitHub Actions cron workflow
├── src/
│   ├── __init__.py
│   ├── config.py                 # All configuration (alert rules, API keys)
│   ├── steam/
│   │   ├── __init__.py
│   │   ├── client.py             # Steam WebAPI client
│   │   ├── parser.py             # HTML parser for gem/socket details
│   │   └── models.py             # Data models (Listing, Gem, Item)
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py         # DB connection pool
│   │   ├── schema.sql            # SQL schema
│   │   ├── repositories.py       # Data access layer
│   │   └── migrations/           # Alembic migrations
│   ├── analyzer/
│   │   ├── __init__.py
│   │   ├── price_analyzer.py     # Price comparison logic
│   │   └── gem_detector.py       # Rare socket/gem detection
│   ├── notifier/
│   │   ├── __init__.py
│   │   └── discord.py            # Discord webhook sender
│   └── main.py                   # Entry point for GH Action
├── tests/
│   ├── test_gem_detector.py
│   ├── test_price_analyzer.py
│   └── test_parser.py
├── alembic.ini
├── pyproject.toml
├── requirements.txt
├── dockerfile                    # Optional: for local dev
└── README.md
```

---

## 3. Steam APIs & Endpoints

| Purpose | Endpoint/Method | Notes |
|---------|-----------------|-------|
| **Market Search** | `GET https://steamcommunity.com/market/search/render/` | Public, no auth, supports query params |
| **Listing Details** | `GET https://steamcommunity.com/market/listings/{appid}/{market_hash_name}` | HTML page — parse for gem data |
| **Item Schema** | `GET https://api.steampowered.com/IEconItems_570/GetSchema/v1/` | WebAPI key needed, full item schema |
| **Asset Details** | `GET https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/` | For item details |

**Rate Limits (conservative):**
- Public market search: ~20 req/min
- Listing pages: ~10 req/min
- WebAPI: 100,000 req/day with key

**Gem Detection Strategy:**
1. Search market for socketable items (couriers, wards, etc.)
2. For each listing → fetch listing page HTML
3. Parse `market_listing_largeimage` and description for gem names
4. Match against known Ethereal/Prismatic gem database

---

## 4. Database Schema (PostgreSQL on Supabase)

```sql
-- Items that can have sockets
CREATE TABLE items (
    id SERIAL PRIMARY KEY,
    market_hash_name VARCHAR(500) UNIQUE NOT NULL,
    name VARCHAR(200) NOT NULL,
    item_type VARCHAR(50),           -- 'courier', 'ward', 'announcer', etc.
    quality VARCHAR(50),             -- 'Unusual', 'Normal', etc.
    rarity VARCHAR(50),
    can_have_sockets BOOLEAN DEFAULT FALSE,
    max_sockets INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Individual market listings
CREATE TABLE listings (
    id BIGSERIAL PRIMARY KEY,
    item_id INTEGER REFERENCES items(id),
    listing_id VARCHAR(100),         -- Steam listing ID if available
    price_usd DECIMAL(10,2) NOT NULL,
    price_raw INTEGER NOT NULL,      -- Price in cents
    currency VARCHAR(10) DEFAULT 'USD',
    seller_steamid VARCHAR(50),
    listing_url TEXT,
    raw_description TEXT,            -- Full HTML description
    fetched_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP
);

-- Gems found in listings
CREATE TABLE gems (
    id SERIAL PRIMARY KEY,
    listing_id BIGINT REFERENCES listings(id) ON DELETE CASCADE,
    gem_type VARCHAR(20) NOT NULL,   -- 'ethereal', 'prismatic', 'regular'
    gem_name VARCHAR(200) NOT NULL,
    socket_index INTEGER,            -- Which socket (0, 1, 2...)
    is_rare BOOLEAN DEFAULT FALSE,   -- Known valuable gem
    created_at TIMESTAMP DEFAULT NOW()
);

-- Price history for trend analysis
CREATE TABLE price_history (
    id BIGSERIAL PRIMARY KEY,
    item_id INTEGER REFERENCES items(id),
    min_price_usd DECIMAL(10,2),
    avg_price_usd DECIMAL(10,2),
    max_price_usd DECIMAL(10,2),
    listing_count INTEGER,
    recorded_at TIMESTAMP DEFAULT NOW()
);

-- Alert configuration (hardcoded rules in code, but schema for future)
CREATE TABLE alert_rules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    gem_type VARCHAR(20),            -- 'ethereal', 'prismatic', or NULL for any
    gem_names TEXT[],                -- Specific gems or empty for all
    max_price_usd DECIMAL(10,2),
    min_discount_pct DECIMAL(5,2),   -- % below average
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Sent alerts (deduplication)
CREATE TABLE alerts_sent (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT REFERENCES listings(id),
    rule_id INTEGER REFERENCES alert_rules(id),
    message_hash VARCHAR(64),        -- For deduplication
    sent_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_listings_item_fetched ON listings(item_id, fetched_at DESC);
CREATE INDEX idx_gems_listing ON gems(listing_id);
CREATE INDEX idx_gems_rare ON gems(is_rare, gem_type) WHERE is_rare = TRUE;
CREATE INDEX idx_price_history_item_time ON price_history(item_id, recorded_at DESC);
CREATE INDEX idx_alerts_listing ON alerts_sent(listing_id);
```

**Retention:** 30 days (auto-cleanup job in GitHub Actions)

---

## 5. Development Milestones

| Phase | Deliverable | Est. Effort |
|-------|-------------|-------------|
| **1. Foundation** | Project setup, config, DB schema, Steam client (search + listing fetch) | 1-2 days |
| **2. Gem Detection** | HTML parser for listing pages, gem database (ethereal/prismatic names), detector logic | 2-3 days |
| **3. Price Analysis** | Price history tracking, average calculation, discount detection, alert rules engine | 1-2 days |
| **4. Notifications** | Discord webhook, alert formatting, deduplication | 0.5 day |
| **5. GitHub Actions** | Workflow cron, secrets management, artifact retention | 0.5 day |
| **6. Testing & Deploy** | Unit tests, integration test, deploy to Supabase, verify end-to-end | 1 day |

**Total: ~6-9 days** for MVP

---

## 6. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Steam rate limits / IP blocks** | High | Respect limits, exponential backoff, rotate user agents, use WebAPI where possible |
| **HTML structure changes** | Medium | Robust selectors, fallback parsing, monitor for breakage |
| **Gem name variations** | Medium | Canonical gem name mapping, fuzzy matching (`rapidfuzz`) |
| **GitHub Actions 6hr timeout** | Low | Light scans fit; split into matrix if needed |
| **Supabase 500MB limit** | Low | 30-day retention + auto-cleanup |
| **Discord webhook rate limits** | Low | Batch alerts, respect 30 msg/min |
| **False positives** | Medium | Confidence scoring, curated gem database |

---

## 7. Accuracy & Reliability Improvements

1. **Gem Database**: Curated JSON of known Ethereal/Prismatic gems with canonical names + aliases
2. **Fuzzy Matching**: `rapidfuzz` for gem name matching against descriptions
3. **Confidence Scoring**: Exact match=1.0, fuzzy=0.7, partial=0.4
4. **Deduplication**: Hash alert content, don't re-send same opportunity within 24hr
5. **Health Monitoring**: Log scan metrics (listings checked, gems found, alerts sent) to DB
6. **Graceful Degradation**: If listing page fails, still record listing price; parse gems on retry
7. **Test Fixtures**: Save real listing HTML samples for regression testing parser

---

## 8. Configuration (Hardcoded in `config.py`)

```python
ALERT_RULES = [
    {
        "name": "Ethereal Gem Under $100",
        "gem_types": ["ethereal"],
        "max_price_usd": 100.0,
        "min_discount_pct": 30.0,  # 30% below average
    },
    {
        "name": "Prismatic Gem Under $150",
        "gem_types": ["prismatic"],
        "max_price_usd": 150.0,
        "min_discount_pct": 25.0,
    },
    {
        "name": "Any Rare Socket Deep Discount",
        "gem_types": ["ethereal", "prismatic"],
        "max_price_usd": 500.0,
        "min_discount_pct": 50.0,
    },
]

SCAN_CONFIG = {
    "item_types": ["courier", "ward", "announcer", "hud", "loading_screen"],
    "max_pages_per_type": 5,           # ~100 listings per type
    "request_delay_seconds": 3,        # Rate limiting
    "max_retries": 3,
}
```

---

## 9. Discord Alert Format

```
🔥 Rare Socket Opportunity

**Item:** Unusual Courier Name
**Gems:**
  • Ethereal: Trail of the Burning Doom
  • Prismatic: Ships in the Night

**Price:** $87.50
**Avg Price:** $175.00
**Discount:** 50% below average

**Market Link:** https://steamcommunity.com/market/listings/570/...

**Rule Matched:** Ethereal Gem Under $100
```