# herdr-autoname

根据 Agent 最近的真实会话内容，自动更新 Herdr 的 Workspace、Tab 和 Pane 名称。

## 功能

- 读取 OpenCode、Claude Code、Codex 的原生 session
- 使用最近两次用户输入和最新结论判断当前任务
- 通过 Pika Chat API、DeepSeek API、Codex CLI 或 Claude CLI 生成中文短名称
- 在写入前校验 Workspace、Tab、Pane 数量和 Pane 显示宽度
- 自动配置 Herdr Sidebar 的标题、项目路径、Git 分支和状态
- 将同一项目的 Workspace 连续排列；SSH 会话按远程主机分组
- SSH 会话显示 `SSH · 主机名`，不沿用切换前的本地项目目录
- `--dry-run` 无需 API Key，不调用模型、不修改名称；宽终端用粗体双栏预览 Herdr Sidebar，窄终端自动改为上下排列
- 仅依赖 Python 标准库

## 前提

- Python 3.10+
- 已安装并运行 Herdr，且 `herdr` 命令可用
- 可使用 `PIKA_CHAT_API_KEY`、`DEEPSEEK_API_KEY`，或本机已登录的 Codex/Claude CLI

## 安装

推荐使用 `uv`：

```bash
uv tool install git+https://github.com/thejiajun/herdr-autoname.git
```

### 在其他电脑安装

不需要克隆仓库。在任何目录打开终端，确认已安装 `uv` 和 Herdr CLI 后运行：

```bash
uv tool install --force git+https://github.com/thejiajun/herdr-autoname.git
```

这条命令同时适用于首次安装和覆盖升级。安装后可检查 Provider：

```bash
herdr-autoname provider
```

也可以使用 `pipx`：

```bash
pipx install git+https://github.com/thejiajun/herdr-autoname.git
```

## 配置 Provider

默认使用 `auto`，按以下顺序选择第一个可用通道：

1. `pika`：检测到 `PIKA_CHAT_API_KEY`
2. `deepseek`：检测到 `DEEPSEEK_API_KEY`
3. `codex`：检测到本机 `codex` 命令
4. `claude`：检测到本机 `claude` 命令

查看当前选择和可用状态：

```bash
herdr-autoname provider
```

### 可用 Provider

实测 23 个 Workspace 一轮命名：

| Provider | 类型 | 耗时 | 成本 | 凭证 |
|---|---|---|---|---|
| pika | HTTP | 5.9s | $0.0057 | `PIKA_CHAT_API_KEY` |
| openrouter | HTTP | 6.8s | $0.0058 | `OPENROUTER_API_KEY`，没有则借用 pi 的 OAuth token |
| deepseek | HTTP | — | — | `DEEPSEEK_API_KEY` |
| pi | CLI | 5.3s | — | `pi auth`（任一 provider 就绪即可） |
| claude | CLI | 10.7s | $0.0185 | `claude` 已登录 |
| codex | CLI | — | — | `codex login` |
| gemini | CLI | 44.5s | — | `gemini` OAuth 或 `GEMINI_API_KEY` |
| cursor | CLI | 52.8s | — | `cursor-agent` 已登录 |

HTTP Provider 只需要 base URL、模型名和鉴权头，任何 OpenAI 兼容端点都能接进来。
Agent CLI 每次都要启动完整会话，所以只适合当没有 API Key 时的兜底；调用时统一关掉
工具、会话保存、skill、MCP 和扩展，并把系统提示直接替换成命名提示——Claude CLI
一轮的上下文因此从 28k 降到 8k token。Pane 对话一律走 stdin，不进进程列表。

### 凭证放哪

Pika / DeepSeek / OpenRouter 的 Key 写进 `~/.config/herdr/autoname.env`、当前目录的
`.env`，或导出为环境变量。密钥不会出现在命令参数和输出中。

## 使用

```bash
# 调用模型并更新全部名称
herdr-autoname

# 不调用模型、不修改名称，预览分组后的 Herdr Sidebar
herdr-autoname --dry-run

# 只查看当前会话状态
herdr-autoname preview

# 只更新 Sidebar 配置和 metadata
herdr-autoname configure

# 仅处理指定 Workspace
herdr-autoname --workspace w1W

# 查看和切换默认模型
herdr-autoname model
herdr-autoname model google/gemini-3.7-flash
herdr-autoname model reset

# 查看、自动选择或切换 Provider
herdr-autoname provider
herdr-autoname provider auto
herdr-autoname provider codex
herdr-autoname provider claude

# 临时使用其他模型
herdr-autoname --model anthropic/claude-haiku-4.5
herdr-autoname --provider codex
herdr-autoname --provider claude --model sonnet

# 查看最近 20 次模型请求（失败时用来定位原因）
herdr-autoname log
herdr-autoname log failed
herdr-autoname log 12

# 查看完整帮助
herdr-autoname --help
```

默认模型是 `google/gemini-3.7-flash`。模型设置保存在
`~/.config/herdr/autoname.env`。

首次执行真实命名时，CLI 会检测 API Key，以及本机 Codex/Claude CLI 是否安装并
登录，再按 `Pika Key → DeepSeek Key → Codex CLI → Claude CLI` 自动选择并持久
保存一个 Provider，同时在终端说明检测和选择结果。之后运行 `herdr-autoname provider`
会在交互式终端显示可选 Provider 菜单；在脚本或管道里则只输出状态，不等待输入。
`provider auto` 可恢复动态选择。

`provider <name>` 会将选择持久保存到同一配置文件。
Codex 使用 ephemeral、read-only、low-effort 无头请求；Claude 使用 safe mode、
Haiku、low-effort 和 JSON Schema，不会创建可恢复会话或调用工具。

## 会话信号提取

喂给模型的不是整段终端内容，而是每个 Pane 的三行信号：上一句用户诉求（`PREV`）、
当前用户诉求（`ASK`，完整保留）、Agent 最新一句回复（`DID`）。Agent 的思考过程、
工具调用和工具结果都不进入输入。

被过滤掉的「假用户消息」包括 hook 反馈、`<task-notification>`、slash command 包装、
compact 摘要、粘贴的表格边框和 `key=value` 配置输出——实测这类内容占原先「用户消息」
的 23%。长粘贴保留尾部，因为真正的诉求通常写在粘贴内容之后。没有 Agent 会话的
shell Pane 走终端截屏压缩，去掉进度条和分隔线后只保留有字的行。

实测 23 个 Workspace：输入从 4915 tokens 降到 3556，Pane 名称更贴近当前动作。

## 请求日志

每次模型请求都会写入 `~/.local/share/herdr-autoname/requests.db`（SQLite，只保留
最近 200 条），记录模型、HTTP 状态、finish_reason、token 数、完整的请求输入和原始
响应。命名失败时错误信息会直接给出记录 ID，用 `herdr-autoname log <ID>` 就能看到
模型当时到底返回了什么。

上游偶尔会返回 HTTP 200 但生成中断（`finish_reason` 不是 `stop`），也可能吐出少一个
右括号的 JSON。前者自动重试最多 3 次，后者会先尝试补齐括号再解析，补齐结果仍要通过
数量校验才会应用。

每次命名或运行 `herdr-autoname configure` 时，还会按当前项目稳定分组 Workspace。
同一项目保持连续，组内维持原顺序。SSH Pane 会根据终端标题识别远程主机，Sidebar
第二行改为 `SSH · 主机名`，不会继续显示切换前的本地目录和 Git 分支。
每个项目组只在第一个 Workspace 显示项目、分支和 Git 状态，组间在预览中留出空行。
Pane 名称使用“动词 + 对象”，不生成重复、含糊的“把……好”句式。

## 输出

运行前会展示：

- Workspace 名称
- 当前路径和 Git 状态
- Agent 类型和运行状态
- 最后一次用户输入

运行后会用表格展示新的 Workspace、Tab 和 Pane 名称，并给出请求耗时、token 和成本。

## 开发

```bash
python3 -m py_compile scripts/rename_workspaces.py
uv build
```

## License

MIT
