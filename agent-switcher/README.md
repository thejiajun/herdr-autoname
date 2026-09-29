# Herdr Agent Switcher

Herdr Popup 中的 Agent 表格、原生对话摘录和后台总结。

## 使用

- **Control+Option+J / K**：打开弹窗并预选下一个 / 上一个 Agent。弹窗打开后保持显示，松开按键不会关闭。
- **J / K** 或 ↑↓：上下移动；**Enter** 跳转；**Esc** 关闭。
- **直接输入**关键词即开始搜索（j/k 开头的关键词先按 `/`）。可以命中窗口名、实际工作目录、最近对话内容和后台总结。搜索中 ↑↓ 移动，Esc 清空搜索，退格删空后回到 J/K 导航。
- **Tab** 切换对话／终端视图；PgUp/PgDn 滚动详情。
- 弹窗打开时自动切换到英文键盘（macOS 本机），关闭后恢复原输入法，以免中文输入法吞掉 J/K。要用中文搜索，在弹窗内手动切换输入法即可。

## 信息与布局

表格：状态、**与 Herdr 左侧一致的 pane 名称**、项目目录。
项目目录取 Agent 自己最新记录的工作目录（Claude 会话每行的 `cwd`、Codex 最新 `turn_context.cwd`），因为 pane 的 `cwd` 停留在启动目录；读不到时退回 `foreground_cwd`、`cwd`。

- **脚本解析**：直接读取 Codex/Claude/Pi/OpenCode 的原生对话，提取最新用户请求及其后的助手回复。不读取分析通道或工具调用；没有回复就明确显示尚无回复。
- **对话总结**：最近 6 条有效用户／助手消息交给 Autoname 已配置的 Provider 压缩成请求、进度和待办。标注更新时间；对话变化后旧总结仍可看，并标记待更新。
- **终端画面**：单独视图，保留原始可见终端内容。

宽窗口采用左右布局，窄窗口上下布局；最低 32 列 × 16 行。
状态：▶ 运行中、! 待确认、✓ 已完成、○ 空闲、? 未知。
实时状态使用 Herdr 的识别结果，不由总结模型判断。
列表有两种排序，按 ←→ 切换，选择会被记住：**目录**（默认）按实际工作目录分组，同目录内最近活跃在前；**最近**按最后一次对话时间（会话文件修改时间）从新到旧。列表刷新保持当前选中的 pane。

## 安装

需要 Herdr 0.9.0+、Python 3.9+（curses）、macOS/Linux。
后台总结复用已安装的 `thejiajun.autoname` 插件（同仓库 `autoname/`）的 Provider 适配器和配置。
不调用 Autoname 的重命名操作，不更改它的配置。

```sh
herdr plugin install thejiajun/jc-herdr/agent-switcher
# 本地开发：herdr plugin link /absolute/path/to/jc-herdr/agent-switcher
```

从 `[keys]` 移除占用 J/K 的 `next_agent` / `previous_agent`，添加：

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

```sh
herdr server reload-config
# macOS: install one interval job for this Herdr socket/session
python3 install_background.py
```

定时任务每 120 秒检查一次，每批最多 8 个有变化的会话；无变化不调用模型。
一个文件锁防止并发重复生成。已有积压按上次尝试时间公平分批处理。
定时任务不启动或重启 Herdr；不使用 KeepAlive。插件运行和缓存按 socket/session 隔离。
模型调用会使用 Autoname 当前配置的服务及额度；只传最近消息，不传完整历史。
本地缓存包含请求／回复摘录及总结，使用 0700 目录和 0600 原子写入文件。
缓存位于 `$HERDR_PLUGIN_STATE_DIR/<socket-hash>/context.json`。

查看任务：`launchctl list | rg herdr-agent-switcher`。
停止后台：对 `~/Library/LaunchAgents/com.jiajun.herdr-agent-switcher.<socket-hash>.plist`
运行 `launchctl bootout gui/$(id -u) <plist-path>`，然后移除该 plist。
移除插件：`herdr plugin unlink thejiajun.agent-switcher`，并移除对应快捷键。

## 验证与诊断

```sh
python3 -m unittest discover -s tests
python3 switcher.py --check
herdr plugin action invoke thejiajun.agent-switcher.diagnostics
herdr plugin log list --plugin thejiajun.agent-switcher --limit 10
```

`popup-status.json` 记录弹窗进程和打开方向；`last-jump.json` 记录最后跳转目标与 API 结果，不记录按键内容。
