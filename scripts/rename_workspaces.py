#!/usr/bin/env python3
"""Name every current Herdr workspace with fast parallel LLM requests."""

import argparse
import glob
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor


DIRECT_BASE = "https://api.deepseek.com"
DIRECT_MODEL = "deepseek-chat"
PIKA_BASE = "https://api.dev.pika.art"
PIKA_MODEL = "google/gemini-3.7-flash"
AUTONAME_ENV = os.path.expanduser("~/.config/herdr/autoname.env")
MAX_PANE_CHARS = 1_200
PANE_MAX_DISPLAY_WIDTH = 24
DEEPSEEK_WORKSPACES_PER_REQUEST = 3
DEFAULT_WORKSPACES_PER_REQUEST = 20
AGENT_LABELS = {
    "opencode": "◈ opencode",
    "claude": "✦ claude",
    "codex": "⌘ codex",
    "dsh-tui": "◆ dsh-tui",
    "shell": "› shell",
}
STATUS_ICONS = {
    "working": "▶",
    "idle": "○",
    "waiting": "◌",
    "error": "✕",
    "completed": "✓",
    "unknown": "·",
}
PROVIDERS = ("auto", "pika", "deepseek", "codex", "claude")
PROVIDER_LABELS = {
    "auto": "自动选择",
    "pika": "Pika Chat API",
    "deepseek": "DeepSeek API",
    "codex": "Codex CLI",
    "claude": "Claude CLI",
}
HERDR_CONFIG = os.path.expanduser("~/.config/herdr/config.toml")
SIDEBAR_ROWS = {
    "ui.sidebar.spaces": (
        'rows = [["state_icon", { token = "$workspace_label", bold = true }], '
        '["$context"]]'
    ),
    "ui.sidebar.agents": (
        'rows = [["state_icon", { token = "pane", bold = true }], '
        '["agent", { token = "$workspace_plain", bold = false }]]'
    ),
}

SYSTEM_PROMPT = """给 Herdr 工作空间命名。输入：{"w":[{"n":"当前workspace","t":["当前tab"],"p":[["agent","终端标题","最近对话"]]}]}。

只返回：{"n":[["📊 中文主题",["📊 短标签"],["📊 把对象改好"]]]}。n 中每项必须是长度为 3 的数组，禁止改成对象。输入输出的 workspace、tab、pane 必须保持相同顺序和数量。

规则：
- 必须覆盖输入中的每一项，不得增删或调换顺序。
- Workspace：一个相关 emoji + 当前一段工作的稳定主题，中文优先，不加序号。
- Tab：使用相同 emoji + 更短的主题分类。
- Pane 只描述最近对话中的下一步动作，不概括整段会话。使用「相同 emoji + 把 + 对象 + 动作」；emoji 后最多 9 个汉字，一个英文词算两个汉字。
- 极力压缩但保留对象和动作，删除可推断的过程词。推荐：「⚡ 把轮询并入Store」「🚚 把Tunnel部署Hornet」「🏷️ 把Pane改按近两轮」「📊 把账号全显Tracker」。
- 不要写成长句，不要使用「处理问题」「继续工作」「代码优化」「配置到环境变量」「基于最近两轮对话」等空泛、冗长或角色式名称。
- 同一 workspace 的三层使用相同 emoji。
- Workspace/Tab 可在旧名称准确时保留；Pane 必须按最近两轮的当前动作重新判断。
- 只输出 JSON，不要解释。"""


def run(args, timeout=120):
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def run_json(args, timeout=120):
    code, stdout, stderr = run(args, timeout=timeout)
    if code != 0:
        raise RuntimeError(stderr or f"{' '.join(args)} exited {code}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{' '.join(args)} returned invalid JSON") from exc


def result_of(payload, expected_type):
    if payload.get("id") and isinstance(payload.get("result"), dict):
        result = payload["result"]
    else:
        result = payload
    if expected_type and result.get("type") != expected_type:
        raise RuntimeError(f"Expected {expected_type}, got {result.get('type')!r}")
    return result


def snapshot():
    payload = result_of(run_json(["herdr", "api", "snapshot"]), "session_snapshot")
    return payload["snapshot"]


def last_two_rounds(messages):
    messages = [(role, text.strip()) for role, text in messages if text and text.strip()]
    user_indexes = [index for index, (role, _) in enumerate(messages) if role == "user"]
    if not user_indexes:
        return ""
    selected = [messages[index] for index in user_indexes[-2:]]
    latest_user = user_indexes[-1]
    latest_assistant = next(
        (message for message in reversed(messages[latest_user + 1:]) if message[0] == "assistant"),
        None,
    )
    if latest_assistant:
        selected.append(latest_assistant)
    limits = {"user": 240, "assistant": 170}
    rendered = "\n".join(
        f"{role.upper()}: {sanitize_content(text)[:limits[role]]}"
        for role, text in selected
    )
    return rendered[-650:]


def last_user_input(messages):
    for role, text in reversed(messages):
        if role == "user" and text and text.strip():
            return sanitize_content(text).replace("\n", " ")[:650]
    return ""


def opencode_messages(session_id):
    path = os.path.expanduser("~/.local/share/opencode/opencode.db")
    if not os.path.isfile(path):
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        rows = connection.execute(
            """
            SELECT m.data, p.data
            FROM message m
            JOIN part p ON p.message_id = m.id
            WHERE m.session_id = ?
              AND json_extract(m.data, '$.role') IN ('user', 'assistant')
              AND json_extract(p.data, '$.type') = 'text'
            ORDER BY m.time_created DESC, p.time_created DESC
            LIMIT 100
            """,
            (session_id,),
        ).fetchall()
    finally:
        connection.close()
    messages = []
    for message_data, part_data in reversed(rows):
        try:
            role = json.loads(message_data).get("role")
            text = json.loads(part_data).get("text", "")
        except (json.JSONDecodeError, TypeError):
            continue
        if role in ("user", "assistant") and isinstance(text, str):
            messages.append((role, text))
    return messages


def jsonl_messages(pattern, parser):
    paths = glob.glob(os.path.expanduser(pattern), recursive=True)
    if not paths:
        return []
    recent = deque(maxlen=200)
    try:
        with open(max(paths, key=os.path.getmtime), encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = parser(json.loads(line))
                except (json.JSONDecodeError, TypeError, AttributeError):
                    continue
                if item:
                    recent.append(item)
    except OSError:
        return []
    return list(recent)


def claude_message(row):
    message = row.get("message") or {}
    role = message.get("role")
    if role not in ("user", "assistant"):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return role, content
    if not isinstance(content, list):
        return None
    texts = [part.get("text", "") for part in content if part.get("type") == "text"]
    text = "\n".join(text for text in texts if isinstance(text, str) and text.strip())
    return (role, text) if text else None


def codex_message(row):
    if row.get("type") != "response_item":
        return None
    payload = row.get("payload") or {}
    role = payload.get("role")
    if payload.get("type") != "message" or role not in ("user", "assistant"):
        return None
    content = payload.get("content") or []
    allowed = "input_text" if role == "user" else "output_text"
    texts = [part.get("text", "") for part in content if part.get("type") == allowed]
    text = "\n".join(text for text in texts if isinstance(text, str) and text.strip())
    return (role, text) if text else None


def native_session_content(pane):
    session = pane.get("agent_session") or {}
    session_id = session.get("value")
    agent = session.get("agent") or pane.get("agent")
    if not session_id:
        return None
    if agent == "opencode":
        messages = opencode_messages(session_id)
    elif agent == "claude":
        messages = jsonl_messages(
            f"~/.claude/projects/**/*{glob.escape(session_id)}*.jsonl", claude_message
        )
    elif agent == "codex":
        messages = jsonl_messages(
            f"~/.codex/sessions/**/*{glob.escape(session_id)}*.jsonl", codex_message
        )
    else:
        return None
    return {
        "recent_content": last_two_rounds(messages),
        "last_user_input": last_user_input(messages),
    }


def read_pane(pane):
    native = native_session_content(pane)
    if native:
        return native
    pane_id = pane["pane_id"]
    command = "agent" if pane.get("agent") else "pane"
    attempts = [("recent-unwrapped", "60"), ("visible", "30")]
    for source, lines in attempts:
        code, stdout, _ = run(
            ["herdr", command, "read", pane_id, "--source", source, "--lines", lines],
            timeout=30,
        )
        if code == 0 and stdout:
            content = sanitize_content(stdout)[-MAX_PANE_CHARS:]
            matches = re.findall(
                r"(?:^|\n)USER:\s*(.*?)(?=\n(?:USER|ASSISTANT):|\Z)", content, re.S
            )
            return {
                "recent_content": content,
                "last_user_input": matches[-1].replace("\n", " ")[:650] if matches else "",
            }
    return {"recent_content": "(no readable recent content)", "last_user_input": ""}


def sanitize_content(text):
    """Keep conversational evidence while removing WAF-sensitive command payloads."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    kept = []
    command = re.compile(
        r"^(?:[$#>%❯➜]|ssh\b|rsync\b|curl\b|launchctl\b|npm\b|npx\b|"
        r"python\d*\b|git\b|sed\b|grep\b|node\b|export\b|source\b)", re.I
    )
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or command.match(line):
            continue
        # Pure code/command output is noisy for naming and frequently trips an
        # API edge WAF. English prose remains available through terminal_title.
        has_cjk = bool(re.search(r"[\u3400-\u9fff]", line))
        if not has_cjk and len(re.findall(r"[/\\{}();=$<>]", line)) >= 3:
            continue
        line = re.sub(r"https?://\S+", "<url>", line)
        line = re.sub(r"\b[A-Z][A-Z0-9_]{4,}\b", "<ENV>", line)
        line = re.sub(r"(?:^|\s)/(?:[^\s]+/)+[^\s]*", " <path>", line)
        kept.append(line[:500])
    return "\n".join(kept)


def git_summary(path):
    if not path:
        return "—"
    code, stdout, _ = run(
        ["git", "-C", path, "status", "--short", "--branch", "--untracked-files=normal"],
        timeout=15,
    )
    if code != 0 or not stdout:
        return "—"
    lines = stdout.splitlines()
    branch_line = lines[0].removeprefix("## ")
    branch = branch_line.split("...", 1)[0].split(" ", 1)[0]
    if branch == "HEAD":
        branch = "detached"
    parts = [branch]
    ahead = re.search(r"ahead (\d+)", branch_line)
    behind = re.search(r"behind (\d+)", branch_line)
    if ahead:
        parts.append(f"↑{ahead.group(1)}")
    if behind:
        parts.append(f"↓{behind.group(1)}")
    parts.append(f"✱{len(lines) - 1}" if len(lines) > 1 else "✓")
    return " ".join(parts)


def compact_path(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def remote_host(pane):
    title = pane.get("terminal_title_stripped", "")
    match = re.search(r"(?:^|\s)[^@\s]+@([^:\s]+):(?:~|/)", title)
    if not match:
        match = re.match(r"([A-Za-z0-9._-]+\.local):\s", title)
    if not match:
        return ""
    host = match.group(1).lower().removesuffix(".local")
    local_hosts = {
        socket.gethostname().lower().removesuffix(".local"),
        socket.getfqdn().lower().removesuffix(".local"),
    }
    return "" if host in local_hosts else host


def workspace_location(workspace_id, panes):
    host = ""
    for pane in panes:
        host = remote_host(pane)
        if host:
            break
    if host:
        return {
            "path": f"ssh://{host}",
            "project": f"SSH · {host}",
            "group_key": f"remote:{host.lower()}",
            "remote_host": host,
        }
    path = next((
        pane.get("foreground_cwd") or pane.get("cwd", "")
        for pane in panes
        if pane.get("foreground_cwd") or pane.get("cwd")
    ), "")
    home = os.path.expanduser("~")
    project = "" if not path or os.path.realpath(path) == os.path.realpath(home) else os.path.basename(path)
    group_key = f"path:{os.path.realpath(path)}" if project else f"workspace:{workspace_id}"
    return {
        "path": path,
        "project": project,
        "group_key": group_key,
        "remote_host": "",
    }


def collect(only_workspaces=None):
    state = snapshot()
    wanted = set(only_workspaces or [])
    all_workspace_ids = {item["workspace_id"] for item in state.get("workspaces", [])}
    workspaces = [
        item for item in state.get("workspaces", [])
        if not wanted or item["workspace_id"] in wanted
    ]
    found = {item["workspace_id"] for item in workspaces}
    missing = wanted - found
    if missing:
        raise RuntimeError("Unknown workspace: " + ", ".join(sorted(missing)))

    workspace_ids = {item["workspace_id"] for item in workspaces}
    tabs = [item for item in state.get("tabs", []) if item["workspace_id"] in workspace_ids]
    panes = [item for item in state.get("panes", []) if item["workspace_id"] in workspace_ids]
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(panes)))) as pool:
        contents = dict(zip((p["pane_id"] for p in panes), pool.map(read_pane, panes)))
    paths = {
        item.get("foreground_cwd") or item.get("cwd", "")
        for item in panes
        if not remote_host(item)
    }
    paths.discard("")
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(paths)))) as pool:
        git_states = dict(zip(paths, pool.map(git_summary, paths)))

    rows = []
    for workspace in workspaces:
        workspace_id = workspace["workspace_id"]
        row_tabs = [item for item in tabs if item["workspace_id"] == workspace_id]
        row_panes = [item for item in panes if item["workspace_id"] == workspace_id]
        location = workspace_location(workspace_id, row_panes)
        primary_path = location["path"]
        rows.append({
            "workspace_id": workspace_id,
            "current_workspace": workspace.get("label", ""),
            "path": primary_path,
            "git_status": "—" if location["remote_host"] else git_states.get(primary_path, "—"),
            "project": location["project"],
            "group_key": location["group_key"],
            "remote_host": location["remote_host"],
            "tabs": [
                {"tab_id": item["tab_id"], "current_label": item.get("label", "")}
                for item in row_tabs
            ],
            "panes": [
                {
                    "pane_id": item["pane_id"],
                    "current_label": item.get("label", ""),
                    "agent": item.get("agent", "shell"),
                    "project": location["project"],
                    "status": item.get("agent_status", "unknown"),
                    "cwd": item.get("foreground_cwd") or item.get("cwd", ""),
                    "terminal_title": item.get("terminal_title_stripped", ""),
                    "recent_content": contents[item["pane_id"]]["recent_content"],
                    "last_user_input": contents[item["pane_id"]]["last_user_input"],
                }
                for item in row_panes
            ],
        })
    return rows, all_workspace_ids


def env_value(name, env_file=None):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    paths = [env_file] if env_file else [
        os.path.join(os.getcwd(), ".env"),
        os.path.expanduser("~/.config/herdr/autoname.env"),
    ]
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.lstrip().startswith("#") or "=" not in line:
                    continue
                key, candidate = line.split("=", 1)
                if key.strip() == name:
                    return candidate.strip().strip("'\"")
    return ""


def set_env_value(path, name, value):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        mode = os.stat(path).st_mode
    except FileNotFoundError:
        lines = []
        mode = 0o600
    except OSError as exc:
        raise RuntimeError(f"Cannot read {path}: {exc}") from exc

    prefix = f"{name}="
    updated = [line for line in lines if not line.strip().startswith(prefix)]
    if value:
        updated.append(f"{name}={value}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=os.path.dirname(path), delete=False
        ) as handle:
            temporary_path = handle.name
            handle.write("\n".join(updated) + ("\n" if updated else ""))
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise RuntimeError(f"Cannot update {path}: {exc}") from exc


def saved_model():
    return env_value("HERDR_AUTONAME_MODEL", AUTONAME_ENV)


def saved_provider():
    return env_value("HERDR_AUTONAME_PROVIDER", AUTONAME_ENV) or "auto"


def model_command(value, as_json=False):
    if value == "reset":
        set_env_value(AUTONAME_ENV, "HERDR_AUTONAME_MODEL", "")
        model = PIKA_MODEL
        source = "built-in default"
    elif value:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value):
            raise RuntimeError(f"Invalid model ID: {value}")
        set_env_value(AUTONAME_ENV, "HERDR_AUTONAME_MODEL", value)
        model = value
        source = AUTONAME_ENV
    else:
        model = saved_model() or PIKA_MODEL
        source = AUTONAME_ENV if saved_model() else "built-in default"
    result = {"model": model, "source": source}
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Model: {model}")
        print(f"Source: {source}")
    return 0


def local_cli_authenticated(provider):
    executable = shutil.which(provider)
    if not executable:
        return False
    if provider == "codex":
        code, stdout, stderr = run([executable, "login", "status"], timeout=10)
        return code == 0 and "logged in" in f"{stdout}\n{stderr}".lower()
    code, stdout, _ = run([executable, "auth", "status", "--json"], timeout=10)
    if code != 0:
        return False
    try:
        return bool(json.loads(stdout).get("loggedIn"))
    except json.JSONDecodeError:
        return False


def provider_availability(args):
    return {
        "pika": bool(env_value("PIKA_CHAT_API_KEY", args.env_file)),
        "deepseek": bool(env_value("DEEPSEEK_API_KEY", args.env_file)),
        "codex": local_cli_authenticated("codex"),
        "claude": local_cli_authenticated("claude"),
    }


def resolve_provider(args, available=None):
    requested = args.provider or saved_provider()
    if requested != "auto":
        return requested
    available = available or provider_availability(args)
    return next((name for name in ("pika", "deepseek", "codex", "claude") if available[name]), None)


def provider_command(value, args):
    available = provider_availability(args)
    if value == "reset":
        set_env_value(AUTONAME_ENV, "HERDR_AUTONAME_PROVIDER", "")
        selected = "auto"
    elif value:
        if value not in PROVIDERS:
            raise RuntimeError("Provider 必须是：" + ", ".join(PROVIDERS))
        if value != "auto" and not available[value]:
            raise RuntimeError(f"Provider {value} 当前不可用或尚未登录")
        set_env_value(AUTONAME_ENV, "HERDR_AUTONAME_PROVIDER", value)
        selected = value
    else:
        selected = saved_provider()
    resolved = resolve_provider(args, available) if selected == "auto" else selected
    if not value and sys.stdin.isatty() and sys.stdout.isatty():
        choices = ["auto", *(name for name in PROVIDERS[1:] if available[name])]
        print("可选 Provider：")
        for index, name in enumerate(choices, 1):
            suffix = "（当前）" if name == selected else ""
            print(f"  {index}. {PROVIDER_LABELS[name]} {suffix}".rstrip())
        answer = input(f"选择 [当前：{PROVIDER_LABELS.get(selected, selected)}]：").strip()
        if answer:
            if not answer.isdigit() or not 1 <= int(answer) <= len(choices):
                raise RuntimeError("无效选择")
            selected = choices[int(answer) - 1]
            set_env_value(AUTONAME_ENV, "HERDR_AUTONAME_PROVIDER", selected)
            resolved = resolve_provider(args, available) if selected == "auto" else selected
    result = {
        "provider": selected,
        "resolved": resolved,
        "available": available,
        "source": AUTONAME_ENV if env_value("HERDR_AUTONAME_PROVIDER", AUTONAME_ENV) else "built-in default",
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Provider: {selected}" + (f" → {resolved}" if selected == "auto" and resolved else ""))
        print("可用：" + "  ".join(
            f"{'✓' if enabled else '✕'} {name}" for name, enabled in available.items()
        ))
        print(f"Source: {result['source']}")
    return 0


def initialize_provider(args):
    if args.provider or env_value("HERDR_AUTONAME_PROVIDER", AUTONAME_ENV):
        return None, provider_availability(args)
    available = provider_availability(args)
    selected = resolve_provider(args, available)
    if not selected:
        return None, available
    set_env_value(AUTONAME_ENV, "HERDR_AUTONAME_PROVIDER", selected)
    return selected, available


def print_provider_detection(config, available, initialized=None):
    local = "  ".join(
        f"{'✓' if available[name] else '✕'} {PROVIDER_LABELS[name]}"
        for name in ("codex", "claude")
    )
    if initialized:
        detected = ", ".join(
            PROVIDER_LABELS[name] for name in PROVIDERS[1:] if available[name]
        )
        print(
            f"首次运行：检测到 {detected}；已自动选择 {PROVIDER_LABELS[initialized]}。",
            file=sys.stderr,
        )
    print(f"Provider：{config['provider']}  |  本地无头：{local}", file=sys.stderr)


def provider_config(args):
    provider = resolve_provider(args)
    if not provider:
        raise RuntimeError(
            "没有可用 Provider：请配置 PIKA_CHAT_API_KEY / DEEPSEEK_API_KEY，"
            "或安装并登录 codex / claude CLI"
        )
    requested_model = args.model or saved_model()
    if provider == "deepseek":
        direct_key = env_value("DEEPSEEK_API_KEY", args.env_file)
        if not direct_key:
            raise RuntimeError("Provider deepseek 需要 DEEPSEEK_API_KEY")
        return {
            "kind": "http",
            "base": args.base_url or DIRECT_BASE,
            "model": requested_model if requested_model in ("deepseek-chat", "deepseek-reasoner") else DIRECT_MODEL,
            "headers": {"Authorization": f"Bearer {direct_key}"},
            "provider": "DeepSeek API",
        }
    if provider == "pika":
        pika_key = env_value("PIKA_CHAT_API_KEY", args.env_file)
        if not pika_key:
            raise RuntimeError("Provider pika 需要 PIKA_CHAT_API_KEY")
        return {
            "kind": "http",
            "base": args.base_url or os.environ.get("PIKA_API_BASE", PIKA_BASE),
            "model": requested_model or PIKA_MODEL,
            "headers": {"X-API-Key": pika_key},
            "provider": "Pika Chat API",
        }
    if provider == "codex":
        if not shutil.which("codex"):
            raise RuntimeError("找不到 codex CLI，请先安装并登录")
        return {
            "kind": "codex",
            "model": args.model or "CLI default",
            "provider": "Codex CLI",
        }
    if not shutil.which("claude"):
        raise RuntimeError("找不到 claude CLI，请先安装并登录")
    return {
        "kind": "claude",
        "model": args.model or "haiku",
        "provider": "Claude CLI",
    }


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise RuntimeError("LLM response did not contain JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise RuntimeError("LLM returned malformed JSON") from exc


def naming_payload(rows):
    return {
        "w": [
            {
                "n": row["current_workspace"],
                "t": [tab["current_label"] for tab in row["tabs"]],
                "p": [
                    [pane["agent"], pane["terminal_title"][:100], pane["recent_content"][:650]]
                    for pane in row["panes"]
                ],
            }
            for row in rows
        ]
    }


def request_http_batch(rows, config):
    compact_input = json.dumps(
        naming_payload(rows), ensure_ascii=False, separators=(",", ":")
    )
    body = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": compact_input},
        ],
        "temperature": 0.1,
        # Hidden reasoning dominates output size. This budget covers it plus
        # compact names without encouraging a long chain of thought.
        "max_tokens": min(12_000, max(7_000, len(rows) * 350)),
        "response_format": {"type": "json_object"},
    }
    if config["provider"] == "Pika Chat API" and config["model"].startswith(
        ("deepseek/", "google/gemini-3.6", "google/gemini-3.7")
    ):
        body["reasoning_effort"] = "low"
    request_data = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    # Pika's Cloudflare edge rejects urllib's default client fingerprint. Keep
    # credentials out of argv/process listings by passing headers via a 0600
    # curl config that is deleted immediately after the one request.
    headers = {"Content-Type": "application/json", **config["headers"]}
    config_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            config_path = handle.name
            handle.write("silent\nshow-error\nmax-time = 240\n")
            for key, value in headers.items():
                escaped = f"{key}: {value}".replace("\\", "\\\\").replace('"', '\\"')
                handle.write(f'header = "{escaped}"\n')
        os.chmod(config_path, 0o600)
        proc = subprocess.run(
            [
                "curl", "--config", config_path, "--request", "POST",
                "--data-binary", "@-", "--write-out", "\n%{http_code}",
                config["base"].rstrip("/") + "/v1/chat/completions",
            ],
            input=request_data,
            capture_output=True,
            text=True,
            timeout=250,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc
    finally:
        if config_path:
            try:
                os.unlink(config_path)
            except OSError:
                pass
    response_body, separator, status = proc.stdout.rpartition("\n")
    if proc.returncode != 0:
        raise RuntimeError(f"LLM request failed: {proc.stderr.strip()[:500]}")
    if not separator or not status.isdigit():
        raise RuntimeError("LLM response had no HTTP status")
    if not 200 <= int(status) < 300:
        raise RuntimeError(f"LLM HTTP {status}: {response_body[:500]}")
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM returned a non-JSON HTTP response") from exc
    try:
        choice = payload["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LLM response had no message content") from exc
    if not content.strip():
        reasoning = message.get("reasoning_content") or ""
        usage = payload.get("usage") or {}
        raise RuntimeError(
            "LLM exhausted its output budget before JSON "
            f"(finish={choice.get('finish_reason')}, reasoning_chars={len(reasoning)}, "
            f"completion_tokens={usage.get('completion_tokens', 'unknown')})"
        )
    usage = payload.get("usage", {})
    usage["request_bytes"] = len(request_data.encode("utf-8"))
    usage["response_bytes"] = len(response_body.encode("utf-8"))
    return extract_json(content), usage


def naming_schema():
    return {
        "type": "object",
        "properties": {
            "n": {
                "type": "array",
                "items": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                },
            }
        },
        "required": ["n"],
        "additionalProperties": False,
    }


def request_cli_batch(rows, config):
    compact_input = json.dumps(
        naming_payload(rows), ensure_ascii=False, separators=(",", ":")
    )
    prompt = SYSTEM_PROMPT + "\n\n输入：\n" + compact_input
    if config["kind"] == "codex":
        command = [
            "codex", "exec", "--skip-git-repo-check", "--ephemeral",
            "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only",
            "--color", "never", "-c", 'model_reasoning_effort="low"',
        ]
        if config["model"] != "CLI default":
            command.extend(["--model", config["model"]])
        command.append("-")
    else:
        command = [
            "claude", "-p", "--safe-mode", "--model", config["model"],
            "--effort", "low", "--output-format", "json",
            "--no-session-persistence", "--permission-mode", "dontAsk",
            "--tools", "", "--json-schema",
            json.dumps(naming_schema(), separators=(",", ":")),
        ]
    try:
        proc = subprocess.run(
            command, input=prompt, capture_output=True, text=True,
            timeout=250, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{config['provider']} 请求失败：{exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"{config['provider']} 请求失败：{(proc.stderr or proc.stdout).strip()[:500]}"
        )
    usage = {
        "request_bytes": len(prompt.encode("utf-8")),
        "response_bytes": len(proc.stdout.encode("utf-8")),
    }
    if config["kind"] == "codex":
        token_match = re.search(r"tokens used\s*\n([\d,]+)", proc.stderr, re.I)
        if token_match:
            usage["total_tokens"] = int(token_match.group(1).replace(",", ""))
        return extract_json(proc.stdout), usage

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Claude CLI 返回了无效 JSON") from exc
    claude_usage = payload.get("usage") or {}
    usage.update(claude_usage)
    usage["total_tokens"] = sum(
        claude_usage.get(key, 0)
        for key in (
            "input_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens", "output_tokens",
        )
    )
    usage["cost"] = payload.get("total_cost_usd", 0)
    structured = payload.get("structured_output")
    result = structured if isinstance(structured, dict) else extract_json(payload.get("result", ""))
    return result, usage


def request_name_batch(rows, config):
    if config["kind"] == "http":
        return request_http_batch(rows, config)
    return request_cli_batch(rows, config)


def merge_usage(items):
    merged = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            elif isinstance(value, dict):
                merged[key] = merge_usage([merged.get(key, {}), value])
    return merged


def request_names(rows, config):
    batch_size = (
        DEEPSEEK_WORKSPACES_PER_REQUEST
        if config["model"].startswith("deepseek/")
        else DEFAULT_WORKSPACES_PER_REQUEST
    )
    batches = [
        rows[index:index + batch_size]
        for index in range(0, len(rows), batch_size)
    ]
    with ThreadPoolExecutor(max_workers=min(4, len(batches))) as pool:
        results = list(pool.map(lambda batch: request_name_batch(batch, config), batches))
    names = []
    usage_items = []
    for payload, usage in results:
        batch_names = payload.get("n")
        if not isinstance(batch_names, list):
            raise RuntimeError("LLM JSON is missing the n list")
        names.extend(batch_names)
        usage_items.append(usage)
    return {"n": names}, merge_usage(usage_items), len(batches)


def validate_names(raw, rows):
    suggestions = raw.get("n")
    if not isinstance(suggestions, list):
        raise RuntimeError("LLM JSON is missing the n list")
    if len(suggestions) != len(rows):
        raise RuntimeError("LLM returned the wrong workspace count; no names were applied")

    normalized = []
    for row, item in zip(rows, suggestions):
        workspace_id = row["workspace_id"]
        if isinstance(item, dict):
            item = [
                item.get("workspace", item.get("n")),
                item.get("tabs", item.get("t")),
                item.get("panes", item.get("p")),
            ]
        if not isinstance(item, list) or len(item) != 3:
            raise RuntimeError(f"LLM returned an invalid item for {workspace_id}")
        workspace_name, tabs, panes = item
        if isinstance(tabs, dict):
            tabs = list(tabs.values())
        elif isinstance(tabs, str):
            tabs = [tabs]
        if isinstance(panes, dict):
            panes = list(panes.values())
        elif isinstance(panes, str):
            panes = [panes]
        if not isinstance(tabs, list) or len(tabs) != len(row["tabs"]):
            raise RuntimeError(f"LLM returned the wrong tab count for {workspace_id}")
        if not isinstance(panes, list) or len(panes) != len(row["panes"]):
            raise RuntimeError(f"LLM returned the wrong pane count for {workspace_id}")
        names = [workspace_name, *tabs, *panes]
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise RuntimeError(f"LLM returned an empty name for {workspace_id}")
        oversized = [
            name for name in panes
            if display_width(name.strip()) > PANE_MAX_DISPLAY_WIDTH
        ]
        if oversized:
            raise RuntimeError(
                f"LLM returned an oversized pane name for {workspace_id}: {oversized[0]}"
            )
        normalized.append({
            "workspace_id": workspace_id,
            "workspace": workspace_name.strip(),
            "tabs": {
                tab["tab_id"]: name.strip()
                for tab, name in zip(row["tabs"], tabs)
            },
            "panes": {
                pane["pane_id"]: name.strip()
                for pane, name in zip(row["panes"], panes)
            },
        })
    return normalized


def current_workspace_ids():
    payload = result_of(run_json(["herdr", "workspace", "list"]), "workspace_list")
    return {item["workspace_id"] for item in payload.get("workspaces", [])}


def herdr_socket_request(method, params):
    socket_path = os.environ.get(
        "HERDR_SOCKET_PATH", os.path.expanduser("~/.config/herdr/herdr.sock")
    )
    request_id = f"herdr-autoname:{time.time_ns()}"
    request = json.dumps(
        {"id": request_id, "method": method, "params": params},
        separators=(",", ":"),
    ) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(socket_path)
            client.sendall(request.encode("utf-8"))
            response = client.makefile("r", encoding="utf-8").readline()
    except OSError as exc:
        raise RuntimeError(f"Herdr socket request failed: {exc}") from exc
    if not response:
        raise RuntimeError("Herdr socket returned no response")
    payload = json.loads(response)
    if payload.get("error"):
        raise RuntimeError(f"Herdr socket error: {payload['error']}")
    return payload.get("result", {})


def grouped_workspace_ids(rows):
    groups = {}
    order = []
    for row in rows:
        key = row["group_key"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row["workspace_id"])
    return [workspace_id for key in order for workspace_id in groups[key]]


def group_workspaces(rows):
    current = result_of(run_json(["herdr", "workspace", "list"]), "workspace_list")
    current_ids = [item["workspace_id"] for item in current.get("workspaces", [])]
    row_ids = [row["workspace_id"] for row in rows]
    if set(current_ids) != set(row_ids):
        return False, row_ids
    desired = grouped_workspace_ids(rows)
    if desired == current_ids:
        return False, desired
    herdr_socket_request("workspace.move_block", {"workspace_ids": desired})
    return True, desired


def rename_if_live(kind, target_id, label, workspace_id):
    if workspace_id not in current_workspace_ids():
        return False, "workspace closed"
    code, _, stderr = run(["herdr", kind, "rename", target_id, label])
    return (True, "renamed") if code == 0 else (False, stderr or "target disappeared")


def apply_names(names):
    results = []
    stages = (
        ("workspace", lambda item: [(item["workspace_id"], item["workspace"])]),
        ("tab", lambda item: list(item["tabs"].items())),
        ("pane", lambda item: list(item["panes"].items())),
    )
    for kind, targets_for in stages:
        for item in names:
            for target_id, label in targets_for(item):
                ok, status = rename_if_live(kind, target_id, label, item["workspace_id"])
                results.append({"kind": kind, "id": target_id, "ok": ok, "status": status})
    return results


def plain_workspace_name(name):
    name = name.strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1].strip()
    return re.sub(r"^[^\w\u3400-\u9fff]+", "", name).strip()


def ensure_sidebar_layout(path=HERDR_CONFIG):
    try:
        with open(path, encoding="utf-8") as handle:
            original = handle.read()
    except OSError as exc:
        raise RuntimeError(f"Cannot read Herdr config {path}: {exc}") from exc

    lines = original.splitlines()
    for section, desired_rows in SIDEBAR_ROWS.items():
        header = f"[{section}]"
        try:
            start = next(index for index, line in enumerate(lines) if line.strip() == header)
        except StopIteration:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([header, desired_rows])
            continue
        end = next(
            (index for index in range(start + 1, len(lines)) if lines[index].lstrip().startswith("[")),
            len(lines),
        )
        row_indexes = [
            index for index in range(start + 1, end)
            if re.match(r"\s*rows\s*=", lines[index])
        ]
        if row_indexes:
            lines[row_indexes[0]] = desired_rows
            for index in reversed(row_indexes[1:]):
                del lines[index]
        else:
            lines.insert(start + 1, desired_rows)

    updated = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
    if updated == original:
        return False
    config_dir = os.path.dirname(path)
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=config_dir, delete=False
        ) as handle:
            temporary_path = handle.name
            handle.write(updated)
        os.chmod(temporary_path, os.stat(path).st_mode)
        os.replace(temporary_path, path)
    except OSError as exc:
        if "temporary_path" in locals() and os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise RuntimeError(f"Cannot update Herdr config {path}: {exc}") from exc
    return True


def configure_sidebar(rows, names=None):
    config_changed = ensure_sidebar_layout()
    updated = sync_project_metadata(rows, names)
    code, _, stderr = run(["herdr", "server", "reload-config"])
    if code != 0:
        raise RuntimeError(stderr or "Herdr config reload failed")
    return config_changed, updated


def sync_project_metadata(rows, names=None):
    renamed = {item["workspace_id"]: item["workspace"] for item in (names or [])}
    updated = 0
    for row in rows:
        workspace_name = renamed.get(row["workspace_id"], row["current_workspace"])
        workspace_name = workspace_name.strip().strip("[]").strip()
        context = row["project"]
        if context and not row["remote_host"] and row["git_status"] != "—":
            context = f"{context} · {row['git_status']}"
        tokens = [f"workspace_label=[{workspace_name}]"]
        if context:
            tokens.append(f"context={context}")
        command = [
            "herdr", "workspace", "report-metadata", row["workspace_id"],
            "--source", "herdr-autoname", "--clear-token", "project",
        ]
        if not context:
            command.extend(["--clear-token", "context"])
        for token in tokens:
            command.extend(["--token", token])
        code, _, _ = run(command)
        updated += code == 0
        for pane in row["panes"]:
            pane_command = [
                "herdr", "pane", "report-metadata", pane["pane_id"],
                "--source", "herdr-autoname", "--clear-token", "project",
            ]
            if pane["agent"] != "shell":
                pane_command.extend([
                    "--token", f"workspace_plain={plain_workspace_name(workspace_name)}",
                ])
            else:
                pane_command.extend(["--clear-token", "workspace_plain"])
            run(pane_command)
    return updated


def print_result_table(names, rows, detailed=False):
    agents = {
        row["workspace_id"]: ", ".join(dict.fromkeys(
            AGENT_LABELS.get(pane["agent"], f"◇ {pane['agent']}") for pane in row["panes"]
        ))
        for row in rows
    }
    table_rows = []
    for index, item in enumerate(names, 1):
        tabs = " / ".join(item["tabs"].values())
        tasks = " / ".join(item["panes"].values())
        table_rows.append([
            str(index), item["workspace"], tabs,
            agents.get(item["workspace_id"], "› shell"), tasks,
        ])
    terminal_width = shutil.get_terminal_size((120, 40)).columns
    task_width = max(24, min(40, terminal_width - 85))
    print_table_rows(
        ["#", "Workspace", "Tab", "Agent", "Pane 当前任务"],
        table_rows,
        [3, 28, 24, 12, task_width],
        stream=sys.stdout,
    )
    if detailed:
        detail_rows = []
        for item in names:
            detail_rows.append([
                item["workspace_id"],
                ", ".join(item["tabs"].values()),
                ", ".join(item["panes"].values()),
            ])
        print("\n详细名称：")
        print_table_rows(
            ["Workspace ID", "Tab", "Pane"], detail_rows, [14, 30, 38], stream=sys.stdout
        )


def current_names(rows):
    return [
        {
            "workspace_id": row["workspace_id"],
            "workspace": row["current_workspace"] or "（未命名）",
            "tabs": {
                tab["tab_id"]: tab["current_label"] or "（未命名）"
                for tab in row["tabs"]
            },
            "panes": {
                pane["pane_id"]: pane["current_label"] or pane["terminal_title"] or "（未命名）"
                for pane in row["panes"]
            },
        }
        for row in rows
    ]


def content_excerpt(text, limit):
    noise = re.compile(
        r"(?:bypass permissions|esc interrupt|ctrl\+[a-z]|tokens?\b|ctx[: ]|"
        r"rate:|\bBuild\s*·|Ask Codex|To continue this session|^※ recap:|"
        r"gpt-[\w.-]+\s*·|^工单关闭|^[╭╰╹┃─━▀▾⌘⬝■]+|"
        r"[█▯▮╌]{2,}|[✻※]\s*(?:Cogitated|Crunched|Brewed)|⎿\s*Tip:)", re.I
    )
    cjk_lines = []
    prose_lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or noise.search(line):
            continue
        visible = re.sub(r"[^\w\u3400-\u9fff]", "", line)
        if len(visible) < 8:
            continue
        if re.search(r"[\u3400-\u9fff]", line):
            cjk_lines.append(line)
            continue
        if any(char in line for char in "{}[]=<>`"):
            continue
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", line)
        if len(words) >= 6:
            prose_lines.append(line)
    lines = cjk_lines or prose_lines
    excerpt = " ".join(lines[-3:])
    if len(excerpt) > limit:
        excerpt = "..." + excerpt[-(limit - 3):]
    return excerpt or "（当前窗口没有可用的对话摘要）"


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in "WFA" else 1 for char in text)


def fit_cell(text, width):
    text = re.sub(r"\s+", " ", str(text)).strip()
    if display_width(text) <= width:
        return text + " " * (width - display_width(text))
    target = max(1, width - 3)
    output = []
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in "WFA" else 1
        if used + char_width > target:
            break
        output.append(char)
        used += char_width
    shortened = "".join(output) + "..."
    return shortened + " " * max(0, width - display_width(shortened))


def print_table_rows(headers, rows, widths, stream=sys.stderr):
    divider = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    print(divider, file=stream)
    print(
        "|" + "|".join(f" {fit_cell(value, width)} " for value, width in zip(headers, widths)) + "|",
        file=stream,
    )
    print(divider, file=stream)
    for row in rows:
        print(
            "|" + "|".join(f" {fit_cell(value, width)} " for value, width in zip(row, widths)) + "|",
            file=stream,
        )
    print(divider, file=stream)


def print_session_preview(rows, detailed=False):
    print(f"当前 Herdr 会话（{len(rows)} 个）：", file=sys.stderr)
    terminal_width = shutil.get_terminal_size((140, 40)).columns
    fixed_width = 3 + 26 + 30 + 16 + 12 + 4
    separator_width = 22
    recent_width = max(24, min(52, terminal_width - fixed_width - separator_width))
    headers = ["#", "Workspace", "路径", "Git", "Agent", "状态", "上次输入"]
    table_rows = []
    for index, row in enumerate(rows, 1):
        pane = row["panes"][0] if row["panes"] else {}
        agent = pane.get("agent", "shell")
        status = pane.get("status", "unknown")
        table_rows.append([
            str(index), row["current_workspace"],
            compact_path(row.get("path", "")) or "—",
            row.get("git_status", "—"),
            AGENT_LABELS.get(agent, f"◇ {agent}"),
            STATUS_ICONS.get(status, "·"),
            pane.get("last_user_input") or "—",
        ])
    print_table_rows(headers, table_rows, [3, 26, 30, 16, 12, 4, recent_width])
    print("状态：▶ 工作中  ○ 空闲  ◌ 等待  ✕ 错误  · 未知", file=sys.stderr)
    if detailed:
        detail_rows = []
        for row in rows:
            tabs = ", ".join(tab["current_label"] for tab in row["tabs"])
            panes = ", ".join(
                pane["current_label"] or pane["terminal_title"] or "(unnamed)"
                for pane in row["panes"]
            )
            detail_rows.append([row["current_workspace"], tabs, panes])
        print("Tab / Pane 详情：", file=sys.stderr)
        print_table_rows(["Workspace", "Tabs", "Panes"], detail_rows, [28, 32, 38])
    print(file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(
        prog="herdr-autoname",
        description="读取当前 Herdr 会话内容，用 LLM 批量更新 Workspace、Tab 和 Pane 名称。",
        add_help=False,
        epilog=(
            "示例：\n"
            "  herdr-autoname                         调用模型并应用全部名称\n"
            "  herdr-autoname --dry-run               不调用模型，只预览输出格式\n"
            "  herdr-autoname preview                 只查看当前会话，不调用模型\n"
            "  herdr-autoname configure               只修复侧边栏配置和 metadata\n"
            "  herdr-autoname provider                查看 Provider 和可用状态\n"
            "  herdr-autoname provider codex          持久切换到 Codex CLI\n"
            "  herdr-autoname model                   查看默认模型\n"
            "  herdr-autoname model google/gemini-3.7-flash\n"
            "                                           持久切换默认模型"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_argument_group("命令")
    commands.add_argument(
        "command", nargs="?", choices=("preview", "configure", "projects", "model", "provider"),
        metavar="{preview,configure,projects,model,provider}",
        help="预览会话、修复侧边栏，或管理模型和 Provider",
    )
    commands.add_argument(
        "value", nargs="?", metavar="值|reset",
        help="配合 model/provider 使用：设置值，或用 reset 恢复默认值",
    )
    options = parser.add_argument_group("选项")
    options.add_argument("-h", "--help", action="help", help="显示此帮助信息并退出")
    mode = options.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="应用生成的名称（默认行为）")
    mode.add_argument("--dry-run", action="store_true", help="不调用模型、不写入，只预览输出格式")
    options.add_argument(
        "--workspace", action="append", metavar="ID",
        help="只处理指定 Workspace ID；可重复传入",
    )
    options.add_argument("--env-file", metavar="路径", help="指定包含 API Key 的 env 文件")
    options.add_argument("--base-url", metavar="URL", help="临时覆盖 OpenAI 兼容 API 地址")
    options.add_argument("--model", metavar="模型ID", help="仅本次运行临时覆盖 LLM 模型")
    options.add_argument(
        "--provider", choices=PROVIDERS, metavar="名称",
        help="仅本次使用 auto/pika/deepseek/codex/claude",
    )
    options.add_argument("--json", action="store_true", help="输出机器可读的 JSON")
    options.add_argument("--verbose", action="store_true", help="预览时额外显示 Tab 和 Pane 详情")
    args = parser.parse_args()

    if args.command == "model":
        return model_command(args.value, args.json)
    if args.command == "provider":
        return provider_command(args.value, args)
    if args.value:
        parser.error("value 只能与 model 或 provider 命令一起使用")

    config = None
    if args.command is None and not args.dry_run:
        initialized, available = initialize_provider(args)
        config = provider_config(args)
        print_provider_detection(config, available, initialized)

    print("正在读取 Herdr 会话...", file=sys.stderr, flush=True)
    rows, before_ids = collect(args.workspace)
    if not rows:
        raise RuntimeError("No Herdr workspaces found")
    print(f"找到 {len(rows)} 个 Workspace。", file=sys.stderr, flush=True)
    print_session_preview(rows, detailed=args.verbose)
    if args.command == "preview":
        if args.json:
            print(json.dumps({"mode": "preview", "workspaces": rows}, ensure_ascii=False, indent=2))
        return 0
    if args.dry_run:
        names = current_names(rows)
        output = {
            "mode": "dry-run",
            "model_called": False,
            "names": names,
            "mutations": [],
        }
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print("✓ Dry run：未调用模型，未修改任何名称。")
            print("  以下使用当前名称预览最终输出格式：\n")
            print_result_table(names, rows, detailed=args.verbose)
        return 0
    if args.command in ("configure", "projects"):
        changed, updated = configure_sidebar(rows)
        grouped, _ = group_workspaces(rows)
        action = "已更新" if changed else "已确认"
        print(
            f"✓ {action}侧边栏布局，刷新 {updated} 个 Workspace 标签"
            f"，项目分组{'已更新' if grouped else '无需调整'}。",
            file=sys.stderr,
        )
        return 0
    if not args.dry_run:
        changed, updated = configure_sidebar(rows)
        action = "已更新" if changed else "已确认"
        print(
            f"✓ {action}侧边栏布局，并刷新 {updated} 个 Workspace 标签。",
            file=sys.stderr,
            flush=True,
        )
    batch_size = (
        DEEPSEEK_WORKSPACES_PER_REQUEST
        if config["model"].startswith("deepseek/")
        else DEFAULT_WORKSPACES_PER_REQUEST
    )
    request_count = (len(rows) + batch_size - 1) // batch_size
    started_at = time.monotonic()
    print(
        f"正在使用 {config['model']} 生成名称"
        f"（{request_count} 次请求）...",
        file=sys.stderr,
        flush=True,
    )
    raw_names, usage, request_count = request_names(rows, config)
    names = validate_names(raw_names, rows)
    elapsed = time.monotonic() - started_at
    do_apply = True
    print("正在写入 Workspace、Tab 和 Pane 名称...", file=sys.stderr, flush=True)
    mutations = apply_names(names)
    sync_project_metadata(rows, names)
    grouped, workspace_order = group_workspaces(rows)
    after_ids = current_workspace_ids()
    output = {
        "mode": "apply" if do_apply else "dry-run",
        "provider": config["provider"],
        "model": config["model"],
        "request_count": request_count,
        "elapsed_seconds": round(elapsed, 2),
        "usage": usage,
        "names": names,
        "mutations": mutations,
        "workspace_grouped": grouped,
        "workspace_order": workspace_order,
        "closed_during_run": sorted(before_ids - after_ids),
        "added_during_run": sorted(after_ids - before_ids),
    }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        successful = [item for item in mutations if item["ok"]]
        counts = {
            kind: sum(item["kind"] == kind for item in successful)
            for kind in ("workspace", "tab", "pane")
        }
        print(
            f"\n✓ 已更新 {counts['workspace']} 个 Workspace · "
            f"{counts['tab']} 个 Tab · {counts['pane']} 个 Pane"
        )
        print(f"  项目分组：{'已更新' if grouped else '无需调整'}")
        tokens = usage.get("total_tokens")
        cost = usage.get("cost")
        metrics = [config["model"], f"{elapsed:.2f}s", f"{request_count} 次请求"]
        if isinstance(tokens, (int, float)):
            metrics.append(f"{int(tokens):,} tokens")
        if isinstance(cost, (int, float)):
            metrics.append(f"${cost:.5f}")
        print("  " + " · ".join(metrics) + "\n")
        print_result_table(names, rows, detailed=args.verbose)
        if output["closed_during_run"] or output["added_during_run"]:
            print("运行期间关闭：", ", ".join(output["closed_during_run"]) or "无")
            print("运行期间新增：", ", ".join(output["added_during_run"]) or "无")
    if do_apply and any(not item["ok"] for item in mutations):
        return 2
    return 0


def cli():
    try:
        return main()
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
