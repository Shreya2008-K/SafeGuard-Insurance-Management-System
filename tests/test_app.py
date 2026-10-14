"""Automated tests for SafeGuard IMS.  Run from the project folder:  python -m unittest -v"""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta

os.environ["SAFEGUARD_NO_AUTOCREATE"] = "1"  # do not create the real database on import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
import database as db  # noqa: E402

TOKEN = "test-token"
iso = lambda offset: (date.today() + timedelta(days=offset)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "test.db")
        self.app = app_module.create_app({
            "TESTING": True, "DATABASE": self.path, "SECRET_KEY": "test-secret",
            "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "Admin@123", "SEED_DEMO": True})
        self.client = self.app.test_client()
        app_module.LOGIN_ATTEMPTS.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, user="admin", pw="Admin@123"):
        with self.client.session_transaction() as s:
            s["csrf_token"] = TOKEN
        r = self.client.post("/login", data={"username": user, "password": pw, "csrf_token": TOKEN})
        with self.client.session_transaction() as s:  # login rotates the token; pin it for the tests
            s["csrf_token"] = TOKEN
        return r

    def call(self, method, url, body=None):
        return getattr(self.client, method)(url, json=body, headers={"X-CSRF-Token": TOKEN})

    def conn(self):
        return db.get_connection(self.path)

    def customer(self, **kw):
        d = {"full_name": "Test Person", "age": 30, "gender": "Male", "phone": "9876543210", "email": "t@example.com"}
        d.update(kw)
        return d

    def policy(self, **kw):
        d = {"customer_id": 1, "agent_id": 1, "policy_type": "Life Insurance", "premium_amount": 1000,
             "coverage_amount": 100000, "start_date": iso(-10), "end_date": iso(355), "status": "Auto"}
        d.update(kw)
        return d


class AuthTests(Base):
    def test_login_success(self):
        r = self.login()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_login_failure(self):
        r = self.login(pw="wrong")
        self.assertEqual(r.status_code, 401)
        self.assertIn(b"Invalid username or password", r.data)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)

    def test_login_requires_csrf(self):
        self.client.get("/login")
        r = self.client.post("/login", data={"username": "admin", "password": "Admin@123"})
        self.assertEqual(r.status_code, 400)

    def test_logout(self):
        self.login()
        r = self.client.post("/logout", data={"csrf_token": TOKEN})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_unauthorized_access(self):
        for page in ("/dashboard", "/customers", "/agents", "/policies", "/claims", "/reports", "/settings", "/profile", "/users"):
            r = self.client.get(page)
            self.assertEqual(r.status_code, 302, page)
            self.assertIn("/login", r.headers["Location"])
        for url in ("/api/customers", "/api/policies", "/api/claims", "/api/reports/customers"):
            self.assertEqual(self.client.get(url).status_code, 401, url)
        self.assertEqual(self.client.post("/api/customers", json={}, headers={"X-CSRF-Token": TOKEN}).status_code, 400)

    def test_password_is_hashed(self):
        with self.conn() as c:
            h = c.execute("SELECT password_hash FROM APP_USER").fetchone()[0]
        self.assertNotIn("Admin@123", h)
        self.assertTrue(h.startswith(("scrypt:", "pbkdf2:")))

    def test_api_post_without_csrf_is_rejected(self):
        self.login()
        r = self.client.post("/api/customers", json=self.customer())
        self.assertEqual(r.status_code, 400)

    def test_lockout_after_repeated_failures(self):
        for _ in range(5):
            self.login(pw="bad")
        self.assertEqual(self.login().status_code, 429)


class DatabaseTests(Base):
    def test_initialization_creates_objects(self):
        with self.conn() as c:
            names = {(r[0], r[1]) for r in c.execute("SELECT type, name FROM sqlite_master")}
        for t in ("APP_USER", "CUSTOMER", "INSURANCE_AGENT", "INSURANCE_POLICY", "CLAIM"):
            self.assertIn(("table", t), names)
        for v in ("V_POLICY_DETAILS", "V_CLAIM_DETAILS", "V_POLICY_TYPE_SUMMARY"):
            self.assertIn(("view", v), names)
        for t in ("trg_claim_within_coverage", "trg_claim_only_pending_changes", "trg_claim_stamp_decision_date"):
            self.assertIn(("trigger", t), names)
        with self.conn() as c:  # ALTER TABLE column exists
            cols = [r[1] for r in c.execute("PRAGMA table_info(CLAIM)")]
        self.assertIn("decision_note", cols)

    def test_init_is_idempotent_and_seeds_once(self):
        db.init_db(self.path, "admin", "Admin@123", True, demo_users=True)  # second start: nothing is duplicated
        with self.conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM CUSTOMER").fetchone()[0], 12)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM APP_USER").fetchone()[0], 3)  # admin + demo manager + demo staff

    def test_foreign_keys_enforced(self):
        with self.conn() as c:
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO INSURANCE_POLICY (customer_id,policy_type,premium_amount,coverage_amount,"
                          "start_date,end_date,status) VALUES (9999,'Life Insurance',1,10,'2030-01-01','2031-01-01','Pending')")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM CUSTOMER WHERE customer_id = 1")

    def test_check_constraints(self):
        with self.conn() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO INSURANCE_POLICY (customer_id,policy_type,premium_amount,coverage_amount,"
                          "start_date,end_date,status) VALUES (1,'Life Insurance',-5,100,'2030-01-01','2031-01-01','Pending')")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO INSURANCE_POLICY (customer_id,policy_type,premium_amount,coverage_amount,"
                          "start_date,end_date,status) VALUES (1,'Life Insurance',5,100,'2031-01-01','2030-01-01','Pending')")

    def test_triggers(self):
        with self.conn() as c:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "exceeds the policy coverage"):
                c.execute("INSERT INTO CLAIM (policy_id,claim_date,claim_amount) VALUES (1,?,999999999)", (iso(-1),))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "Only pending claims"):
                c.execute("UPDATE CLAIM SET claim_status='Rejected' WHERE claim_id=1")  # claim 1 is Approved
            c.execute("UPDATE CLAIM SET claim_status='Approved' WHERE claim_id=4")  # pending -> decision date stamped
            self.assertIsNotNone(c.execute("SELECT decision_date FROM CLAIM WHERE claim_id=4").fetchone()[0])

    def test_dashboard_statistics_match_database(self):
        self.login()
        d = self.call("get", "/api/dashboard").get_json()
        with self.conn() as c:
            n = lambda sql: c.execute(sql).fetchone()[0]
            self.assertEqual(d["cards"]["total_customers"], n("SELECT COUNT(*) FROM CUSTOMER"))
            self.assertEqual(d["cards"]["active_policies"], n("SELECT COUNT(*) FROM INSURANCE_POLICY WHERE status='Active'"))
            self.assertEqual(d["cards"]["pending_claims"], n("SELECT COUNT(*) FROM CLAIM WHERE claim_status='Pending'"))
            self.assertEqual(d["cards"]["total_agents"], n("SELECT COUNT(*) FROM INSURANCE_AGENT"))
        self.assertEqual(sum(x["count"] for x in d["distribution"]), d["total_active"])
        self.assertEqual(len(d["claims_chart"]["labels"]), 6)

    def test_dashboard_empty_database_zero_states(self):
        path = os.path.join(self.tmp.name, "empty.db")
        a = app_module.create_app({"TESTING": True, "DATABASE": path, "SECRET_KEY": "k", "SEED_DEMO": False,
                                   "ADMIN_PASSWORD": "Admin@123"})
        c = a.test_client()
        with c.session_transaction() as s:
            s["csrf_token"] = TOKEN
        c.post("/login", data={"username": "admin", "password": "Admin@123", "csrf_token": TOKEN})
        d = c.get("/api/dashboard").get_json()
        self.assertEqual(d["cards"]["total_customers"], 0)
        self.assertEqual(d["total_active"], 0)
        self.assertEqual(d["recent_policies"], [])


class CustomerTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_create_update_delete(self):
        r = self.call("post", "/api/customers", self.customer())
        self.assertEqual(r.status_code, 201)
        cid = r.get_json()["item"]["customer_id"]
        r = self.call("put", f"/api/customers/{cid}", self.customer(full_name="Renamed Person"))
        self.assertEqual(r.get_json()["item"]["full_name"], "Renamed Person")
        self.assertEqual(self.call("delete", f"/api/customers/{cid}").status_code, 200)
        self.assertEqual(self.call("get", f"/api/customers/{cid}").status_code, 404)

    def test_validation(self):
        r = self.call("post", "/api/customers", self.customer(email="not-an-email", age=5, phone="abc", full_name=""))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(set(r.get_json()["errors"]), {"email", "age", "phone", "full_name"})

    def test_duplicate_email_rejected(self):
        self.call("post", "/api/customers", self.customer())
        r = self.call("post", "/api/customers", self.customer(full_name="Other"))
        self.assertEqual(r.status_code, 409)
        self.assertIn("email", r.get_json()["errors"])

    def test_cannot_delete_customer_with_policies(self):
        r = self.call("delete", "/api/customers/1")
        self.assertEqual(r.status_code, 409)
        self.assertIn("cannot be deleted", r.get_json()["error"])

    def test_search_and_pagination(self):
        d = self.call("get", "/api/customers?q=Priya").get_json()
        self.assertEqual(d["total"], 1)
        d = self.call("get", "/api/customers?q=3").get_json()  # numeric also matches id
        self.assertTrue(any(i["customer_id"] == 3 for i in d["items"]))
        d = self.call("get", "/api/customers?per_page=5&page=2").get_json()
        self.assertEqual((len(d["items"]), d["pages"]), (5, 3))


class AgentTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_agent_lifecycle(self):
        body = {"agent_name": "New Agent", "phone": "9000011111", "email": "new.agent@example.com",
                "commission_percentage": 6.5, "status": "Active"}
        r = self.call("post", "/api/agents", body)
        self.assertEqual(r.status_code, 201)
        aid = r.get_json()["item"]["agent_id"]
        self.assertEqual(self.call("patch", f"/api/agents/{aid}/status", {"status": "Inactive"}).get_json()["item"]["status"], "Inactive")
        self.assertEqual(self.call("delete", f"/api/agents/{aid}").status_code, 200)

    def test_agent_validation_and_delete_rule(self):
        r = self.call("post", "/api/agents", {"agent_name": "X", "phone": "1", "email": "bad", "commission_percentage": 99})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.call("delete", "/api/agents/1").status_code, 409)

    def test_inactive_agent_cannot_get_new_policy(self):
        self.call("patch", "/api/agents/1/status", {"status": "Inactive"})
        r = self.call("post", "/api/policies", self.policy(agent_id=1))
        self.assertEqual(r.status_code, 422)
        self.assertIn("agent_id", r.get_json()["errors"])


class PolicyTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_create_and_status_rules(self):
        r = self.call("post", "/api/policies", self.policy())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["item"]["status"], "Active")
        self.assertEqual(self.call("post", "/api/policies", self.policy(start_date=iso(5), end_date=iso(370))).get_json()["item"]["status"], "Pending")
        self.assertEqual(self.call("post", "/api/policies", self.policy(start_date=iso(-400), end_date=iso(-35))).get_json()["item"]["status"], "Expired")
        self.assertEqual(self.call("post", "/api/policies", self.policy(status="Cancelled")).get_json()["item"]["status"], "Cancelled")

    def test_invalid_dates_and_amounts(self):
        self.assertEqual(self.call("post", "/api/policies", self.policy(end_date=iso(-20))).status_code, 422)  # end before start
        self.assertEqual(self.call("post", "/api/policies", self.policy(start_date="2026-02-31")).status_code, 422)
        self.assertEqual(self.call("post", "/api/policies", self.policy(premium_amount=-5)).status_code, 422)
        self.assertEqual(self.call("post", "/api/policies", self.policy(coverage_amount=0)).status_code, 422)
        self.assertEqual(self.call("post", "/api/policies", self.policy(customer_id=9999)).status_code, 422)
        self.assertEqual(self.call("post", "/api/policies", self.policy(policy_type="Pet Insurance")).status_code, 422)

    def test_filters_and_totals(self):
        d = self.call("get", "/api/policies?status=Active&policy_type=Life%20Insurance").get_json()
        self.assertTrue(all(i["status"] == "Active" and i["policy_type"] == "Life Insurance" for i in d["items"]))
        with self.conn() as c:
            expected = c.execute("SELECT SUM(premium_amount) FROM INSURANCE_POLICY WHERE status='Active' AND policy_type='Life Insurance'").fetchone()[0]
        self.assertAlmostEqual(d["summary"]["total_premium"], expected)
        d = self.call("get", "/api/policies?customer_id=1").get_json()
        self.assertTrue(d["total"] >= 1 and all(i["customer_id"] == 1 for i in d["items"]))

    def test_expired_status_is_synced(self):
        with self.conn() as c:
            with c:
                c.execute("UPDATE INSURANCE_POLICY SET end_date=?, start_date=? WHERE policy_id=2", (iso(-1), iso(-366)))
        d = self.call("get", "/api/policies/2").get_json()
        self.assertEqual(d["status"], "Expired")

    def test_premium_estimate(self):
        r = self.call("get", "/api/premium-estimate?policy_type=Life%20Insurance&coverage_amount=1000000&age=40")
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(r.get_json()["premium"], 1000000 * 0.012 * 1.15)
        self.assertEqual(self.call("get", "/api/premium-estimate?policy_type=Bad&coverage_amount=1").status_code, 422)


class ClaimTests(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.pid = self.call("post", "/api/policies", self.policy(coverage_amount=50000)).get_json()["item"]["policy_id"]

    def claim(self, **kw):
        d = {"policy_id": self.pid, "claim_date": iso(-1), "claim_amount": 1000, "claim_reason": "Test claim reason"}
        d.update(kw)
        return self.call("post", "/api/claims", d)

    def test_submit_approve_reject(self):
        r = self.claim()
        self.assertEqual(r.status_code, 201)
        cid = r.get_json()["item"]["claim_id"]
        self.assertEqual(r.get_json()["item"]["claim_status"], "Pending")
        a = self.call("post", f"/api/claims/{cid}/approve", {"note": "Looks fine"})
        self.assertEqual(a.get_json()["item"]["claim_status"], "Approved")
        self.assertEqual(a.get_json()["item"]["decision_date"], date.today().isoformat())
        self.assertEqual(self.call("post", f"/api/claims/{cid}/reject").status_code, 409)  # already decided
        cid2 = self.claim().get_json()["item"]["claim_id"]
        self.assertEqual(self.call("post", f"/api/claims/{cid2}/reject").get_json()["item"]["claim_status"], "Rejected")
        self.assertEqual(self.call("post", f"/api/claims/{cid2}/approve").status_code, 409)

    def test_invalid_claims(self):
        self.assertEqual(self.claim(claim_amount=-10).status_code, 422)
        self.assertEqual(self.claim(claim_amount=0).status_code, 422)
        r = self.claim(claim_amount=50001)
        self.assertEqual(r.status_code, 422)
        self.assertIn("claim_amount", r.get_json()["errors"])
        self.assertEqual(self.claim(policy_id=99999).status_code, 422)
        self.assertEqual(self.claim(claim_date=iso(3)).status_code, 422)         # future
        self.assertEqual(self.claim(claim_date=iso(-100)).status_code, 422)      # before policy start
        self.assertEqual(self.claim(claim_date="nonsense").status_code, 422)
        self.assertEqual(self.claim(claim_reason="no").status_code, 422)

    def test_claim_on_inactive_policy_rejected(self):
        expired = self.call("post", "/api/policies", self.policy(start_date=iso(-400), end_date=iso(-35))).get_json()["item"]["policy_id"]
        r = self.claim(policy_id=expired, claim_date=iso(-100))
        self.assertEqual(r.status_code, 422)
        self.assertIn("Active", r.get_json()["errors"]["policy_id"])

    def test_cannot_lower_coverage_below_claim(self):
        self.claim(claim_amount=40000)
        r = self.call("put", f"/api/policies/{self.pid}", self.policy(coverage_amount=30000))
        self.assertEqual(r.status_code, 422)

    def test_filters_and_approved_total(self):
        d = self.call("get", "/api/claims?status=Approved").get_json()
        with self.conn() as c:
            expected = c.execute("SELECT SUM(claim_amount) FROM CLAIM WHERE claim_status='Approved'").fetchone()[0]
        self.assertAlmostEqual(d["summary"]["approved_total"], expected)
        self.assertEqual(self.call("get", "/api/claims?claim_id=1").get_json()["total"], 1)
        self.assertTrue(self.call("get", "/api/claims/1").get_json()["history"])


class ReportTests(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_all_reports_run(self):
        for name in ("customers", "agents", "policies", "policies-active", "policies-expired", "by-type",
                     "claims-pending", "claims-approved", "claims-rejected", "totals"):
            r = self.call("get", f"/api/reports/{name}")
            self.assertEqual(r.status_code, 200, name)
            self.assertTrue(r.get_json()["columns"], name)
        r = self.call("get", "/api/reports/customer-history?customer_id=1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.call("get", "/api/reports/customer-history").status_code, 422)
        self.assertEqual(self.call("get", "/api/reports/nope").status_code, 404)

    def test_report_values_and_filters(self):
        d = self.call("get", "/api/reports/totals").get_json()
        with self.conn() as c:
            tp = c.execute("SELECT SUM(premium_amount) FROM INSURANCE_POLICY").fetchone()[0]
            ta = c.execute("SELECT SUM(claim_amount) FROM CLAIM WHERE claim_status='Approved'").fetchone()[0]
        self.assertAlmostEqual(d["summary"][0]["value"], tp)
        self.assertAlmostEqual(d["summary"][1]["value"], ta)
        self.assertTrue(all(r["status"] == "Expired" for r in self.call("get", "/api/reports/policies-expired").get_json()["rows"]))
        d = self.call("get", "/api/reports/by-type?min_count=7").get_json()
        self.assertTrue(all(r["policies"] >= 7 for r in d["rows"]))
        self.assertEqual(self.call("get", "/api/reports/policies?date_from=bad").status_code, 422)

    def test_profile_password_change(self):
        r = self.call("post", "/api/profile/password", {"current_password": "wrong", "new_password": "NewPass123", "confirm_password": "NewPass123"})
        self.assertEqual(r.status_code, 422)
        r = self.call("post", "/api/profile/password", {"current_password": "Admin@123", "new_password": "NewPass123", "confirm_password": "NewPass123"})
        self.assertEqual(r.status_code, 200)
        self.client.post("/logout", data={"csrf_token": TOKEN})
        self.assertEqual(self.login(pw="NewPass123").status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_pages_render(self):
        for page in ("/dashboard", "/customers", "/agents", "/policies", "/claims", "/reports", "/settings", "/profile", "/users"):
            r = self.client.get(page)
            self.assertEqual(r.status_code, 200, page)
            self.assertIn(b"SafeGuard", r.data)
        self.assertIn(b'nav-link active', self.client.get("/claims").data)
        self.assertEqual(self.client.get("/nope").status_code, 404)


# =====================================================================================
#  Roles, registration, profile, user management, migration
# =====================================================================================
from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

PASSWORDS = {"admin": "Admin@123", "manager": "Manager@123", "staff": "Staff@123"}


class Who:
    """A test browser signed in as one account."""

    def __init__(self, app, user):
        self.client = app.test_client()
        with self.client.session_transaction() as s:
            s["csrf_token"] = TOKEN
        self.response = self.client.post("/login", data={"username": user, "password": PASSWORDS.get(user, "Passw0rd1"),
                                                         "csrf_token": TOKEN})
        with self.client.session_transaction() as s:
            s["csrf_token"] = TOKEN

    def call(self, method, url, body=None):
        return getattr(self.client, method)(url, json=body, headers={"X-CSRF-Token": TOKEN})


class RoleBase(Base):
    def setUp(self):
        super().setUp()
        self.admin, self.manager, self.staff = (Who(self.app, u) for u in ("admin", "manager", "staff"))

    def user_row(self, username):
        with self.conn() as c:
            return c.execute("SELECT * FROM APP_USER WHERE username = ?", (username,)).fetchone()


class LoginForAllRolesTests(RoleBase):
    def test_all_three_roles_can_log_in(self):
        for who in (self.admin, self.manager, self.staff):
            self.assertEqual(who.response.status_code, 302)
            self.assertEqual(who.client.get("/dashboard").status_code, 200)

    def test_wrong_password_rejected_for_every_role(self):
        for user in ("admin", "manager", "staff"):
            c = self.app.test_client()
            with c.session_transaction() as s:
                s["csrf_token"] = TOKEN
            r = c.post("/login", data={"username": user, "password": "nope", "csrf_token": TOKEN})
            self.assertEqual(r.status_code, 401)
            self.assertEqual(c.get("/dashboard").status_code, 302)

    def test_logout_still_works_for_every_role(self):
        for who in (self.admin, self.manager, self.staff):
            self.assertEqual(who.client.post("/logout", data={"csrf_token": TOKEN}).status_code, 302)
            self.assertEqual(who.client.get("/dashboard").status_code, 302)

    def test_disabled_account_cannot_log_in_and_is_signed_out(self):
        uid = self.user_row("staff")["user_id"]
        self.assertEqual(self.admin.call("patch", f"/api/users/{uid}/active", {"is_active": False}).status_code, 200)
        self.assertEqual(self.staff.client.get("/dashboard").status_code, 302)  # existing session ends at once
        r = Who(self.app, "staff")
        self.assertEqual(r.response.status_code, 403)

    def test_malformed_stored_hash_does_not_crash_login(self):
        with self.conn() as c:
            with c:
                c.execute("UPDATE APP_USER SET password_hash = 'plain-text-not-a-hash' WHERE username='staff'")
        self.assertEqual(Who(self.app, "staff").response.status_code, 401)


class PermissionTests(RoleBase):
    def test_staff_is_read_only_everywhere(self):
        s = self.staff
        for url in ("/api/customers", "/api/agents", "/api/policies", "/api/claims", "/api/dashboard", "/api/claims/1"):
            self.assertEqual(s.call("get", url).status_code, 200, url)
        for page in ("/dashboard", "/customers", "/agents", "/policies", "/claims", "/profile"):
            self.assertEqual(s.client.get(page).status_code, 200, page)
        writes = [("post", "/api/customers", self.customer()), ("put", "/api/customers/1", self.customer()),
                  ("delete", "/api/customers/12"), ("post", "/api/agents", {"agent_name": "X Y"}),
                  ("put", "/api/agents/1", {}), ("patch", "/api/agents/1/status", {"status": "Inactive"}),
                  ("delete", "/api/agents/6"), ("post", "/api/policies", self.policy()), ("put", "/api/policies/1", self.policy()),
                  ("post", "/api/claims", {}), ("post", "/api/claims/4/approve"), ("post", "/api/claims/4/reject")]
        for w in writes:
            self.assertEqual(s.call(*w).status_code, 403, w[:2])
        with self.conn() as c:  # nothing changed
            self.assertEqual(c.execute("SELECT claim_status FROM CLAIM WHERE claim_id=4").fetchone()[0], "Pending")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM CUSTOMER").fetchone()[0], 12)

    def test_staff_cannot_see_reports_settings_or_users(self):
        for page in ("/reports", "/settings", "/users"):
            self.assertEqual(self.staff.client.get(page).status_code, 403, page)
        for url in ("/api/reports/customers", "/api/settings", "/api/users", "/api/premium-estimate?policy_type=Life%20Insurance&coverage_amount=1000"):
            self.assertEqual(self.staff.call("get", url).status_code, 403, url)

    def test_manager_can_manage_insurance_records(self):
        m = self.manager
        r = m.call("post", "/api/customers", self.customer())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(m.call("post", "/api/policies", self.policy()).status_code, 201)
        self.assertEqual(m.call("post", "/api/claims/4/approve").status_code, 200)
        self.assertEqual(m.call("get", "/api/reports/customers").status_code, 200)
        self.assertEqual(m.client.get("/reports").status_code, 200)

    def test_manager_cannot_use_admin_features(self):
        for page in ("/users", "/settings"):
            self.assertEqual(self.manager.client.get(page).status_code, 403, page)
        self.assertEqual(self.manager.call("get", "/api/users").status_code, 403)
        self.assertEqual(self.manager.call("get", "/api/settings").status_code, 403)
        r = self.manager.call("post", "/api/users", {"username": "evil", "password": "Passw0rd1", "confirm_password": "Passw0rd1", "role": "admin"})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.user_row("evil"))

    def test_admin_has_full_access(self):
        for page in ("/dashboard", "/customers", "/agents", "/policies", "/claims", "/reports", "/settings", "/users", "/profile"):
            self.assertEqual(self.admin.client.get(page).status_code, 200, page)

    def test_403_is_json_for_api_and_html_for_pages(self):
        r = self.staff.call("post", "/api/customers", self.customer())
        self.assertEqual(r.status_code, 403)
        self.assertIn("permission", r.get_json()["error"])
        self.assertIn(b"permission", self.staff.client.get("/users").data)

    def test_csrf_still_enforced_for_every_role(self):
        for who in (self.admin, self.manager, self.staff):
            r = who.client.post("/api/customers", json=self.customer())  # no token header
            self.assertEqual(r.status_code, 400)

    def test_role_change_takes_effect_immediately(self):
        uid = self.user_row("manager")["user_id"]
        self.assertEqual(self.manager.call("get", "/api/reports/customers").status_code, 200)
        self.admin.call("patch", f"/api/users/{uid}/role", {"role": "staff"})
        self.assertEqual(self.manager.call("get", "/api/reports/customers").status_code, 403)

    def test_navigation_matches_role(self):
        self.assertIn(b"User Management", self.admin.client.get("/dashboard").data)
        for who in (self.manager, self.staff):
            page = who.client.get("/dashboard").data
            self.assertNotIn(b"User Management", page)
            self.assertIn(b"Profile", page)
        self.assertNotIn(b"Reports", self.staff.client.get("/dashboard").data.split(b"</nav>")[0])
        self.assertIn(b"Reports", self.manager.client.get("/dashboard").data)


class RegistrationTests(Base):
    def post(self, **kw):
        c = self.client
        with c.session_transaction() as s:
            s["csrf_token"] = TOKEN
        data = {"username": "newperson", "password": "Passw0rd1", "confirm_password": "Passw0rd1", "role": "staff", "csrf_token": TOKEN}
        data.update(kw)
        return c.post("/register", data=data)

    def row(self, username):
        with self.conn() as c:
            return c.execute("SELECT * FROM APP_USER WHERE username = ?", (username,)).fetchone()

    def test_create_account_page_opens(self):
        r = self.client.get("/register")
        self.assertEqual(r.status_code, 200)
        for text in (b"Username", b"Confirm password", b"Create Account", b"Back to Login"):
            self.assertIn(text, r.data)
        self.assertEqual(r.data.count(b"<option"), 3)  # exactly Admin, Manager, Staff
        for label in (b"Admin", b"Manager", b"Staff"):
            self.assertIn(label, r.data)

    def test_login_page_links_to_register(self):
        r = self.client.get("/login")
        self.assertIn(b'href="/register"', r.data)
        self.assertIn(b"Remember me", r.data)
        self.assertIn(b"togglePw", r.data)

    def test_public_signup_creates_staff_and_redirects_to_login(self):
        r = self.post()
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/login"))
        self.assertEqual(self.row("newperson")["role"], "staff")
        self.assertIn(b"Account created successfully", self.client.get("/login").data)
        self.assertEqual(Who(self.app, "newperson").response.status_code, 302)

    def test_public_cannot_create_admin_or_manager(self):
        for role in ("admin", "manager", "ADMIN"):
            r = self.post(username=f"sneaky{role.lower()}", role=role)
            self.assertEqual(r.status_code, 403, role)
            self.assertIsNone(self.row(f"sneaky{role.lower()}"))

    def test_validation_messages(self):
        self.assertIn(b"already taken", self.post(username="admin").data)
        self.assertIn(b"do not match", self.post(confirm_password="Different1").data)
        self.assertIn(b"at least one letter", self.post(password="short", confirm_password="short").data)
        self.assertIn(b"3-30 characters", self.post(username="a b").data)
        self.assertEqual(self.post(username="ADMIN").status_code, 422)  # usernames are case-insensitive

    def test_register_requires_csrf(self):
        self.client.get("/register")
        r = self.client.post("/register", data={"username": "nocsrf", "password": "Passw0rd1", "confirm_password": "Passw0rd1", "role": "staff"})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.row("nocsrf"))

    def test_signed_in_user_is_redirected_away(self):
        who = Who(self.app, "staff")
        self.assertEqual(who.client.get("/register").status_code, 302)

    def test_signup_throttle(self):
        app_module.REGISTRATIONS.clear()
        for i in range(5):
            self.assertEqual(self.post(username=f"person{i}").status_code, 302)
        self.assertEqual(self.post(username="person9").status_code, 429)
        app_module.REGISTRATIONS.clear()


class UserManagementTests(RoleBase):
    def new(self, **kw):
        d = {"username": "created1", "password": "Passw0rd1", "confirm_password": "Passw0rd1", "role": "staff"}
        d.update(kw)
        return d

    def test_admin_creates_all_three_roles_and_they_can_log_in(self):
        for role in ("admin", "manager", "staff"):
            r = self.admin.call("post", "/api/users", self.new(username=f"made_{role}", role=role))
            self.assertEqual(r.status_code, 201, role)
            self.assertEqual(self.user_row(f"made_{role}")["role"], role)
            self.assertEqual(Who(self.app, f"made_{role}").response.status_code, 302)

    def test_duplicate_username_rejected(self):
        self.assertEqual(self.admin.call("post", "/api/users", self.new(username="staff")).status_code, 422)
        self.assertEqual(self.admin.call("post", "/api/users", self.new(username="STAFF")).status_code, 422)

    def test_password_rules_and_hashing(self):
        self.assertEqual(self.admin.call("post", "/api/users", self.new(password="short1", confirm_password="short1")).status_code, 422)
        self.assertEqual(self.admin.call("post", "/api/users", self.new(confirm_password="Other1234")).status_code, 422)
        self.assertEqual(self.admin.call("post", "/api/users", self.new(role="superuser")).status_code, 422)
        self.admin.call("post", "/api/users", self.new())
        stored = self.user_row("created1")["password_hash"]
        self.assertNotIn("Passw0rd1", stored)
        self.assertTrue(check_password_hash(stored, "Passw0rd1"))

    def test_api_never_returns_password_hashes(self):
        for text in (self.admin.call("get", "/api/users").get_data(as_text=True),
                     self.admin.call("get", "/api/profile").get_data(as_text=True),
                     self.admin.call("post", "/api/users", self.new()).get_data(as_text=True)):
            self.assertNotIn("password_hash", text)
            self.assertNotIn("scrypt:", text)
            self.assertNotIn("pbkdf2:", text)

    def test_admin_changes_role_disables_and_resets_password(self):
        uid = self.user_row("staff")["user_id"]
        self.assertEqual(self.admin.call("patch", f"/api/users/{uid}/role", {"role": "manager"}).status_code, 200)
        self.assertEqual(self.user_row("staff")["role"], "manager")
        self.assertEqual(self.admin.call("patch", f"/api/users/{uid}/role", {"role": "owner"}).status_code, 422)
        r = self.admin.call("post", f"/api/users/{uid}/password", {"new_password": "Reset12345", "confirm_password": "Reset12345"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(check_password_hash(self.user_row("staff")["password_hash"], "Reset12345"))
        self.assertEqual(self.admin.call("post", f"/api/users/{uid}/password", {"new_password": "weak", "confirm_password": "weak"}).status_code, 422)

    def test_last_admin_cannot_be_demoted_disabled_or_deleted(self):
        admin_id = self.user_row("admin")["user_id"]
        r = self.admin.call("patch", f"/api/users/{admin_id}/role", {"role": "staff"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("last active administrator", r.get_json()["error"])
        self.assertEqual(self.admin.call("patch", f"/api/users/{admin_id}/active", {"is_active": False}).status_code, 409)
        self.assertEqual(self.admin.call("delete", f"/api/users/{admin_id}").status_code, 409)
        self.assertEqual(self.user_row("admin")["role"], "admin")
        with self.conn() as c:  # same protection at the database layer
            with self.assertRaises(db.BusinessRuleError):
                db.set_user_role(c, admin_id, "manager")
            with self.assertRaises(db.BusinessRuleError):
                db.delete_user(c, admin_id)

    def test_second_admin_allows_demotion_but_never_the_last(self):
        self.admin.call("post", "/api/users", self.new(username="admin2", role="admin"))
        a1, a2 = self.user_row("admin")["user_id"], self.user_row("admin2")["user_id"]
        self.assertEqual(self.admin.call("patch", f"/api/users/{a2}/role", {"role": "manager"}).status_code, 200)
        self.assertEqual(self.admin.call("patch", f"/api/users/{a1}/role", {"role": "manager"}).status_code, 409)

    def test_admin_cannot_delete_own_account(self):
        self.admin.call("post", "/api/users", self.new(username="admin2", role="admin"))
        self.assertEqual(self.admin.call("delete", f"/api/users/{self.user_row('admin')['user_id']}").status_code, 409)

    def test_manager_and_staff_cannot_touch_user_management(self):
        uid = self.user_row("staff")["user_id"]
        for who in (self.manager, self.staff):
            for call in (("get", "/api/users"), ("post", "/api/users", self.new()), ("patch", f"/api/users/{uid}/role", {"role": "admin"}),
                         ("patch", f"/api/users/{uid}/active", {"is_active": False}), ("delete", f"/api/users/{uid}"),
                         ("post", f"/api/users/{uid}/password", {"new_password": "Hacked1234", "confirm_password": "Hacked1234"})):
                self.assertEqual(who.call(*call).status_code, 403, call[:2])
        self.assertEqual(self.user_row("staff")["role"], "staff")


class ProfileTests(RoleBase):
    def test_every_role_can_view_and_update_own_profile(self):
        for name, who in (("admin", self.admin), ("manager", self.manager), ("staff", self.staff)):
            me = who.call("get", "/api/profile").get_json()
            self.assertEqual((me["username"], me["role"]), (name, name))
            self.assertNotIn("password_hash", me)
            r = who.call("put", "/api/profile", {"display_name": f"New {name.title()} Name", "email": f"{name}.new@example.com", "phone": "9876500000"})
            self.assertEqual(r.status_code, 200, name)
            self.assertIn("successfully", r.get_json()["message"])
            self.assertEqual(self.user_row(name)["display_name"], f"New {name.title()} Name")
            self.assertEqual(self.user_row(name)["email"], f"{name}.new@example.com")
            self.assertEqual(who.client.get("/profile").status_code, 200)

    def test_profile_validation_messages(self):
        r = self.staff.call("put", "/api/profile", {"email": "not-an-email", "phone": "abc", "display_name": "x"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(set(r.get_json()["errors"]), {"email", "phone", "display_name"})
        self.staff.call("put", "/api/profile", {"email": "shared@example.com"})
        r = self.manager.call("put", "/api/profile", {"email": "SHARED@example.com"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("email", r.get_json()["errors"])

    def test_no_role_can_change_its_own_role(self):
        for name, who in (("admin", self.admin), ("manager", self.manager), ("staff", self.staff)):
            for payload in ({"role": "admin"}, {"role": "manager"}, {"role": "staff" if name != "staff" else "admin"},
                            {"is_active": 0}, {"display_name": "Ok Name", "role": "admin"}):
                if payload.get("role") == name:
                    continue
                r = who.call("put", "/api/profile", payload)
                self.assertEqual(r.status_code, 403, (name, payload))
            self.assertEqual(self.user_row(name)["role"], name)
        self.assertEqual(self.user_row("staff")["role"], "staff")

    def test_user_id_in_url_or_body_is_ignored(self):
        victim = self.user_row("admin")["user_id"]
        r = self.staff.call("put", "/api/profile", {"user_id": victim, "display_name": "Hijacked"})
        self.assertEqual(r.status_code, 403)
        self.assertNotEqual(self.user_row("admin")["display_name"], "Hijacked")
        self.assertEqual(self.staff.call("put", f"/api/profile/{victim}", {"display_name": "Hijacked"}).status_code, 404)
        self.assertEqual(self.staff.call("get", f"/api/profile/{victim}").status_code, 404)
        r = self.staff.call("put", "/api/profile?user_id=%d" % victim, {"display_name": "Staff Real Name"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.user_row("staff")["display_name"], "Staff Real Name")
        self.assertNotEqual(self.user_row("admin")["display_name"], "Staff Real Name")

    def test_all_roles_change_password_after_verifying_current(self):
        for name, who in (("admin", self.admin), ("manager", self.manager), ("staff", self.staff)):
            bad = who.call("post", "/api/profile/password", {"current_password": "wrong", "new_password": "BrandNew123", "confirm_password": "BrandNew123"})
            self.assertEqual(bad.status_code, 422)
            self.assertIn("current_password", bad.get_json()["errors"])
            mismatch = who.call("post", "/api/profile/password", {"current_password": PASSWORDS[name], "new_password": "BrandNew123", "confirm_password": "BrandNew124"})
            self.assertEqual(mismatch.status_code, 422)
            weak = who.call("post", "/api/profile/password", {"current_password": PASSWORDS[name], "new_password": "short", "confirm_password": "short"})
            self.assertEqual(weak.status_code, 422)
            ok = who.call("post", "/api/profile/password", {"current_password": PASSWORDS[name], "new_password": "BrandNew123", "confirm_password": "BrandNew123"})
            self.assertEqual(ok.status_code, 200, name)
            self.assertNotIn("hash", ok.get_data(as_text=True).lower())
            self.assertTrue(check_password_hash(self.user_row(name)["password_hash"], "BrandNew123"))
            c = self.app.test_client()
            with c.session_transaction() as s:
                s["csrf_token"] = TOKEN
            self.assertEqual(c.post("/login", data={"username": name, "password": "BrandNew123", "csrf_token": TOKEN}).status_code, 302)

    def test_password_change_only_affects_own_account(self):
        before = self.user_row("admin")["password_hash"]
        self.staff.call("post", "/api/profile/password", {"current_password": "Staff@123", "new_password": "BrandNew123", "confirm_password": "BrandNew123", "user_id": 1})
        self.assertEqual(self.user_row("admin")["password_hash"], before)

    def test_profile_requires_login_and_csrf(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/profile").status_code, 302)
        self.assertEqual(anon.get("/api/profile").status_code, 401)
        self.assertEqual(self.staff.client.put("/api/profile", json={"display_name": "No Token"}).status_code, 400)


class MigrationTests(unittest.TestCase):
    LEGACY_USER_DDL = """CREATE TABLE APP_USER (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(username)) >= 3),
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin', 'staff')),
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "legacy.db")
        db.init_db(self.path, "admin", "Admin@123", True)
        c = db.get_connection(self.path)  # turn the new database back into the OLD layout
        with c:
            c.execute("DROP TABLE APP_USER")
        c.execute(self.LEGACY_USER_DDL)
        self.hashes = {"admin": generate_password_hash("Admin@123"), "olduser": generate_password_hash("OldPass123")}
        with c:
            c.execute("INSERT INTO APP_USER (user_id, username, password_hash, role, created_at) VALUES (1,'admin',?, 'admin','2025-01-01 10:00:00')", (self.hashes["admin"],))
            c.execute("INSERT INTO APP_USER (user_id, username, password_hash, role, created_at) VALUES (7,'olduser',?, 'staff','2025-02-02 11:00:00')", (self.hashes["olduser"],))
        c.execute("PRAGMA user_version = 0")
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def counts(self):
        c = db.get_connection(self.path)
        out = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("CUSTOMER", "INSURANCE_AGENT", "INSURANCE_POLICY", "CLAIM")}
        c.close()
        return out

    def test_legacy_database_rejects_manager_before_migration(self):
        c = db.get_connection(self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO APP_USER (username, password_hash, role) VALUES ('mgr','x','manager')")
        c.close()

    def test_migration_preserves_everything_and_enables_manager(self):
        before = self.counts()
        result = db.migrate_database(self.path)
        self.assertTrue(result["actions"])
        self.assertTrue(os.path.exists(result["backup"]))
        c = db.get_connection(self.path)
        rows = {r["username"]: r for r in c.execute("SELECT * FROM APP_USER")}
        self.assertEqual((rows["olduser"]["user_id"], rows["olduser"]["role"], rows["olduser"]["created_at"]), (7, "staff", "2025-02-02 11:00:00"))
        for name in ("admin", "olduser"):
            self.assertEqual(rows[name]["password_hash"], self.hashes[name])  # hashes untouched
            self.assertEqual(rows[name]["is_active"], 1)
        self.assertEqual(self.counts(), before)
        with c:
            c.execute("INSERT INTO APP_USER (username, password_hash, role) VALUES ('mgr','x','manager')")
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 2)
        c.close()

    def test_migration_is_repeatable_and_makes_no_second_backup(self):
        first = db.migrate_database(self.path)
        second = db.migrate_database(self.path)
        self.assertEqual(second["actions"], [])
        self.assertIsNone(second["backup"])
        self.assertEqual(len(os.listdir(os.path.join(self.tmp.name, "backups"))), 1)
        self.assertTrue(first["backup"])

    def test_dry_run_changes_nothing(self):
        r = db.migrate_database(self.path, dry_run=True)
        self.assertTrue(r["actions"])
        c = db.get_connection(self.path)
        self.assertNotIn("manager", c.execute("SELECT sql FROM sqlite_master WHERE name='APP_USER'").fetchone()[0])
        c.close()

    def test_migrated_layout_matches_fresh_install(self):
        db.migrate_database(self.path)
        fresh = os.path.join(self.tmp.name, "fresh.db")
        db.init_db(fresh, "admin", "Admin@123", False)
        a, b = db.get_connection(self.path), db.get_connection(fresh)
        cols = lambda c: [(r[1], r[2], r[3], r[4]) for r in c.execute("PRAGMA table_info(APP_USER)")]
        self.assertEqual(sorted(cols(a)), sorted(cols(b)))
        a.close(); b.close()

    def test_existing_users_can_still_log_in_through_the_app(self):
        app = app_module.create_app({"TESTING": True, "DATABASE": self.path, "SECRET_KEY": "k", "ADMIN_PASSWORD": "Admin@123"})
        c = app.test_client()
        with c.session_transaction() as s:
            s["csrf_token"] = TOKEN
        self.assertEqual(c.post("/login", data={"username": "olduser", "password": "OldPass123", "csrf_token": TOKEN}).status_code, 302)
        self.assertEqual(c.get("/dashboard").status_code, 200)

    def test_diagnostics_flag_invalid_hashes_without_showing_them(self):
        c = db.get_connection(self.path)
        with c:
            c.execute("UPDATE APP_USER SET password_hash='plaintext' WHERE username='olduser'")
        diag = {d["username"]: d for d in db.user_diagnostics(c)}
        self.assertFalse(diag["olduser"]["valid_hash"])
        self.assertTrue(diag["admin"]["valid_hash"])
        self.assertNotIn("password_hash", diag["admin"])
        c.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
