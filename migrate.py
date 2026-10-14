"""SafeGuard IMS - database migration / diagnostics tool (safe to run any number of times).

    python migrate.py            back up the database, then upgrade it if an upgrade is needed
    python migrate.py --check    report only: shows what would change and lists users (no hashes shown)
    python migrate.py --db PATH  use a specific database file (default: SAFEGUARD_DB_PATH or instance/safeguard.db)
"""
import argparse
import os

import database as db


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("SAFEGUARD_DB_PATH") or db.default_db_path())
    ap.add_argument("--check", action="store_true", help="dry run: change nothing")
    ap.add_argument("--no-backup", action="store_true", help="skip the automatic backup (not recommended)")
    args = ap.parse_args()

    print("Database:", args.db)
    result = db.migrate_database(args.db, backup=not args.no_backup, dry_run=args.check)
    print(result["message"])
    for step in result["actions"]:
        print("  -", step)
    if result["backup"]:
        print("Backup written to:", result["backup"])

    if os.path.exists(args.db):
        conn = db.get_connection(args.db)
        try:
            print("\nUser accounts (password hashes are never printed):")
            print(f"  {'ID':<4} {'Username':<20} {'Role':<9} {'Active':<7} Hash format")
            for u in db.user_diagnostics(conn):
                print(f"  {u['user_id']:<4} {u['username']:<20} {u['role']:<9} {str(u['active']):<7} "
                      f"{'valid' if u['valid_hash'] else 'INVALID - reset this password from User Management'}")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
