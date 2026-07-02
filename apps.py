"""Resolve friendly app names to Android package names via ADB."""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass

from monitor import PACKAGE_PATTERN, run_adb

PACKAGE_LINE_RE = re.compile(r"^package:(?P<package>[a-zA-Z][\w.]*)$")

# Common distraction apps — intersected with installed apps for deep-work defaults
DISTRACTION_PACKAGES = frozenset(
    {
        "com.instagram.android",
        "com.instagram.barcelona",
        "com.snapchat.android",
        "com.zhiliaoapp.musically",
        "com.twitter.android",
        "com.facebook.katana",
        "com.reddit.frontpage",
        "com.google.android.youtube",
        "com.netflix.mediaclient",
        "com.spotify.music",
        "com.discord",
        "com.linkedin.android",
    }
)

KNOWN_LABELS: dict[str, str] = {
    "com.instagram.android": "Instagram",
    "com.instagram.barcelona": "Threads",
    "com.snapchat.android": "Snapchat",
    "com.google.android.gm": "Gmail",
    "com.google.android.youtube": "YouTube",
    "com.whatsapp": "WhatsApp",
    "com.twitter.android": "X",
    "com.facebook.katana": "Facebook",
    "com.reddit.frontpage": "Reddit",
    "com.spotify.music": "Spotify",
    "com.linkedin.android": "LinkedIn",
    "com.zhiliaoapp.musically": "TikTok",
    "com.nothing.launcher": "Nothing Launcher",
    "com.android.chrome": "Chrome",
    "com.android.settings": "Settings",
    "com.google.android.apps.maps": "Google Maps",
    "com.google.android.apps.photos": "Google Photos",
    "com.netflix.mediaclient": "Netflix",
    "com.discord": "Discord",
    "com.telegram.messenger": "Telegram",
}

SKIP_SLUGS = frozenset(
    {"android", "client", "app", "mobile", "lite", "hm", "db1", "katana", "musically"}
)

CACHE_TTL_SECONDS = 300
_app_cache: dict[str, str] = {}
_cache_loaded_at: float = 0.0


@dataclass
class AppMatchError(ValueError):
    message: str
    suggestions: list[str]

    def __str__(self) -> str:
        if self.suggestions:
            return f"{self.message} Did you mean: {', '.join(self.suggestions)}?"
        return self.message

    def __post_init__(self) -> None:
        ValueError.__init__(self, str(self))


def looks_like_package(name: str) -> bool:
    return "." in name and bool(PACKAGE_PATTERN.fullmatch(name.strip()))


def _list_user_packages() -> list[str]:
    result = run_adb(["shell", "pm", "list", "packages", "-3"])
    if result is None:
        return []
    packages: list[str] = []
    for line in result.stdout.splitlines():
        match = PACKAGE_LINE_RE.match(line.strip())
        if match:
            packages.append(match.group("package"))
    return packages


def label_for_package(package: str) -> str:
    if package in KNOWN_LABELS:
        return KNOWN_LABELS[package]
    parts = package.split(".")
    for part in reversed(parts):
        if part not in SKIP_SLUGS and len(part) > 2:
            return part.replace("_", " ").title()
    return package.rsplit(".", 1)[-1].replace("_", " ").title()


def get_installed_apps(*, force_refresh: bool = False) -> dict[str, str]:
    """Return mapping of package name -> human-readable app label."""
    global _app_cache, _cache_loaded_at

    now = time.monotonic()
    if not force_refresh and _app_cache and (now - _cache_loaded_at) < CACHE_TTL_SECONDS:
        return dict(_app_cache)

    packages = _list_user_packages()
    if not packages:
        return dict(_app_cache)

    _app_cache = {package: label_for_package(package) for package in packages}
    _cache_loaded_at = now
    return dict(_app_cache)


def display_name(package: str, app_map: dict[str, str] | None = None) -> str:
    if package in KNOWN_LABELS:
        return KNOWN_LABELS[package]
    apps = app_map if app_map is not None else get_installed_apps()
    return apps.get(package, label_for_package(package))


def format_app_line(package: str, app_map: dict[str, str]) -> str:
    label = display_name(package, app_map)
    if label.lower() == package.lower():
        return package
    return f"{label} ({package})"


def _suggestions(query: str, app_map: dict[str, str], limit: int = 5) -> list[str]:
    query_lower = query.lower()
    label_matches = [
        label
        for label in app_map.values()
        if query_lower in label.lower() or label.lower() in query_lower
    ]
    if label_matches:
        return sorted(set(label_matches))[:limit]

    close = difflib.get_close_matches(
        query_lower,
        [label.lower() for label in app_map.values()],
        n=limit,
        cutoff=0.5,
    )
    if close:
        reverse = {label.lower(): label for label in app_map.values()}
        return [reverse[item] for item in close]

    return sorted(app_map.values())[:limit]


def resolve_app(name_or_package: str) -> str:
    """Resolve a friendly app name or package string to a package name."""
    query = name_or_package.strip()
    if not query:
        raise AppMatchError("App name or package is required.", [])

    if looks_like_package(query):
        return query

    app_map = get_installed_apps()
    if not app_map:
        query_lower = query.lower()
        for package, label in KNOWN_LABELS.items():
            if label.lower() == query_lower or query_lower in label.lower():
                return package
        if looks_like_package(query):
            return query
        raise AppMatchError(
            "Could not load apps from the device. Connect ADB and try a package name like com.instagram.android.",
            sorted(KNOWN_LABELS.values())[:5],
        )

    query_lower = query.lower()

    for package, label in app_map.items():
        if package.lower() == query_lower:
            return package

    exact_label = [package for package, label in app_map.items() if label.lower() == query_lower]
    if len(exact_label) == 1:
        return exact_label[0]

    substring_label = [
        package for package, label in app_map.items() if query_lower in label.lower()
    ]
    if len(substring_label) == 1:
        return substring_label[0]
    if len(substring_label) > 1:
        labels = [app_map[package] for package in substring_label]
        raise AppMatchError(
            f"Multiple apps match {query!r}: {', '.join(labels)}.",
            labels,
        )

    substring_package = [package for package in app_map if query_lower in package.lower()]
    if len(substring_package) == 1:
        return substring_package[0]

    close_packages = difflib.get_close_matches(
        query_lower,
        [label.lower() for label in app_map.values()],
        n=1,
        cutoff=0.6,
    )
    if close_packages:
        target = close_packages[0]
        for package, label in app_map.items():
            if label.lower() == target:
                return package

    raise AppMatchError(
        f"No app found matching {query!r}.",
        _suggestions(query, app_map),
    )


def list_apps_text() -> str:
    app_map = get_installed_apps(force_refresh=True)
    if not app_map:
        return "Could not load apps from the device. Is ADB connected?"

    lines = ["Installed apps (label → package)", "-" * 40]
    for package in sorted(app_map, key=lambda pkg: app_map[pkg].lower()):
        lines.append(f"{app_map[package]} → {package}")
    lines.append(f"\nTotal: {len(app_map)} apps")
    return "\n".join(lines)


def default_distraction_packages() -> list[str]:
    """Installed distraction apps used when deep work starts without an explicit list."""
    installed = get_installed_apps()
    matched = sorted(pkg for pkg in installed if pkg in DISTRACTION_PACKAGES)
    if matched:
        return matched
    # ADB unavailable — still start session with known distraction packages
    return sorted(DISTRACTION_PACKAGES)


def resolve_app_list(apps_csv: str) -> list[str]:
    """Parse comma-separated app names/packages into package list."""
    if not apps_csv.strip():
        return default_distraction_packages()
    packages: list[str] = []
    for item in apps_csv.split(","):
        name = item.strip()
        if name:
            packages.append(resolve_app(name))
    return packages
