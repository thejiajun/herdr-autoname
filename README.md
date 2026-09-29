# jc-herdr

[简体中文](README.zh-CN.md)

Herdr plugins by [@thejiajun](https://github.com/thejiajun). Each folder is a separately installable plugin.

| Plugin | What it does | Install |
|---|---|---|
| [autoname](autoname/) | Renames Herdr workspaces, tabs, and panes from each agent's recent conversations. | `herdr plugin install thejiajun/jc-herdr/autoname` |
| [agent-switcher](agent-switcher/) | Popup to search agents by name, working folder, or conversation, preview them, and jump to their pane. | `herdr plugin install thejiajun/jc-herdr/agent-switcher` |

Agent Switcher reuses Autoname's provider settings for its background summaries, so install both to get summaries.

## Moved from `herdr-autoname`

This repository was previously `thejiajun/herdr-autoname`, with the plugin at the root. If you installed it with the old command, reinstall once:

```sh
herdr plugin uninstall thejiajun.autoname
herdr plugin install thejiajun/jc-herdr/autoname
```

The standalone CLI now installs from the subfolder:

```sh
uv tool install --force "git+https://github.com/thejiajun/jc-herdr.git#subdirectory=autoname"
```

## Development

```sh
herdr plugin link "$PWD/autoname"
herdr plugin link "$PWD/agent-switcher"
(cd autoname && python3 -m unittest discover -s tests)
(cd agent-switcher && python3 -m unittest discover -s tests)
```
