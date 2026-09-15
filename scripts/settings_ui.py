#!/usr/bin/env python3
"""Interactive terminal settings UI for the Herdr Autoname plugin."""

import argparse
import curses
import os
import subprocess
import sys

import rename_workspaces as autoname


DEFAULT_INTERVAL = 3
MENU = ("provider", "model", "interval", "enabled", "preview", "rename", "save", "cancel")


def load_settings():
    raw_interval = autoname.env_value("HERDR_AUTONAME_TRIGGER_EVERY", autoname.AUTONAME_ENV)
    try:
        interval = max(1, int(raw_interval or DEFAULT_INTERVAL))
    except ValueError:
        interval = DEFAULT_INTERVAL
    raw_enabled = autoname.env_value("HERDR_AUTONAME_ENABLED", autoname.AUTONAME_ENV)
    return {
        "provider": autoname.saved_provider(),
        "model": autoname.saved_model(),
        "interval": interval,
        "enabled": raw_enabled.lower() not in {"0", "false", "no", "off"},
    }


def save_settings(settings):
    autoname.set_env_value(autoname.AUTONAME_ENV, "HERDR_AUTONAME_PROVIDER", settings["provider"])
    autoname.set_env_value(autoname.AUTONAME_ENV, "HERDR_AUTONAME_MODEL", settings["model"])
    autoname.set_env_value(
        autoname.AUTONAME_ENV, "HERDR_AUTONAME_TRIGGER_EVERY", str(settings["interval"])
    )
    autoname.set_env_value(
        autoname.AUTONAME_ENV,
        "HERDR_AUTONAME_ENABLED",
        "true" if settings["enabled"] else "false",
    )


def available_providers():
    args = argparse.Namespace(env_file=autoname.AUTONAME_ENV)
    availability = autoname.provider_availability(args)
    return [name for name in autoname.PROVIDERS if name == "auto" or availability.get(name)]


def fit(text, width):
    return autoname.fit_cell(str(text), width).rstrip()


def add(stdscr, row, col, text, style=0):
    height, width = stdscr.getmaxyx()
    if 0 <= row < height and col < width:
        try:
            stdscr.addstr(row, col, fit(text, max(0, width - col - 1)), style)
        except curses.error:
            pass


def edit_text(stdscr, row, current, label):
    height, width = stdscr.getmaxyx()
    prompt = f"{label}: "
    stdscr.move(row, 0)
    stdscr.clrtoeol()
    add(stdscr, row, 2, prompt + current)
    curses.echo()
    curses.curs_set(1)
    try:
        stdscr.move(row, min(width - 2, 2 + len(prompt)))
        value = stdscr.getstr(row, 2 + len(prompt), max(1, width - len(prompt) - 5))
        return value.decode("utf-8").strip()
    except (curses.error, UnicodeDecodeError):
        return current
    finally:
        curses.noecho()
        curses.curs_set(0)


def run_command(stdscr, args):
    curses.def_prog_mode()
    curses.endwin()
    command = [sys.executable, os.path.join(os.path.dirname(__file__), "rename_workspaces.py"), *args]
    code = subprocess.run(command, check=False).returncode
    print("\nPress Enter to return to Settings…", flush=True)
    input()
    curses.reset_prog_mode()
    stdscr.refresh()
    return code


def preview_lines():
    rows, _ = autoname.collect()
    names = autoname.current_names(rows)
    preview = autoname.sidebar_preview_data(names, rows)
    lines = [f"Current Sidebar · {len(rows)} workspaces", ""]
    agents = {}
    for item in preview["agents"]:
        if item.get("separator"):
            continue
        agents.setdefault(item["workspace_id"], []).append(item)
    for item in preview["spaces"]:
        if item.get("separator"):
            lines.append("")
            continue
        icon = autoname.STATUS_ICONS.get(item["status"], "·")
        lines.append(f"{icon} {item['label']}")
        if item["context"]:
            lines.append(f"  {item['context']}")
        for pane in agents.get(item["workspace_id"], []):
            pane_icon = autoname.STATUS_ICONS.get(pane["status"], "·")
            lines.append(f"    {pane_icon} {pane['label']}")
    return lines


def show_scroll_view(stdscr, title, lines):
    offset = 0
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        add(stdscr, 1, 2, title, curses.A_BOLD)
        add(stdscr, 2, 2, f"{len(lines)} rows · read-only", curses.A_DIM)
        viewport = max(1, height - 6)
        maximum = max(0, len(lines) - viewport)
        offset = min(offset, maximum)
        for index, line in enumerate(lines[offset:offset + viewport]):
            style = curses.A_BOLD if line and not line.startswith(" ") else 0
            add(stdscr, 4 + index, 2, line, style)
        footer = f"↑↓ Scroll  PgUp/PgDn  Esc Back     {offset + 1}-{min(len(lines), offset + viewport)} of {len(lines)}"
        add(stdscr, height - 1, 2, footer, curses.A_DIM)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (27, ord("q"), ord("Q"), curses.KEY_LEFT):
            return
        if key in (curses.KEY_UP, ord("k")):
            offset = max(0, offset - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            offset = min(maximum, offset + 1)
        elif key == curses.KEY_PPAGE:
            offset = max(0, offset - viewport)
        elif key == curses.KEY_NPAGE:
            offset = min(maximum, offset + viewport)


def draw(stdscr, settings, providers, selected, message):
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    add(stdscr, 1, 2, "Herdr Autoname", curses.A_BOLD)
    add(stdscr, 2, 2, "Automatically rename workspaces from conversation activity.", curses.A_DIM)
    if width < 50 or height < 18:
        add(stdscr, 5, 2, "Popup is too small. Use at least 50×18.", curses.A_BOLD)
        stdscr.refresh()
        return
    provider_label = autoname.PROVIDER_LABELS.get(settings["provider"], settings["provider"])
    model_label = settings["model"] or "Provider default"
    rows = [
        (5, "Provider", f"< {provider_label} >"),
        (6, "Model", model_label),
        (10, "Rename every", f"< {settings['interval']} > conversations"),
        (9, "Auto rename", "[x] Enabled" if settings["enabled"] else "[ ] Disabled"),
        (14, "Preview Sidebar", "›"),
        (15, "Rename now", "›"),
        (18, "Save changes", ""),
        (19, "Cancel", ""),
    ]
    add(stdscr, 4, 3, "MODEL", curses.A_DIM | curses.A_BOLD)
    add(stdscr, 8, 3, "AUTOMATION", curses.A_DIM | curses.A_BOLD)
    add(stdscr, 13, 3, "ACTIONS", curses.A_DIM | curses.A_BOLD)
    for index, (row, label, value) in enumerate(rows):
        marker = ">" if index == selected else " "
        style = curses.A_BOLD if index == selected else 0
        label_cell = fit(label, 18)
        label_cell += " " * max(0, 18 - autoname.display_width(label_cell))
        add(stdscr, row, 3, f"{marker} {label_cell}{value}", style)
    add(stdscr, height - 2, 2, fit(message, max(1, width - 4)), curses.A_DIM)
    add(stdscr, height - 1, 2, "↑↓ Move  ←→ Change  Enter Select  S Save  Esc Close", curses.A_DIM)
    stdscr.refresh()


def ui(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    settings = load_settings()
    providers = available_providers()
    if settings["provider"] not in providers:
        providers.append(settings["provider"])
    selected = 0
    message = "These settings affect the Herdr plugin only."
    while True:
        draw(stdscr, settings, providers, selected, message)
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return 0
        if key in (ord("s"), ord("S")):
            save_settings(settings)
            return 0
        if key in (curses.KEY_UP, ord("k")):
            selected = (selected - 1) % len(MENU)
            continue
        if key in (curses.KEY_DOWN, ord("j"), 9):
            selected = (selected + 1) % len(MENU)
            continue
        direction = -1 if key in (curses.KEY_LEFT, ord("h"), ord("-")) else 1
        activate = key in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("h"), ord("l"), ord("-"), ord("+"), 10, 13, 32)
        if not activate:
            continue
        item = MENU[selected]
        if item == "provider":
            index = providers.index(settings["provider"])
            settings["provider"] = providers[(index + direction) % len(providers)]
        elif item == "model":
            settings["model"] = edit_text(stdscr, 20, settings["model"], "Model (blank = default)")
        elif item == "interval":
            if key in (10, 13):
                raw = edit_text(stdscr, 20, str(settings["interval"]), "Trigger every")
                if raw.isdigit() and int(raw) > 0:
                    settings["interval"] = int(raw)
            else:
                settings["interval"] = max(1, settings["interval"] + direction)
        elif item == "enabled":
            settings["enabled"] = not settings["enabled"]
        elif item == "preview":
            draw(stdscr, settings, providers, selected, "Loading current Herdr Sidebar…")
            try:
                show_scroll_view(stdscr, "Sidebar Preview", preview_lines())
                message = "Preview loaded without calling a model or changing labels."
            except RuntimeError as exc:
                message = f"Preview failed: {exc}"
        elif item == "rename":
            draw(stdscr, settings, providers, selected, "Run the model now? Press Y to confirm.")
            if stdscr.getch() in (ord("y"), ord("Y")):
                save_settings(settings)
                message = "Rename completed." if run_command(stdscr, []) == 0 else "Rename failed. Check the output."
        elif item == "save":
            save_settings(settings)
            return 0
        elif item == "cancel":
            return 0


def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Settings must run inside a Herdr Popup TTY")
    return curses.wrapper(ui)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
