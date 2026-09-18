# herdr-autoname

[简体中文](README.zh-CN.md)

Automatically rename Herdr workspaces, tabs, and panes from the latest real conversations in each coding-agent session.

## Highlights

- Runs as a native Herdr Plugin with an interactive Settings Popup.
- Names a new agent session after its first completed conversation, then refreshes every configurable number of conversations per pane.
- Reads native OpenCode, Claude Code, and Codex sessions.
- Uses the latest two user requests and the latest agent conclusion instead of raw terminal noise.
- Supports Pika, OpenRouter, DeepSeek, Pi, Claude, Codex, Gemini, Cursor, and Grok providers.
- Applies deterministic names to recognizable tools, SSH panes, and idle shells without calling a model.
- Validates workspace, tab, and pane counts before applying names.
- Keeps one malformed workspace from blocking all other updates.
- Groups workspaces by project and reports project, Git, and remote-host metadata to the Herdr Sidebar.
- Uses only the Python standard library.

## Requirements

- Herdr 0.9.0 or later
- Python 3.10 or later
- One available provider: an API key or an authenticated local agent CLI

## Install as a Herdr Plugin

```bash
herdr plugin install thejiajun/herdr-autoname
```

The plugin registers:

- `open-settings` — open the interactive Settings Popup
- `preview` — print a read-only Sidebar preview
- `rename-now` — rename immediately
- `pane.agent_status_changed` — count completed user conversations and trigger automatically

Open Settings:

```bash
herdr plugin action invoke thejiajun.autoname.open-settings
```

The Popup lets you select a provider and model, change the conversation interval, enable or disable automatic naming, preview the Sidebar, and run a rename immediately.

A newly observed session is named as soon as its first user conversation completes. After that, names refresh every 3 completed user conversations per pane by default. History from before installation is not counted.

Plugin configuration is stored in Herdr's isolated config directory:

```bash
herdr plugin config-dir thejiajun.autoname
```

Plugin counters, provider cache, and request logs are stored in Herdr's isolated plugin state directory. They are never written into the repository.

### Manual actions

```bash
herdr plugin action invoke thejiajun.autoname.preview
herdr plugin action invoke thejiajun.autoname.rename-now
herdr plugin log list --plugin thejiajun.autoname
```

### Local plugin development

```bash
herdr plugin link /path/to/herdr-autoname
```

## Install as a standalone CLI

With `uv`:

```bash
uv tool install git+https://github.com/thejiajun/herdr-autoname.git
```

Upgrade or reinstall:

```bash
uv tool install --force git+https://github.com/thejiajun/herdr-autoname.git
```

With `pipx`:

```bash
pipx install git+https://github.com/thejiajun/herdr-autoname.git
```

## Providers

The default `auto` mode selects the first available provider. Use the Settings Popup for the plugin, or the following commands for the standalone CLI:

```bash
herdr-autoname provider
herdr-autoname provider auto
herdr-autoname provider pi
herdr-autoname provider codex
```

| Provider | Type | Credential |
| --- | --- | --- |
| `pika` | HTTP | `PIKA_CHAT_API_KEY` |
| `openrouter` | HTTP | `OPENROUTER_API_KEY`, or Pi OAuth when available |
| `deepseek` | HTTP | `DEEPSEEK_API_KEY` |
| `pi` | CLI | Any provider authenticated through `pi auth` |
| `claude` | CLI | Authenticated Claude CLI |
| `codex` | CLI | `codex login` |
| `gemini` | CLI | Gemini OAuth or `GEMINI_API_KEY` |
| `cursor` | CLI | Authenticated Cursor Agent CLI |
| `grok` | CLI | `grok login` or `XAI_API_KEY` |

HTTP providers use an OpenAI-compatible endpoint adapter. Local CLI providers run as ephemeral, tool-disabled naming requests and do not create resumable sessions. Conversation content is sent over stdin rather than process arguments.

## Standalone CLI usage

```bash
# Rename all current workspaces
herdr-autoname

# Preview without calling a model or changing labels
herdr-autoname --dry-run

# Inspect current sessions
herdr-autoname preview

# Refresh Sidebar configuration and metadata
herdr-autoname configure

# Rename one workspace
herdr-autoname --workspace w1W

# Manage provider and model
herdr-autoname provider
herdr-autoname provider pi
herdr-autoname model
herdr-autoname model google/gemini-3.7-flash

# Inspect request logs
herdr-autoname log
herdr-autoname log failed
herdr-autoname log 12
```

## How naming works

Before using a model, the tool applies deterministic rules:

- Known foreground tools such as `lazygit`, `vim`, `docker`, `pytest`, and `vite` are named directly.
- SSH panes are named from the remote host.
- A workspace containing only one idle shell is named from its project and idle state.

For agent panes, the model receives three compact signals per pane:

- `PREV` — the previous user request
- `ASK` — the current user request
- `DID` — the latest agent conclusion

Agent reasoning, tool calls, tool results, progress bars, injected hooks, compact summaries, and configuration dumps are filtered out.

## Reliability and logs

Model requests are recorded in a local SQLite database and capped at the latest 200 entries. The log includes provider, model, response status, finish reason, token usage, request input, and raw response.

Interrupted generations and repairable JSON endings are retried. Every returned workspace is validated against the live Herdr layout before names are applied. A workspace that remains malformed is skipped while valid workspaces continue.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py
uv build
```

## License

MIT
