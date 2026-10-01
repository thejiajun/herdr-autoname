#!/usr/bin/env python3
"""Name a Herdr pane after its first completed turn, then every N turns."""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile

import rename_workspaces as autoname


DEFAULT_TRIGGER_EVERY = 3
TRIGGER_STATUSES = {"blocked", "done", "idle"}
WAITING_LABEL = "💤 等待任务"


def read_json_env(name):
    try:
        value = json.loads(os.environ.get(name, "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def event_value(event, name):
    data = event.get("data")
    if isinstance(data, dict) and data.get(name) is not None:
        return data[name]
    return event.get(name)


def trigger_every():
    raw = autoname.env_value("HERDR_AUTONAME_TRIGGER_EVERY", autoname.AUTONAME_ENV)
    if not raw:
        return DEFAULT_TRIGGER_EVERY
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("HERDR_AUTONAME_TRIGGER_EVERY must be a positive integer") from exc
    if value < 1:
        raise RuntimeError("HERDR_AUTONAME_TRIGGER_EVERY must be greater than zero")
    return value


def enabled():
    raw = autoname.env_value("HERDR_AUTONAME_ENABLED", autoname.AUTONAME_ENV)
    return raw.lower() not in {"0", "false", "no", "off"}


def state_paths():
    root = os.path.join(autoname.STATE_DIR, "plugin")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "turns.json"), os.path.join(root, "turns.lock")


def load_state(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return {"panes": {}}
    return value if isinstance(value, dict) and isinstance(value.get("panes"), dict) else {"panes": {}}


def save_state(path, state):
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=os.path.dirname(path), delete=False
    ) as handle:
        temporary = handle.name
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def user_turn_fingerprints(messages):
    fingerprints = []
    for role, text in messages:
        if role != "user" or not text or autoname.is_injected_turn(text):
            continue
        normalized = " ".join(text.split())
        if normalized:
            fingerprints.append(hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20])
    return fingerprints


def new_turn_count(fingerprints, previous):
    if not fingerprints:
        return 0
    if not previous:
        return 0
    try:
        index = len(fingerprints) - 1 - fingerprints[::-1].index(previous)
    except ValueError:
        return 1
    return max(0, len(fingerprints) - index - 1)


def should_name(entry, threshold):
    """A new session is named after its first turn, then refreshed every N turns."""
    pending = int(entry.get("pending_turns", 0))
    return pending > 0 and (not entry.get("named") or pending >= threshold)


def find(items, key, value):
    return next((item for item in items if item.get(key) == value), None)


def needs_sidebar_label(workspace):
    """A workspace without our label token shows a blank title in the Sidebar."""
    return bool(workspace) and not (workspace.get("tokens") or {}).get("workspace_label")


def needs_agent_label(pane):
    tokens = (pane or {}).get("tokens") or {}
    return bool(pane) and not autoname.active_agent_state(tokens)


def invoke_autoname(*arguments):
    command = [sys.executable, os.path.join(os.path.dirname(__file__), "rename_workspaces.py")]
    return subprocess.run([*command, *arguments], check=False).returncode


def name_untracked_pane(pane, workspace_id):
    """Give panes without a native session a label when Herdr leaves it blank."""
    if pane.get("label") and pane["label"] not in (WAITING_LABEL, " 等待任务"):
        return 0
    title = (pane.get("terminal_title_stripped") or "").strip()
    project = os.path.basename((pane.get("foreground_cwd") or pane.get("cwd") or "").rstrip("/"))
    if not title or title == project:
        # There is no task evidence in a bare agent prompt.
        if pane.get("label") == WAITING_LABEL:
            return 0
        code, _, stderr = autoname.run(["herdr", "pane", "rename", pane["pane_id"], WAITING_LABEL])
        if code:
            print(stderr or "Could not label idle pane", file=sys.stderr)
        else:
            invoke_autoname("configure")
        return code
    return invoke_autoname("--workspace", workspace_id)


def main():
    autoname.migrate_plugin_settings()
    event = read_json_env("HERDR_PLUGIN_EVENT_JSON")
    status = str(event_value(event, "agent_status") or "").lower()
    pane_id = str(event_value(event, "pane_id") or os.environ.get("HERDR_PANE_ID", ""))
    workspace_id = str(
        event_value(event, "workspace_id") or os.environ.get("HERDR_WORKSPACE_ID", "")
    )
    if not pane_id or not workspace_id:
        return 0
    state = autoname.snapshot()
    if (needs_sidebar_label(find(state.get("workspaces", []), "workspace_id", workspace_id))
            or needs_agent_label(find(state.get("panes", []), "pane_id", pane_id))):
        # Relabel and regroup the Sidebar without a model call, so a new
        # workspace is visible and placed with its project before its first turn.
        invoke_autoname("configure")
        state = autoname.snapshot()
    pane = find(state.get("panes", []), "pane_id", pane_id)
    if pane and status:
        autoname.sync_agent_state(pane_id, status, pane.get("tokens") or {})
    if not enabled():
        return 0
    if status not in TRIGGER_STATUSES:
        return 0
    if not pane:
        return 0
    messages = autoname.native_session_messages(pane)
    if messages is None:
        _, lock_path = state_paths()
        with open(lock_path, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = find(autoname.snapshot().get("panes", []), "pane_id", pane_id)
            return name_untracked_pane(current, workspace_id) if current else 0
    fingerprints = user_turn_fingerprints(messages)
    if not fingerprints:
        return 0

    state_path, lock_path = state_paths()
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state(state_path)
        panes = state["panes"]
        session = pane.get("agent_session") or {}
        session_id = str(session.get("value") or "")
        previous = panes.get(pane_id, {})
        threshold = trigger_every()
        if previous.get("session_id") != session_id:
            previous = {
                "session_id": session_id,
                "named": False,
                "last_user_turn": fingerprints[-1],
                # This hook runs at the end of a real turn. Count that turn,
                # while using only its latest fingerprint as the baseline for
                # any older history that predates plugin observation.
                "pending_turns": 1,
            }
            pending = 1
        else:
            added = new_turn_count(fingerprints, previous.get("last_user_turn"))
            pending = int(previous.get("pending_turns", 0)) + added
            previous.update({
                "session_id": session_id,
                "last_user_turn": fingerprints[-1],
                "pending_turns": pending,
            })
        panes[pane_id] = previous
        if not should_name(previous, threshold):
            save_state(state_path, state)
            return 0

        # Keep the lock through naming so simultaneous pane events cannot spend
        # twice or overwrite one another's counters.
        code = invoke_autoname("--workspace", workspace_id)
        if code == 0:
            first = not previous.get("named")
            previous["pending_turns"] = 0 if first else pending % threshold
            previous["named"] = True
        save_state(state_path, state)
        return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
