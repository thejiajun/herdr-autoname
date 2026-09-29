# jc-herdr-plugins

[English](README.md)

[@thejiajun](https://github.com/thejiajun) 的 Herdr 插件集合。每个子目录是一个可以单独安装的插件。

| 插件 | 用途 | 安装 |
|---|---|---|
| [autoname](autoname/) | 根据每个 Agent 最近的对话，自动命名 Herdr 的 Workspace、Tab 和 Pane。 | `herdr plugin install thejiajun/jc-herdr-plugins/autoname` |
| [agent-switcher](agent-switcher/) | 弹窗按名称、工作目录或对话内容搜索 Agent，预览后跳转到对应 Pane。 | `herdr plugin install thejiajun/jc-herdr-plugins/agent-switcher` |

Agent Switcher 的后台总结复用 Autoname 的 Provider 配置，两个都装才有总结。

## 从 `herdr-autoname` 迁移

本仓库原名 `thejiajun/herdr-autoname`，插件在根目录。用旧命令安装过的，需要重装一次：

```sh
herdr plugin uninstall thejiajun.autoname
herdr plugin install thejiajun/jc-herdr-plugins/autoname
```

命令行版改为从子目录安装：

```sh
uv tool install --force "git+https://github.com/thejiajun/jc-herdr-plugins.git#subdirectory=autoname"
```

## 开发

```sh
herdr plugin link "$PWD/autoname"
herdr plugin link "$PWD/agent-switcher"
(cd autoname && python3 -m unittest discover -s tests)
(cd agent-switcher && python3 -m unittest discover -s tests)
```
