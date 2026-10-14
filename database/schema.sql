-- =====================================================================
-- SafeGuard IMS - SQLite schema (DDL)
-- Run automatically by database.init_db() when safeguard.db is created.
-- All dates are stored as ISO-8601 text: YYYY-MM-DD (timestamps: YYYY-MM-DD HH:MM:SS)
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. APP_USER : application logins (passwords are stored as hashes only)
--    roles: admin, manager, staff.  Existing databases are upgraded by database.migrate_database().
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS APP_USER (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE
                  CHECK (length(trim(username)) >= 3),
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'staff' CHECK (role IN ('admin', 'manager', 'staff')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    display_name  TEXT,
    email         TEXT,
    phone         TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);
-- One account per e-mail address (accounts without an e-mail are allowed).
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_email ON APP_USER (email COLLATE NOCASE) WHERE email IS NOT NULL;

-- ---------------------------------------------------------------------
-- 2. CUSTOMER
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CUSTOMER (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name   TEXT NOT NULL CHECK (length(trim(full_name)) >= 2),
    age         INTEGER CHECK (age IS NULL OR age BETWEEN 18 AND 100),
    gender      TEXT CHECK (gender IS NULL OR gender IN ('Male', 'Female', 'Other')),
    phone       TEXT,
    email       TEXT UNIQUE COLLATE NOCASE,
    address     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------------------------------------------------------------------
-- 3. INSURANCE_AGENT
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS INSURANCE_AGENT (
    agent_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name            TEXT NOT NULL CHECK (length(trim(agent_name)) >= 2),
    phone                 TEXT,
    email                 TEXT UNIQUE COLLATE NOCASE,
    commission_percentage REAL NOT NULL DEFAULT 5
                          CHECK (commission_percentage >= 0 AND commission_percentage <= 50),
    status                TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Inactive')),
    created_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------------------------------------------------------------------
-- 4. INSURANCE_POLICY
--    status is derived from the dates by the application (see database.derive_status)
--    except 'Cancelled', which is an explicit decision.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS INSURANCE_POLICY (
    policy_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    agent_id        INTEGER,
    policy_type     TEXT NOT NULL CHECK (policy_type IN
                    ('Life Insurance', 'Health Insurance', 'Vehicle Insurance', 'Property Insurance')),
    premium_amount  REAL NOT NULL CHECK (premium_amount > 0),
    coverage_amount REAL NOT NULL CHECK (coverage_amount > 0),
    start_date      TEXT NOT NULL CHECK (date(start_date) IS NOT NULL AND date(start_date) = start_date),
    end_date        TEXT NOT NULL CHECK (date(end_date) IS NOT NULL AND date(end_date) = end_date),
    status          TEXT NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Active', 'Pending', 'Expired', 'Cancelled')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (end_date > start_date),
    CHECK (premium_amount <= coverage_amount),
    FOREIGN KEY (customer_id) REFERENCES CUSTOMER (customer_id)
        ON DELETE RESTRICT ON UPDATE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES INSURANCE_AGENT (agent_id)
        ON DELETE RESTRICT ON UPDATE CASCADE
);

-- ---------------------------------------------------------------------
-- 5. CLAIM
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CLAIM (
    claim_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id     INTEGER NOT NULL,
    claim_date    TEXT NOT NULL CHECK (date(claim_date) IS NOT NULL AND date(claim_date) = claim_date),
    claim_amount  REAL NOT NULL CHECK (claim_amount > 0),
    claim_reason  TEXT,
    claim_status  TEXT NOT NULL DEFAULT 'Pending'
                  CHECK (claim_status IN ('Pending', 'Approved', 'Rejected')),
    decision_date TEXT CHECK (decision_date IS NULL OR
                              (date(decision_date) IS NOT NULL AND date(decision_date) = decision_date)),
    decision_note TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (policy_id) REFERENCES INSURANCE_POLICY (policy_id)
        ON DELETE RESTRICT ON UPDATE CASCADE
);

-- (ALTER TABLE ... ADD COLUMN is used by database.migrate_database() to upgrade older databases;
--  it is guarded so it can never fail with 'duplicate column name'.)

-- ---------------------------------------------------------------------
-- Indexes for commonly searched / joined columns
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_customer_name     ON CUSTOMER (full_name);
CREATE INDEX IF NOT EXISTS idx_agent_name        ON INSURANCE_AGENT (agent_name);
CREATE INDEX IF NOT EXISTS idx_policy_customer   ON INSURANCE_POLICY (customer_id);
CREATE INDEX IF NOT EXISTS idx_policy_agent      ON INSURANCE_POLICY (agent_id);
CREATE INDEX IF NOT EXISTS idx_policy_status     ON INSURANCE_POLICY (status);
CREATE INDEX IF NOT EXISTS idx_policy_type       ON INSURANCE_POLICY (policy_type);
CREATE INDEX IF NOT EXISTS idx_claim_policy      ON CLAIM (policy_id);
CREATE INDEX IF NOT EXISTS idx_claim_status      ON CLAIM (claim_status);
CREATE INDEX IF NOT EXISTS idx_claim_date        ON CLAIM (claim_date);

-- ---------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS V_POLICY_DETAILS AS
SELECT p.policy_id, p.customer_id, c.full_name AS customer_name,
       p.agent_id, a.agent_name,
       p.policy_type, p.premium_amount, p.coverage_amount,
       p.start_date, p.end_date, p.status, p.created_at
FROM INSURANCE_POLICY p
INNER JOIN CUSTOMER c ON c.customer_id = p.customer_id
LEFT JOIN INSURANCE_AGENT a ON a.agent_id = p.agent_id;

CREATE VIEW IF NOT EXISTS V_CLAIM_DETAILS AS
SELECT cl.claim_id, cl.policy_id, p.customer_id, c.full_name AS customer_name,
       p.policy_type, p.coverage_amount, p.status AS policy_status,
       cl.claim_date, cl.claim_amount, cl.claim_reason, cl.claim_status,
       cl.decision_date, cl.decision_note, cl.created_at
FROM CLAIM cl
INNER JOIN INSURANCE_POLICY p ON p.policy_id = cl.policy_id
INNER JOIN CUSTOMER c ON c.customer_id = p.customer_id;

CREATE VIEW IF NOT EXISTS V_POLICY_TYPE_SUMMARY AS
SELECT policy_type,
       COUNT(*)            AS policy_count,
       SUM(premium_amount) AS total_premium,
       AVG(premium_amount) AS avg_premium,
       SUM(coverage_amount) AS total_coverage
FROM INSURANCE_POLICY
GROUP BY policy_type;

-- ---------------------------------------------------------------------
-- Triggers (behaviour is verified in tests/test_app.py)
-- ---------------------------------------------------------------------
-- A claim may not exceed the coverage of the policy it refers to.
CREATE TRIGGER IF NOT EXISTS trg_claim_within_coverage
BEFORE INSERT ON CLAIM
FOR EACH ROW
WHEN NEW.claim_amount > (SELECT coverage_amount FROM INSURANCE_POLICY WHERE policy_id = NEW.policy_id)
BEGIN
    SELECT RAISE(ABORT, 'Claim amount exceeds the policy coverage amount.');
END;

-- Only a pending claim can change status.
CREATE TRIGGER IF NOT EXISTS trg_claim_only_pending_changes
BEFORE UPDATE OF claim_status ON CLAIM
FOR EACH ROW
WHEN OLD.claim_status <> 'Pending' AND NEW.claim_status <> OLD.claim_status
BEGIN
    SELECT RAISE(ABORT, 'Only pending claims can be approved or rejected.');
END;

-- Stamp the decision date automatically when a pending claim is decided.
CREATE TRIGGER IF NOT EXISTS trg_claim_stamp_decision_date
AFTER UPDATE OF claim_status ON CLAIM
FOR EACH ROW
WHEN OLD.claim_status = 'Pending' AND NEW.claim_status IN ('Approved', 'Rejected')
     AND NEW.decision_date IS NULL
BEGIN
    UPDATE CLAIM SET decision_date = date('now', 'localtime') WHERE claim_id = NEW.claim_id;
END;

-- Schema version: 2 = three roles + profile columns. Read by database.migrate_database().
PRAGMA user_version = 2;
