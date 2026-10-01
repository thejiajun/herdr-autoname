#!/usr/bin/env python3
"""Open the plugin's interactive settings popup."""

import os
import shutil
import subprocess
import sys


def main():
    configured_herdr = os.environ.get("HERDR_BIN_PATH", "").strip()
    herdr = (
        configured_herdr
        if configured_herdr and shutil.which(configured_herdr)
        else shutil.which("herdr") or "herdr"
    )
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
