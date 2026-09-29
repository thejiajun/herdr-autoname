# herdr-autoname

[English](README.md)

根据每个编程 Agent 最近的真实会话，自动更新 Herdr 的 Workspace、Tab 和 Pane 名称。

## 核心功能

- 原生 Herdr Plugin，带交互式设置 Popup。
- 新的 Agent 会话完成第 1 条对话就自动命名，之后每个 Pane 累计 N 条对话刷新一次，默认 N=3。
- 读取 OpenCode、Claude Code 和 Codex 原生 Session。
- 支持 Pika、OpenRouter、DeepSeek、Pi、Claude、Codex、Gemini、Cursor 和 Grok。
- 识别 SSH、常见前台工具和空闲 Shell，这些情况不调用模型。
- 写入前校验 Workspace、Tab、Pane 数量和名称宽度。
- 单个 Workspace 返回异常时跳过该项，不阻塞其他 Workspace。
- 自动按项目分组，并向 Herdr Sidebar 写入项目、Git 和远程主机信息。
- 仅依赖 Python 标准库。

## 前提

- Herdr 0.9.0+
- Python 3.10+
- 至少一个可用 Provider：API Key 或已经登录的本地 Agent CLI

## 作为 Herdr Plugin 安装

```bash
herdr plugin install thejiajun/jc-herdr-plugins/autoname
```

打开设置面板：

```bash
herdr plugin action invoke thejiajun.autoname.open-settings
```

设置 Popup 可以选择 Provider 和模型、修改每 N 条对话触发、开启或关闭自动命名、预览 Sidebar，以及立即执行命名。

插件包含三个 Action：

- `open-settings`：打开交互式设置 Popup
- `preview`：只读预览，不调用模型、不修改名称
- `rename-now`：立即执行重命名

自动触发监听 `pane.agent_status_changed`。新观察到的会话在第 1 条用户对话完成后立即命名，之后每累计 N 条再刷新；安装前的历史对话不计入。

查看运行日志：

```bash
herdr plugin log list --plugin thejiajun.autoname
```

Plugin 和独立 CLI 共用同一个设置文件 `~/.config/herdr/autoname.env`：在 Popup 里改，或用 `herdr-autoname provider` / `model` 改，两边同时生效。0.16.0 版 Plugin 存在 Herdr 插件配置目录里的设置，会在下次运行时自动合并到这个文件。

对话计数、Provider 缓存和请求日志统一放在 `~/.local/share/herdr-autoname/`，所以 `herdr-autoname log` 也能看到 Plugin 发出的请求。这些都不写入仓库。

## 作为独立 CLI 安装

```bash
uv tool install "git+https://github.com/thejiajun/jc-herdr-plugins.git#subdirectory=autoname"
```

覆盖升级：

```bash
uv tool install --force "git+https://github.com/thejiajun/jc-herdr-plugins.git#subdirectory=autoname"
```

也可以使用：

```bash
pipx install "git+https://github.com/thejiajun/jc-herdr-plugins.git#subdirectory=autoname"
```

## Provider

默认 `auto` 会选择第一个可用 Provider。Plugin 建议通过设置 Popup 修改；独立 CLI 使用：

```bash
herdr-autoname provider
herdr-autoname provider pi
herdr-autoname provider codex
```

| Provider | 类型 | 凭证 |
| --- | --- | --- |
| `pika` | HTTP | `PIKA_CHAT_API_KEY` |
| `openrouter` | HTTP | `OPENROUTER_API_KEY`，或可用的 Pi OAuth |
| `deepseek` | HTTP | `DEEPSEEK_API_KEY` |
| `pi` | CLI | `pi auth` 中任一可用 Provider |
| `claude` | CLI | 已登录 Claude CLI |
| `codex` | CLI | `codex login` |
| `gemini` | CLI | Gemini OAuth 或 `GEMINI_API_KEY` |
| `cursor` | CLI | 已登录 Cursor Agent CLI |
| `grok` | CLI | `grok login` 或 `XAI_API_KEY` |

## 常用命令

```bash
# 重命名全部 Workspace
herdr-autoname

# 不调用模型、不修改名称
herdr-autoname --dry-run

# 查看当前 Session
herdr-autoname preview

# 仅刷新 Sidebar 配置和 metadata
herdr-autoname configure

# 只处理指定 Workspace
herdr-autoname --workspace w1W

# Provider、模型与日志
herdr-autoname provider pi
herdr-autoname model google/gemini-3.7-flash
herdr-autoname log failed
```

## 命名逻辑

调用模型前先应用规则：常见前台工具直接命名；SSH Pane 按远程主机命名；只有一个空闲 Shell 时按项目和空闲状态命名。

Agent Pane 只向模型发送三类压缩信号：上一句用户诉求 `PREV`、当前诉求 `ASK` 和最新结论 `DID`。思考过程、工具调用、工具结果、进度条、Hook 消息和 compact 摘要不会进入输入。

每次模型请求写入本地 SQLite，最多保留最近 200 条。生成中断和可修复 JSON 会自动重试；仍然不合法的 Workspace 保留原名，其他合法结果继续写入。

## 开发

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py
uv build
```

## License

MIT
