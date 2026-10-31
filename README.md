# GitHub PR 管理（Neo-MoFox）

这是一个通过 GitHub REST API 管理 Pull Request 的 Neo-MoFox 插件。每位用户按平台和用户 ID 使用自己的 GitHub Token。

> 本插件为 AI 编程生成；不建议参考其代码作为示例。使用前请自行审阅并在自己的环境中验证。

## 安装

将插件目录放入 Neo-MoFox 的 `plugins/gh_pr_manager`，重启或重新加载插件。插件依赖 Playwright；在 Neo-MoFox 使用的 Python 环境中安装依赖并安装 Chromium：

```bash
python -m pip install 'playwright>=1.40,<2'
python -m playwright install chromium
```

`manifest.json` 声明了 Playwright Python 依赖。Chromium 浏览器还需单独安装一次。

## 设置 Token

在**主人私聊**中执行：

```text
/gh token set <gh_token>
```

插件会先调用 GitHub `/user` 验证 Token，然后按平台和用户 ID 分开存入 `data/json_storage/gh_pr_manager/`。不同用户或不同平台的 Token 不会共用。命令回复和日志不会回显 Token。Token 命令本身仍会作为原始聊天消息存在，因此请在私聊中设置；插件也会拒绝在群聊中设置 Token。

从 v1.0.x 升级后，旧版共享 Token 不会迁移；首次使用新版时会清除旧共享凭据文件。每位用户需要重新设置自己的 Token。

清除凭据：

```text
/gh token clear
```

建议使用只授权目标仓库的 fine-grained token。至少需要读取仓库与 PR 的权限；审批需要 Pull requests: write；合并需要 Contents: write；检查状态需要 Checks 和 Commit statuses: read。若要读取经典分支保护配置，Token 还需要相应的分支保护读取权限。GitHub 仍会对实际审批和合并操作执行服务端权限及规则校验。

## 命令

```text
/gh                                      显示帮助
/gh list <owner/repo> [open|closed|all]  列出最多 20 个 PR
/gh view <owner/repo#PR> [文件路径]       截图并发送 PR diff
/gh approve <owner/repo#PR>              提交 APPROVE review
/gh close <owner/repo#PR>                关闭尚未合并的 PR
/gh merge <owner/repo#PR> [方式]           检查规则后合并
```

PR 标识支持 `octocat/Hello-World#42` 或完整的 GitHub PR URL。`view` 使用无头 Chromium 把 GitHub API 返回的 diff 渲染成图片，按文件和页发送；可以给出文件路径来只查看单个文件。

`merge` 会读取经典分支保护和活动 Rulesets，检查 PR 状态、Draft/冲突/落后状态、审批数、审批者是否具有写入权限、最新提交审批要求及必需状态检查。若目标分支要求 Merge Queue，插件不会直接合并。通过本地预检查后，仍由 GitHub 的合并 API 最终校验仓库的全部保护规则；插件不请求或使用任何管理员绕过选项。规则或权限读取失败时会停止，不会尝试合并。

## 令牌范围

- 查看 PR：Metadata: read、Pull requests: read
- 审批：Pull requests: write
- 合并：Contents: write
- 检查状态与审阅者权限：Checks: read、Commit statuses: read、必要的 Administration: read

具体 Token 权限会因公共/私有仓库、组织策略和 GitHub Token 类型而不同；以 GitHub 返回的权限错误为准。
