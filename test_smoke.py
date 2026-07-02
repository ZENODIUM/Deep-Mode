"""Automated smoke tests for Deepmode (run: uv run python test_smoke.py)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import apps
import database
import monitor
import server


def ok(label: str) -> None:
    print(f"  OK  {label}")


def fail(label: str, detail: str = "") -> None:
    print(f"  FAIL {label}" + (f": {detail}" if detail else ""))
    sys.exit(1)


def test_database() -> None:
    print("\n[database]")
    db_path = Path(__file__).resolve().parent / ".test_guardian.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    os.environ["DEEPMODE_DB"] = str(db_path)
    database.init_db()

    database.upsert_goal("com.test.app", 5)
    ok("upsert_goal")
    assert database.get_goal("com.test.app") == 5
    ok("get_goal")

    database.increment_usage_minute("com.test.app")
    database.increment_usage_minute("com.test.app")
    summary = database.get_today_usage_summary()
    assert summary == [("com.test.app", 2)]
    ok("increment_usage_minute + summary")

    database.log_intervention("com.test.app", "soft-nudge")
    history = database.get_intervention_history()
    assert len(history) == 1 and history[0][2] == "soft-nudge"
    ok("log_intervention + history")

    assert database.delete_goal("com.test.app")
    ok("delete_goal")


def test_adb_foreground() -> None:
    print("\n[adb]")
    pkg = monitor.get_foreground_package()
    if pkg is None:
        fail("get_foreground_package", "no device or parse failed — is phone connected?")
    ok(f"foreground package = {pkg}")


def test_monitor_helpers() -> None:
    print("\n[monitor]")
    sample = "  mCurrentFocus=Window{abc u0 com.example.app/com.example.MainActivity}"
    assert monitor.parse_foreground_package(sample) == "com.example.app"
    ok("parse_foreground_package")

    assert monitor.is_system_package("com.android.launcher3")
    assert not monitor.is_system_package("com.example.app")
    ok("is_system_package")


async def test_server_layer() -> None:
    print("\n[server]")
    # Use project DB for read-only resource smoke test
    os.environ.pop("DEEPMODE_DB", None)
    database.init_db()

    summary = await server.usage_summary()
    assert "Daily usage" in summary
    ok("usage_summary resource")

    history = await server.intervention_history()
    assert history  # string, may be empty message
    ok("intervention_history resource")

    result = await server.set_app_limit("com.smoke.test", 10)
    assert "com.smoke.test" in result
    ok("set_app_limit tool")

    result = await server.remove_app_limit("com.smoke.test")
    assert "Removed" in result
    ok("remove_app_limit tool")

    prompt = server.behavior_audit()
    assert "usage-summary" in prompt and "intervention-history" in prompt
    ok("behavior-audit prompt")

    start = server.start_deep_work_prompt()
    assert "start_deep_work" in start and "monitor.py" in start
    ok("start-deep-work prompt")

    # session scaling
    database.end_active_session()
    sid = database.start_session(["com.test.app"], base_limit_minutes=5, bonus_every_minutes=30)
    assert sid > 0
    assert database.get_effective_limit("com.test.app") == 5
    ok("session start + base limit")
    database.end_active_session()


def test_app_name_resolution() -> None:
    print("\n[apps]")
    sample = {
        "com.instagram.android": "Instagram",
        "com.snapchat.android": "Snapchat",
        "com.whatsapp": "WhatsApp",
    }
    apps._app_cache = dict(sample)
    apps._cache_loaded_at = time.monotonic()

    assert apps.resolve_app("com.instagram.android") == "com.instagram.android"
    assert apps.resolve_app("Instagram") == "com.instagram.android"
    assert apps.resolve_app("snapchat") == "com.snapchat.android"
    ok("resolve_app friendly names")

    assert "Instagram" in apps.format_app_line("com.instagram.android", sample)
    ok("format_app_line")

    apps._app_cache = {}
    apps._cache_loaded_at = 0.0


def test_monitor_poll_once() -> None:
    print("\n[monitor poll]")
    database.init_db()
    pkg = monitor.get_foreground_package()
    if pkg and not monitor.is_system_package(pkg):
        before = database.get_today_usage_for_package(pkg)
        ok(f"poll read {pkg} (usage today: {before} min)")
    else:
        ok(f"poll read system/launcher ({pkg}) — usage tally skipped as expected")


def main() -> None:
    print("Deepmode smoke tests")
    test_database()
    test_app_name_resolution()
    test_monitor_helpers()
    test_adb_foreground()
    test_monitor_poll_once()
    asyncio.run(test_server_layer())
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
