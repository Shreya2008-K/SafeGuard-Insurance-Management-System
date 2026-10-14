-- =====================================================================
-- SafeGuard IMS - SQL demonstration queries (SQLite syntax)
-- Open the database with:   sqlite3 instance/safeguard.db     (or DB Browser for SQLite)
-- Then:  .read database/queries.sql
-- Statements that change data are wrapped in a transaction and rolled back at
-- the end, so you can run the whole file repeatedly without altering the data.
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ============================ 1. DDL ================================
-- CREATE TABLE / CREATE INDEX / CREATE VIEW / CREATE TRIGGER are in schema.sql.
-- Inspect what exists:
SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index', 'view', 'trigger') ORDER BY type, name;

-- ALTER TABLE (demonstration on a scratch table so the real schema is untouched)
BEGIN;
CREATE TABLE demo_notes (id INTEGER PRIMARY KEY, note TEXT);
ALTER TABLE demo_notes ADD COLUMN created_by TEXT;
ALTER TABLE demo_notes RENAME TO demo_remarks;
SELECT sql FROM sqlite_master WHERE name = 'demo_remarks';
DROP TABLE demo_remarks;
ROLLBACK;

-- ============================ 2. DML ================================
BEGIN;
-- INSERT
INSERT INTO CUSTOMER (full_name, age, gender, phone, email, address)
VALUES ('Demo Customer', 30, 'Other', '9000000000', 'demo.customer@example.com', 'Demo Street');
-- UPDATE
UPDATE CUSTOMER SET phone = '9111111111' WHERE email = 'demo.customer@example.com';
-- SELECT
SELECT * FROM CUSTOMER WHERE email = 'demo.customer@example.com';
-- DELETE
DELETE FROM CUSTOMER WHERE email = 'demo.customer@example.com';
ROLLBACK;

-- ======================= 3. WHERE / ORDER BY ========================
-- Active policies with premium above 15,000, highest premium first
SELECT policy_id, policy_type, premium_amount, status
FROM INSURANCE_POLICY
WHERE status = 'Active' AND premium_amount > 15000
ORDER BY premium_amount DESC;

-- ================== 4. Aggregates: COUNT SUM AVG MIN MAX =============
SELECT COUNT(*)            AS total_policies,
       SUM(premium_amount) AS total_premium,
       AVG(premium_amount) AS average_premium,
       MIN(premium_amount) AS lowest_premium,
       MAX(premium_amount) AS highest_premium
FROM INSURANCE_POLICY;

-- ===================== 5. GROUP BY / HAVING =========================
-- Policy types with more than three policies
SELECT policy_type, COUNT(*) AS policies, SUM(premium_amount) AS total_premium
FROM INSURANCE_POLICY
GROUP BY policy_type
HAVING COUNT(*) > 3
ORDER BY policies DESC;

-- Claims per status with total amount
SELECT claim_status, COUNT(*) AS claims, SUM(claim_amount) AS total_amount
FROM CLAIM
GROUP BY claim_status;

-- ========================= 6. INNER JOIN ============================
-- Each policy with its customer
SELECT p.policy_id, c.full_name, p.policy_type, p.premium_amount
FROM INSURANCE_POLICY p
INNER JOIN CUSTOMER c ON c.customer_id = p.customer_id
ORDER BY p.policy_id;

-- Claims with policy and customer (three-table join)
SELECT cl.claim_id, c.full_name, p.policy_type, cl.claim_amount, cl.claim_status
FROM CLAIM cl
INNER JOIN INSURANCE_POLICY p ON p.policy_id = cl.policy_id
INNER JOIN CUSTOMER c ON c.customer_id = p.customer_id;

-- ========================== 7. LEFT JOIN ============================
-- All customers, including those with no policies (policy count 0)
SELECT c.customer_id, c.full_name, COUNT(p.policy_id) AS policies
FROM CUSTOMER c
LEFT JOIN INSURANCE_POLICY p ON p.customer_id = c.customer_id
GROUP BY c.customer_id
ORDER BY policies DESC, c.full_name;

-- Agents and the premium they brought in (agents with no policies show 0)
SELECT a.agent_name, COUNT(p.policy_id) AS policies,
       COALESCE(SUM(p.premium_amount), 0) AS premium,
       ROUND(COALESCE(SUM(p.premium_amount), 0) * a.commission_percentage / 100, 2) AS commission_earned
FROM INSURANCE_AGENT a
LEFT JOIN INSURANCE_POLICY p ON p.agent_id = a.agent_id
GROUP BY a.agent_id
ORDER BY premium DESC;

-- ========================== 8. Subqueries ===========================
-- Customers whose total premium is above the average customer total
SELECT c.full_name, SUM(p.premium_amount) AS total_premium
FROM CUSTOMER c
JOIN INSURANCE_POLICY p ON p.customer_id = c.customer_id
GROUP BY c.customer_id
HAVING SUM(p.premium_amount) > (
    SELECT AVG(customer_total) FROM (
        SELECT SUM(premium_amount) AS customer_total FROM INSURANCE_POLICY GROUP BY customer_id));

-- Customers who have never made a claim
SELECT full_name FROM CUSTOMER
WHERE customer_id NOT IN (
    SELECT p.customer_id FROM INSURANCE_POLICY p JOIN CLAIM cl ON cl.policy_id = p.policy_id);

-- ============================ 9. Views ==============================
SELECT * FROM V_POLICY_DETAILS ORDER BY policy_id LIMIT 5;
SELECT * FROM V_CLAIM_DETAILS WHERE claim_status = 'Pending';
SELECT * FROM V_POLICY_TYPE_SUMMARY;

-- ===================== 10. Triggers (demonstration) =================
-- Each statement below is EXPECTED to fail with the trigger's message.
-- (In the sqlite3 shell, the error is printed and execution continues.)
-- a) Claim larger than the policy coverage -> trg_claim_within_coverage
INSERT INTO CLAIM (policy_id, claim_date, claim_amount, claim_reason)
VALUES (1, date('now'), 999999999, 'Too large');
-- b) Changing the status of an already decided claim -> trg_claim_only_pending_changes
UPDATE CLAIM SET claim_status = 'Rejected' WHERE claim_id = 1;
-- c) Deciding a pending claim stamps decision_date automatically -> trg_claim_stamp_decision_date
BEGIN;
UPDATE CLAIM SET claim_status = 'Approved' WHERE claim_id = 4;
SELECT claim_id, claim_status, decision_date FROM CLAIM WHERE claim_id = 4;
ROLLBACK;

-- ================= 11. Foreign key enforcement ======================
-- Expected to fail: a policy cannot reference a customer that does not exist.
INSERT INTO INSURANCE_POLICY (customer_id, policy_type, premium_amount, coverage_amount, start_date, end_date, status)
VALUES (9999, 'Life Insurance', 1000, 100000, '2030-01-01', '2031-01-01', 'Pending');
-- Expected to fail: a customer who still has policies cannot be deleted (ON DELETE RESTRICT).
DELETE FROM CUSTOMER WHERE customer_id = 1;

-- ============== 12. Users and roles (APP_USER) - password hashes are never selected =============
SELECT user_id, username, role, is_active, display_name FROM APP_USER ORDER BY user_id;
SELECT role, COUNT(*) AS accounts FROM APP_USER GROUP BY role;
-- Expected to fail: the CHECK constraint only allows admin, manager and staff.
INSERT INTO APP_USER (username, password_hash, role) VALUES ('bad_role_user', 'x', 'superuser');
