"""SafeGuard IMS - database layer (SQLite via the standard sqlite3 module).

All SQL uses parameter placeholders (?). The only text ever formatted into a SQL
string is a fixed fragment written in this file (column lists, whitelisted ORDER BY
clauses) - never user input.
"""
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
SQL_DIR = BASE_DIR / "database"

ROLES = ("admin", "manager", "staff")
SCHEMA_VERSION = 2
POLICY_TYPES = ("Life Insurance", "Health Insurance", "Vehicle Insurance", "Property Insurance")
TODAY_SQL = "date('now','localtime')"


class BusinessRuleError(Exception):
    """Raised when an operation is refused by an application business rule."""

    def __init__(self, message, status=409, errors=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors or {}


# ----------------------------------------------------------------- connection / init
def default_db_path():
    return os.environ.get("SAFEGUARD_DB_PATH") or str(BASE_DIR / "instance" / "safeguard.db")


def get_connection(path=None):
    conn = sqlite3.connect(path or default_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # must be enabled on EVERY connection
    return conn


# Dev-only demo accounts, created ONLY in a brand-new development database (never in production).
DEMO_USERS = (("manager", "Manager@123", "manager", "Demo Manager"),
              ("staff", "Staff@123", "staff", "Demo Staff"))


def init_db(path=None, admin_username="admin", admin_password=None, seed_demo=True, demo_users=False):
    """Create the database on first run (or safely upgrade an older one), then ensure an admin exists."""
    path = path or default_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    try:
        is_new = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='APP_USER'"
        ).fetchone() is None
        if is_new:
            conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
            if seed_demo:
                conn.executescript((SQL_DIR / "seed.sql").read_text(encoding="utf-8"))
            conn.commit()
    finally:
        conn.close()
    if not is_new:
        migrate_database(path)  # no-op when already up to date; backs up first when it must change something
    conn = get_connection(path)
    try:
        if conn.execute("SELECT COUNT(*) FROM APP_USER").fetchone()[0] == 0:
            if not admin_password:
                raise RuntimeError(
                    "No administrator exists. Set ADMIN_USERNAME and ADMIN_PASSWORD "
                    "environment variables before the first start.")
            create_user(conn, admin_username, generate_password_hash(admin_password), "admin",
                        display_name="Administrator")
            if is_new and demo_users:
                for uname, pw, role, name in DEMO_USERS:
                    create_user(conn, uname, generate_password_hash(pw), role, display_name=name)
        sync_policy_statuses(conn)
    finally:
        conn.close()


# ----------------------------------------------------------------- migration (existing databases)
APP_USER_DDL = """CREATE TABLE {name} (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(username)) >= 3),
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'staff' CHECK (role IN ('admin', 'manager', 'staff')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    display_name  TEXT,
    email         TEXT,
    phone         TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
)"""
NEW_USER_COLUMNS = {"display_name": "TEXT", "email": "TEXT", "phone": "TEXT",
                    "is_active": "INTEGER NOT NULL DEFAULT 1"}
USER_EMAIL_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_email "
                    "ON APP_USER (email COLLATE NOCASE) WHERE email IS NOT NULL")


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def migration_plan(conn):
    """Return the list of steps still needed (an empty list means the database is up to date)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='APP_USER'").fetchone():
        return []
    plan = []
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='APP_USER'").fetchone()[0] or ""
    if "'manager'" not in ddl:
        plan.append("rebuild APP_USER so the role column accepts admin, manager and staff")
    else:
        have = _columns(conn, "APP_USER")
        plan += [f"add APP_USER.{c}" for c in NEW_USER_COLUMNS if c not in have]
    if "decision_note" not in _columns(conn, "CLAIM"):
        plan.append("add CLAIM.decision_note")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_user_email'").fetchone():
        plan.append("create unique index idx_user_email")
    return plan


def backup_database(path):
    """Consistent copy of the database (sqlite3 backup API) into <db folder>/backups/."""
    src = Path(path)
    folder = src.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{src.stem}-backup-{datetime.now():%Y%m%d-%H%M%S}{src.suffix}"
    source, dest = sqlite3.connect(str(src)), sqlite3.connect(str(target))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    return str(target)


def migrate_database(path=None, backup=True, dry_run=False):
    """Safe, repeatable upgrade of an existing database to schema version 2.

    * Nothing is dropped except the OLD APP_USER table, after its rows were copied into the new one.
    * User IDs, usernames, password hashes and created_at values are copied unchanged.
    * Every step checks the current structure first, so running it again changes nothing.
    * A backup is taken before the first change; the whole upgrade is one transaction.
    """
    path = path or default_db_path()
    if not Path(path).exists():
        return {"backup": None, "actions": [], "message": "No database file yet - nothing to migrate."}
    conn = sqlite3.connect(path, isolation_level=None)  # manual transaction control
    conn.row_factory = sqlite3.Row
    try:
        plan = migration_plan(conn)
        if dry_run or not plan:
            if not plan and not dry_run and conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return {"backup": None, "actions": plan,
                    "message": "Database is already up to date." if not plan else "Migration needed (dry run, nothing changed)."}
        backup_path = backup_database(path) if backup else None
        conn.execute("PRAGMA foreign_keys = OFF")  # required by SQLite's table-rebuild procedure
        conn.execute("BEGIN IMMEDIATE")
        try:
            ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='APP_USER'").fetchone()[0] or ""
            if "'manager'" not in ddl:
                have = _columns(conn, "APP_USER")
                conn.execute(APP_USER_DDL.format(name="APP_USER_new"))
                extra = ", ".join(c if c in have else ("1" if c == "is_active" else "NULL") for c in NEW_USER_COLUMNS)
                conn.execute(
                    "INSERT INTO APP_USER_new (user_id, username, password_hash, role, created_at, "
                    + ", ".join(NEW_USER_COLUMNS) + ") SELECT user_id, username, password_hash, "
                    "CASE WHEN lower(trim(role)) IN ('admin','manager','staff') THEN lower(trim(role)) ELSE 'staff' END, "
                    "created_at, " + extra + " FROM APP_USER")
                conn.execute("DROP TABLE APP_USER")
                conn.execute("ALTER TABLE APP_USER_new RENAME TO APP_USER")
            else:
                have = _columns(conn, "APP_USER")
                for col, decl in NEW_USER_COLUMNS.items():
                    if col not in have:
                        conn.execute(f"ALTER TABLE APP_USER ADD COLUMN {col} {decl}")
            if "decision_note" not in _columns(conn, "CLAIM"):
                conn.execute("ALTER TABLE CLAIM ADD COLUMN decision_note TEXT")
            conn.execute(USER_EMAIL_INDEX)
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Foreign key check failed after migration; rolled back.")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
        return {"backup": backup_path, "actions": plan, "message": "Migration completed."}
    finally:
        conn.close()


def looks_like_password_hash(value):
    """True if the value has the shape of a Werkzeug hash (never prints the hash itself)."""
    return bool(value) and bool(re.match(r"^(scrypt|pbkdf2):[^$]+\$[^$]+\$[0-9a-f]+$", value))


def user_diagnostics(conn):
    rows = conn.execute("SELECT * FROM APP_USER ORDER BY user_id")  # * so it also works before migration
    return [{"user_id": r["user_id"], "username": r["username"], "role": r["role"],
             "active": bool(r["is_active"]) if "is_active" in r.keys() else True,
             "valid_hash": looks_like_password_hash(r["password_hash"])} for r in rows]


# ----------------------------------------------------------------- policy status rules
def derive_status(start_date, end_date, cancelled=False, today=None):
    """Single source of truth for policy status.

    Cancelled (explicit) > Expired (end date passed) > Pending (starts in future) > Active.
    """
    today = (today or date.today()).isoformat()
    if cancelled:
        return "Cancelled"
    if end_date < today:
        return "Expired"
    if start_date > today:
        return "Pending"
    return "Active"


def sync_policy_statuses(conn):
    """Bring stored statuses in line with derive_status() (SQL equivalent of the same rules)."""
    with conn:
        conn.execute(f"UPDATE INSURANCE_POLICY SET status='Expired' "
                     f"WHERE status IN ('Active','Pending') AND end_date < {TODAY_SQL}")
        conn.execute(f"UPDATE INSURANCE_POLICY SET status='Active' "
                     f"WHERE status='Pending' AND start_date <= {TODAY_SQL} AND end_date >= {TODAY_SQL}")
        conn.execute(f"UPDATE INSURANCE_POLICY SET status='Pending' "
                     f"WHERE status='Active' AND start_date > {TODAY_SQL}")


# ----------------------------------------------------------------- helpers
def _rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def _paginate(conn, cols, from_sql, where, params, order_sql, page, per_page):
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM {from_sql}{where_sql}", params).fetchone()[0]
    sql = f"SELECT {cols} FROM {from_sql}{where_sql} ORDER BY {order_sql}"
    args = list(params)
    if page is not None:
        sql += " LIMIT ? OFFSET ?"
        args += [per_page, (page - 1) * per_page]
    pages = max(1, math.ceil(total / per_page)) if page is not None else 1
    return {"items": _rows(conn.execute(sql, args)), "total": total,
            "page": page or 1, "pages": pages, "per_page": per_page}


def _like(q):
    return f"%{q}%"


# ----------------------------------------------------------------- users
PUBLIC_USER_COLS = "user_id, username, role, display_name, email, phone, is_active, created_at"


def get_user_by_username(conn, username):
    return conn.execute("SELECT * FROM APP_USER WHERE username = ?", (username,)).fetchone()


def get_user(conn, user_id):
    return conn.execute("SELECT * FROM APP_USER WHERE user_id = ?", (user_id,)).fetchone()


def public_user(conn, user_id):
    """User as a dict WITHOUT the password hash (safe to return from the API)."""
    r = conn.execute(f"SELECT {PUBLIC_USER_COLS} FROM APP_USER WHERE user_id = ?", (user_id,)).fetchone()
    return dict(r) if r else None


def list_users(conn):
    return _rows(conn.execute(f"SELECT {PUBLIC_USER_COLS} FROM APP_USER ORDER BY user_id"))


def username_taken(conn, username, exclude_id=None):
    return conn.execute("SELECT 1 FROM APP_USER WHERE username = ? AND user_id IS NOT ?",
                        (username, exclude_id)).fetchone() is not None


def email_taken(conn, email, exclude_id=None):
    return bool(email) and conn.execute("SELECT 1 FROM APP_USER WHERE email = ? COLLATE NOCASE AND user_id IS NOT ?",
                                        (email, exclude_id)).fetchone() is not None


def create_user(conn, username, password_hash, role, display_name=None, email=None, phone=None):
    if role not in ROLES:
        raise BusinessRuleError("Role must be admin, manager or staff.", 422)
    with conn:
        cur = conn.execute(
            "INSERT INTO APP_USER (username, password_hash, role, display_name, email, phone) VALUES (?,?,?,?,?,?)",
            (username, password_hash, role, display_name, email, phone))
    return cur.lastrowid


def update_profile(conn, user_id, display_name, email, phone):
    """Personal details only. Role, username and active flag are deliberately NOT touched here."""
    with conn:
        conn.execute("UPDATE APP_USER SET display_name = ?, email = ?, phone = ? WHERE user_id = ?",
                     (display_name, email, phone, user_id))


def update_password(conn, user_id, new_hash):
    with conn:
        conn.execute("UPDATE APP_USER SET password_hash = ? WHERE user_id = ?", (new_hash, user_id))


@contextmanager
def _immediate(conn):
    """Write transaction that takes the lock first, so check-then-change cannot race."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _guard_last_admin(conn, user_id, action):
    target = get_user(conn, user_id)
    if target is None:
        raise BusinessRuleError("User not found.", 404)
    if target["role"] == "admin" and target["is_active"]:
        others = conn.execute("SELECT COUNT(*) FROM APP_USER WHERE role='admin' AND is_active=1 AND user_id <> ?",
                              (user_id,)).fetchone()[0]
        if others == 0:
            raise BusinessRuleError(f"This is the last active administrator and cannot be {action}. "
                                    "Create or enable another administrator first.", 409)
    return target


def set_user_role(conn, user_id, role):
    if role not in ROLES:
        raise BusinessRuleError("Role must be admin, manager or staff.", 422)
    with _immediate(conn):
        target = get_user(conn, user_id)
        if target is None:
            raise BusinessRuleError("User not found.", 404)
        if role != "admin":
            _guard_last_admin(conn, user_id, "demoted")
        conn.execute("UPDATE APP_USER SET role = ? WHERE user_id = ?", (role, user_id))


def set_user_active(conn, user_id, active):
    with _immediate(conn):
        if not active:
            _guard_last_admin(conn, user_id, "disabled")
        elif get_user(conn, user_id) is None:
            raise BusinessRuleError("User not found.", 404)
        conn.execute("UPDATE APP_USER SET is_active = ? WHERE user_id = ?", (1 if active else 0, user_id))


def delete_user(conn, user_id):
    with _immediate(conn):
        _guard_last_admin(conn, user_id, "deleted")
        conn.execute("DELETE FROM APP_USER WHERE user_id = ?", (user_id,))


# ----------------------------------------------------------------- customers
def list_customers(conn, q=None, page=1, per_page=10):
    where, params = [], []
    if q:
        clause = "(c.full_name LIKE ? OR c.email LIKE ? OR c.phone LIKE ?"
        params += [_like(q)] * 3
        if q.isdigit():
            clause += " OR c.customer_id = ?"
            params.append(int(q))
        where.append(clause + ")")
    cols = ("c.*, (SELECT COUNT(*) FROM INSURANCE_POLICY p "
            "WHERE p.customer_id = c.customer_id) AS policy_count")
    return _paginate(conn, cols, "CUSTOMER c", where, params, "c.customer_id DESC", page, per_page)


def get_customer(conn, customer_id):
    r = conn.execute("SELECT * FROM CUSTOMER WHERE customer_id = ?", (customer_id,)).fetchone()
    return dict(r) if r else None


def insert_customer(conn, d):
    with conn:
        cur = conn.execute(
            "INSERT INTO CUSTOMER (full_name, age, gender, phone, email, address) VALUES (?,?,?,?,?,?)",
            (d["full_name"], d["age"], d["gender"], d["phone"], d["email"], d["address"]))
    return cur.lastrowid


def update_customer(conn, customer_id, d):
    with conn:
        cur = conn.execute(
            "UPDATE CUSTOMER SET full_name=?, age=?, gender=?, phone=?, email=?, address=? WHERE customer_id=?",
            (d["full_name"], d["age"], d["gender"], d["phone"], d["email"], d["address"], customer_id))
    return cur.rowcount


def delete_customer(conn, customer_id):
    n = conn.execute("SELECT COUNT(*) FROM INSURANCE_POLICY WHERE customer_id = ?",
                     (customer_id,)).fetchone()[0]
    if n:
        raise BusinessRuleError(
            f"This customer has {n} polic{'y' if n == 1 else 'ies'} and cannot be deleted. "
            "Cancel or reassign the policies first.")
    with conn:
        cur = conn.execute("DELETE FROM CUSTOMER WHERE customer_id = ?", (customer_id,))
    return cur.rowcount


# ----------------------------------------------------------------- agents
def list_agents(conn, q=None, status=None, page=1, per_page=10):
    where, params = [], []
    if q:
        clause = "(a.agent_name LIKE ? OR a.email LIKE ? OR a.phone LIKE ?"
        params += [_like(q)] * 3
        if q.isdigit():
            clause += " OR a.agent_id = ?"
            params.append(int(q))
        where.append(clause + ")")
    if status in ("Active", "Inactive"):
        where.append("a.status = ?")
        params.append(status)
    cols = ("a.*, (SELECT COUNT(*) FROM INSURANCE_POLICY p "
            "WHERE p.agent_id = a.agent_id) AS policy_count")
    return _paginate(conn, cols, "INSURANCE_AGENT a", where, params, "a.agent_id DESC", page, per_page)


def get_agent(conn, agent_id):
    r = conn.execute("SELECT * FROM INSURANCE_AGENT WHERE agent_id = ?", (agent_id,)).fetchone()
    return dict(r) if r else None


def insert_agent(conn, d):
    with conn:
        cur = conn.execute(
            "INSERT INTO INSURANCE_AGENT (agent_name, phone, email, commission_percentage, status) "
            "VALUES (?,?,?,?,?)",
            (d["agent_name"], d["phone"], d["email"], d["commission_percentage"], d["status"]))
    return cur.lastrowid


def update_agent(conn, agent_id, d):
    with conn:
        cur = conn.execute(
            "UPDATE INSURANCE_AGENT SET agent_name=?, phone=?, email=?, commission_percentage=?, status=? "
            "WHERE agent_id=?",
            (d["agent_name"], d["phone"], d["email"], d["commission_percentage"], d["status"], agent_id))
    return cur.rowcount


def set_agent_status(conn, agent_id, status):
    with conn:
        cur = conn.execute("UPDATE INSURANCE_AGENT SET status=? WHERE agent_id=?", (status, agent_id))
    return cur.rowcount


def delete_agent(conn, agent_id):
    n = conn.execute("SELECT COUNT(*) FROM INSURANCE_POLICY WHERE agent_id = ?", (agent_id,)).fetchone()[0]
    if n:
        raise BusinessRuleError(
            f"This agent is assigned to {n} polic{'y' if n == 1 else 'ies'} and cannot be deleted. "
            "Set the agent to Inactive instead.")
    with conn:
        cur = conn.execute("DELETE FROM INSURANCE_AGENT WHERE agent_id = ?", (agent_id,))
    return cur.rowcount


# ----------------------------------------------------------------- policies
def _policy_filters(f):
    where, params = [], []
    q = f.get("q")
    if q:
        clause = "(v.customer_name LIKE ?"
        params.append(_like(q))
        if q.isdigit():
            clause += " OR v.policy_id = ?"
            params.append(int(q))
        where.append(clause + ")")
    for key, col in (("customer_id", "v.customer_id"), ("agent_id", "v.agent_id"),
                     ("policy_type", "v.policy_type"), ("status", "v.status")):
        if f.get(key):
            where.append(f"{col} = ?")
            params.append(f[key])
    if f.get("date_from"):
        where.append("v.start_date >= ?")
        params.append(f["date_from"])
    if f.get("date_to"):
        where.append("v.start_date <= ?")
        params.append(f["date_to"])
    return where, params


def list_policies(conn, filters, page=1, per_page=10):
    where, params = _policy_filters(filters)
    result = _paginate(conn, "v.*", "V_POLICY_DETAILS v", where, params,
                       "v.policy_id DESC", page, per_page)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    s = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(premium_amount),0), COALESCE(SUM(coverage_amount),0) "
        f"FROM V_POLICY_DETAILS v{where_sql}", params).fetchone()
    result["summary"] = {"count": s[0], "total_premium": s[1], "total_coverage": s[2]}
    return result


def get_policy(conn, policy_id):
    r = conn.execute("SELECT * FROM V_POLICY_DETAILS WHERE policy_id = ?", (policy_id,)).fetchone()
    return dict(r) if r else None


def insert_policy(conn, d):
    with conn:
        cur = conn.execute(
            "INSERT INTO INSURANCE_POLICY (customer_id, agent_id, policy_type, premium_amount, "
            "coverage_amount, start_date, end_date, status) VALUES (?,?,?,?,?,?,?,?)",
            (d["customer_id"], d["agent_id"], d["policy_type"], d["premium_amount"],
             d["coverage_amount"], d["start_date"], d["end_date"], d["status"]))
    return cur.lastrowid


def update_policy(conn, policy_id, d):
    with conn:
        cur = conn.execute(
            "UPDATE INSURANCE_POLICY SET customer_id=?, agent_id=?, policy_type=?, premium_amount=?, "
            "coverage_amount=?, start_date=?, end_date=?, status=? WHERE policy_id=?",
            (d["customer_id"], d["agent_id"], d["policy_type"], d["premium_amount"],
             d["coverage_amount"], d["start_date"], d["end_date"], d["status"], policy_id))
    return cur.rowcount


def max_claim_amount(conn, policy_id):
    return conn.execute("SELECT COALESCE(MAX(claim_amount),0) FROM CLAIM WHERE policy_id=?",
                        (policy_id,)).fetchone()[0]


# ----------------------------------------------------------------- claims
def _claim_filters(f):
    where, params = [], []
    if f.get("claim_id"):
        where.append("v.claim_id = ?")
        params.append(f["claim_id"])
    if f.get("policy_id"):
        where.append("v.policy_id = ?")
        params.append(f["policy_id"])
    if f.get("customer_id"):
        where.append("v.customer_id = ?")
        params.append(f["customer_id"])
    if f.get("status"):
        where.append("v.claim_status = ?")
        params.append(f["status"])
    if f.get("date_from"):
        where.append("v.claim_date >= ?")
        params.append(f["date_from"])
    if f.get("date_to"):
        where.append("v.claim_date <= ?")
        params.append(f["date_to"])
    return where, params


def list_claims(conn, filters, page=1, per_page=10):
    where, params = _claim_filters(filters)
    result = _paginate(conn, "v.*", "V_CLAIM_DETAILS v", where, params,
                       "v.claim_id DESC", page, per_page)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    s = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN claim_status='Approved' THEN claim_amount END),0), "
        "COALESCE(SUM(CASE WHEN claim_status='Pending' THEN 1 END),0) "
        f"FROM V_CLAIM_DETAILS v{where_sql}", params).fetchone()
    result["summary"] = {"count": s[0], "approved_total": s[1], "pending_count": s[2]}
    return result


def get_claim(conn, claim_id):
    r = conn.execute("SELECT * FROM V_CLAIM_DETAILS WHERE claim_id = ?", (claim_id,)).fetchone()
    return dict(r) if r else None


def claim_history(conn, policy_id):
    return _rows(conn.execute(
        "SELECT claim_id, claim_date, claim_amount, claim_status, decision_date "
        "FROM CLAIM WHERE policy_id = ? ORDER BY claim_date DESC, claim_id DESC", (policy_id,)))


def insert_claim(conn, d):
    with conn:
        cur = conn.execute(
            "INSERT INTO CLAIM (policy_id, claim_date, claim_amount, claim_reason) VALUES (?,?,?,?)",
            (d["policy_id"], d["claim_date"], d["claim_amount"], d["claim_reason"]))
    return cur.lastrowid


def decide_claim(conn, claim_id, new_status, note=None):
    """Approve/reject a pending claim in one transaction. Raises if it is not pending."""
    with conn:  # commits on success, rolls back on any exception
        cur = conn.execute(
            f"UPDATE CLAIM SET claim_status = ?, decision_date = {TODAY_SQL}, decision_note = ? "
            "WHERE claim_id = ? AND claim_status = 'Pending'", (new_status, note, claim_id))
        if cur.rowcount == 0:
            exists = conn.execute("SELECT 1 FROM CLAIM WHERE claim_id = ?", (claim_id,)).fetchone()
            if not exists:
                raise BusinessRuleError("Claim not found.", 404)
            raise BusinessRuleError("Only pending claims can be approved or rejected.", 409)


# ----------------------------------------------------------------- dashboard
def _month_starts(n=6):
    today = date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def dashboard(conn):
    one = lambda sql, p=(): conn.execute(sql, p).fetchone()[0]
    total_customers = one("SELECT COUNT(*) FROM CUSTOMER")
    prev = one("SELECT COUNT(*) FROM CUSTOMER WHERE created_at < datetime('now','localtime','-30 days')")
    growth = round((total_customers - prev) * 100.0 / prev, 1) if prev else (100.0 if total_customers else 0.0)

    dist = {t: 0 for t in POLICY_TYPES}
    for r in conn.execute("SELECT policy_type, COUNT(*) AS n FROM INSURANCE_POLICY "
                          "WHERE status = 'Active' GROUP BY policy_type"):
        dist[r["policy_type"]] = r["n"]
    total_active = sum(dist.values())
    distribution = [{"type": t, "count": n,
                     "percent": round(n * 100.0 / total_active, 1) if total_active else 0.0}
                    for t, n in dist.items()]

    months = _month_starts(6)
    first = f"{months[0][0]:04d}-{months[0][1]:02d}-01"
    counts = {}
    for r in conn.execute("SELECT strftime('%Y-%m', claim_date) AS ym, claim_status, COUNT(*) AS n "
                          "FROM CLAIM WHERE claim_date >= ? GROUP BY ym, claim_status", (first,)):
        counts[(r["ym"], r["claim_status"])] = r["n"]
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    claims_chart = {"labels": [names[m - 1] for _, m in months]}
    for status in ("Approved", "Pending", "Rejected"):
        claims_chart[status.lower()] = [counts.get((f"{y:04d}-{m:02d}", status), 0) for y, m in months]

    return {
        "cards": {
            "total_customers": total_customers, "customer_growth": growth,
            "active_policies": one("SELECT COUNT(*) FROM INSURANCE_POLICY WHERE status='Active'"),
            "pending_claims": one("SELECT COUNT(*) FROM CLAIM WHERE claim_status='Pending'"),
            "total_agents": one("SELECT COUNT(*) FROM INSURANCE_AGENT"),
        },
        "distribution": distribution, "total_active": total_active,
        "claims_chart": claims_chart,
        "recent_policies": _rows(conn.execute(
            "SELECT policy_id, customer_name, policy_type, premium_amount, start_date, status "
            "FROM V_POLICY_DETAILS ORDER BY created_at DESC, policy_id DESC LIMIT 6")),
        "recent_claims": _rows(conn.execute(
            "SELECT claim_id, claim_date, claim_amount, claim_status FROM V_CLAIM_DETAILS "
            "ORDER BY claim_date DESC, claim_id DESC LIMIT 5")),
    }


def notifications(conn):
    pending = _rows(conn.execute(
        "SELECT claim_id, customer_name, claim_amount FROM V_CLAIM_DETAILS "
        "WHERE claim_status='Pending' ORDER BY claim_date DESC LIMIT 5"))
    pending_total = conn.execute("SELECT COUNT(*) FROM CLAIM WHERE claim_status='Pending'").fetchone()[0]
    expiring = _rows(conn.execute(
        "SELECT policy_id, customer_name, end_date FROM V_POLICY_DETAILS WHERE status='Active' "
        f"AND end_date BETWEEN {TODAY_SQL} AND date('now','localtime','+30 days') "
        "ORDER BY end_date LIMIT 5"))
    expiring_total = conn.execute(
        "SELECT COUNT(*) FROM INSURANCE_POLICY WHERE status='Active' "
        f"AND end_date BETWEEN {TODAY_SQL} AND date('now','localtime','+30 days')").fetchone()[0]
    return {"pending_claims": pending, "pending_total": pending_total,
            "expiring": expiring, "expiring_total": expiring_total,
            "count": pending_total + expiring_total}


def search(conn, q):
    customers = _rows(conn.execute(
        "SELECT customer_id, full_name, email FROM CUSTOMER WHERE full_name LIKE ? OR email LIKE ? "
        "ORDER BY full_name LIMIT 5", (_like(q), _like(q))))
    where, params = ["v.customer_name LIKE ?"], [_like(q)]
    if q.isdigit():
        where.append("v.policy_id = ?")
        params.append(int(q))
    policies = _rows(conn.execute(
        "SELECT policy_id, customer_name, policy_type FROM V_POLICY_DETAILS v WHERE "
        + " OR ".join(where) + " ORDER BY policy_id DESC LIMIT 5", params))
    return {"customers": customers, "policies": policies}


def lookups(conn):
    return {
        "customers": _rows(conn.execute("SELECT customer_id, full_name, age FROM CUSTOMER ORDER BY full_name")),
        "agents": _rows(conn.execute(
            "SELECT agent_id, agent_name FROM INSURANCE_AGENT WHERE status='Active' ORDER BY agent_name")),
        "active_policies": _rows(conn.execute(
            "SELECT policy_id, customer_name, policy_type, coverage_amount, start_date, end_date "
            "FROM V_POLICY_DETAILS WHERE status='Active' ORDER BY policy_id DESC")),
    }


def table_counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("APP_USER", "CUSTOMER", "INSURANCE_AGENT", "INSURANCE_POLICY", "CLAIM")}


# ----------------------------------------------------------------- premium calculator (educational)
BASE_RATES = {"Life Insurance": 0.012, "Health Insurance": 0.030,
              "Vehicle Insurance": 0.025, "Property Insurance": 0.004}


def estimate_premium(policy_type, coverage, age=None):
    """EDUCATIONAL formula only - not a real insurer's underwriting model.

    annual premium = coverage x base rate(type) x age factor
    age factor = 1 + 1.5% per year above 30 (Life and Health only), capped at 2.0
    """
    rate = BASE_RATES[policy_type]
    factor = 1.0
    if policy_type in ("Life Insurance", "Health Insurance") and age and age > 30:
        factor = min(2.0, 1 + 0.015 * (age - 30))
    return round(coverage * rate * factor, 2), rate, round(factor, 3)


# ----------------------------------------------------------------- reports
def _money(key, label):
    return {"key": key, "label": label, "type": "money"}


def report(conn, name, a):
    """Return {title, columns, rows, summary} for a report. `a` holds validated filters."""
    q, df, dt = a.get("q"), a.get("date_from"), a.get("date_to")
    S = lambda label, value, kind="number": {"label": label, "value": value, "type": kind}

    if name == "customers":
        where, params = [], []
        if q:
            where.append("(c.full_name LIKE ? OR c.email LIKE ?)")
            params += [_like(q)] * 2
        if df:
            where.append("date(c.created_at) >= ?")
            params.append(df)
        if dt:
            where.append("date(c.created_at) <= ?")
            params.append(dt)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        rows = _rows(conn.execute(
            "SELECT c.customer_id, c.full_name, c.age, c.gender, c.phone, c.email, "
            "date(c.created_at) AS created_at, COUNT(p.policy_id) AS policies, "
            "COALESCE(SUM(p.premium_amount),0) AS total_premium "
            f"FROM CUSTOMER c LEFT JOIN INSURANCE_POLICY p ON p.customer_id = c.customer_id{w} "
            "GROUP BY c.customer_id ORDER BY c.full_name", params))
        cols = [{"key": "customer_id", "label": "ID", "type": "number"},
                {"key": "full_name", "label": "Customer"}, {"key": "age", "label": "Age", "type": "number"},
                {"key": "gender", "label": "Gender"}, {"key": "phone", "label": "Phone"},
                {"key": "email", "label": "Email"}, {"key": "created_at", "label": "Joined", "type": "date"},
                {"key": "policies", "label": "Policies", "type": "number"}, _money("total_premium", "Total premium")]
        summ = [S("Customers", len(rows)), S("Total premium", sum(r["total_premium"] for r in rows), "money")]
        return {"title": "Customer report", "columns": cols, "rows": rows, "summary": summ}

    if name == "agents":
        where, params = [], []
        if q:
            where.append("(a.agent_name LIKE ? OR a.email LIKE ?)")
            params += [_like(q)] * 2
        w = (" WHERE " + " AND ".join(where)) if where else ""
        rows = _rows(conn.execute(
            "SELECT a.agent_id, a.agent_name, a.phone, a.email, a.commission_percentage, a.status, "
            "COUNT(p.policy_id) AS policies, COALESCE(SUM(p.premium_amount),0) AS total_premium, "
            "ROUND(COALESCE(SUM(p.premium_amount),0) * a.commission_percentage / 100, 2) AS commission "
            f"FROM INSURANCE_AGENT a LEFT JOIN INSURANCE_POLICY p ON p.agent_id = a.agent_id{w} "
            "GROUP BY a.agent_id ORDER BY total_premium DESC", params))
        cols = [{"key": "agent_id", "label": "ID", "type": "number"}, {"key": "agent_name", "label": "Agent"},
                {"key": "phone", "label": "Phone"}, {"key": "email", "label": "Email"},
                {"key": "commission_percentage", "label": "Commission %", "type": "number"},
                {"key": "status", "label": "Status", "type": "badge"},
                {"key": "policies", "label": "Policies", "type": "number"},
                _money("total_premium", "Premium written"), _money("commission", "Commission earned")]
        summ = [S("Agents", len(rows)), S("Premium written", sum(r["total_premium"] for r in rows), "money"),
                S("Commission earned", sum(r["commission"] for r in rows), "money")]
        return {"title": "Agent report", "columns": cols, "rows": rows, "summary": summ}

    if name in ("policies", "policies-active", "policies-expired"):
        f = {"q": q, "policy_type": a.get("policy_type"), "status": a.get("status"),
             "date_from": df, "date_to": dt}
        if name == "policies-active":
            f["status"] = "Active"
        elif name == "policies-expired":
            f["status"] = "Expired"
        data = list_policies(conn, f, page=None)
        title = {"policies": "All policies", "policies-active": "Active policies",
                 "policies-expired": "Expired policies"}[name]
        cols = [{"key": "policy_id", "label": "Policy ID", "type": "number"},
                {"key": "customer_name", "label": "Customer"}, {"key": "agent_name", "label": "Agent"},
                {"key": "policy_type", "label": "Type"}, _money("premium_amount", "Premium"),
                _money("coverage_amount", "Coverage"), {"key": "start_date", "label": "Start", "type": "date"},
                {"key": "end_date", "label": "End", "type": "date"}, {"key": "status", "label": "Status", "type": "badge"}]
        s = data["summary"]
        return {"title": title, "columns": cols, "rows": data["items"],
                "summary": [S("Policies", s["count"]), S("Total premium", s["total_premium"], "money"),
                            S("Total coverage", s["total_coverage"], "money")]}

    if name == "by-type":
        having, params = "", []
        if a.get("min_count"):
            having = " HAVING COUNT(*) >= ?"
            params.append(a["min_count"])
        rows = _rows(conn.execute(
            "SELECT policy_type, COUNT(*) AS policies, SUM(premium_amount) AS total_premium, "
            "ROUND(AVG(premium_amount),2) AS avg_premium, MIN(premium_amount) AS min_premium, "
            "MAX(premium_amount) AS max_premium, SUM(coverage_amount) AS total_coverage "
            f"FROM INSURANCE_POLICY GROUP BY policy_type{having} ORDER BY policies DESC", params))
        cols = [{"key": "policy_type", "label": "Policy type"}, {"key": "policies", "label": "Policies", "type": "number"},
                _money("total_premium", "Total premium"), _money("avg_premium", "Average premium"),
                _money("min_premium", "Lowest"), _money("max_premium", "Highest"),
                _money("total_coverage", "Total coverage")]
        return {"title": "Policies grouped by type", "columns": cols, "rows": rows,
                "summary": [S("Types", len(rows)), S("Policies", sum(r["policies"] for r in rows)),
                            S("Total premium", sum(r["total_premium"] for r in rows), "money")]}

    if name == "customer-history":
        cid = a["customer_id"]
        cust = get_customer(conn, cid)
        rows = _rows(conn.execute(
            "SELECT p.policy_id, p.policy_type, p.premium_amount, p.coverage_amount, p.start_date, "
            "p.end_date, p.status, COUNT(cl.claim_id) AS claims, "
            "COALESCE(SUM(CASE WHEN cl.claim_status='Approved' THEN cl.claim_amount END),0) AS approved_total "
            "FROM INSURANCE_POLICY p LEFT JOIN CLAIM cl ON cl.policy_id = p.policy_id "
            "WHERE p.customer_id = ? GROUP BY p.policy_id ORDER BY p.start_date DESC", (cid,)))
        cols = [{"key": "policy_id", "label": "Policy ID", "type": "number"}, {"key": "policy_type", "label": "Type"},
                _money("premium_amount", "Premium"), _money("coverage_amount", "Coverage"),
                {"key": "start_date", "label": "Start", "type": "date"}, {"key": "end_date", "label": "End", "type": "date"},
                {"key": "status", "label": "Status", "type": "badge"}, {"key": "claims", "label": "Claims", "type": "number"},
                _money("approved_total", "Approved claims")]
        return {"title": f"Policy history - {cust['full_name']}" if cust else "Policy history",
                "columns": cols, "rows": rows,
                "summary": [S("Policies", len(rows)), S("Premium", sum(r["premium_amount"] for r in rows), "money"),
                            S("Approved claims", sum(r["approved_total"] for r in rows), "money")]}

    if name in ("claims-pending", "claims-approved", "claims-rejected"):
        status = name.split("-")[1].capitalize()
        f = {"status": status, "policy_id": a.get("policy_id"), "date_from": df, "date_to": dt}
        data = list_claims(conn, f, page=None)
        cols = [{"key": "claim_id", "label": "Claim ID", "type": "number"},
                {"key": "policy_id", "label": "Policy ID", "type": "number"},
                {"key": "customer_name", "label": "Customer"}, {"key": "policy_type", "label": "Policy type"},
                {"key": "claim_date", "label": "Claim date", "type": "date"}, _money("claim_amount", "Amount"),
                {"key": "claim_reason", "label": "Reason"}, {"key": "decision_date", "label": "Decision date", "type": "date"}]
        total = sum(r["claim_amount"] for r in data["items"])
        return {"title": f"{status} claims", "columns": cols, "rows": data["items"],
                "summary": [S("Claims", data["total"]), S("Total amount", total, "money")]}

    if name == "totals":
        rows = _rows(conn.execute(
            "SELECT p.policy_type, COUNT(*) AS policies, "
            "(SELECT COALESCE(SUM(premium_amount),0) FROM INSURANCE_POLICY x WHERE x.policy_type = p.policy_type) AS premiums, "
            "(SELECT COALESCE(SUM(c.claim_amount),0) FROM CLAIM c JOIN INSURANCE_POLICY y ON y.policy_id = c.policy_id "
            " WHERE y.policy_type = p.policy_type AND c.claim_status='Approved') AS approved_claims "
            "FROM INSURANCE_POLICY p GROUP BY p.policy_type ORDER BY premiums DESC"))
        for r in rows:
            r["ratio"] = round(r["approved_claims"] * 100.0 / r["premiums"], 1) if r["premiums"] else 0
        tp = conn.execute("SELECT COALESCE(SUM(premium_amount),0) FROM INSURANCE_POLICY").fetchone()[0]
        ta = conn.execute("SELECT COALESCE(SUM(claim_amount),0) FROM CLAIM WHERE claim_status='Approved'").fetchone()[0]
        cols = [{"key": "policy_type", "label": "Policy type"}, {"key": "policies", "label": "Policies", "type": "number"},
                _money("premiums", "Premiums recorded"), _money("approved_claims", "Approved claims"),
                {"key": "ratio", "label": "Claims / premium %", "type": "number"}]
        return {"title": "Premiums and approved claims", "columns": cols, "rows": rows,
                "summary": [S("Total premiums recorded", tp, "money"), S("Total approved claims", ta, "money")]}

    raise BusinessRuleError("Unknown report.", 404)


if __name__ == "__main__":
    # `python database.py` creates the database (and the admin account) without starting the web app.
    init_db(admin_username=os.environ.get("ADMIN_USERNAME", "admin"),
            admin_password=os.environ.get("ADMIN_PASSWORD", "Admin@123"),
            seed_demo=os.environ.get("SAFEGUARD_SEED_DEMO", "1") == "1",
            demo_users=os.environ.get("SAFEGUARD_ENV", "development").lower() != "production")
    print("Database ready:", default_db_path())
