"""Reset the Admin or Scorekeeper password from the command line.

Usage (inside the LXC):
    cd /opt/axeleague
    ./venv/bin/python set_password.py admin
    ./venv/bin/python set_password.py scorekeeper

Or non-interactively (be careful — password lands in shell history):
    ./venv/bin/python set_password.py admin --password "newpw"

Changes take effect on the next login; no restart required.
"""
import argparse
import getpass
import sqlite3
import sys

from werkzeug.security import generate_password_hash

import db as dbmod
from auth import ROLES, ensure_schema


def main():
    ap = argparse.ArgumentParser(description="Reset a login password.")
    ap.add_argument("role", choices=ROLES, help="which login to reset")
    ap.add_argument("--password", help="new password (omit to be prompted)")
    args = ap.parse_args()

    pw = args.password
    if not pw:
        pw = getpass.getpass(f"New {args.role} password: ")
        if pw != getpass.getpass("Confirm: "):
            sys.exit("Passwords don't match — nothing changed.")
    if len(pw) < 4:
        sys.exit("Password must be at least 4 characters — nothing changed.")

    ensure_schema()
    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.execute(
        "INSERT INTO users (role, password_hash) VALUES (?, ?)"
        " ON CONFLICT(role) DO UPDATE SET password_hash=excluded.password_hash",
        (args.role, generate_password_hash(pw)))
    conn.commit()
    conn.close()
    print(f"{args.role} password updated ({dbmod.DB_PATH}). "
          "It applies to the next login — no restart needed.")


if __name__ == "__main__":
    main()
