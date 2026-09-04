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
# Budgets per pane, measured against real sessions: the current ask earns the
# most room, the previous one only frames it, and the reply only has to say
# what is happening now.
ASK_CHARS = 260
PREV_CHARS = 90
REPLY_CHARS = 130
SCREEN_CHARS = 350
INJECTED_TURN = re.compile(
    r"^(<task-notification|<command-name|<command-message|<local-command|<system-reminder"
    r"|Caveat:|Stop hook feedback|Continue if you have next steps"
    r"|This session is being continued from a previous conversation"
    r"|\[Request interrupted|\[Image:)", re.I)
FRAME_LINE = re.compile(r"^[\s\u2502\u251c\u2514\u250c\u2510\u2518\u2500\u253c\u2524"
                        r"\u258c\u2588\u2591\u254c\u256d\u256e\u2570\u256f=+|-]{12,}")
CONFIG_DUMP = re.compile(r"^[\w.\-]+=[^\s]*$")
# A spent quota fails the same way on every retry, so report it instead.
QUOTA_ERROR = re.compile(r"usage limit|quota|rate limit|insufficient credit", re.I)
PANE_MAX_DISPLAY_WIDTH = 28
DEEPSEEK_WORKSPACES_PER_REQUEST = 3
DEFAULT_WORKSPACES_PER_REQUEST = 30
REQUEST_LOG_DB = os.path.expanduser("~/.local/share/herdr-autoname/requests.db")
REQUEST_LOG_KEEP = 200
LLM_MAX_ATTEMPTS = 3
LLM_RETRY_BACKOFF = 1.5
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
    "done": "✓",
    "unknown": "·",
}
OPENROUTER_BASE = "https://openrouter.ai/api"
OPENROUTER_MODEL = "google/gemini-3.7-flash"
# An OpenAI-compatible endpoint needs no request code of its own: a base URL,
# a model and the shape of its auth header are the whole adapter.
HTTP_PROVIDERS = {
    "pika": {"label": "Pika Chat API", "base": PIKA_BASE, "model": PIKA_MODEL,
             "key_env": "PIKA_CHAT_API_KEY", "auth": "x-api-key"},
    "openrouter": {"label": "OpenRouter", "base": OPENROUTER_BASE, "model": OPENROUTER_MODEL,
                   "key_env": "OPENROUTER_API_KEY", "auth": "bearer"},
    "deepseek": {"label": "DeepSeek API", "base": DIRECT_BASE, "model": DIRECT_MODEL,
                 "key_env": "DEEPSEEK_API_KEY", "auth": "bearer"},
}
CLI_DEFAULT_MODEL = "CLI default"
# Every agent CLI can answer this, but each spawns a whole agent session, so
# they are ordered by how little of that session they insist on carrying.
CLI_PROVIDERS = {
    "pi": {"label": "Pi CLI", "executable": "pi", "model": OPENROUTER_MODEL},
    "claude": {"label": "Claude CLI", "executable": "claude", "model": "sonnet"},
    "codex": {"label": "Codex CLI", "executable": "codex", "model": CLI_DEFAULT_MODEL},
    "gemini": {"label": "Gemini CLI", "executable": "gemini", "model": CLI_DEFAULT_MODEL},
    "cursor": {"label": "Cursor CLI", "executable": "cursor-agent", "model": CLI_DEFAULT_MODEL},
}
PROVIDERS = ("auto", *HTTP_PROVIDERS, *CLI_PROVIDERS)
PROVIDER_LABELS = {
    "auto": "自动选择",
    **{name: spec["label"] for name, spec in HTTP_PROVIDERS.items()},
    **{name: spec["label"] for name, spec in CLI_PROVIDERS.items()},
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
- Pane 只描述最近对话中的下一步动作，不概括整段会话。使用「相同 emoji + 明确动词 + 对象」；emoji 后最多 11 个汉字，一个英文词算两个汉字。
- 极力压缩但保留动作和对象，删除可推断的过程词。推荐：「⚡ 合并Store轮询」「🚚 部署Tunnel到Hornet」「🏷️ 按近两轮命名Pane」「📊 显示全部Tracker账号」。
- 禁止使用「把……好」句式。不要写成长句，不要使用「处理问题」「继续工作」「代码优化」「配置到环境变量」「基于最近两轮对话」等空泛、冗长或角色式名称。
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


def is_injected_turn(text):
    """Hook output, task notifications and pasted frames are not user intent."""
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S).strip()
    if not text or INJECTED_TURN.match(text) or FRAME_LINE.match(text):
        return True
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and sum(bool(CONFIG_DUMP.match(line)) for line in lines) / len(lines) > 0.5:
        return True
    letters = len(re.findall(r"[\w\u4e00-\u9fff]", text))
    return letters < 4 or letters / len(text) < 0.35


def condense(text, limit):
    """Long pastes carry the ask at the end, so keep the tail and a head hint."""
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S)
    text = re.sub(r"\s+", " ", sanitize_content(text)).strip()
    if len(text) <= limit:
        return text
    head = max(0, limit // 3)
    return f"{text[:head]} … {text[-(limit - head):]}".strip()


def recent_signals(messages):
    """The two asks that frame the current arc, plus what the agent just said."""
    kept = [
        (role, text) for role, text in messages
        if text and text.strip() and not is_injected_turn(text)
    ]
    asks = [text for text in (condense(text, ASK_CHARS) for role, text in kept if role == "user") if text]
    lines = []
    if len(asks) > 1:
        lines.append("PREV: " + asks[-2][:PREV_CHARS])
    if asks:
        lines.append("ASK: " + asks[-1])
    reply = next((text for role, text in reversed(kept) if role == "assistant"), "")
    if reply:
        lines.append("DID: " + condense(reply, REPLY_CHARS))
    return "\n".join(lines)


def last_user_input(messages):
    for role, text in reversed(messages):
        if role != "user" or not text or not text.strip() or is_injected_turn(text):
            continue
        # A scrubbed command line condenses to nothing; keep looking back.
        condensed = condense(text, 650)
        if condensed:
            return condensed
    return ""


def compress_terminal(text):
    """A screen frame is mostly chrome; keep the lines that carry words."""
    kept = []
    for line in sanitize_content(text).splitlines():
        line = re.sub(r"[\u2588\u2591\u254c\u2500\u2502\u2503\u258c\u258f]{2,}", " ", line.strip())
        line = re.sub(r"\s{2,}", " ", line).strip()
        if not line or FRAME_LINE.match(line):
            continue
        letters = len(re.findall(r"[\w\u4e00-\u9fff]", line))
        if letters >= 4 and letters / len(line) >= 0.4:
            kept.append(line)
    return "\n".join(kept[-10:])[-SCREEN_CHARS:]


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
        "recent_content": recent_signals(messages),
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
            content = compress_terminal(stdout[-MAX_PANE_CHARS:])
            matches = re.findall(
                r"(?:^|\n)USER:\s*(.*?)(?=\n(?:USER|ASSISTANT):|\Z)", content, re.S
            )
            return {
                "recent_content": content,
                "last_user_input": condense(matches[-1], 650) if matches else "",
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


# A pane running a recognizable program does not need a model to name it, and an
# idle prompt has no work to describe at all. Cheap rules answer both; the model
# is only worth paying for where the work is ambiguous.
SHELL_NAMES = {"zsh", "bash", "sh", "fish", "dash", "login", "tmux", "screen"}
PROCESS_RULES = (
    (r"^(top|htop|btop|bpytop|glances|mole|status-go|nettop|iftop)$", "📈", "系统监控", "资源", "查看系统负载"),
    (r"^(lazygit|gitui|tig)$", "🌿", "Git 操作", "仓库", "查看提交状态"),
    (r"^(vim|nvim|hx|helix|emacs|nano|micro)$", "📝", "编辑文件", "编辑器", "编辑文件"),
    (r"^(less|more|tail|bat|journalctl)$", "📜", "查看日志", "日志", "查看日志输出"),
    (r"^(docker|docker-compose|kubectl|k9s|lazydocker)$", "🐳", "容器运维", "容器", "查看容器状态"),
    (r"^(psql|mysql|sqlite3|redis-cli|duckdb)$", "🗄️", "数据库查询", "数据库", "查询数据库"),
    (r"^(btm|ncdu|dust|duf)$", "💽", "磁盘占用", "磁盘", "查看磁盘占用"),
)
COMMAND_RULES = (
    (r"\b(vite|next dev|nuxt|webpack|run dev|dev-server)\b", "🚀", "本地开发服务", "开发服务", "运行开发服务"),
    (r"\b(pytest|jest|vitest|go test|cargo test|run test)\b", "🧪", "运行测试", "测试", "运行测试套件"),
    (r"\b(run build|cargo build|make build|tsc)\b", "📦", "构建产物", "构建", "运行构建"),
    (r"\b(git (log|status|diff))\b", "🌿", "Git 操作", "仓库", "查看仓库变更"),
)


def foreground_command(process_info):
    """The deepest non-shell process is what the pane is actually running."""
    for item in reversed((process_info or {}).get("foreground_processes") or []):
        name = (item.get("name") or item.get("argv0") or "").lstrip("-")
        if name and name not in SHELL_NAMES:
            return {"name": name, "cmdline": (item.get("cmdline") or "")[:200]}
    return {"name": "", "cmdline": ""}


def rule_named_workspace(row):
    """Name a whole workspace from rules, or return None to let the model take it."""
    if any(pane["agent"] not in ("", "shell", None) for pane in row["panes"]):
        return None
    if not row["panes"]:
        return None
    pane_names = []
    headline = None
    for pane in row["panes"]:
        named = rule_named_pane(pane, row)
        if not named:
            return None
        emoji, theme, tab, action = named
        headline = headline or (emoji, theme, tab)
        pane_names.append(f"{emoji} {action}")
    emoji, theme, tab = headline
    return [f"{emoji} {theme}", [f"{emoji} {tab}" for _ in row["tabs"]], pane_names]


def rule_named_pane(pane, row):
    if pane.get("remote_host"):
        host = pane["remote_host"]
        return "💻", f"远程 {host}", host, f"连接 {host}"
    command = pane.get("command") or {}
    name = command.get("name", "")
    cmdline = command.get("cmdline", "")
    for pattern, emoji, theme, tab, action in PROCESS_RULES:
        if re.match(pattern, name, re.I):
            return emoji, theme, tab, action
    for pattern, emoji, theme, tab, action in COMMAND_RULES:
        if re.search(pattern, cmdline, re.I):
            return emoji, theme, tab, action
    if not name:
        # A bare prompt: say so instead of inventing work from stale screen text.
        label = row.get("project") or os.path.basename(pane.get("cwd", "")) or "Shell"
        return "🖥️", label, "空闲", "空闲 Shell"
    return None


def split_rule_named(rows):
    ruled_rows, ruled_items, pending = [], [], []
    for row in rows:
        item = rule_named_workspace(row)
        if item:
            ruled_rows.append(row)
            ruled_items.append(item)
        else:
            pending.append(row)
    return ruled_rows, ruled_items, pending


def read_process_info(pane):
    code, stdout, _ = run([
        "herdr", "pane", "process-info", "--pane", pane["pane_id"]
    ])
    if code != 0:
        return {}
    try:
        payload = json.loads(stdout)
        return payload.get("result", {}).get("process_info", {})
    except json.JSONDecodeError:
        return {}


def ssh_destination(argv):
    options_with_values = {
        "-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J",
        "-L", "-l", "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w",
    }
    skip_next = False
    for argument in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if argument in options_with_values:
            skip_next = True
            continue
        if argument.startswith("-"):
            continue
        host = argument.rsplit("@", 1)[-1].strip("[]")
        return host.split(":", 1)[0]
    return ""


def remote_host(pane, process_info=None):
    process_info = process_info or {}
    foreground_pid = process_info.get("foreground_process_group_id")
    for process in process_info.get("foreground_processes", []):
        if process.get("pid") != foreground_pid:
            continue
        argv = process.get("argv") or []
        executable = os.path.basename(str(process.get("argv0") or process.get("name") or ""))
        if executable == "ssh" and argv:
            return ssh_destination(argv)

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


def workspace_location(workspace_id, panes, process_infos=None):
    process_infos = process_infos or {}
    host = ""
    for pane in panes:
        host = remote_host(pane, process_infos.get(pane["pane_id"]))
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
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(panes)))) as pool:
        process_infos = dict(zip(
            (p["pane_id"] for p in panes), pool.map(read_process_info, panes)
        ))
    paths = {
        item.get("foreground_cwd") or item.get("cwd", "")
        for item in panes
        if not remote_host(item, process_infos.get(item["pane_id"]))
    }
    paths.discard("")
    with ThreadPoolExecutor(max_workers=min(12, max(1, len(paths)))) as pool:
        git_states = dict(zip(paths, pool.map(git_summary, paths)))

    rows = []
    for workspace in workspaces:
        workspace_id = workspace["workspace_id"]
        row_tabs = [item for item in tabs if item["workspace_id"] == workspace_id]
        row_panes = [item for item in panes if item["workspace_id"] == workspace_id]
        location = workspace_location(workspace_id, row_panes, process_infos)
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
                    "command": foreground_command(process_infos.get(item["pane_id"])),
                    "remote_host": remote_host(item, process_infos.get(item["pane_id"])),
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


def open_request_log():
    if not os.path.exists(REQUEST_LOG_DB):
        raise RuntimeError(f"还没有请求记录（{REQUEST_LOG_DB} 不存在）")
    connection = sqlite3.connect(f"file:{REQUEST_LOG_DB}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def print_log_detail(record):
    for key in REQUEST_LOG_COLUMNS:
        if key in ("request_json", "response_text"):
            continue
        print(f"{key}: {record.get(key)}")
    for key, title in (("request_json", "请求输入"), ("response_text", "原始响应")):
        text = record.get(key) or ""
        print(f"\n--- {title}（{len(text)} 字符）---")
        print(text)


def log_command(value, as_json=False):
    connection = open_request_log()
    try:
        if value and value.isdigit():
            row = connection.execute(
                "SELECT * FROM requests WHERE id = ?", (int(value),)
            ).fetchone()
            if row is None:
                raise RuntimeError(f"找不到记录 {value}")
            record = dict(row)
            if as_json:
                print(json.dumps(record, ensure_ascii=False, indent=2))
            else:
                print_log_detail(record)
            return 0
        query = "SELECT * FROM requests"
        if value in ("failed", "errors"):
            query += " WHERE error IS NOT NULL"
        elif value:
            raise RuntimeError("log 只接受记录 ID 或 failed")
        rows = [
            dict(item)
            for item in connection.execute(query + " ORDER BY id DESC LIMIT 20")
        ]
    finally:
        connection.close()
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("没有匹配的请求记录。")
        return 0
    table = [
        [
            str(row["id"]),
            (row["started_at"] or "")[5:],
            row["model"] or "",
            str(row["attempt"] or 1),
            f"{row['http_status'] or '-'} / {row['finish_reason'] or '-'}",
            f"{row['completion_tokens'] or 0}+{row['reasoning_chars'] or 0}r",
            f"{len(row['response_text'] or '')}",
            row["error"] or "ok",
        ]
        for row in rows
    ]
    print_table_rows(
        ["ID", "时间", "模型", "#", "HTTP/finish", "Tokens", "响应字符", "结果"],
        table,
        [4, 14, 24, 2, 18, 14, 8, 38],
        stream=sys.stdout,
    )
    print(f"\n数据库：{REQUEST_LOG_DB}")
    print("查看完整请求和响应：herdr-autoname log <ID>")
    return 0


_BORROWED_KEYS = {}


def openrouter_key(env_file):
    """Fall back to the OpenRouter token pi already holds, so one login serves both."""
    key = env_value("OPENROUTER_API_KEY", env_file)
    if key:
        return key
    if "openrouter" not in _BORROWED_KEYS:
        borrowed = ""
        if shutil.which("pi"):
            code, stdout, _ = run(
                ["pi", "auth", "print-bearer-token", "--provider", "openrouter"], timeout=20
            )
            borrowed = stdout.strip() if code == 0 else ""
        _BORROWED_KEYS["openrouter"] = borrowed
    return _BORROWED_KEYS["openrouter"]


def http_provider_key(provider, env_file):
    if provider == "openrouter":
        return openrouter_key(env_file)
    return env_value(HTTP_PROVIDERS[provider]["key_env"], env_file)


def local_cli_authenticated(provider):
    executable = shutil.which(CLI_PROVIDERS[provider]["executable"])
    if not executable:
        return False
    if provider == "codex":
        code, stdout, stderr = run([executable, "login", "status"], timeout=10)
        return code == 0 and "logged in" in f"{stdout}\n{stderr}".lower()
    if provider == "cursor":
        code, stdout, _ = run([executable, "status"], timeout=15)
        return code == 0 and "logged in" in stdout.lower()
    if provider == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) or any(
            os.path.exists(os.path.expanduser(f"~/.gemini/{name}"))
            for name in ("google_accounts.json", "oauth_creds.json")
        )
    if provider == "pi":
        return any(
            run([executable, "auth", "check", "--provider", name, "--json"], timeout=15)[1]
            .find('"status":"ready"') >= 0
            for name in ("openrouter", "anthropic", "google", "openai")
        )
    code, stdout, _ = run([executable, "auth", "status", "--json"], timeout=10)
    if code != 0:
        return False
    try:
        return bool(json.loads(stdout).get("loggedIn"))
    except json.JSONDecodeError:
        return False


def provider_availability(args):
    return {
        **{name: bool(http_provider_key(name, args.env_file)) for name in HTTP_PROVIDERS},
        **{name: local_cli_authenticated(name) for name in CLI_PROVIDERS},
    }


def resolve_provider(args, available=None):
    requested = args.provider or saved_provider()
    if requested != "auto":
        return requested
    available = available or provider_availability(args)
    return next((name for name in PROVIDERS[1:] if available[name]), None)


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
        for name in CLI_PROVIDERS
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
            "没有可用 Provider：请配置 "
            + " / ".join(spec["key_env"] for spec in HTTP_PROVIDERS.values())
            + "，或安装并登录 " + " / ".join(CLI_PROVIDERS) + " CLI"
        )
    requested_model = args.model or saved_model()
    if provider in HTTP_PROVIDERS:
        spec = HTTP_PROVIDERS[provider]
        key = http_provider_key(provider, args.env_file)
        if not key:
            raise RuntimeError(f"Provider {provider} 需要 {spec['key_env']}")
        if provider == "deepseek":
            model = requested_model if requested_model in ("deepseek-chat", "deepseek-reasoner") else spec["model"]
        elif provider == "pika":
            model = requested_model or spec["model"]
        else:
            model = args.model or spec["model"]
        base = args.base_url or spec["base"]
        if provider == "pika":
            base = args.base_url or os.environ.get("PIKA_API_BASE", spec["base"])
        return {
            "kind": "http",
            "base": base,
            "model": model,
            "headers": ({"X-API-Key": key} if spec["auth"] == "x-api-key"
                        else {"Authorization": f"Bearer {key}"}),
            "provider": spec["label"],
        }
    spec = CLI_PROVIDERS[provider]
    if not shutil.which(spec["executable"]):
        raise RuntimeError(f"找不到 {spec['executable']} CLI，请先安装并登录")
    return {
        "kind": "cli",
        "cli": provider,
        # Haiku ignores --effort here and spends thousands of thinking tokens on
        # this task; Sonnet answers it directly and costs less doing so.
        "model": args.model or spec["model"],
        "provider": spec["label"],
    }


def balance_brackets(text):
    """Rebuild JSON whose closing brackets are missing, stray, or out of order."""
    closers = {"[": "]", "{": "}"}
    stack = []
    fixed = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            fixed.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in closers:
            stack.append(char)
        elif char in ("]", "}"):
            # A closer that arrives too early means the levels below it were
            # never closed; emit those first instead of dropping the value.
            while stack and closers[stack[-1]] != char:
                fixed.append(closers[stack.pop()])
            if not stack:
                continue  # Stray closer with nothing open: drop it.
            stack.pop()
        fixed.append(char)
    if in_string:
        fixed.append('"')
    while fixed and fixed[-1] in " \t\r\n,":
        fixed.pop()
    while stack:
        fixed.append(closers[stack.pop()])
    return "".join(fixed)


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    start = text.find("{")
    if start < 0:
        raise RuntimeError("LLM response did not contain JSON")
    match = re.search(r"\{.*\}", text, re.S)
    # Models drop a bracket often enough that repairing the nesting beats a
    # retry; validate_names still rejects anything with the wrong shape.
    candidates = [match.group(0)] if match else []
    candidates.append(balance_brackets(text[start:]))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("LLM returned malformed JSON")


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


class TransientLLMError(RuntimeError):
    """A failure that a plain retry of the same request usually clears."""


REQUEST_LOG_COLUMNS = (
    "started_at", "provider", "model", "workspace_ids", "workspace_count", "attempt",
    "elapsed_ms", "http_status", "finish_reason", "prompt_tokens",
    "completion_tokens", "reasoning_chars", "request_json", "response_text",
    "error",
)
REQUEST_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    provider TEXT,
    model TEXT,
    workspace_ids TEXT,
    workspace_count INTEGER,
    attempt INTEGER,
    elapsed_ms INTEGER,
    http_status INTEGER,
    finish_reason TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    reasoning_chars INTEGER,
    request_json TEXT,
    response_text TEXT,
    error TEXT
)
"""


def log_request(record):
    """Store one LLM exchange so a bad response stays inspectable afterwards."""
    try:
        os.makedirs(os.path.dirname(REQUEST_LOG_DB), exist_ok=True)
        with sqlite3.connect(REQUEST_LOG_DB, timeout=10) as connection:
            connection.execute(REQUEST_LOG_SCHEMA)
            existing = {
                column[1]
                for column in connection.execute("PRAGMA table_info(requests)")
            }
            for column in REQUEST_LOG_COLUMNS:
                if column not in existing:
                    connection.execute(f"ALTER TABLE requests ADD COLUMN {column}")
            cursor = connection.execute(
                "INSERT INTO requests ({}) VALUES ({})".format(
                    ", ".join(REQUEST_LOG_COLUMNS),
                    ", ".join("?" * len(REQUEST_LOG_COLUMNS)),
                ),
                [record.get(column) for column in REQUEST_LOG_COLUMNS],
            )
            connection.execute(
                "DELETE FROM requests WHERE id <= (SELECT MAX(id) FROM requests) - ?",
                (REQUEST_LOG_KEEP,),
            )
            return cursor.lastrowid
    except (sqlite3.Error, OSError):
        return None  # Logging must never break a naming run.


def log_hint(message, row_id):
    if row_id is None:
        return str(message)
    return f"{message}（完整请求和响应：herdr-autoname log {row_id}）"


def request_http_batch(rows, config, record):
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
    record["request_json"] = compact_input
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
        raise TransientLLMError(f"请求失败：{exc}") from exc
    finally:
        if config_path:
            try:
                os.unlink(config_path)
            except OSError:
                pass
    response_body, separator, status = proc.stdout.rpartition("\n")
    if proc.returncode != 0:
        raise TransientLLMError(f"请求失败：{proc.stderr.strip()[:500]}")
    if not separator or not status.isdigit():
        raise RuntimeError("LLM response had no HTTP status")
    record["http_status"] = int(status)
    record["response_text"] = response_body
    if int(status) == 429 or int(status) >= 500:
        raise TransientLLMError(f"上游 HTTP {status}：{response_body[:300]}")
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
    reasoning = message.get("reasoning_content") or ""
    usage = payload.get("usage") or {}
    finish_reason = choice.get("finish_reason")
    record["response_text"] = content
    record["finish_reason"] = finish_reason
    record["reasoning_chars"] = len(reasoning)
    record["prompt_tokens"] = usage.get("prompt_tokens")
    record["completion_tokens"] = usage.get("completion_tokens")
    if finish_reason not in (None, "stop") or not content.strip():
        record["response_text"] = response_body
        raise TransientLLMError(
            f"上游生成未完成（finish={finish_reason}, "
            f"completion_tokens={usage.get('completion_tokens', 'unknown')}, "
            f"reasoning_chars={len(reasoning)}, content={len(content)} 字符）"
        )
    usage["request_bytes"] = len(request_data.encode("utf-8"))
    usage["response_bytes"] = len(response_body.encode("utf-8"))
    try:
        return extract_json(content), usage
    except RuntimeError as exc:
        raise TransientLLMError(str(exc)) from exc


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


def cli_failure_reason(proc):
    """A CLI banner echoes the whole prompt; the real cause sits at the tail."""
    output = (proc.stderr or proc.stdout).strip()
    errors = [
        line.strip() for line in output.splitlines()
        if re.match(r"\s*(ERROR\b|error:)", line, re.I)
    ]
    return "\n".join(dict.fromkeys(errors))[:400] if errors else output[-400:]


def cli_invocation(provider, model, payload):
    """The leanest one-shot form each CLI supports: no tools, sessions or skills.

    The payload rides on stdin wherever the CLI allows it, so pane conversation
    never lands in a process listing.
    """
    schema = json.dumps(naming_schema(), separators=(",", ":"))
    if provider == "claude":
        return [
            "claude", "-p", "--safe-mode", "--model", model, "--effort", "low",
            "--output-format", "json", "--no-session-persistence",
            "--permission-mode", "dontAsk", "--tools", "",
            # Skills, MCP servers and per-machine context are dead weight for a
            # naming call; replacing the coding-assistant prompt cuts the
            # context from 28k to 8k tokens on a full run.
            "--disable-slash-commands", "--strict-mcp-config", "--no-chrome",
            "--exclude-dynamic-system-prompt-sections",
            "--system-prompt", SYSTEM_PROMPT, "--json-schema", schema,
        ], payload
    if provider == "codex":
        command = [
            "codex", "exec", "--skip-git-repo-check", "--ephemeral",
            "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only",
            "--color", "never", "-c", 'model_reasoning_effort="low"',
        ]
        if model != CLI_DEFAULT_MODEL:
            command.extend(["--model", model])
        command.append("-")
        return command, SYSTEM_PROMPT + "\n\n输入：\n" + payload
    if provider == "pi":
        command = ["pi", "-p", "--no-tools", "--no-session", "--no-extensions",
                   "--system-prompt", SYSTEM_PROMPT]
        if model != CLI_DEFAULT_MODEL:
            command.extend(["--model", model])
        return command, payload
    if provider == "gemini":
        command = ["gemini", "--skip-trust", "--approval-mode", "plan"]
        if model != CLI_DEFAULT_MODEL:
            command.extend(["--model", model])
        # gemini appends -p after stdin, so the instructions land last.
        command.extend(["-p", SYSTEM_PROMPT])
        return command, "输入：\n" + payload
    command = ["cursor-agent", "-p", "--trust", "--output-format", "text"]
    if model != CLI_DEFAULT_MODEL:
        command.extend(["--model", model])
    return command, SYSTEM_PROMPT + "\n\n输入：\n" + payload


def cli_failure_reason(proc):
    """A CLI banner echoes the whole prompt; the real cause sits at the tail."""
    output = (proc.stderr or proc.stdout).strip()
    errors = [
        line.strip() for line in output.splitlines()
        if re.match(r"\s*(ERROR\b|error:)", line, re.I)
    ]
    return "\n".join(dict.fromkeys(errors))[:400] if errors else output[-400:]


def request_cli_batch(rows, config, record):
    payload = json.dumps(
        naming_payload(rows), ensure_ascii=False, separators=(",", ":")
    )
    provider = config["cli"]
    command, stdin_text = cli_invocation(provider, config["model"], payload)
    record["request_json"] = payload
    try:
        proc = subprocess.run(
            command, input=stdin_text, capture_output=True, text=True,
            timeout=250, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TransientLLMError(f"{config['provider']} 请求失败：{exc}") from exc
    record["response_text"] = proc.stdout
    if proc.returncode != 0:
        reason = cli_failure_reason(proc)
        failure = RuntimeError if QUOTA_ERROR.search(reason) else TransientLLMError
        raise failure(f"{config['provider']} 请求失败：{reason}")
    usage = {
        "request_bytes": len(stdin_text.encode("utf-8")),
        "response_bytes": len(proc.stdout.encode("utf-8")),
    }
    text = proc.stdout
    if provider == "claude":
        try:
            payload_json = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise TransientLLMError("Claude CLI 返回了无效 JSON") from exc
        text = payload_json.get("result") or ""
        cli_usage = payload_json.get("usage") or {}
        usage.update({
            key: value for key, value in cli_usage.items() if isinstance(value, (int, float))
        })
        if isinstance(payload_json.get("total_cost_usd"), (int, float)):
            usage["cost"] = payload_json["total_cost_usd"]
    elif provider == "codex":
        token_match = re.search(r"tokens used\s*\n([\d,]+)", proc.stderr, re.I)
        if token_match:
            usage["total_tokens"] = int(token_match.group(1).replace(",", ""))
    try:
        return extract_json(text), usage
    except RuntimeError as exc:
        raise TransientLLMError(str(exc)) from exc


def request_name_batch(rows, config):
    record = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "provider": config["provider"],
        "model": config["model"],
        "workspace_ids": ",".join(row["workspace_id"] for row in rows),
        "workspace_count": len(rows),
    }
    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        attempt_record = dict(record, attempt=attempt)
        started_at = time.monotonic()
        try:
            if config["kind"] == "http":
                result = request_http_batch(rows, config, attempt_record)
            else:
                result = request_cli_batch(rows, config, attempt_record)
        except RuntimeError as exc:
            attempt_record["error"] = str(exc)
            attempt_record["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
            row_id = log_request(attempt_record)
            if not isinstance(exc, TransientLLMError) or attempt == LLM_MAX_ATTEMPTS:
                raise RuntimeError(log_hint(exc, row_id)) from exc
            time.sleep(LLM_RETRY_BACKOFF * attempt)
            continue
        attempt_record["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
        log_request(attempt_record)
        return result


def merge_usage(items):
    merged = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            elif isinstance(value, dict):
                merged[key] = merge_usage([merged.get(key, {}), value])
    return merged


def workspaces_per_request(config):
    if config["model"].startswith("deepseek"):
        return DEEPSEEK_WORKSPACES_PER_REQUEST
    return DEFAULT_WORKSPACES_PER_REQUEST


def request_names(rows, config):
    batch_size = workspaces_per_request(config)
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
    contexts = grouped_sidebar_contexts(rows)
    updated = 0
    for row in rows:
        workspace_name = renamed.get(row["workspace_id"], row["current_workspace"])
        workspace_name = workspace_name.strip().strip("[]").strip()
        context = contexts[row["workspace_id"]]
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


def sidebar_context(row):
    context = row["project"]
    if context and not row["remote_host"] and row["git_status"] != "—":
        context = f"{context} · {row['git_status']}"
    return context


def grouped_sidebar_contexts(rows):
    contexts = {}
    seen = set()
    for row in rows:
        key = row["group_key"]
        contexts[row["workspace_id"]] = sidebar_context(row) if key not in seen else ""
        seen.add(key)
    return contexts


def sidebar_preview_data(names, rows):
    named = {item["workspace_id"]: item for item in names}
    rows_by_id = {row["workspace_id"]: row for row in rows}
    ordered_rows = [rows_by_id[item] for item in grouped_workspace_ids(rows)]
    spaces = []
    agents = []
    contexts = grouped_sidebar_contexts(ordered_rows)
    status_rank = {
        "working": 0, "waiting": 1, "error": 2, "idle": 3,
        "completed": 4, "done": 4, "unknown": 5,
    }
    previous_group = None
    for row in ordered_rows:
        item = named[row["workspace_id"]]
        if previous_group is not None and row["group_key"] != previous_group:
            spaces.append({"separator": True})
            agents.append({"separator": True})
        statuses = [pane["status"] for pane in row["panes"]] or ["unknown"]
        status = min(statuses, key=lambda value: status_rank.get(value, 5))
        spaces.append({
            "workspace_id": row["workspace_id"],
            "status": status,
            "label": f"[{item['workspace'].strip().strip('[]').strip()}]",
            "context": contexts[row["workspace_id"]],
        })
        for pane in row["panes"]:
            agents.append({
                "workspace_id": row["workspace_id"],
                "pane_id": pane["pane_id"],
                "status": pane["status"],
                "label": item["panes"].get(pane["pane_id"], pane["current_label"]),
                "context": (
                    f"{pane['agent']} · {plain_workspace_name(item['workspace'])}"
                ),
            })
        previous_group = row["group_key"]
    return {"spaces": spaces, "agents": agents}


def print_sidebar_preview(names, rows):
    preview = sidebar_preview_data(names, rows)
    terminal_width = shutil.get_terminal_size((100, 40)).columns
    use_bold = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def cell(text, width, bold=False):
        text = str(text).replace("\n", " ")
        if display_width(text) > width:
            text = fit_cell(text, width).rstrip()
        else:
            text += " " * (width - display_width(text))
        return f"\033[1m{text}\033[0m" if bold and use_bold else text

    agents_by_workspace = {}
    for item in preview["agents"]:
        if item.get("separator"):
            continue
        agents_by_workspace.setdefault(item["workspace_id"], []).extend([
            (f"{STATUS_ICONS.get(item['status'], '·')} {item['label']}", True),
            (f"  {item['context']}", False),
        ])

    spaces = [("spaces", True), ("", False)]
    agents = [("agents", True), ("", False)]
    for item in preview["spaces"]:
        if item.get("separator"):
            spaces.append(("", False))
            agents.append(("", False))
            continue
        left = [(f"{STATUS_ICONS.get(item['status'], '·')} {item['label']}", True)]
        if item["context"]:
            left.append((f"  {item['context']}", False))
        right = agents_by_workspace.get(item["workspace_id"], [])
        block_size = max(len(left), len(right))
        spaces.extend(left + [("", False)] * (block_size - len(left)))
        agents.extend(right + [("", False)] * (block_size - len(right)))

    title = "Herdr Sidebar Preview"
    print(f"\033[1m{title}\033[0m" if use_bold else title)
    if terminal_width >= 82:
        width = max(36, min(54, (terminal_width - 7) // 2))
        print("┌" + "─" * (width + 2) + "┬" + "─" * (width + 2) + "┐")
        count = max(len(spaces), len(agents))
        for index in range(count):
            left = spaces[index] if index < len(spaces) else ("", False)
            right = agents[index] if index < len(agents) else ("", False)
            if index == 0:
                right = (
                    "agents" + " " * max(1, width - len("agents") - len("priority")) + "priority",
                    True,
                )
            print(
                f"│ {cell(left[0], width, left[1])} │ "
                f"{cell(right[0], width, right[1])} │"
            )
        print("└" + "─" * (width + 2) + "┴" + "─" * (width + 2) + "┘")
        return

    width = max(38, terminal_width - 4)
    print("┌" + "─" * (width + 2) + "┐")
    for section in (spaces, agents):
        for index, (text, bold) in enumerate(section):
            if section is agents and index == 0:
                text = "agents" + " " * max(1, width - len("agents") - len("priority")) + "priority"
            print(f"│ {cell(text, width, bold)} │")
        if section is spaces:
            print(f"│ {cell('', width)} │")
    print("└" + "─" * (width + 2) + "┘")


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
    width = 0
    previous_width = 0
    for char in str(text):
        if char == "\ufe0f":
            if previous_width == 1:
                width += 1
                previous_width = 2
            continue
        if unicodedata.combining(char) or unicodedata.category(char) in ("Cf", "Mn", "Me"):
            continue
        previous_width = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        width += previous_width
    return width


def fit_cell(text, width):
    text = re.sub(r"\s+", " ", str(text)).strip()
    if display_width(text) <= width:
        return text + " " * (width - display_width(text))
    target = max(1, width - 3)
    output = []
    used = 0
    for char in text:
        candidate = "".join(output) + char
        candidate_width = display_width(candidate)
        if candidate_width > target:
            break
        output.append(char)
        used = candidate_width
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
            "  herdr-autoname -d                      不调用模型，只预览输出格式\n"
            "  herdr-autoname preview                 只查看当前会话，不调用模型\n"
            "  herdr-autoname configure               只修复侧边栏配置和 metadata\n"
            "  herdr-autoname provider                查看 Provider 和可用状态\n"
            "  herdr-autoname provider codex          持久切换到 Codex CLI\n"
            "  herdr-autoname model                   查看默认模型\n"
            "  herdr-autoname log                     查看最近 20 次模型请求\n"
            "  herdr-autoname log failed              只看失败的请求\n"
            "  herdr-autoname log 12                  打印第 12 条的完整请求和响应\n"
            "  herdr-autoname model google/gemini-3.7-flash\n"
            "                                           持久切换默认模型"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_argument_group("命令")
    commands.add_argument(
        "command", nargs="?",
        choices=("preview", "configure", "projects", "model", "provider", "log"),
        metavar="{preview,configure,projects,model,provider,log}",
        help="预览会话、修复侧边栏、管理模型和 Provider，或查看请求日志",
    )
    commands.add_argument(
        "value", nargs="?", metavar="值|reset|ID",
        help="配合 model/provider 设置值（reset 恢复默认），或配合 log 传记录 ID / failed",
    )
    options = parser.add_argument_group("选项")
    options.add_argument("-h", "--help", action="help", help="显示此帮助信息并退出")
    mode = options.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="应用生成的名称（默认行为）")
    mode.add_argument("-d", "--dry-run", action="store_true", help="不调用模型、不写入，只预览输出格式")
    options.add_argument(
        "--workspace", action="append", metavar="ID",
        help="只处理指定 Workspace ID；可重复传入",
    )
    options.add_argument("--env-file", metavar="路径", help="指定包含 API Key 的 env 文件")
    options.add_argument("--base-url", metavar="URL", help="临时覆盖 OpenAI 兼容 API 地址")
    options.add_argument("--model", metavar="模型ID", help="仅本次运行临时覆盖 LLM 模型")
    options.add_argument(
        "--provider", choices=PROVIDERS, metavar="名称",
        help="仅本次使用 " + "/".join(PROVIDERS),
    )
    options.add_argument("--json", action="store_true", help="输出机器可读的 JSON")
    options.add_argument("--verbose", action="store_true", help="预览时额外显示 Tab 和 Pane 详情")
    args = parser.parse_args()

    if args.command == "model":
        return model_command(args.value, args.json)
    if args.command == "provider":
        return provider_command(args.value, args)
    if args.command == "log":
        return log_command(args.value, args.json)
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
    if not args.dry_run:
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
            "sidebar_preview": sidebar_preview_data(names, rows),
            "mutations": [],
        }
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print("✓ Dry run：未调用模型，未修改任何名称。")
            print("  以下按实际分组顺序预览 Herdr Sidebar：\n")
            print_sidebar_preview(names, rows)
            if args.verbose:
                print("\n详细名称：")
                print_result_table(names, rows, detailed=True)
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
    ruled_rows, ruled_items, pending = split_rule_named(rows)
    started_at = time.monotonic()
    if ruled_rows:
        print(
            f"规则命名 {len(ruled_rows)} 个 Workspace（无需模型）。",
            file=sys.stderr,
            flush=True,
        )
    names = validate_names({"n": ruled_items}, ruled_rows)
    usage, request_count = {}, 0
    if pending:
        batch_size = workspaces_per_request(config)
        print(
            f"正在使用 {config['model']} 为其余 {len(pending)} 个生成名称"
            f"（{(len(pending) + batch_size - 1) // batch_size} 次请求）...",
            file=sys.stderr,
            flush=True,
        )
        raw_names, usage, request_count = request_names(pending, config)
        names += validate_names(raw_names, pending)
    order = {row["workspace_id"]: index for index, row in enumerate(rows)}
    names.sort(key=lambda item: order[item["workspace_id"]])
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
        "rule_named": len(ruled_rows),
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
        if ruled_rows:
            metrics.append(f"{len(ruled_rows)} 个走规则")
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
