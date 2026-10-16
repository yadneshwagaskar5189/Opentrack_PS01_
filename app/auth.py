"""Password hashing and login tokens (stdlib only)."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime

ITERATIONS = 120_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except ValueError:
        return False


def public_user(row) -> dict:
    return {"id": row["id"], "username": row["username"], "role": row["role"],
            "display_name": row["display_name"], "trainer_id": row["trainer_id"]}


def login(conn, username: str, password: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
                 (token, row["id"], datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return {"token": token, "user": public_user(row)}


def user_from_token(conn, token: str) -> dict | None:
    row = conn.execute("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
                       (token,)).fetchone()
    return public_user(row) if row else None


def logout(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
