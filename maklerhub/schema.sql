-- MaklerHub verilənlər bazası sxemi (PostgreSQL / Neon)
-- Tarixlər "YYYY-MM-DD HH:MM:SS" formatlı TEXT kimi saxlanılır (Asia/Baku).

CREATE TABLE IF NOT EXISTS users (
  id                  SERIAL PRIMARY KEY,
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
  plan_expires_at     TEXT,                             -- lisenziyanın bitmə tarixi (NULL = məhdudiyyətsiz)
  license_id          INTEGER,                          -- qeydiyyatda istifadə olunan lisenziya
  created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
  id            SERIAL PRIMARY KEY,
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
  id             SERIAL PRIMARY KEY,
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
  -- Gün 4-7: yeni meyarlar (NULL = şərt qoyulmayıb)
  area_min       DOUBLE PRECISION,
  area_max       DOUBLE PRECISION,
  floor_min      INTEGER,
  floor_max      INTEGER,
  floor_not_first INTEGER NOT NULL DEFAULT 0,            -- 1-ci mərtəbə olmasın
  floor_not_last  INTEGER NOT NULL DEFAULT 0,            -- sonuncu mərtəbə olmasın
  renovation     TEXT,
  metro          TEXT,
  -- hər meyar üçün prioritet 1-5 ulduz və "mütləq" bayrağı
  prio_district  INTEGER NOT NULL DEFAULT 3,
  prio_price     INTEGER NOT NULL DEFAULT 3,
  prio_rooms     INTEGER NOT NULL DEFAULT 3,
  prio_area      INTEGER NOT NULL DEFAULT 3,
  prio_type      INTEGER NOT NULL DEFAULT 3,
  prio_renovation INTEGER NOT NULL DEFAULT 3,
  prio_metro     INTEGER NOT NULL DEFAULT 3,
  prio_floor     INTEGER NOT NULL DEFAULT 3,
  must_district  INTEGER NOT NULL DEFAULT 0,
  must_price     INTEGER NOT NULL DEFAULT 0,
  must_rooms     INTEGER NOT NULL DEFAULT 0,
  must_area      INTEGER NOT NULL DEFAULT 0,
  must_type      INTEGER NOT NULL DEFAULT 0,
  must_renovation INTEGER NOT NULL DEFAULT 0,
  must_metro     INTEGER NOT NULL DEFAULT 0,
  must_floor     INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_profiles_user ON profiles(user_id);

CREATE TABLE IF NOT EXISTS sources (
  id          SERIAL PRIMARY KEY,
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
  id          SERIAL PRIMARY KEY,
  source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  started_at  TEXT NOT NULL,
  ok          INTEGER NOT NULL,
  latency     DOUBLE PRECISION NOT NULL DEFAULT 0,
  duration    INTEGER NOT NULL DEFAULT 0,
  checked     INTEGER NOT NULL DEFAULT 0,
  new_count   INTEGER NOT NULL DEFAULT 0,
  updated     INTEGER NOT NULL DEFAULT 0,
  removed     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_runs_src_time ON sync_runs(source_id, started_at);

CREATE TABLE IF NOT EXISTS listings (
  id           SERIAL PRIMARY KEY,
  source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  external_id  TEXT NOT NULL,
  url          TEXT,
  title        TEXT NOT NULL,
  suffix       TEXT NOT NULL DEFAULT '',
  district     TEXT NOT NULL,
  rooms        INTEGER,
  area         DOUBLE PRECISION,                         -- məlumat yoxdursa NULL
  floor        INTEGER,                                  -- məlumat yoxdursa NULL
  floors       INTEGER,                                  -- binanın mərtəbə sayı
  price        INTEGER NOT NULL,
  price_period TEXT,                                     -- month | day | total (NULL = məlum deyil)
  deal_type    TEXT NOT NULL,
  property_type TEXT,
  building     TEXT NOT NULL DEFAULT '',
  renovation   TEXT NOT NULL DEFAULT '',
  metro_name   TEXT,
  metro_m      INTEGER,
  images       TEXT,                                     -- şəkil linkləri (JSON massiv), şəkil kopyalanmır
  phone        TEXT,                                     -- yalnız uyğunluğu 70%+ olan elanlar üçün
  quality      INTEGER,                                  -- keyfiyyət balı (sahələrin tamlığına görə)
  suspicious   INTEGER NOT NULL DEFAULT 0,               -- məntiqsiz qiymət → uyğunluğa girmir
  dup_group    INTEGER,                                  -- duplikat qrupu (birləşmiş kart)
  active       INTEGER NOT NULL DEFAULT 1,
  posted_at    TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  last_seen_at TEXT,
  removed_at   TEXT,
  UNIQUE(source_id, external_id)
);
CREATE INDEX IF NOT EXISTS ix_listings_active ON listings(active, deal_type, district);

CREATE TABLE IF NOT EXISTS price_history (
  id           SERIAL PRIMARY KEY,
  listing_id   INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  price        INTEGER NOT NULL,
  recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ph_listing ON price_history(listing_id);

CREATE TABLE IF NOT EXISTS matches (
  id          SERIAL PRIMARY KEY,
  profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  score       INTEGER NOT NULL,
  reasons     TEXT NOT NULL DEFAULT '[]',
  created_at  TEXT NOT NULL,
  UNIQUE(profile_id, listing_id)
);
CREATE INDEX IF NOT EXISTS ix_matches_time ON matches(created_at);

CREATE TABLE IF NOT EXISTS logs (
  id          SERIAL PRIMARY KEY,
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
  id          SERIAL PRIMARY KEY,
  email       TEXT NOT NULL,
  ip          TEXT NOT NULL DEFAULT '',
  success     INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attempts ON login_attempts(email, created_at);

CREATE INDEX IF NOT EXISTS ix_listings_dup ON listings(dup_group);

-- ------------------------------------------------------------ Gün 4-7: gələcək cədvəllər
CREATE TABLE IF NOT EXISTS plans (
  key              TEXT PRIMARY KEY,                     -- free | pro | business | agency
  name             TEXT NOT NULL,
  price            INTEGER,
  max_clients      INTEGER,
  max_profiles     INTEGER,
  max_devices      INTEGER,                              -- NULL = məhdudiyyətsiz
  device_changes_per_month INTEGER,
  daily_phone_views INTEGER,
  features         TEXT NOT NULL DEFAULT '[]',
  sort             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS licenses (
  id            SERIAL PRIMARY KEY,
  code          TEXT NOT NULL UNIQUE,                    -- MH-XXXX-XXXX-XXXX
  plan_key      TEXT NOT NULL REFERENCES plans(key),
  duration_days INTEGER NOT NULL,
  makler_name   TEXT NOT NULL DEFAULT '',
  payment_note  TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'new',             -- new | used | revoked
  created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at    TEXT NOT NULL,
  used_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
  used_at       TEXT,
  expires_at    TEXT,                                    -- istifadədən sonra hesablanır
  revoked_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_licenses_status ON licenses(status, expires_at);

CREATE TABLE IF NOT EXISTS license_attempts (
  id          SERIAL PRIMARY KEY,
  ip          TEXT NOT NULL DEFAULT '',
  success     INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_license_attempts ON license_attempts(ip, created_at);

CREATE TABLE IF NOT EXISTS devices (
  id           SERIAL PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  fingerprint  TEXT NOT NULL,
  label        TEXT NOT NULL DEFAULT '',
  first_seen   TEXT NOT NULL,
  last_seen    TEXT NOT NULL,
  last_ip      TEXT NOT NULL DEFAULT '',
  last_city    TEXT NOT NULL DEFAULT '',
  suspicious   INTEGER NOT NULL DEFAULT 0,
  UNIQUE(user_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS sessions (
  id          SERIAL PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_id   INTEGER REFERENCES devices(id) ON DELETE SET NULL,
  token_hash  TEXT NOT NULL UNIQUE,
  ip          TEXT NOT NULL DEFAULT '',
  user_agent  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  revoked_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id, revoked_at);

CREATE TABLE IF NOT EXISTS phone_views (
  id          SERIAL PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  viewed_at   TEXT NOT NULL,
  ip          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_phone_views_user_time ON phone_views(user_id, viewed_at);

CREATE TABLE IF NOT EXISTS notifications (
  id          SERIAL PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,                             -- new_match | high_match | price_drop | duplicate_merged
  title       TEXT NOT NULL,
  body        TEXT NOT NULL DEFAULT '',
  listing_id  INTEGER REFERENCES listings(id) ON DELETE CASCADE,
  profile_id  INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL,
  read_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id, read_at, created_at);