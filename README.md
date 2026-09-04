# herdr-autoname

根据 Agent 最近的真实会话内容，自动更新 Herdr 的 Workspace、Tab 和 Pane 名称。

## 功能

- 读取 OpenCode、Claude Code、Codex 的原生 session
- 使用最近两次用户输入和最新结论判断当前任务
- 通过 Pika Chat API 或 DeepSeek API 批量生成中文短名称
- 在写入前校验 Workspace、Tab、Pane 数量和 Pane 显示宽度
- 自动配置 Herdr Sidebar 的标题、项目路径、Git 分支和状态
- `--dry-run` 无需 API Key，不调用模型、不修改名称
- 仅依赖 Python 标准库

## 前提

- Python 3.10+
- 已安装并运行 Herdr，且 `herdr` 命令可用
- 默认命名需要 `PIKA_CHAT_API_KEY`；也支持 `DEEPSEEK_API_KEY`

## 安装

推荐使用 `uv`：

```bash
uv tool install git+https://github.com/thejiajun/herdr-autoname.git
```

也可以使用 `pipx`：

```bash
pipx install git+https://github.com/thejiajun/herdr-autoname.git
```

升级：

```bash
uv tool install --force git+https://github.com/thejiajun/herdr-autoname.git
```

## 配置 API Key

创建 `~/.config/herdr/autoname.env`：

```dotenv
PIKA_CHAT_API_KEY=your_key
```

也可以使用当前目录的 `.env`，或直接导出环境变量。密钥不会出现在命令参数和输出中。

## 使用

```bash
# 调用模型并更新全部名称
herdr-autoname

# 不调用模型、不修改名称，只预览完整输出格式
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

# 临时使用其他模型
herdr-autoname --model anthropic/claude-haiku-4.5

# 查看完整帮助
herdr-autoname --help
```

默认模型是 `google/gemini-3.7-flash`。模型设置保存在
`~/.config/herdr/autoname.env`。

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
