"""
CLI helper to generate a bcrypt hash for the ADMIN_PASSWORD_HASH env var.

Run interactively and paste the resulting hash into your `.env` file:

    $ python scripts/hash_password.py
    Password:
    Confirm:
    $2b$12$...

The password is read with `getpass` so it never echoes to the terminal
and never appears in shell history.
"""

from __future__ import annotations

import getpass
import sys

from src.web.auth import hash_password


def _read_confirmed_password() -> str:
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm:  ")
    if pw1 != pw2:
        sys.stderr.write("Passwords do not match.\n")
        sys.exit(1)
    if not pw1:
        sys.stderr.write("Password must not be empty.\n")
        sys.exit(1)
    if len(pw1) < 12:
        sys.stderr.write(
            "Warning: password is shorter than 12 characters. "
            "Consider a longer passphrase.\n"
        )
    return pw1


def main() -> None:
    password = _read_confirmed_password()
    sys.stdout.write(hash_password(password) + "\n")


if __name__ == "__main__":
    main()
