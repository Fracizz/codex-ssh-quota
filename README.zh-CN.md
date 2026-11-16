# Codex SSH Quota

[English](README.md) · MIT

在 Codex 桌面端查看 SSH 环境最近记录的额度。通过现有 SSH 连接读取会话中的额度快照，缓存 10 分钟，支持手动刷新和断线时显示缓存。Windows 桌面端本机使用 API Key 也可以查询远端的订阅额度。

面板显示来源主机、记录中的剩余比例、快照时间和重置时间。数据来自历史记录；切换账号或额度窗口重置后，旧快照可能不再代表当前账号状态。

## 安装到正确的执行主机

Python 3.10+，Codex CLI（也可以使用桌面版内置 CLI）。从桌面查询远端时还需要 OpenSSH Client、已确认的主机指纹和免密连接；远端需要 Python 3。

```powershell
git clone https://github.com/Fracizz/codex-ssh-quota.git
cd codex-ssh-quota
python install.py --ssh-host my-codex-server
```

`my-codex-server` 是已有的 SSH 配置别名。安装器会创建被 Git 忽略的 `.runtime` 运行目录，保存本机 Python 路径和 SSH 设置，再通过 Codex 插件 CLI 完成安装。源码里的清单不包含个人主机地址或解释器路径。保留仓库目录以便更新。

**如果会话本身运行在 SSH 主机上，也需要在该主机安装。** 仅在 Windows 安装不会把工具自动传给远端执行的会话。在 SSH 终端运行：

```bash
git clone https://github.com/Fracizz/codex-ssh-quota.git
cd codex-ssh-quota
python3 install.py --local
```

`--local` 表示在当前 SSH 执行主机直接读取快照。使用本地会话和 SSH 会话时，可以分别在桌面主机和远端安装同一个插件。

打开对应主机上的新会话并选择 **Codex SSH 额度**。客户端保留旧插件列表时，先结束当前工作，再重启桌面端。面板入口需要客户端支持 MCP Apps / OpenAI MCP Extensions；仅有文本工具能力的客户端也可以读取返回的结构化额度数据。

## 查询与维护

```powershell
python .runtime/plugin/server.py --probe
python install.py --ssh-host my-codex-server
codex plugin remove codex-ssh-quota@codex-ssh-quota
codex plugin marketplace remove codex-ssh-quota
```

更新时执行 `git pull --ff-only`，再重复原安装命令。数据源修改可使用安装参数，或复制 `settings.example.json` 为被 Git 忽略的 `settings.json`。`CODEX_QUOTA_SSH_HOST`、`CODEX_QUOTA_MODE` 可覆盖配置。

旧原型用户在新版安装验证通过后，可以移除 `codex-quota@codex-quota-local` 及 `codex-quota-local` marketplace。

## 读取范围与隐私

- 查找最近 14 天、最多 64 个会话文件；每个文件最多读取末尾 512 KiB，提取最新的 `token_count.rate_limits` 事件。
- 会话文件可能包含聊天内容，脚本仅返回额度字段、来源主机与记录时间。不会读取登录令牌，也不调用模型或实时额度接口。
- SSH 使用现有配置，开启 `BatchMode` 和严格主机指纹校验。连接错误时提示先在终端验证 SSH。
- 每 10 分钟检查一次，手动刷新绕过缓存；查询失败时显示上次缓存并标明状态。记录窗口已过期时保留原数值并提示。
- MCP 输入单条最多 1 MiB，额度结果最多 20 个分组 / 32 KiB。缓存存放在 `%LOCALAPPDATA%/CodexSSHQuota`（Windows）或 `~/.cache/CodexSSHQuota`（Linux）。

## 同类项目

| 项目 | 主要方向 |
| --- | --- |
| [CodexBar](https://github.com/steipete/CodexBar) | macOS 菜单栏中的多种 AI 工具额度 |
| [Codex Usage](https://github.com/jayhilwig/codexusage) | Windows/macOS 标题区域的额度伴随窗口 |
| [CodexCast](https://github.com/digitdash/codexcast) | macOS Codex 插件，额度、余额和剩余活跃时长预测 |
| [Usage Meter](https://github.com/zhaoshuoxp/agent-tools) | 本机 ChatGPT/Codex 账号的侧栏额度指示和详情面板 |

本项目专注于已有 SSH 环境中的历史额度快照，不需要在桌面端复制远端登录凭证。

## 验证

```bash
python3 -m unittest -q
python3 smoke_install.py
```

自动测试不连接真实 SSH 主机、不调用模型。`smoke_install.py` 需要 Codex CLI，在临时 `CODEX_HOME` 中验证实际插件安装及工具发现。CI 覆盖 Windows/Linux 和 Python 3.10、3.12、3.14。实际桌面面板渲染需要在支持相关扩展的客户端验收。

[会话面板规范](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#thread-entrypoint) · [桌面插件打包](https://developers.openai.com/plugins/deploy/desktop-only)
