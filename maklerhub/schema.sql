-- MaklerHub verilənlər bazası sxemi (PostgreSQL / Neon)
-- Tarixlər "YYYY-MM-DD HH:MM:SS" formatlı TEXT kimi saxlanılır (Asia/Baku, helpers.ts()).
-- Model: müştəri tələb yazır → maklerlər təklif göndərir → müştəri qəbul edəndə nömrəsi həmin maklerə açılır.

-- ------------------------------------------------------------ istifadəçilər və lisenziyalar
CREATE TABLE IF NOT EXISTS licenses (
  id            SERIAL PRIMARY KEY,
  code          TEXT NOT NULL UNIQUE,                    -- MH-XXXX-XXXX-XXXX
  days          INTEGER NOT NULL,                        -- aktivləşdirmədən sonra etibarlılıq müddəti
  status        TEXT NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'used', 'revoked')),
  user_id       INTEGER,                                 -- istifadə edən makler (FK aşağıda)
  note          TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  activated_at  TEXT,
  expires_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_licenses_status ON licenses(status, expires_at);

CREATE TABLE IF NOT EXISTS users (
  id                  SERIAL PRIMARY KEY,
  first_name          TEXT NOT NULL,
  last_name           TEXT NOT NULL DEFAULT '',
  email               TEXT NOT NULL UNIQUE,
  phone               TEXT NOT NULL DEFAULT '',
  password_hash       TEXT NOT NULL,
  role                TEXT NOT NULL DEFAULT 'makler' CHECK (role IN ('admin', 'makler')),
  status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
  license_id          INTEGER REFERENCES licenses(id) ON DELETE SET NULL,
  created_at          TEXT NOT NULL,
  password_changed_at TEXT,
  last_login_at       TEXT
);

DO $$ BEGIN
  ALTER TABLE licenses ADD CONSTRAINT fk_licenses_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE INDEX IF NOT EXISTS ix_licenses_user ON licenses(user_id);

CREATE TABLE IF NOT EXISTS devices (
  id            SERIAL PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_token  TEXT NOT NULL UNIQUE,
  user_agent    TEXT NOT NULL DEFAULT '',
  ip            TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_devices_user ON devices(user_id);

CREATE TABLE IF NOT EXISTS sessions (
  id            SERIAL PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_id     INTEGER REFERENCES devices(id) ON DELETE SET NULL,
  token         TEXT NOT NULL UNIQUE,
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id, active);

CREATE TABLE IF NOT EXISTS login_attempts (
  id          SERIAL PRIMARY KEY,
  email       TEXT NOT NULL,
  ip          TEXT NOT NULL DEFAULT '',
  success     INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attempts ON login_attempts(email, created_at);

CREATE TABLE IF NOT EXISTS settings (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
  id          SERIAL PRIMARY KEY,
  created_at  TEXT NOT NULL,
  level       TEXT NOT NULL,          -- ok | warn | err | info
  source      TEXT NOT NULL,          -- Makler | Müştəri | Sistem
  message     TEXT NOT NULL,
  details     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_logs_time ON logs(created_at);

-- ------------------------------------------------------------ makler xidmət əraziləri
CREATE TABLE IF NOT EXISTS agent_areas (
  id         SERIAL PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  district   TEXT NOT NULL,                              -- helpers.BAKU_DISTRICTS
  deal_type  TEXT NOT NULL CHECK (deal_type IN ('rent', 'sale')),
  price_min  INTEGER,                                    -- NULL = həd yoxdur
  price_max  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_agent_areas_user ON agent_areas(user_id);
CREATE INDEX IF NOT EXISTS ix_agent_areas_match ON agent_areas(deal_type, district);

-- ------------------------------------------------------------ müştəri tələbləri və təkliflər
CREATE TABLE IF NOT EXISTS customer_requests (
  id          SERIAL PRIMARY KEY,
  token       TEXT NOT NULL UNIQUE,                      -- müştərinin gizli linki
  name        TEXT NOT NULL DEFAULT '',
  phone       TEXT NOT NULL,
  deal_type   TEXT NOT NULL CHECK (deal_type IN ('rent', 'sale')),
  districts   TEXT NOT NULL DEFAULT '[]',                -- JSON massiv (mətn)
  rooms       INTEGER,                                   -- 5 = 5+; NULL = fərqi yoxdur
  budget_min  INTEGER,
  budget_max  INTEGER,
  urgency     TEXT NOT NULL DEFAULT 'flexible' CHECK (urgency IN ('urgent', 'month', 'flexible')),
  note        TEXT NOT NULL DEFAULT '',
  consent     INTEGER NOT NULL DEFAULT 0,
  consent_at  TEXT,
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed', 'expired')),
  ip          TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_requests_status ON customer_requests(status, deal_type, created_at);

CREATE TABLE IF NOT EXISTS offers (
  id           SERIAL PRIMARY KEY,
  request_id   INTEGER NOT NULL REFERENCES customer_requests(id) ON DELETE CASCADE,
  agent_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  price        INTEGER,
  district     TEXT,
  rooms        INTEGER,
  area         DOUBLE PRECISION,
  image_url    TEXT,
  description  TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent', 'accepted', 'rejected', 'withdrawn')),
  created_at   TEXT NOT NULL,
  UNIQUE (request_id, agent_id)
);
CREATE INDEX IF NOT EXISTS ix_offers_agent ON offers(agent_id, created_at);

CREATE TABLE IF NOT EXISTS contacts (
  id          SERIAL PRIMARY KEY,
  offer_id    INTEGER NOT NULL UNIQUE REFERENCES offers(id) ON DELETE CASCADE,
  request_id  INTEGER NOT NULL REFERENCES customer_requests(id) ON DELETE CASCADE,
  agent_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_contacts_agent ON contacts(agent_id, created_at);
CREATE INDEX IF NOT EXISTS ix_contacts_request ON contacts(request_id);

CREATE TABLE IF NOT EXISTS notifications (
  id          SERIAL PRIMARY KEY,
  user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,                 -- NULL = müştəriyə aid
  request_id  INTEGER REFERENCES customer_requests(id) ON DELETE CASCADE,     -- NULL = makler/sistem
  kind        TEXT NOT NULL,
  text        TEXT NOT NULL,
  link        TEXT NOT NULL DEFAULT '',
  is_read     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id, is_read, created_at);
CREATE INDEX IF NOT EXISTS ix_notifications_request ON notifications(request_id, is_read);

CREATE TABLE IF NOT EXISTS complaints (
  id          SERIAL PRIMARY KEY,
  request_id  INTEGER NOT NULL REFERENCES customer_requests(id) ON DELETE CASCADE,
  offer_id    INTEGER REFERENCES offers(id) ON DELETE SET NULL,
  agent_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  from_side   TEXT NOT NULL CHECK (from_side IN ('customer', 'agent')),
  reason      TEXT NOT NULL,
  text        TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed')),
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_complaints_status ON complaints(status, created_at);

-- ------------------------------------------------------------ Gün 4 (canlı bazada startup-da tətbiq olunur)
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS close_reason TEXT;      -- found_here | found_elsewhere | not_needed
ALTER TABLE customer_requests ADD COLUMN IF NOT EXISTS customer_seen_at TEXT;  -- müştərinin səhifəyə son baxışı ("Yeni" nişanı)
ALTER TABLE offers ADD COLUMN IF NOT EXISTS decided_at TEXT;                   -- qəbul/rədd vaxtı
CREATE INDEX IF NOT EXISTS ix_offers_request ON offers(request_id, status);
-- şikayət: eyni obyekt üçün eyni tərəfdən 1 dəfə (müştəri → təklif, makler → tələb)
CREATE UNIQUE INDEX IF NOT EXISTS ux_complaints_customer ON complaints(offer_id) WHERE from_side = 'customer';
CREATE UNIQUE INDEX IF NOT EXISTS ux_complaints_agent ON complaints(request_id, agent_id) WHERE from_side = 'agent';
