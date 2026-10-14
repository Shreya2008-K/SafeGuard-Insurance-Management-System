# SafeGuard IMS — Insurance Management System

*Secure. Manage. Protect.*

A full-stack web application for a college PAT 4 practical: **Flask + SQLite + HTML/CSS/vanilla JavaScript**.
An administrator signs in, manages customers and agents, creates policies, processes claims and runs reports.
Every figure, table and chart is read from the SQLite database.

> **Honest scope note:** this is an academic project. Login, password hashing, CSRF protection and parameterised SQL are
> implemented, but that does not make it production-hardened. Policy status rules, claim validation and the premium
> calculator are **educational business rules**, not how a real insurer underwrites or settles claims.

---

## 1. Project structure

```
SafeGuard-IMS/
├── app.py                 Flask app: auth, CSRF, page routes, JSON API, validation
├── database.py            SQLite access layer (connection, init, migration, all SQL queries)
├── migrate.py             Safe, repeatable database upgrade + user diagnostics (see section 3d)
├── requirements.txt
├── database/
│   ├── schema.sql         DDL: tables, ALTER, indexes, views, triggers
│   ├── seed.sql           Demo data (relative dates, so it always looks current)
│   └── queries.sql        Labelled SQL demonstration queries
├── templates/             base.html (sidebar + header), login, register, dashboard, customers, agents,
│                          policies, claims, reports, users, profile, settings (+ _icons.html, error.html)
├── static/
│   ├── css/style.css
│   └── js/                app.js (shared helpers), one script per page (incl. register.js, profile.js, users.js), vendor/chart.umd.js (Chart.js, bundled)
└── tests/test_app.py      82 automated tests
```
Added beyond the requested layout, for clear reasons: `tests/` (testing requirement), `static/js/settings.js` (Settings page
behaviour), `static/js/vendor/chart.umd.js` (Chart.js bundled so charts work offline), `templates/_icons.html` and `error.html`.

## 2. Run on Windows with VS Code

1. **Check Python** (3.10 or newer). In the VS Code terminal (`Ctrl+\``, PowerShell):
   ```powershell
   python --version
   ```
   If it fails, install Python from python.org and tick *Add python.exe to PATH*.
2. **Open the folder**: *File → Open Folder…* → `SafeGuard-IMS`.
3. **Create a virtual environment**:
   ```powershell
   python -m venv .venv
   ```
4. **Activate it**:
   ```powershell
   .venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks scripts, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use `.venv\Scripts\activate.bat` in Command Prompt.
   In VS Code, choose the `.venv` interpreter (`Ctrl+Shift+P` → *Python: Select Interpreter*).
5. **Install dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```
   (Gunicorn is skipped on Windows automatically.)
6. **Start the app**:
   ```powershell
   python app.py
   ```
7. Open **http://127.0.0.1:5000** and sign in.

### Database initialisation
Automatic. On first start the app creates `instance/safeguard.db`, runs `database/schema.sql`, loads `database/seed.sql`
(12 customers, 6 agents, 24 policies, 15 claims) and creates the administrator. To initialise without starting the web server:
```powershell
python database.py
```
To reset to a fresh demo database, stop the app and delete the `instance` folder's `safeguard.db`, then start again.

### Accounts
The admin is **not** hard-coded in the code or the seed file. `database.init_db()` creates it on first run, only if no user
exists, storing a **Werkzeug password hash**. Credentials come from environment variables:

| Variable | Development default | Production |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | set it |
| `ADMIN_PASSWORD` | `Admin@123` (development only) | **required**, app refuses to start without it on a new DB |

In a brand-new **development** database only, two extra demo accounts are created so you can try every role immediately:

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `Admin@123` |
| Manager | `manager` | `Manager@123` |
| Staff | `staff` | `Staff@123` |

They are never created when `SAFEGUARD_ENV=production`, and never added to an existing database. Change passwords on the **Profile** page,
or delete the demo accounts under **User Management**.
To start with your own admin password (PowerShell): `$env:ADMIN_PASSWORD="MyStrongPass1"; python app.py` (before the first start).

## 3. Features

* **Login/logout/Create Account** — Flask sessions, hashed passwords, CSRF token on every state-changing request, "remember me" (7-day persistent session), temporary lockout after 5 failed attempts (per process).
* **Dashboard** — four live stat cards, donut chart (active policies by type), grouped bar chart (approved/pending/rejected claims for the last 6 months), recent policies and claims. Empty database shows zero states.
* **Customers** — add, search (ID/name/email/phone), edit, delete (blocked while policies exist), pagination, view a customer's policies.
* **Agents** — add, search, edit, activate/deactivate, delete (blocked while policies exist), commission 0–50%.
* **Policies** — create/edit, search by ID or customer, filter by type/status, total premium and coverage of the filtered set, illustrative premium calculator.
* **Claims** — submit, search by claim or policy ID, filter by status, review details and claim history, approve/reject pending claims (atomic transaction).
* **Reports** — 11 reports (customers, agents, all/active/expired policies, by type, customer policy history, pending/approved/rejected claims, premium and approved-claim totals) with filters, sortable columns and a print-friendly layout.
* **Profile** (every role) — own username/role (read-only), display name, email, phone, password change.
* **User Management** (Admin only) — create Admin/Manager/Staff accounts, change roles, disable/enable, reset passwords, delete.
* **Settings** (Admin only) — database counts and environment.
* **Global search** and **notifications** (pending claims, policies ending within 30 days) in the header.
* Currency is formatted as Indian rupees (₹). Change `currency: 'INR'` / `'en-IN'` at the top of `static/js/app.js` for another currency.

## 3a. Roles and permissions

Permissions are enforced **on the server** by the `access()` decorator in `app.py` (`login_required`, `writer_required`,
`admin_required`). The role is read from the **database on every request**, so a demoted, disabled or deleted account loses access immediately.
Hidden buttons are only a convenience. Unauthorized requests get **HTTP 403** (JSON for `/api/...`, an error page for pages); not-signed-in requests are redirected to `/login` (pages) or get 401 (API).

| Capability | Admin | Manager | Staff |
|---|:--:|:--:|:--:|
| Log in, Dashboard, own Profile + password | yes | yes | yes |
| View customers, agents, policies, claims | yes | yes | yes |
| Add / edit / delete customers, agents; create / edit policies; submit, approve, reject claims | yes | yes | no (403) |
| Reports, premium calculator | yes | yes | no (403) |
| Settings | yes | no (403) | no (403) |
| User Management (create accounts, assign roles, disable, reset password, delete) | yes | no (403) | no (403) |

**Last-admin protection:** the last *active* Admin cannot be demoted, disabled or deleted (checked in `database.py`, inside a locking transaction).

## 3b. Registration workflow (final design)

* **Create Account** (`/register`, linked from the login page) shows the dropdown **Admin / Manager / Staff**, but Admin and Manager are *disabled* options.
* Public sign-up can **only create Staff** (read-only) accounts. The server rejects any attempt to post `role=admin` or `role=manager` with **HTTP 403**, whatever the browser shows.
* After success the visitor sees "Account created successfully" and is redirected to the login page.
* **Admin and Manager accounts are created only by a signed-in Admin** on **User Management**, which offers all three roles.
* Safeguards: CSRF token on the form, username rules (3-30 letters/numbers/`.`/`_`/`-`, case-insensitive unique), password rules (8-128 chars, a letter and a number, not equal to the username, not a very common password), Werkzeug password hashing, and a small per-connection sign-up limit (5 per hour).

### Profile security
`/api/profile` always works on the account in the **server-side session**. No `user_id` from a URL, form or JSON body is ever used, a request that tries to send a different `role`, `user_id`, `username` or `is_active` gets 403, and password hashes are never returned by any endpoint.

## 3c. Why Manager/Staff accounts could not work (root cause)

I reproduced this on the original code before changing it:
1. `APP_USER.role` had `CHECK (role IN ('admin','staff'))`, so SQLite refused to store a **manager** row at all (`CHECK constraint failed`).
2. Nothing in the app created non-admin accounts: there was no registration route and no user-management page in this project, so the only account that could exist by default was the admin.
3. There was no role check anywhere, so every signed-in user could do everything.

A *staff* row with a proper hash logged in fine in this code. If a Staff/Manager login still fails on your own copy, run `python migrate.py --check`: it lists every user, role and whether the stored password is a valid hash (hashes are never printed). An account marked `INVALID` was created with a plain-text or non-Werkzeug password; fix it with **User Management → Reset password**.

## 3d. Upgrading an existing database (migration)

Editing `schema.sql` does **not** change a database that already exists. `database.migrate_database()` (run by `migrate.py`, and automatically at app start) upgrades it safely:

* Rebuilds `APP_USER` so `role` accepts `admin`, `manager`, `staff` (SQLite cannot change a CHECK constraint in place), copying **user IDs, usernames, password hashes, roles and created dates unchanged**; adds `display_name`, `email`, `phone`, `is_active` (existing users become active).
* Adds `CLAIM.decision_note` **only if it is missing** (so there is no "duplicate column" error), and a unique e-mail index.
* Checks foreign keys before committing; everything runs in **one transaction** that rolls back on any error.
* Takes a **backup first** (`<database folder>\backups\safeguard-backup-YYYYMMDD-HHMMSS.db`), only when a change is actually needed.
* Running it again changes nothing and makes no new backup. No customer, agent, policy or claim data is touched.

**Local (PowerShell, inside the project folder with the venv active):**
```powershell
# 1. stop the app (Ctrl+C), then manually copy the database as an extra safety net
New-Item -ItemType Directory -Force backups_manual | Out-Null
Copy-Item instance\safeguard.db "backups_manual\safeguard-$(Get-Date -Format yyyyMMdd-HHmmss).db"

# 2. see what would change (changes nothing) and check your users
python migrate.py --check

# 3. run the migration (makes its own backup in instance\backups first)
python migrate.py

# 4. start the app
python app.py
```
If you use a custom location: `python migrate.py --db C:\path\to\safeguard.db`.

**PythonAnywhere (Bash console):**
```bash
cd ~/SafeGuard-IMS && source .venv/bin/activate
mkdir -p ~/manual-backups && cp ~/safeguard-data/safeguard.db ~/manual-backups/safeguard-$(date +%Y%m%d-%H%M%S).db   # use your SAFEGUARD_DB_PATH
export SAFEGUARD_DB_PATH=~/safeguard-data/safeguard.db        # same path as in your WSGI file
python migrate.py --check
python migrate.py
```
then press **Reload** on the Web tab.

### Deploying this update to PythonAnywhere
Every changed file must be uploaded, not only `app.py`. Replace these (keep your `instance/` or `safeguard-data/` database and your WSGI settings):
`app.py`, `database.py`, **`migrate.py` (new)**, `database/schema.sql`, `database/queries.sql`, all of `templates/` (new: `register.html`, `users.html`, `profile.html`; changed: `base.html`, `login.html`, `dashboard.html`, `settings.html`, `_icons.html`), `static/css/style.css`, and all of `static/js/` (new: `register.js`, `profile.js`, `users.js`; changed: `app.js`, `customers.js`, `agents.js`, `policies.js`, `claims.js`, `settings.js`), `tests/test_app.py`, `README.md`.
Then: back up the database, run the migration commands above, and **Reload** the web app. No new Python packages are needed (`requirements.txt` is unchanged).

## 4. Business rules (educational)

* **Policy status is derived, never typed in:** *Cancelled* (explicit) → *Expired* (end date passed) → *Pending* (starts in the future) → *Active*. `database.derive_status()` applies this on save, and `sync_policy_statuses()` re-applies it on every API request so stored values never go stale.
* **Policy rules:** customer must exist; agent (optional) must exist and be active; premium and coverage positive; premium ≤ coverage; valid ISO dates with end after start; coverage cannot be lowered below an existing claim.
* **Claim rules:** policy must exist and be Active; claim date valid, not in the future and inside the policy period; amount positive and not above the policy coverage; reason 5–500 characters; only *Pending* claims can be approved or rejected.
* **Premium calculator:** `coverage × base rate(type) × age factor` (base rates 1.2% life, 3.0% health, 2.5% vehicle, 0.4% property; age factor +1.5% per year over 30 for life/health, capped at 2×). It is only a teaching formula.
* The claim approval/rejection, status derivation and premium calculation are **Python business logic** (plus SQL triggers where stated below). They are not Oracle stored procedures or PL/SQL.

## 5. Database

SQLite file `safeguard.db`, foreign keys enabled on **every** connection (`PRAGMA foreign_keys = ON` in `database.get_connection`). Dates are ISO text (`YYYY-MM-DD`).

| Object | Details |
|---|---|
| Tables | `APP_USER` (roles admin/manager/staff, display_name, email, phone, is_active), `CUSTOMER`, `INSURANCE_AGENT`, `INSURANCE_POLICY`, `CLAIM` with PRIMARY KEY, FOREIGN KEY (`ON DELETE RESTRICT`), NOT NULL, UNIQUE, CHECK and DEFAULT constraints |
| ALTER TABLE | `ALTER TABLE ... ADD COLUMN` is used by the guarded migration (`database.migrate_database`) to add `APP_USER.display_name/email/phone/is_active` and `CLAIM.decision_note` to older databases; a scratch-table demo is in `queries.sql` |
| Indexes | customer name, agent name, policy customer/agent/status/type, claim policy/status/date |
| Views | `V_POLICY_DETAILS`, `V_CLAIM_DETAILS`, `V_POLICY_TYPE_SUMMARY` (used by the API) |
| Triggers | `trg_claim_within_coverage` (claim ≤ coverage), `trg_claim_only_pending_changes`, `trg_claim_stamp_decision_date` — all exercised in `tests/test_app.py` |

### SQL demonstration guide
`database/queries.sql` contains labelled examples: DDL/ALTER, INSERT/SELECT/UPDATE/DELETE (inside a rolled-back transaction),
WHERE, ORDER BY, GROUP BY, HAVING, INNER JOIN, LEFT JOIN, COUNT/SUM/AVG/MIN/MAX, subqueries, views, trigger demonstrations and foreign-key failures.

Run it with the SQLite shell, or open `instance/safeguard.db` in *DB Browser for SQLite*:
```powershell
sqlite3 instance\safeguard.db ".read database/queries.sql"
```
The statements in sections 10–11 are *meant* to fail (that is the demonstration). Where each SQL feature appears in the app:

* Dashboard → aggregate `COUNT`, `GROUP BY`, `strftime` queries (`database.dashboard`)
* Reports → `LEFT JOIN … GROUP BY`, `HAVING`, `SUM/AVG/MIN/MAX`, subqueries (`database.report`)
* Policies / claims lists → views + `WHERE` filters + `LIMIT/OFFSET` pagination
* All values reach SQL through `?` placeholders; only fixed fragments written in the code are formatted into SQL strings.

## 6. Testing

**Automated** (82 tests: login for all three roles, permissions matrix and HTTP 403, registration, user management and last-admin protection, profile security, migration safety, CSRF, CRUD, validation, triggers, foreign keys, dashboard figures, reports; it takes about a minute because every login hashes a password):
```powershell
python -m unittest discover -s tests -v
```
Tests use a temporary database, never `instance/safeguard.db`.

**Manual test cases**

| # | Action | Expected result |
|---|---|---|
| 1 | Sign in with `admin` / `Admin@123` | Dashboard opens |
| 2 | Sign in with a wrong password | "Invalid username or password."; no session |
| 3 | Click **Logout**, then open `/dashboard` | Redirected to the login page |
| 4 | Open `/customers` while signed out | Redirected to the login page |
| 5 | Customers → Add Customer with valid data | Success toast; row appears at top |
| 6 | Add a customer with an existing email | Field error "That email address is already in use." |
| 7 | Add a customer with bad email / age 5 | Validation message; nothing saved |
| 8 | Edit a customer and save | Success toast; table shows new values |
| 9 | Delete a customer who has policies | Refused: "…has N policies and cannot be deleted." |
| 10 | Delete a customer with no policies | Confirmation dialog, then removed |
| 11 | Agents → Add Agent; then Deactivate | Status badge changes to Inactive |
| 12 | Policies → create with end date before start date | Error "End date must be after the start date." |
| 13 | Policies → create with negative premium | Rejected with a message |
| 14 | Policies → create valid policy | Status set automatically (Active/Pending/Expired from dates) |
| 15 | Premium calculator | Premium field filled with an estimate |
| 16 | Filter policies by type and status | Table, count and totals update |
| 17 | Claims → submit a claim above the coverage | Error naming the coverage limit |
| 18 | Claims → submit a valid claim | Status Pending; appears on dashboard and notifications |
| 19 | Review a pending claim → Approve | Status Approved, decision date set, approved total increases |
| 20 | Review another → Reject | Status Rejected; buttons no longer offered |
| 21 | Dashboard | Cards, donut and bar chart match the database counts |
| 22 | Reports → each report; click a column heading | Data loads; sorting works; Print gives a clean layout |
| 23 | Profile → change password with wrong current password | Error; password unchanged |
| 24 | `queries.sql` section 11 | Foreign-key / restrict errors appear |
| 25 | Login page → **Create Account** | Registration page opens; Admin/Manager options are disabled, Staff selected |
| 26 | Register `newstaff` / `Passw0rd1` | Success message, redirect to login; sign-in works; role is Staff |
| 27 | Sign in as `staff` | No Reports / User Management / Settings in the menu; no Add/Edit/Delete buttons; visiting `/users` shows 403 |
| 28 | Sign in as `manager` | Can add a customer and approve a claim; sees Reports; `/users` and `/settings` show 403 |
| 29 | Sign in as `admin` → User Management → Create User with role Manager | Success toast; new manager can sign in |
| 30 | Admin tries to change the only Admin to Staff | Refused: "last active administrator" |
| 31 | Any role → Profile → change display name and email | Success message; header name updates |
| 32 | Any role → Profile → new password and confirmation differ | "Passwords do not match." |
| 33 | Staff profile: try to send a role (browser dev tools / curl) | HTTP 403; role unchanged |
| 34 | `python migrate.py` twice on an old database | First run backs up and migrates; second says "already up to date" |

## 7. Security notes

Implemented: password hashing (Werkzeug), session authentication on every page and API route, CSRF tokens (header for API calls, hidden field for forms), POST-only login/logout, server-side validation of every field, parameterised SQL, session fixation protection (new session on login), `HttpOnly` + `SameSite=Lax` cookies (and `Secure` when `SAFEGUARD_ENV=production`), generic error messages (no stack traces or SQL errors shown), basic security headers.
Not implemented (so do not claim them): e-mail verification or admin approval of public sign-ups (public sign-up is limited to read-only Staff instead), audit logging, rate limiting shared across processes, HTTPS termination (the host provides it), password reset, two-factor authentication.

## 8. Deployment

SQLite is a **file**. Locally that is `instance/safeguard.db`. On many cloud hosts the filesystem is **ephemeral** (wiped on restart/redeploy), so you must put the database on a **persistent disk** and point `SAFEGUARD_DB_PATH` at it. Do not use serverless platforms (e.g. Vercel, Netlify functions) for this app.

Production environment variables: `SAFEGUARD_ENV=production`, `SECRET_KEY` (long random string — generate with `python -c "import secrets; print(secrets.token_hex(32))"`), `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SAFEGUARD_DB_PATH`, and `SAFEGUARD_SEED_DEMO=0` if you do not want demo data.
Gunicorn is the production WSGI server on Linux (`gunicorn app:app`); it does not run on Windows, which is why local development uses `python app.py`. Run with **one worker** (`--workers 1`) because the login lockout counter is in memory.

### Option A — PythonAnywhere (free tier, persistent files; suggested for a demo)
1. Create an account, open **Files** and upload the project folder (or `git clone` from the Bash console).
2. In a Bash console: `cd SafeGuard-IMS && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.
3. **Web → Add a new web app → Manual configuration** (same Python version as the venv). Set *Virtualenv* to `/home/<you>/SafeGuard-IMS/.venv`.
4. Edit the WSGI file so it contains:
   ```python
   import os, sys
   path = '/home/<you>/SafeGuard-IMS'
   sys.path.insert(0, path)
   os.environ['SAFEGUARD_ENV'] = 'production'
   os.environ['SECRET_KEY'] = '<paste a long random string>'
   os.environ['ADMIN_USERNAME'] = 'admin'
   os.environ['ADMIN_PASSWORD'] = '<choose a strong password>'
   os.environ['SAFEGUARD_DB_PATH'] = '/home/<you>/safeguard-data/safeguard.db'
   from app import app as application
   ```
   and create the folder first: `mkdir -p ~/safeguard-data`.
5. Reload the web app. PythonAnywhere serves over HTTPS, so the secure cookie setting works.

### Option B — Render.com (needs a paid instance + persistent disk)
1. Push the project to GitHub; create a **Web Service** from the repo (Python).
2. Build command `pip install -r requirements.txt`; start command `gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app`.
3. Add a **Disk** (e.g. mount path `/var/data`) and set `SAFEGUARD_DB_PATH=/var/data/safeguard.db`, plus the variables above.

This project has **not** been deployed or tested on either platform; the steps are provided as instructions only.

## 9. Troubleshooting

* *Charts empty / blank:* check the browser console; Chart.js is bundled in `static/js/vendor`, so no internet is needed (only the Inter font uses Google Fonts and falls back to system fonts offline).
* *"No administrator exists…" on start:* set `ADMIN_PASSWORD` before the first start of a new database.
* *Port 5000 busy:* `$env:PORT=5001; python app.py`.
* *Want a clean database:* stop the app, delete `instance\safeguard.db`, start again.
