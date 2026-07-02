"""FastMCP server exposing wellbeing telemetry and limit management to Cursor."""

from __future__ import annotations

import asyncio
import re
from datetime import date

from fastmcp import FastMCP

import apps as apps_mod
import database
import monitor_ctl

mcp = FastMCP("Wellbeing-Guardian")

PACKAGE_PATTERN = re.compile(r"^[a-zA-Z][\w.]*$")


def _resolve_app_input(name_or_package: str) -> str:
    try:
        return apps_mod.resolve_app(name_or_package)
    except apps_mod.AppMatchError:
        raise
    except Exception as exc:
        raise ValueError(str(exc)) from exc


@mcp.resource("device://usage-summary")
async def usage_summary() -> str:
    rows = await asyncio.to_thread(database.get_today_usage_summary)
    app_map = await asyncio.to_thread(apps_mod.get_installed_apps)
    today = date.today().isoformat()
    lines = [f"Daily usage ({today})", "-" * 40]
    if not rows:
        lines.append("(no usage recorded today)")
    else:
        for package, minutes in rows:
            lines.append(f"{apps_mod.format_app_line(package, app_map):<45} {minutes} min")
    return "\n".join(lines)


@mcp.resource("device://intervention-history")
async def intervention_history() -> str:
    rows = await asyncio.to_thread(database.get_intervention_history)
    if not rows:
        return "(no interventions recorded)"
    app_map = await asyncio.to_thread(apps_mod.get_installed_apps)
    return "\n".join(
        f"{timestamp} | {apps_mod.format_app_line(package, app_map)} | {action_type}"
        for timestamp, package, action_type in rows
    )


@mcp.resource("device://session-status")
async def session_status() -> str:
    return await asyncio.to_thread(database.get_session_status_text)


@mcp.tool
async def start_deep_work(
    apps: str = "",
    limit_minutes: int = 5,
    bonus_every_minutes: int = 30,
) -> str:
    """Start a coding focus session. Apps optional (comma-separated); defaults to common distractions at limit_minutes. Limit scales up the longer the session runs (+1 min per bonus_every_minutes, default 30)."""
    if limit_minutes <= 0:
        raise ValueError("limit_minutes must be greater than zero")
    packages = await asyncio.to_thread(apps_mod.resolve_app_list, apps)
    if not packages:
        return (
            "No distraction apps found to limit. Connect ADB or pass apps explicitly "
            "(e.g. 'Instagram,Snapchat')."
        )
    session_id = await asyncio.to_thread(
        database.start_session,
        packages,
        limit_minutes,
        bonus_every_minutes,
        1,
    )
    monitor_msg = await asyncio.to_thread(monitor_ctl.start_monitor)
    app_map = await asyncio.to_thread(apps_mod.get_installed_apps)
    labels = [apps_mod.display_name(pkg, app_map) for pkg in packages]
    return (
        f"Deep-work session #{session_id} started.\n"
        f"Apps ({len(packages)}): {', '.join(labels)}\n"
        f"Starting limit: {limit_minutes} min per app (scales +1 min every {bonus_every_minutes} min you stay in session).\n"
        f"{monitor_msg}"
    )


@mcp.tool
async def end_deep_work() -> str:
    """End the active coding focus session and stop session-scaled limits."""
    ended = await asyncio.to_thread(database.end_active_session)
    monitor_msg = await asyncio.to_thread(monitor_ctl.stop_monitor)
    if ended:
        return f"Deep-work session ended. Session-scaled limits are no longer active.\n{monitor_msg}"
    return f"No active deep-work session to end.\n{monitor_msg}"


@mcp.tool
async def list_apps() -> str:
    """List installed apps on the connected phone as 'App Name → package.name'."""
    return await asyncio.to_thread(apps_mod.list_apps_text)


@mcp.tool
async def set_app_limit(app: str, limit_minutes: int) -> str:
    """Set or replace a daily usage limit. Accepts app name ('Instagram') or package ('com.instagram.android')."""
    if limit_minutes <= 0:
        raise ValueError("limit_minutes must be greater than zero")
    package = await asyncio.to_thread(_resolve_app_input, app)
    await asyncio.to_thread(database.upsert_goal, package, limit_minutes)
    app_map = await asyncio.to_thread(apps_mod.get_installed_apps)
    label = apps_mod.display_name(package, app_map)
    if label != package:
        return f"Set daily limit for {label} ({package}) to {limit_minutes} minutes."
    return f"Set daily limit for {package} to {limit_minutes} minutes."


@mcp.tool
async def remove_app_limit(app: str) -> str:
    """Remove a daily usage limit. Accepts app name ('Instagram') or package ('com.instagram.android')."""
    package = await asyncio.to_thread(_resolve_app_input, app)
    removed = await asyncio.to_thread(database.delete_goal, package)
    app_map = await asyncio.to_thread(apps_mod.get_installed_apps)
    label = apps_mod.display_name(package, app_map)
    display = f"{label} ({package})" if label != package else package
    if removed:
        return f"Removed daily limit for {display}."
    return f"No limit found for {display}."


@mcp.prompt(name="start-deep-work")
def start_deep_work_prompt() -> str:
    """Begin a coding focus session with smart defaults."""
    return (
        "You are starting a Deepmode coding focus session.\n\n"
        "1. By default, call `start_deep_work` with no apps argument — limits common distraction apps at 5 minutes, starts the monitor automatically, scaling +1 min every 30 min of session.\n"
        "2. Only ask which apps or what limit if the user wants to customize.\n"
        "3. If they specify apps or minutes, pass them to `start_deep_work(apps=..., limit_minutes=...)`.\n"
        "4. Summarize what was limited and explain limits grow the longer the coding session runs.\n"
        "5. Call `end_deep_work` when the user is done — this ends the session and stops the monitor."
    )


@mcp.prompt(name="behavior-audit")
def behavior_audit() -> str:
    """Session focus review: cross-reference usage and interventions from a coding session."""
    return (
        "You are reviewing a Deepmode coding focus session.\n\n"
        "1. Read `device://usage-summary` for today's per-app minutes.\n"
        "2. Read `device://intervention-history` for nudges during the session.\n"
        "3. Cross-reference both: which distraction apps were used, how many nudges fired, reopen loops.\n"
        "4. Stay observational and brief — this is a coding focus check, not a lecture.\n"
        "5. If limits seem misaligned, suggest `set_app_limit` changes using friendly app names "
        "(do not change limits without asking).\n"
        "6. Use `list_apps` if you need to resolve an app name.\n"
        "7. End with one actionable takeaway for the next coding session."
    )


def main() -> None:
    database.init_db()
    mcp.run()


if __name__ == "__main__":
    main()
