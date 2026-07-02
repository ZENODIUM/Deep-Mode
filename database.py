"""SQLite persistence for Deepmode Wellbeing Guardian."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

_WRITE_LOCK = threading.Lock()

_DEFAULT_DB = Path(__file__).resolve().parent / "guardian.db"


def get_db_path() -> Path:
    return Path(os.environ.get("DEEPMODE_DB", _DEFAULT_DB))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    if db_path is not None:
        os.environ["DEEPMODE_DB"] = str(db_path)
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS goals (
                package TEXT PRIMARY KEY,
                limit_minutes INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_usage (
                package TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS interventions (
                package TEXT NOT NULL,
                action_type TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                ended_at DATETIME,
                base_limit_minutes INTEGER NOT NULL DEFAULT 5,
                bonus_every_minutes INTEGER NOT NULL DEFAULT 30,
                bonus_minutes INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS session_packages (
                session_id INTEGER NOT NULL,
                package TEXT NOT NULL,
                PRIMARY KEY (session_id, package),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            """
        )
        conn.commit()


def upsert_goal(package: str, limit_minutes: int) -> None:
    with _WRITE_LOCK, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO goals (package, limit_minutes) VALUES (?, ?)",
            (package, limit_minutes),
        )
        conn.commit()


def delete_goal(package: str) -> bool:
    with _WRITE_LOCK, _connect() as conn:
        cursor = conn.execute("DELETE FROM goals WHERE package = ?", (package,))
        conn.commit()
        return cursor.rowcount > 0


def get_goal(package: str) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT limit_minutes FROM goals WHERE package = ?", (package,)
        ).fetchone()
        return int(row["limit_minutes"]) if row else None


def increment_usage_minute(package: str) -> None:
    with _WRITE_LOCK, _connect() as conn:
        row = conn.execute(
            """
            SELECT rowid FROM app_usage
            WHERE package = ? AND date(timestamp) = date('now')
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (package,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE app_usage SET duration_minutes = duration_minutes + 1 WHERE rowid = ?",
                (row["rowid"],),
            )
        else:
            conn.execute(
                "INSERT INTO app_usage (package, duration_minutes) VALUES (?, 1)",
                (package,),
            )
        conn.commit()


def get_today_usage_for_package(package: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(duration_minutes), 0) AS total
            FROM app_usage
            WHERE package = ? AND date(timestamp) = date('now')
            """,
            (package,),
        ).fetchone()
        return int(row["total"])


def get_today_usage_summary() -> list[tuple[str, int]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT package, SUM(duration_minutes) AS total_minutes
            FROM app_usage
            WHERE date(timestamp) = date('now')
            GROUP BY package
            ORDER BY total_minutes DESC, package ASC
            """
        ).fetchall()
        return [(row["package"], int(row["total_minutes"])) for row in rows]


def log_intervention(package: str, action_type: str) -> None:
    with _WRITE_LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO interventions (package, action_type) VALUES (?, ?)",
            (package, action_type),
        )
        conn.commit()


def get_intervention_history() -> list[tuple[str, str, str]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, package, action_type
            FROM interventions
            ORDER BY timestamp ASC
            """
        ).fetchall()
        return [
            (row["timestamp"], row["package"], row["action_type"]) for row in rows
        ]


def last_intervention(package: str, action_type: str) -> datetime | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT timestamp FROM interventions
            WHERE package = ? AND action_type = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (package, action_type),
        ).fetchone()
        if not row:
            return None
        return datetime.fromisoformat(row["timestamp"])


def recent_intervention_within(
    package: str, action_type: str, within_seconds: int
) -> bool:
    last = last_intervention(package, action_type)
    if last is None:
        return False
    elapsed = (datetime.now() - last).total_seconds()
    return elapsed <= within_seconds


def end_active_session() -> bool:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _WRITE_LOCK, _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE sessions SET ended_at = ?
            WHERE ended_at IS NULL
            """,
            (now,),
        )
        conn.commit()
        return cursor.rowcount > 0


def start_session(
    packages: list[str],
    base_limit_minutes: int = 5,
    bonus_every_minutes: int = 30,
    bonus_minutes: int = 1,
) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _WRITE_LOCK, _connect() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL",
            (now,),
        )
        cursor = conn.execute(
            """
            INSERT INTO sessions (
                started_at, base_limit_minutes, bonus_every_minutes, bonus_minutes
            ) VALUES (?, ?, ?, ?)
            """,
            (now, base_limit_minutes, bonus_every_minutes, bonus_minutes),
        )
        session_id = int(cursor.lastrowid)
        for package in packages:
            conn.execute(
                "INSERT INTO session_packages (session_id, package) VALUES (?, ?)",
                (session_id, package),
            )
        conn.commit()
        return session_id


def get_active_session() -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, started_at, base_limit_minutes, bonus_every_minutes, bonus_minutes
            FROM sessions
            WHERE ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        packages = conn.execute(
            "SELECT package FROM session_packages WHERE session_id = ?",
            (row["id"],),
        ).fetchall()
        return {
            "id": int(row["id"]),
            "started_at": datetime.fromisoformat(row["started_at"]),
            "base_limit_minutes": int(row["base_limit_minutes"]),
            "bonus_every_minutes": int(row["bonus_every_minutes"]),
            "bonus_minutes": int(row["bonus_minutes"]),
            "packages": [p["package"] for p in packages],
        }


def get_effective_limit(package: str) -> int | None:
    session = get_active_session()
    if session and package in session["packages"]:
        elapsed_minutes = max(
            0.0, (datetime.now() - session["started_at"]).total_seconds() / 60
        )
        bonus = int(elapsed_minutes // session["bonus_every_minutes"]) * session[
            "bonus_minutes"
        ]
        return session["base_limit_minutes"] + bonus
    return get_goal(package)


def get_session_status_text() -> str:
    session = get_active_session()
    if not session:
        return "No active deep-work session."
    elapsed = int((datetime.now() - session["started_at"]).total_seconds() // 60)
    lines = [
        f"Deep-work session active ({elapsed} min)",
        f"Base limit: {session['base_limit_minutes']} min",
        f"Scales +{session['bonus_minutes']} min every {session['bonus_every_minutes']} min of session",
        f"Apps ({len(session['packages'])}): {', '.join(session['packages'])}",
    ]
    return "\n".join(lines)
