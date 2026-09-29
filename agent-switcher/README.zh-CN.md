# Herdr Agent Switcher

[English](README.md)

在 Herdr 弹窗里用表格查看所有 Agent：按名称、实际工作目录或对话内容搜索，预览最新对话和总结，回车跳到对应 Pane。

## 功能

- 表格显示状态、与 Herdr 左侧一致的 Pane 名称和项目目录。
- 项目目录取 Agent **实际**所在的目录：Claude/Codex 中途切换项目后也会跟着变，不会停留在启动目录。
- 直接输入即可搜索，能命中名称、目录、最近对话内容和后台总结。
- 两种排序：按目录分组，或按最近活跃。
- 详情显示最新一轮请求与回复，以及由 Autoname 的模型生成的请求／进度／待办总结。
- Agent 每说完一轮话就自动更新总结，不需要额外的后台服务。
- macOS 上打开弹窗时自动切换到英文键盘，关闭后恢复原输入法，中文输入法下 J/K 也能用。

## 要求

- Herdr 0.9.0+
- Python 3.9+（系统自带 curses），macOS 或 Linux
- 后台总结需要同时安装 [Autoname](../autoname/)，并复用它的模型配置；不装也能正常搜索和跳转

## 安装

```sh
herdr plugin install thejiajun/jc-herdr/agent-switcher
```

在 `~/.config/herdr/config.toml` 里绑定快捷键。如果 `[keys]` 里的 `next_agent` / `previous_agent` 占用了同样的键，先把它们移除：

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

然后执行 `herdr server reload-config`。插件菜单里的 **Search agents** 也可以打开弹窗。

## 使用

| 按键 | 作用 |
|---|---|
| ⌃⌥J / ⌃⌥K | 打开弹窗，预选下一个／上一个 Agent |
| J / K 或 ↑ / ↓ | 上下选择 |
| Enter | 跳转到选中的 Agent |
| 直接输入 | 开始搜索（关键词以 j/k 开头时先按 `/`） |
| Esc | 有搜索时清空搜索，否则关闭弹窗 |
| ← / → | 切换「目录 / 最近」排序（会被记住） |
| Tab | 切换对话／终端画面 |
| PgUp / PgDn | 滚动详情 |

状态图标：▶ 运行中、! 待确认、✓ 已完成、○ 空闲、? 未知。状态来自 Herdr 自身的识别，不由模型判断。

## 工作原理

- **项目目录**：Claude 会话每一行都记录了 `cwd`，Codex 每轮都会写 `turn_context.cwd`，插件取最新的一条。读不到时退回 Herdr 的 `foreground_cwd` / `cwd`。
- **最新对话**：直接读取 Claude、Codex、Pi、OpenCode 的原生会话文件，提取最近一次用户请求和其后的助手回复，不读取推理过程和工具调用。
- **对话总结**：Agent 状态变为已完成／空闲／待确认时，把该 Agent 最近 6 条有效消息交给 Autoname 当前配置的模型，压缩成请求、进度、待办。对话没有变化就不调用模型，同一 Agent 至少间隔 2 分钟。
- **终端画面**：单独视图，显示 Pane 当前可见的原始内容。

## 隐私

- 只把最近几条消息发送给模型，不发送完整历史，费用和额度计入 Autoname 配置的服务。
- 缓存（摘录与总结）保存在 `$HERDR_PLUGIN_STATE_DIR/<socket-hash>/context.json`，目录权限 0700、文件权限 0600，原子写入。
- 输入法切换只读取和选择系统输入源，不注入任何按键。

## 限制

- 只列出本机 Herdr 的 Agent。远程机器（`herdr --machine`）上的 Agent 暂不显示。
- 部分 Emoji（如 🎙）在终端里的宽度不一致，表格里会替换为外观相近的双宽 Emoji 以保持对齐。

## 开发与诊断

```sh
herdr plugin link "$PWD"
python3 -m unittest discover -s tests
python3 switcher.py --check
herdr plugin action invoke thejiajun.agent-switcher.diagnostics
herdr plugin log list --plugin thejiajun.agent-switcher --limit 10
```

移除：`herdr plugin uninstall thejiajun.agent-switcher`，并删除对应快捷键。
