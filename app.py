"""SafeGuard IMS - Flask application (pages + JSON API).

Run locally:   python app.py
Production:    gunicorn app:app        (see README.md)
"""
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import database as db

PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,14}[0-9]$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
DUMMY_HASH = generate_password_hash("not-a-real-password")
LOGIN_ATTEMPTS = {}  # ip -> [failure_count, locked_until_epoch]  (in-memory, per process)
MAX_FAILURES, LOCK_SECONDS = 5, 300

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,29}$")
ROLE_TITLES = {"admin": "System Admin", "manager": "Manager", "staff": "Staff"}
WEAK_PASSWORDS = {"password1", "password123", "12345678", "qwerty123", "abcd1234", "11111111a"}
REGISTRATIONS = {}  # ip -> [timestamps]  (in-memory throttle for public sign-ups)
MAX_REGISTRATIONS, REGISTRATION_WINDOW = 5, 3600

TRIGGER_MESSAGES = (
    "Claim amount exceeds the policy coverage amount.",
    "Only pending claims can be approved or rejected.",
)


# ============================================================ validation helpers
def _s(v):
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        n = float(str(v).strip())
        return n if n == n and n not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _iso(v):
    try:
        return datetime.strptime(_s(v), "%Y-%m-%d").date()
    except ValueError:
        return None


def validate_customer(d):
    out, err = {}, {}
    out["full_name"] = _s(d.get("full_name"))
    if len(out["full_name"]) < 2 or len(out["full_name"]) > 100:
        err["full_name"] = "Full name is required (2-100 characters)."
    out["age"] = _int(d.get("age"))
    if out["age"] is None or not 18 <= out["age"] <= 100:
        err["age"] = "Age must be a whole number between 18 and 100."
    out["gender"] = _s(d.get("gender"))
    if out["gender"] not in ("Male", "Female", "Other"):
        err["gender"] = "Select a gender."
    out["phone"] = _s(d.get("phone"))
    if not PHONE_RE.match(out["phone"]):
        err["phone"] = "Enter a valid phone number (7-15 digits; spaces, dashes and a leading + allowed)."
    out["email"] = _s(d.get("email")).lower()
    if not EMAIL_RE.match(out["email"]) or len(out["email"]) > 120:
        err["email"] = "Enter a valid email address."
    out["address"] = _s(d.get("address")) or None
    if out["address"] and len(out["address"]) > 255:
        err["address"] = "Address must be 255 characters or fewer."
    return out, err


def validate_agent(d):
    out, err = {}, {}
    out["agent_name"] = _s(d.get("agent_name"))
    if len(out["agent_name"]) < 2 or len(out["agent_name"]) > 100:
        err["agent_name"] = "Agent name is required (2-100 characters)."
    out["phone"] = _s(d.get("phone"))
    if not PHONE_RE.match(out["phone"]):
        err["phone"] = "Enter a valid phone number (7-15 digits; spaces, dashes and a leading + allowed)."
    out["email"] = _s(d.get("email")).lower()
    if not EMAIL_RE.match(out["email"]) or len(out["email"]) > 120:
        err["email"] = "Enter a valid email address."
    out["commission_percentage"] = _num(d.get("commission_percentage"))
    if out["commission_percentage"] is None or not 0 <= out["commission_percentage"] <= 50:
        err["commission_percentage"] = "Commission must be a number between 0 and 50."
    out["status"] = _s(d.get("status")) or "Active"
    if out["status"] not in ("Active", "Inactive"):
        err["status"] = "Status must be Active or Inactive."
    return out, err


def validate_policy(conn, d, policy_id=None):
    out, err = {}, {}
    out["customer_id"] = _int(d.get("customer_id"))
    if out["customer_id"] is None or not db.get_customer(conn, out["customer_id"]):
        err["customer_id"] = "Select an existing customer."
    out["agent_id"] = None
    if _s(d.get("agent_id")):
        out["agent_id"] = _int(d.get("agent_id"))
        agent = db.get_agent(conn, out["agent_id"]) if out["agent_id"] is not None else None
        if not agent:
            err["agent_id"] = "Select an existing agent or leave blank."
        elif agent["status"] != "Active":
            current = db.get_policy(conn, policy_id) if policy_id else None
            if not current or current["agent_id"] != out["agent_id"]:
                err["agent_id"] = "This agent is inactive and cannot be assigned to a policy."
    out["policy_type"] = _s(d.get("policy_type"))
    if out["policy_type"] not in db.POLICY_TYPES:
        err["policy_type"] = "Select a policy type."
    out["premium_amount"] = _num(d.get("premium_amount"))
    if out["premium_amount"] is None or out["premium_amount"] <= 0:
        err["premium_amount"] = "Premium must be a positive number."
    out["coverage_amount"] = _num(d.get("coverage_amount"))
    if out["coverage_amount"] is None or out["coverage_amount"] <= 0:
        err["coverage_amount"] = "Coverage must be a positive number."
    elif "premium_amount" not in err and out["premium_amount"] > out["coverage_amount"]:
        err["premium_amount"] = "Premium cannot be greater than the coverage amount."
    start, end = _iso(d.get("start_date")), _iso(d.get("end_date"))
    if not start:
        err["start_date"] = "Enter a valid start date."
    if not end:
        err["end_date"] = "Enter a valid end date."
    elif start and end <= start:
        err["end_date"] = "End date must be after the start date."
    out["start_date"], out["end_date"] = _s(d.get("start_date")), _s(d.get("end_date"))
    if policy_id and "coverage_amount" not in err:
        biggest = db.max_claim_amount(conn, policy_id)
        if out["coverage_amount"] < biggest:
            err["coverage_amount"] = f"Coverage cannot be lower than an existing claim ({biggest:,.0f})."
    if not err:
        out["status"] = db.derive_status(out["start_date"], out["end_date"],
                                         cancelled=_s(d.get("status")) == "Cancelled")
    return out, err


def validate_claim(conn, d):
    out, err = {}, {}
    policy = None
    out["policy_id"] = _int(d.get("policy_id"))
    if out["policy_id"] is not None:
        policy = db.get_policy(conn, out["policy_id"])
    if not policy:
        err["policy_id"] = "The selected policy does not exist."
    elif policy["status"] != "Active":
        err["policy_id"] = f"Claims can only be submitted for Active policies (this one is {policy['status']})."
    claim_date = _iso(d.get("claim_date"))
    out["claim_date"] = _s(d.get("claim_date"))
    if not claim_date:
        err["claim_date"] = "Enter a valid claim date."
    elif claim_date > date.today():
        err["claim_date"] = "Claim date cannot be in the future."
    elif policy and not (policy["start_date"] <= out["claim_date"] <= policy["end_date"]):
        err["claim_date"] = "Claim date must fall within the policy period."
    out["claim_amount"] = _num(d.get("claim_amount"))
    if out["claim_amount"] is None or out["claim_amount"] <= 0:
        err["claim_amount"] = "Claim amount must be a positive number."
    elif policy and out["claim_amount"] > policy["coverage_amount"]:
        err["claim_amount"] = f"Claim amount cannot exceed the policy coverage ({policy['coverage_amount']:,.0f})."
    out["claim_reason"] = _s(d.get("claim_reason"))
    if len(out["claim_reason"]) < 5 or len(out["claim_reason"]) > 500:
        err["claim_reason"] = "Describe the reason for the claim (5-500 characters)."
    return out, err


def validate_password(password, username=""):
    """Server-side password strength rules. Returns an error message or None."""
    if len(password) < 8 or len(password) > 128 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use 8-128 characters including at least one letter and one number."
    if username and password.lower() == username.lower():
        return "The password must not be the same as the username."
    if password.lower() in WEAK_PASSWORDS:
        return "That password is too common. Choose a less predictable one."
    return None


def validate_new_account(conn, d, allowed_roles):
    """Shared by public registration and admin account creation."""
    out, err = {}, {}
    out["username"] = _s(d.get("username"))
    if not USERNAME_RE.match(out["username"]):
        err["username"] = "Username must be 3-30 characters: letters, numbers, dot, underscore or hyphen."
    elif db.username_taken(conn, out["username"]):
        err["username"] = "That username is already taken."
    password, confirm = d.get("password") or "", d.get("confirm_password") or ""
    msg = validate_password(password, out["username"])
    if msg:
        err["password"] = msg
    elif password != confirm:
        err["confirm_password"] = "Passwords do not match."
    out["password"] = password
    out["role"] = _s(d.get("role")).lower() or "staff"
    if out["role"] not in db.ROLES:
        err["role"] = "Choose Admin, Manager or Staff."
    elif out["role"] not in allowed_roles:
        err["role"] = "Public registration can only create Staff accounts. Ask an administrator for Admin or Manager access."
    out.update(validate_personal(conn, d, None, err))
    return out, err


def validate_personal(conn, d, user_id, err):
    """display_name / email / phone shared by Profile and admin user creation (adds to err)."""
    out = {"display_name": _s(d.get("display_name")) or None, "email": _s(d.get("email")).lower() or None,
           "phone": _s(d.get("phone")) or None}
    if out["display_name"] and not 2 <= len(out["display_name"]) <= 60:
        err["display_name"] = "Display name must be 2-60 characters."
    if out["email"]:
        if not EMAIL_RE.match(out["email"]) or len(out["email"]) > 120:
            err["email"] = "Enter a valid email address."
        elif db.email_taken(conn, out["email"], user_id):
            err["email"] = "That email address is already used by another account."
    if out["phone"] and not PHONE_RE.match(out["phone"]):
        err["phone"] = "Enter a valid phone number (7-15 digits; spaces, dashes and a leading + allowed)."
    return out


def validation_error(errors):
    return jsonify(error="Please correct the highlighted fields.", errors=errors), 422


# ============================================================ app factory
def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    production = os.environ.get("SAFEGUARD_ENV", "development").lower() == "production"
    app.config.update(
        DATABASE=db.default_db_path(),
        PRODUCTION=production,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,  # cookies only over HTTPS in production
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=1024 * 1024,
        SEED_DEMO=os.environ.get("SAFEGUARD_SEED_DEMO", "1") == "1",
        ADMIN_USERNAME=os.environ.get("ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD") or (None if production else "Admin@123"),
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    secret = os.environ.get("SECRET_KEY") or app.config.get("SECRET_KEY")
    if not secret:
        if production:
            raise RuntimeError("SECRET_KEY environment variable is required when SAFEGUARD_ENV=production.")
        key_file = Path(app.instance_path) / "secret_key"  # development only; gitignored
        if not key_file.exists():
            key_file.write_text(secrets.token_hex(32))
        secret = key_file.read_text().strip()
    app.config["SECRET_KEY"] = secret

    db.init_db(app.config["DATABASE"], app.config["ADMIN_USERNAME"], app.config["ADMIN_PASSWORD"],
               app.config["SEED_DEMO"], demo_users=app.config["SEED_DEMO"] and not production)

    # -------------------------------------------------------- per-request plumbing
    def get_db():
        if "db" not in g:
            g.db = db.get_connection(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return session["csrf_token"]

    def current_user():
        """The signed-in user, loaded from the DATABASE on every request (never trusted from the cookie).

        A deleted or disabled account, or a changed role, therefore takes effect immediately."""
        if "user_obj" in g:
            return g.user_obj
        user = None
        uid = session.get("user_id")
        if uid:
            row = db.get_user(get_db(), uid)
            if row is not None and row["is_active"]:
                user = {"user_id": row["user_id"], "username": row["username"], "role": row["role"],
                        "display_name": row["display_name"]}
        g.user_obj = user
        return user

    @app.context_processor
    def inject():
        u = current_user()
        role = u["role"] if u else None
        name = (u["display_name"] or ("Administrator" if role == "admin" else u["username"])) if u else ""
        initials = "".join(w[0] for w in re.split(r"[\s._-]+", name) if w)[:2].upper() or "U"
        return {"csrf_token": csrf_token(), "current_user": u, "role": role, "display_name": name,
                "initials": initials, "role_title": ROLE_TITLES.get(role, ""),
                "is_admin": role == "admin", "can_write": role in ("admin", "manager"),
                "can_report": role in ("admin", "manager")}

    @app.before_request
    def csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(sent, expected):
                if request.path.startswith("/api/"):
                    return jsonify(error="Your session token is invalid or expired. Refresh the page and try again."), 400
                abort(400)

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def access(*roles):
        """Route decorator. access() = any signed-in user; access('admin','manager') = only those roles.

        Not signed in -> redirect to /login (pages) or 401 (API).  Wrong role -> HTTP 403."""
        def decorator(view):
            @wraps(view)
            def wrapped(*a, **kw):
                is_api = request.path.startswith("/api/")
                user = current_user()
                if user is None:
                    session.clear() if session.get("user_id") else None
                    if is_api:
                        return jsonify(error="Please sign in to continue."), 401
                    return redirect(url_for("login", next=request.path))
                session["role"] = user["role"]  # keep the cookie copy in step with the database
                if roles and user["role"] not in roles:
                    if is_api:
                        return jsonify(error="You do not have permission to do that."), 403
                    return render_template("error.html", code=403,
                                           message="You do not have permission to open this page."), 403
                if is_api:
                    db.sync_policy_statuses(get_db())  # keep derived policy statuses current
                return view(*a, **kw)
            return wrapped
        return decorator

    login_required = access()                  # Admin, Manager, Staff
    writer_required = access("admin", "manager")  # create / edit / delete / approve / reject
    admin_required = access("admin")           # user management and system settings

    # -------------------------------------------------------- error handlers
    @app.errorhandler(db.BusinessRuleError)
    def business_error(e):
        return jsonify(error=e.message, errors=e.errors), e.status

    @app.errorhandler(sqlite3.IntegrityError)
    def integrity_error(e):
        msg = str(e)
        if msg in TRIGGER_MESSAGES:
            return jsonify(error=msg), 409
        if "APP_USER.username" in msg:
            return jsonify(error="That username is already taken.", errors={"username": "That username is already taken."}), 409
        if "idx_user_email" in msg:
            return jsonify(error="That email address is already used by another account.",
                           errors={"email": "That email address is already used by another account."}), 409
        if "UNIQUE" in msg and "email" in msg.lower():
            return jsonify(error="That email address is already in use.",
                           errors={"email": "That email address is already in use."}), 409
        if "FOREIGN KEY" in msg:
            return jsonify(error="The referenced record does not exist or is still in use."), 409
        app.logger.warning("Integrity error: %s", msg)
        return jsonify(error="The database rejected this change because it breaks a data rule."), 409

    @app.errorhandler(sqlite3.Error)
    def sqlite_error(e):
        app.logger.exception("Database error")
        return jsonify(error="A database error occurred. Please try again."), 500

    @app.errorhandler(400)
    def bad_request(_e):
        return render_template("error.html", code=400, message="The request could not be verified."), 400

    @app.errorhandler(403)
    def forbidden(_e):
        if request.path.startswith("/api/"):
            return jsonify(error="You do not have permission to do that."), 403
        return render_template("error.html", code=403, message="You do not have permission to open this page."), 403

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found."), 404
        return render_template("error.html", code=404, message="That page does not exist."), 404

    @app.errorhandler(405)
    def not_allowed(_e):
        return jsonify(error="Method not allowed."), 405

    @app.errorhandler(500)
    def server_error(_e):
        if request.path.startswith("/api/"):
            return jsonify(error="Something went wrong on the server."), 500
        return render_template("error.html", code=500, message="Something went wrong on the server."), 500

    # -------------------------------------------------------- auth pages
    def safe_next(target):
        return target if target and target.startswith("/") and not target.startswith("//") else None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if current_user():
                return redirect(url_for("dashboard_page"))
            return render_template("login.html", next=request.args.get("next", ""))

        ip = request.remote_addr or "unknown"
        fails, locked_until = LOGIN_ATTEMPTS.get(ip, [0, 0])
        if locked_until > time.time():
            return render_template("login.html", error="Too many failed attempts. Try again in a few minutes."), 429
        username, password = _s(request.form.get("username")), request.form.get("password") or ""
        if not username or not password:
            return render_template("login.html", error="Enter your username and password.", username=username), 400
        user = db.get_user_by_username(get_db(), username)
        try:
            ok = check_password_hash(user["password_hash"] if user else DUMMY_HASH, password)
        except (ValueError, TypeError):  # a malformed stored hash must never crash the login page
            ok = False
        if not (user and ok):
            fails += 1
            LOGIN_ATTEMPTS[ip] = [fails, time.time() + LOCK_SECONDS if fails >= MAX_FAILURES else 0]
            return render_template("login.html", error="Invalid username or password.", username=username), 401
        if not user["is_active"]:
            return render_template("login.html", error="This account has been disabled. Contact an administrator.",
                                   username=username), 403
        LOGIN_ATTEMPTS.pop(ip, None)
        session.clear()  # new session on login (prevents session fixation)
        session["user_id"], session["username"], session["role"] = user["user_id"], user["username"], user["role"]
        session["csrf_token"] = secrets.token_hex(16)
        session.permanent = bool(request.form.get("remember"))
        return redirect(safe_next(request.form.get("next")) or url_for("dashboard_page"))

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """Public sign-up. SECURITY: it can only ever create STAFF (read-only) accounts.

        Admin and Manager accounts are created by an administrator on the User Management page."""
        user = current_user()
        if user:  # signed-in users have no reason to register again
            return redirect(url_for("users_page" if user["role"] == "admin" else "dashboard_page"))
        if request.method == "GET":
            return render_template("register.html", form={}, errors={})
        ip = request.remote_addr or "unknown"
        recent = [t for t in REGISTRATIONS.get(ip, []) if t > time.time() - REGISTRATION_WINDOW]
        if len(recent) >= MAX_REGISTRATIONS:
            return render_template("register.html", form=request.form, errors={},
                                   error="Too many sign-ups from this connection. Please try again later."), 429
        data, errors = validate_new_account(get_db(), request.form, allowed_roles=("staff",))
        if errors:
            privileged = "role" in errors and data["role"] in ("admin", "manager")
            return render_template("register.html", form=request.form, errors=errors), (403 if privileged else 422)
        db.create_user(get_db(), data["username"], generate_password_hash(data["password"]), "staff")
        REGISTRATIONS[ip] = recent + [time.time()]
        flash("Account created successfully. You can now sign in.", "success")
        return redirect(url_for("login"))

    # -------------------------------------------------------- html pages
    @app.route("/")
    def index():
        return redirect(url_for("dashboard_page"))

    def make_page(path, endpoint, template, active, crumb, roles=()):
        @access(*roles)
        def view():
            return render_template(template, active=active, crumb=crumb)
        view.__name__ = endpoint
        app.add_url_rule(path, endpoint, view)

    make_page("/dashboard", "dashboard_page", "dashboard.html", "dashboard", "Overview")
    make_page("/customers", "customers_page", "customers.html", "customers", "Customers")
    make_page("/agents", "agents_page", "agents.html", "agents", "Agents")
    make_page("/policies", "policies_page", "policies.html", "policies", "Policies")
    make_page("/claims", "claims_page", "claims.html", "claims", "Claims")
    make_page("/reports", "reports_page", "reports.html", "reports", "Reports", roles=("admin", "manager"))
    make_page("/settings", "settings_page", "settings.html", "settings", "Settings", roles=("admin",))
    make_page("/profile", "profile_page", "profile.html", "profile", "My Profile")
    make_page("/users", "users_page", "users.html", "users", "User Management", roles=("admin",))

    # -------------------------------------------------------- JSON helpers
    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def paging(default=10):
        page = max(1, _int(request.args.get("page")) or 1)
        per = min(100, max(1, _int(request.args.get("per_page")) or default))
        return page, per

    def arg(name):
        return _s(request.args.get(name)) or None

    # -------------------------------------------------------- API: dashboard & utility
    @app.get("/api/dashboard")
    @login_required
    def api_dashboard():
        return jsonify(db.dashboard(get_db()))

    @app.get("/api/notifications")
    @login_required
    def api_notifications():
        return jsonify(db.notifications(get_db()))

    @app.get("/api/search")
    @login_required
    def api_search():
        q = arg("q")
        if not q or len(q) < 2:
            return jsonify(customers=[], policies=[])
        return jsonify(db.search(get_db(), q))

    @app.get("/api/lookups")
    @login_required
    def api_lookups():
        return jsonify(db.lookups(get_db()))

    @app.get("/api/premium-estimate")
    @writer_required
    def api_premium_estimate():
        ptype = arg("policy_type")
        coverage, age = _num(request.args.get("coverage_amount")), _int(request.args.get("age"))
        if ptype not in db.POLICY_TYPES or coverage is None or coverage <= 0:
            return validation_error({"policy_type": "Choose a policy type and a coverage amount first."})
        premium, rate, factor = db.estimate_premium(ptype, coverage, age)
        return jsonify(premium=premium, base_rate=rate, age_factor=factor,
                       note="Educational formula only - not a real insurer's underwriting model.")

    # -------------------------------------------------------- API: customers
    @app.get("/api/customers")
    @login_required
    def api_customers():
        page, per = paging()
        return jsonify(db.list_customers(get_db(), arg("q"), page, per))

    @app.post("/api/customers")
    @writer_required
    def api_customer_create():
        data, errors = validate_customer(body())
        if errors:
            return validation_error(errors)
        cid = db.insert_customer(get_db(), data)
        return jsonify(message="Customer added successfully.", item=db.get_customer(get_db(), cid)), 201

    @app.get("/api/customers/<int:cid>")
    @login_required
    def api_customer_get(cid):
        item = db.get_customer(get_db(), cid)
        return (jsonify(item) if item else (jsonify(error="Customer not found."), 404))

    @app.put("/api/customers/<int:cid>")
    @writer_required
    def api_customer_update(cid):
        if not db.get_customer(get_db(), cid):
            return jsonify(error="Customer not found."), 404
        data, errors = validate_customer(body())
        if errors:
            return validation_error(errors)
        db.update_customer(get_db(), cid, data)
        return jsonify(message="Customer updated successfully.", item=db.get_customer(get_db(), cid))

    @app.delete("/api/customers/<int:cid>")
    @writer_required
    def api_customer_delete(cid):
        if not db.get_customer(get_db(), cid):
            return jsonify(error="Customer not found."), 404
        db.delete_customer(get_db(), cid)
        return jsonify(message="Customer deleted.")

    # -------------------------------------------------------- API: agents
    @app.get("/api/agents")
    @login_required
    def api_agents():
        page, per = paging()
        return jsonify(db.list_agents(get_db(), arg("q"), arg("status"), page, per))

    @app.post("/api/agents")
    @writer_required
    def api_agent_create():
        data, errors = validate_agent(body())
        if errors:
            return validation_error(errors)
        aid = db.insert_agent(get_db(), data)
        return jsonify(message="Agent added successfully.", item=db.get_agent(get_db(), aid)), 201

    @app.get("/api/agents/<int:aid>")
    @login_required
    def api_agent_get(aid):
        item = db.get_agent(get_db(), aid)
        return (jsonify(item) if item else (jsonify(error="Agent not found."), 404))

    @app.put("/api/agents/<int:aid>")
    @writer_required
    def api_agent_update(aid):
        if not db.get_agent(get_db(), aid):
            return jsonify(error="Agent not found."), 404
        data, errors = validate_agent(body())
        if errors:
            return validation_error(errors)
        db.update_agent(get_db(), aid, data)
        return jsonify(message="Agent updated successfully.", item=db.get_agent(get_db(), aid))

    @app.patch("/api/agents/<int:aid>/status")
    @writer_required
    def api_agent_status(aid):
        status = _s(body().get("status"))
        if status not in ("Active", "Inactive"):
            return validation_error({"status": "Status must be Active or Inactive."})
        if not db.set_agent_status(get_db(), aid, status):
            return jsonify(error="Agent not found."), 404
        return jsonify(message=f"Agent marked {status}.", item=db.get_agent(get_db(), aid))

    @app.delete("/api/agents/<int:aid>")
    @writer_required
    def api_agent_delete(aid):
        if not db.get_agent(get_db(), aid):
            return jsonify(error="Agent not found."), 404
        db.delete_agent(get_db(), aid)
        return jsonify(message="Agent deleted.")

    # -------------------------------------------------------- API: policies
    def policy_filters():
        status, ptype = arg("status"), arg("policy_type")
        return {"q": arg("q"), "customer_id": _int(arg("customer_id")), "agent_id": _int(arg("agent_id")),
                "policy_type": ptype if ptype in db.POLICY_TYPES else None,
                "status": status if status in ("Active", "Pending", "Expired", "Cancelled") else None}

    @app.get("/api/policies")
    @login_required
    def api_policies():
        page, per = paging()
        return jsonify(db.list_policies(get_db(), policy_filters(), page, per))

    @app.post("/api/policies")
    @writer_required
    def api_policy_create():
        data, errors = validate_policy(get_db(), body())
        if errors:
            return validation_error(errors)
        pid = db.insert_policy(get_db(), data)
        return jsonify(message="Policy created successfully.", item=db.get_policy(get_db(), pid)), 201

    @app.get("/api/policies/<int:pid>")
    @login_required
    def api_policy_get(pid):
        item = db.get_policy(get_db(), pid)
        return (jsonify(item) if item else (jsonify(error="Policy not found."), 404))

    @app.put("/api/policies/<int:pid>")
    @writer_required
    def api_policy_update(pid):
        if not db.get_policy(get_db(), pid):
            return jsonify(error="Policy not found."), 404
        data, errors = validate_policy(get_db(), body(), policy_id=pid)
        if errors:
            return validation_error(errors)
        db.update_policy(get_db(), pid, data)
        return jsonify(message="Policy updated successfully.", item=db.get_policy(get_db(), pid))

    # -------------------------------------------------------- API: claims
    @app.get("/api/claims")
    @login_required
    def api_claims():
        page, per = paging()
        status = arg("status")
        f = {"claim_id": _int(arg("claim_id")), "policy_id": _int(arg("policy_id")),
             "customer_id": _int(arg("customer_id")),
             "status": status if status in ("Pending", "Approved", "Rejected") else None}
        return jsonify(db.list_claims(get_db(), f, page, per))

    @app.post("/api/claims")
    @writer_required
    def api_claim_create():
        data, errors = validate_claim(get_db(), body())
        if errors:
            return validation_error(errors)
        cid = db.insert_claim(get_db(), data)
        return jsonify(message="Claim submitted and is pending review.", item=db.get_claim(get_db(), cid)), 201

    @app.get("/api/claims/<int:cid>")
    @login_required
    def api_claim_get(cid):
        item = db.get_claim(get_db(), cid)
        if not item:
            return jsonify(error="Claim not found."), 404
        item["history"] = db.claim_history(get_db(), item["policy_id"])
        return jsonify(item)

    def decide(cid, status):
        note = _s(body().get("note")) or None
        if note and len(note) > 300:
            return validation_error({"note": "Note must be 300 characters or fewer."})
        db.decide_claim(get_db(), cid, status, note)
        return jsonify(message=f"Claim #{cid} {status.lower()}.", item=db.get_claim(get_db(), cid))

    @app.post("/api/claims/<int:cid>/approve")
    @writer_required
    def api_claim_approve(cid):
        return decide(cid, "Approved")

    @app.post("/api/claims/<int:cid>/reject")
    @writer_required
    def api_claim_reject(cid):
        return decide(cid, "Rejected")

    # -------------------------------------------------------- API: reports
    REPORTS = {"customers", "agents", "policies", "policies-active", "policies-expired", "by-type",
               "customer-history", "claims-pending", "claims-approved", "claims-rejected", "totals"}

    @app.get("/api/reports/<name>")
    @writer_required
    def api_report(name):
        if name not in REPORTS:
            return jsonify(error="Unknown report."), 404
        args, errors = {"q": arg("q")}, {}
        for key in ("date_from", "date_to"):
            if arg(key):
                if _iso(arg(key)):
                    args[key] = arg(key)
                else:
                    errors[key] = "Enter a valid date."
        if args.get("date_from") and args.get("date_to") and args["date_from"] > args["date_to"]:
            errors["date_to"] = "The end date must not be before the start date."
        ptype, status = arg("policy_type"), arg("status")
        args["policy_type"] = ptype if ptype in db.POLICY_TYPES else None
        args["status"] = status if status in ("Active", "Pending", "Expired", "Cancelled") else None
        args["policy_id"] = _int(arg("policy_id"))
        args["min_count"] = _int(arg("min_count"))
        if name == "customer-history":
            args["customer_id"] = _int(arg("customer_id"))
            if args["customer_id"] is None:
                errors["customer_id"] = "Select a customer."
        if errors:
            return validation_error(errors)
        return jsonify(db.report(get_db(), name, args))

    # -------------------------------------------------------- API: settings
    @app.get("/api/settings")
    @admin_required
    def api_settings():
        return jsonify(database=Path(app.config["DATABASE"]).name, counts=db.table_counts(get_db()),
                       environment="production" if app.config["PRODUCTION"] else "development")

    # -------------------------------------------------------- API: my profile (ANY signed-in role)
    # The account is ALWAYS the one in the server-side session. No user_id is ever read from the
    # URL, form or JSON body, so a user cannot reach someone else's profile or change their own role.
    PRIVILEGED_FIELDS = ("user_id", "role", "is_active", "username")

    @app.get("/api/profile")
    @login_required
    def api_profile():
        return jsonify(db.public_user(get_db(), current_user()["user_id"]))

    @app.put("/api/profile")
    @login_required
    def api_profile_update():
        me = db.public_user(get_db(), current_user()["user_id"])
        data = body()
        for field in PRIVILEGED_FIELDS:
            if field in data and str(data[field]).strip().lower() != str(me[field]).strip().lower():
                return jsonify(error="You cannot change your role, username or account status here."), 403
        errors = {}
        values = validate_personal(get_db(), data, me["user_id"], errors)
        if errors:
            return validation_error(errors)
        db.update_profile(get_db(), me["user_id"], values["display_name"], values["email"], values["phone"])
        return jsonify(message="Profile updated successfully.", item=db.public_user(get_db(), me["user_id"]))

    @app.post("/api/profile/password")
    @login_required
    def api_profile_password():
        me = db.get_user(get_db(), current_user()["user_id"])
        d = body()
        current, new, confirm = d.get("current_password") or "", d.get("new_password") or "", d.get("confirm_password") or ""
        errors = {}
        try:
            current_ok = check_password_hash(me["password_hash"], current)
        except (ValueError, TypeError):
            current_ok = False
        if not current_ok:
            errors["current_password"] = "Current password is incorrect."
        msg = validate_password(new, me["username"])
        if msg:
            errors["new_password"] = msg
        elif new == current:
            errors["new_password"] = "The new password must be different from the current one."
        if new != confirm:
            errors["confirm_password"] = "Passwords do not match."
        if errors:
            return validation_error(errors)
        db.update_password(get_db(), me["user_id"], generate_password_hash(new))
        return jsonify(message="Password changed successfully.")

    # -------------------------------------------------------- API: user management (ADMIN only)
    @app.get("/api/users")
    @admin_required
    def api_users():
        return jsonify(items=db.list_users(get_db()), me=current_user()["user_id"])

    @app.post("/api/users")
    @admin_required
    def api_user_create():
        data, errors = validate_new_account(get_db(), body(), allowed_roles=db.ROLES)
        if errors:
            return validation_error(errors)
        uid = db.create_user(get_db(), data["username"], generate_password_hash(data["password"]), data["role"],
                             data["display_name"], data["email"], data["phone"])
        return jsonify(message=f"{data['role'].capitalize()} account '{data['username']}' created.",
                       item=db.public_user(get_db(), uid)), 201

    def user_or_404(uid):
        return db.public_user(get_db(), uid)

    @app.patch("/api/users/<int:uid>/role")
    @admin_required
    def api_user_role(uid):
        role = _s(body().get("role")).lower()
        if role not in db.ROLES:
            return validation_error({"role": "Choose Admin, Manager or Staff."})
        if not user_or_404(uid):
            return jsonify(error="User not found."), 404
        db.set_user_role(get_db(), uid, role)
        return jsonify(message=f"Role changed to {role}.", item=user_or_404(uid))

    @app.patch("/api/users/<int:uid>/active")
    @admin_required
    def api_user_active(uid):
        active = body().get("is_active")
        if not isinstance(active, bool):
            return validation_error({"is_active": "Send true or false."})
        if not user_or_404(uid):
            return jsonify(error="User not found."), 404
        db.set_user_active(get_db(), uid, active)
        return jsonify(message="Account enabled." if active else "Account disabled.", item=user_or_404(uid))

    @app.post("/api/users/<int:uid>/password")
    @admin_required
    def api_user_reset_password(uid):
        target = user_or_404(uid)
        if not target:
            return jsonify(error="User not found."), 404
        d = body()
        new, confirm = d.get("new_password") or "", d.get("confirm_password") or ""
        errors = {}
        msg = validate_password(new, target["username"])
        if msg:
            errors["new_password"] = msg
        if new != confirm:
            errors["confirm_password"] = "Passwords do not match."
        if errors:
            return validation_error(errors)
        db.update_password(get_db(), uid, generate_password_hash(new))
        return jsonify(message=f"Password reset for '{target['username']}'.")

    @app.delete("/api/users/<int:uid>")
    @admin_required
    def api_user_delete(uid):
        if uid == current_user()["user_id"]:
            return jsonify(error="You cannot delete your own account while signed in."), 409
        target = user_or_404(uid)
        if not target:
            return jsonify(error="User not found."), 404
        db.delete_user(get_db(), uid)
        return jsonify(message=f"User '{target['username']}' deleted.")

    return app


app = create_app() if os.environ.get("SAFEGUARD_NO_AUTOCREATE") != "1" else None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)),
            debug=not app.config["PRODUCTION"])
