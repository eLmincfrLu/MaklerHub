-- MaklerHub verilənlər bazası sxemi (SQLite)

CREATE TABLE IF NOT EXISTS users (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name          TEXT NOT NULL,
  last_name           TEXT NOT NULL DEFAULT '',
  email               TEXT NOT NULL UNIQUE,
  phone               TEXT NOT NULL DEFAULT '',
  password_hash       TEXT NOT NULL,
  role                TEXT NOT NULL DEFAULT 'makler',   -- alici | satici | makler | admin
  license_no          TEXT NOT NULL DEFAULT '',
  plan                TEXT NOT NULL DEFAULT 'pro',      -- start | pro | none
  status              TEXT NOT NULL DEFAULT 'active',   -- active | trial | suspended
  trial_ends          TEXT,
  avatar              TEXT,
  notif_match         INTEGER NOT NULL DEFAULT 1,
  notif_price         INTEGER NOT NULL DEFAULT 1,
  notif_daily         INTEGER NOT NULL DEFAULT 0,
  notif_sms           INTEGER NOT NULL DEFAULT 1,
  src_bina            INTEGER NOT NULL DEFAULT 1,
  src_tap             INTEGER NOT NULL DEFAULT 1,
  src_lalafo          INTEGER NOT NULL DEFAULT 1,
  twofa               INTEGER NOT NULL DEFAULT 0,
  password_changed_at TEXT,
  created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  number        INTEGER NOT NULL,
  name          TEXT NOT NULL,
  phone         TEXT NOT NULL DEFAULT '',
  note          TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'active',          -- active | deal | archived
  last_contact  TEXT,
  closed_at     TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_clients_user ON clients(user_id);

CREATE TABLE IF NOT EXISTS profiles (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  client_id      INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  district       TEXT NOT NULL,
  rooms          INTEGER,
  rooms_plus     INTEGER NOT NULL DEFAULT 0,
  price_min      INTEGER NOT NULL,
  price_max      INTEGER NOT NULL,
  deal_type      TEXT NOT NULL DEFAULT 'sale',           -- sale | rent
  property_type  TEXT NOT NULL DEFAULT '',               -- '' | Yeni tikili | Köhnə tikili | Studiya | Ofis | Həyət evi
  status         TEXT NOT NULL DEFAULT 'active',         -- active | paused | closed
  last_checked   TEXT,
  paused_at      TEXT,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_profiles_user ON profiles(user_id);

CREATE TABLE IF NOT EXISTS sources (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  key         TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  logo        TEXT NOT NULL,
  base_url    TEXT NOT NULL DEFAULT '',
  enabled     INTEGER NOT NULL DEFAULT 1,
  status      TEXT NOT NULL DEFAULT 'up',                -- up | warn | down
  rate_limit  INTEGER,                                   -- NULL = ümumi ayar
  last_sync   TEXT,
  sort        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sync_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  started_at  TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  latency     REAL NOT NULL DEFAULT 0,
  duration    INTEGER NOT NULL DEFAULT 0,
  checked     INTEGER NOT NULL DEFAULT 0,
  new_count   INTEGER NOT NULL DEFAULT 0,
  updated     INTEGER NOT NULL DEFAULT 0,
  removed     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_runs_src_time ON sync_runs(source_id, started_at);

CREATE TABLE IF NOT EXISTS listings (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  external_id  TEXT NOT NULL,
  url          TEXT,
  title        TEXT NOT NULL,
  suffix       TEXT NOT NULL DEFAULT '',
  district     TEXT NOT NULL,
  rooms        INTEGER,
  area         REAL NOT NULL,
  floor        INTEGER,
  floors       INTEGER,
  price        INTEGER NOT NULL,
  deal_type    TEXT NOT NULL,
  building     TEXT NOT NULL DEFAULT '',
  renovation   TEXT NOT NULL DEFAULT '',
  metro_m      INTEGER,
  active       INTEGER NOT NULL DEFAULT 1,
  posted_at    TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE(source_id, external_id)
);
CREATE INDEX IF NOT EXISTS ix_listings_active ON listings(active, deal_type, district);

CREATE TABLE IF NOT EXISTS price_history (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  listing_id   INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  price        INTEGER NOT NULL,
  recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ph_listing ON price_history(listing_id);

CREATE TABLE IF NOT EXISTS matches (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  score       INTEGER NOT NULL,
  reasons     TEXT NOT NULL DEFAULT '[]',
  created_at  TEXT NOT NULL,
  UNIQUE(profile_id, listing_id)
);
CREATE INDEX IF NOT EXISTS ix_matches_time ON matches(created_at);

CREATE TABLE IF NOT EXISTS logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT NOT NULL,
  level       TEXT NOT NULL,          -- ok | warn | err | info
  source      TEXT NOT NULL,          -- Bina.az | Tap.az | Lalafo | Uyğunlaşdırma | Makler | Sistem
  message     TEXT NOT NULL,
  details     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_logs_time ON logs(created_at);

CREATE TABLE IF NOT EXISTS settings (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  email       TEXT NOT NULL,
  ip          TEXT NOT NULL DEFAULT '',
  success     INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attempts ON login_attempts(email, created_at);
