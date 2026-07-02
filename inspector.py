"""Launch the MCP Inspector web UI for the Wellbeing-Guardian server."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SERVER_FILE = PROJECT_DIR / "server.py"
DEFAULT_DB = PROJECT_DIR / "guardian.db"


def main() -> None:
    os.environ.setdefault("DEEPMODE_DB", str(DEFAULT_DB))

    ui_port = os.environ.get("DEEPMODE_INSPECTOR_UI_PORT", "6274")
    server_port = os.environ.get("DEEPMODE_INSPECTOR_SERVER_PORT", "6277")

    cmd = [
        "fastmcp",
        "dev",
        "inspector",
        str(SERVER_FILE),
        "--project",
        str(PROJECT_DIR),
        "--ui-port",
        ui_port,
        "--server-port",
        server_port,
    ]

    print("Starting MCP Inspector for Wellbeing-Guardian...")
    print(f"  Server:  {SERVER_FILE.name}")
    print(f"  DB:      {os.environ['DEEPMODE_DB']}")
    print(f"  UI port: {ui_port}")
    print()
    print("A browser window should open automatically.")
    print("In the Inspector, connect with transport STDIO if not already connected.")
    print("Press Ctrl+C to stop.")
    print()

    try:
        subprocess.run(cmd, cwd=PROJECT_DIR, check=True)
    except KeyboardInterrupt:
        print("\nInspector stopped.")
    except FileNotFoundError:
        print("fastmcp CLI not found. Run: uv sync", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


if __name__ == "__main__":
    main()
