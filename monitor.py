"""Background daemon: ADB foreground polling, usage tally, and enforcement."""

from __future__ import annotations

import logging
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime

import database

logger = logging.getLogger("deepmode.monitor")

_adb_dir = os.environ.get("DEEPMODE_ADB_DIR", "").strip()
if _adb_dir:
    os.environ["PATH"] = _adb_dir + os.pathsep + os.environ.get("PATH", "")

PACKAGE_PATTERN = re.compile(r"^[a-zA-Z][\w.]*$")
FOCUS_PATTERNS = (
    re.compile(r"mCurrentFocus=.*?\s([a-zA-Z][\w.]*)/"),
    re.compile(r"mFocusedApp=.*?\s([a-zA-Z][\w.]*)/"),
    re.compile(r"mResumedActivity:.*?\s([a-zA-Z][\w.]*)/"),
)

SYSTEM_PACKAGES = frozenset(
    {
        "com.android.systemui",
        "com.google.android.apps.nexuslauncher",
        "com.android.launcher",
        "com.android.launcher3",
        "com.miui.home",
        "com.sec.android.app.launcher",
        "com.google.android.apps.pixelmigrate",
    }
)

NUDGE_COOLDOWN_SECONDS = 120
REOPEN_WINDOW_SECONDS = 120

_resolved_serial: str | None = None


def resolve_adb_serial() -> str | None:
    """Pick one device when multiple are connected; honor ANDROID_SERIAL if set."""
    global _resolved_serial
    if _resolved_serial is not None:
        return _resolved_serial

    env_serial = os.environ.get("ANDROID_SERIAL", "").strip()
    if env_serial:
        _resolved_serial = env_serial
        return _resolved_serial

    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    devices = [
        line.split()[0]
        for line in result.stdout.splitlines()[1:]
        if line.strip() and "\tdevice" in line
    ]
    if len(devices) == 1:
        return None
    if len(devices) > 1:
        for serial in devices:
            if serial[0].isdigit() and ":" in serial:
                _resolved_serial = serial
                logger.info("multiple devices; using %s", serial)
                return _resolved_serial
        _resolved_serial = devices[0]
        logger.info("multiple devices; using %s", _resolved_serial)
        return _resolved_serial
    return None


def run_adb(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    cmd = ["adb"]
    serial = resolve_adb_serial()
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=15,
        )
    except FileNotFoundError:
        logger.error("adb not found on PATH; install Android platform-tools")
        return None
    except subprocess.TimeoutExpired:
        logger.error("adb command timed out: %s", args)
        return None

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        logger.error("adb failed (%s): %s", result.returncode, stderr or args)
        return None
    return result


def parse_foreground_package(dumpsys_output: str) -> str | None:
    for pattern in FOCUS_PATTERNS:
        match = pattern.search(dumpsys_output)
        if match:
            package = match.group(1)
            if PACKAGE_PATTERN.fullmatch(package):
                return package
    return None


def is_system_package(package: str) -> bool:
    if package in SYSTEM_PACKAGES:
        return True
    return package.startswith("com.android.launcher")


def get_foreground_package() -> str | None:
    result = run_adb(["shell", "dumpsys", "window"])
    if result is None:
        return None
    return parse_foreground_package(result.stdout or "")


def send_home() -> bool:
    result = run_adb(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.HOME",
        ]
    )
    return result is not None


def force_stop(package: str) -> bool:
    if not PACKAGE_PATTERN.fullmatch(package):
        logger.error("refusing force-stop for invalid package: %s", package)
        return False
    result = run_adb(["shell", "am", "force-stop", package])
    return result is not None


def hard_enforce_enabled() -> bool:
    return os.environ.get("DEEPMODE_HARD_ENFORCE", "").strip() in {"1", "true", "yes"}


def poll_interval() -> float:
    poll_min = float(os.environ.get("DEEPMODE_POLL_MIN", "5"))
    poll_max = float(os.environ.get("DEEPMODE_POLL_MAX", "10"))
    if poll_max < poll_min:
        poll_max = poll_min
    return random.uniform(poll_min, poll_max)


def handle_reopen_after_nudge(package: str) -> None:
    if not database.recent_intervention_within(
        package, "soft-nudge", REOPEN_WINDOW_SECONDS
    ):
        return

    if hard_enforce_enabled():
        if force_stop(package):
            database.log_intervention(package, "force-stop")
            logger.info("force-stopped %s after reopen within 2 minutes", package)
    else:
        database.log_intervention(package, "reopen-after-nudge")
        logger.info("logged reopen-after-nudge for %s (hard enforce disabled)", package)


def maybe_enforce_limit(package: str) -> None:
    limit = database.get_effective_limit(package)
    if limit is None:
        return

    total = database.get_today_usage_for_package(package)
    if total < limit:
        return

    if database.recent_intervention_within(
        package, "soft-nudge", NUDGE_COOLDOWN_SECONDS
    ):
        return

    if send_home():
        database.log_intervention(package, "soft-nudge")
        try:
            import apps

            label = apps.display_name(package)
            display = label if label != package else package
        except Exception:
            display = package
        logger.warning(
            "FOCUS: sent you home from %s (%d/%d min today)",
            display,
            total,
            limit,
        )


def run_loop() -> None:
    database.init_db()
    logger.info("Deepmode monitor started (hard_enforce=%s)", hard_enforce_enabled())

    current_package: str | None = None
    accrued_seconds = 0.0
    last_tick = time.monotonic()
    last_reopen_check: str | None = None

    while True:
        time.sleep(poll_interval())

        now = time.monotonic()
        elapsed = now - last_tick
        last_tick = now

        package = get_foreground_package()
        if package is None:
            continue

        if package != current_package:
            current_package = package
            accrued_seconds = 0.0
            last_reopen_check = None

        if is_system_package(package):
            continue

        accrued_seconds += elapsed

        if (
            last_reopen_check != package
            and database.recent_intervention_within(
                package, "soft-nudge", REOPEN_WINDOW_SECONDS
            )
        ):
            handle_reopen_after_nudge(package)
            last_reopen_check = package

        while accrued_seconds >= 60.0:
            database.increment_usage_minute(package)
            accrued_seconds -= 60.0
            maybe_enforce_limit(package)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    try:
        run_loop()
    except KeyboardInterrupt:
        logger.info("monitor stopped")


if __name__ == "__main__":
    main()
