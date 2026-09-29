#!/usr/bin/env python3
"""Open the plugin's interactive settings popup."""

import os
import subprocess
import sys


def main():
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr").strip() or "herdr"
    command = [
        herdr,
        "plugin",
        "pane",
        "open",
        "--plugin",
        os.environ.get("HERDR_PLUGIN_ID", "thejiajun.autoname"),
        "--entrypoint",
        "settings",
        "--focus",
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
