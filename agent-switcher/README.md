# Herdr Agent Switcher

[简体中文](README.zh-CN.md)

A Herdr popup that lists every agent in a table. Search by name, real working folder, or conversation content, preview the latest exchange and a summary, and press Enter to jump to its pane.

## Highlights

- Table of status, the pane name shown in Herdr's sidebar, and the project folder.
- The folder is where the agent **actually** works: it follows Claude and Codex when they move to another project instead of staying at the launch directory.
- Type to search across names, folders, recent conversation text, and summaries.
- Two sort orders: grouped by folder, or by most recent activity.
- Details show the latest request and reply, plus a request / progress / next-step summary written by Autoname's model.
- Summaries refresh whenever an agent finishes a turn. No background service to install.
- On macOS the popup switches to an ASCII keyboard while open and restores your input method afterwards, so J/K work under Chinese or Japanese IMEs.

## Requirements

- Herdr 0.9.0+
- Python 3.9+ with curses, on macOS or Linux
- Summaries need [Autoname](../autoname/) installed and reuse its provider settings. Search and jump work without it.

## Install

```sh
herdr plugin install thejiajun/jc-herdr/agent-switcher
```

Bind keys in `~/.config/herdr/config.toml`. If `next_agent` / `previous_agent` under `[keys]` use the same keys, remove them first:

```toml
[[keys.command]]
key = "ctrl+alt+j"
type = "plugin_action"
command = "thejiajun.agent-switcher.next"
description = "Open agent switcher (next)"

[[keys.command]]
key = "ctrl+alt+k"
type = "plugin_action"
command = "thejiajun.agent-switcher.previous"
description = "Open agent switcher (previous)"
```

Then run `herdr server reload-config`. The **Search agents** action in the plugin menu opens the popup too.

## Usage

| Key | Action |
|---|---|
| ⌃⌥J / ⌃⌥K | Open the popup with the next / previous agent selected |
| J / K or ↑ / ↓ | Move the selection |
| Enter | Jump to the selected agent |
| Any other key | Start searching (press `/` first for a query starting with j or k) |
| Esc | Clear the search, or close when there is none |
| ← / → | Switch between folder and recent sort (remembered) |
| Tab | Toggle conversation / terminal view |
| PgUp / PgDn | Scroll the details |

Status icons: ▶ working, ! needs input, ✓ done, ○ idle, ? unknown. Status comes from Herdr's own detection, never from the model.

## How it works

- **Folder**: Claude stamps `cwd` on every session row and Codex writes `turn_context.cwd` each turn; the plugin takes the latest one, falling back to Herdr's `foreground_cwd` / `cwd`.
- **Latest exchange**: read directly from Claude, Codex, Pi, and OpenCode session files. Reasoning and tool calls are skipped.
- **Summary**: when an agent turns done, idle, or blocked, its last six useful messages go to Autoname's configured model. Unchanged conversations are never resent, and each agent is summarized at most once every two minutes.
- **Terminal view**: the pane's raw visible screen.

## Privacy

- Only the last few messages are sent to the model, never full history. Usage counts against the provider configured in Autoname.
- The cache of excerpts and summaries lives in `$HERDR_PLUGIN_STATE_DIR/<socket-hash>/context.json` (directory 0700, file 0600, atomic writes).
- Input-method switching only reads and selects system input sources; it never injects keystrokes.

## Limitations

- Only agents on the local Herdr server are listed. Agents on remote machines (`herdr --machine`) are not shown yet.
- Some emoji (such as 🎙) have inconsistent terminal widths; the table swaps them for a similar double-width emoji to keep columns aligned.

## Development and diagnostics

```sh
herdr plugin link "$PWD"
python3 -m unittest discover -s tests
python3 switcher.py --check
herdr plugin action invoke thejiajun.agent-switcher.diagnostics
herdr plugin log list --plugin thejiajun.agent-switcher --limit 10
```

Remove with `herdr plugin uninstall thejiajun.agent-switcher` and delete the key bindings.
