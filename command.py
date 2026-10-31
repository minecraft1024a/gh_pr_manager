"""GitHub PR 管理命令。"""

from __future__ import annotations

import asyncio
import base64
import hashlib

from src.app.plugin_system.api import storage_api, stream_api
from src.app.plugin_system.api.send_api import send_image, send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

from .diff_renderer import screenshot_diff
from .github_api import (
    GitHubAPIError,
    GitHubClient,
    MergeBlockedError,
    parse_pr_ref,
)


_HELP = """/gh 用法：
  /gh token set <gh_token>                 在私聊设置个人 GitHub Token
  /gh token clear                         删除当前平台和用户 ID 的 Token
  /gh list <owner/repo> [open|closed|all]  列出 PR
  /gh view <owner/repo#PR> [文件路径]       查看 PR 并发送 diff 截图
  /gh approve <owner/repo#PR>              提交 APPROVE 审批
  /gh close <owner/repo#PR>                关闭 PR
  /gh merge <owner/repo#PR> [merge|squash|rebase] 检查规则并尝试合并

PR 示例：/gh view octocat/Hello-World#42"""


class GitHubPRCommand(BaseCommand):
    """用户使用各自 GitHub Token 管理 PR 的命令。"""

    name: str = "gh"
    description: str = "查看、审批和按仓库规则合并 GitHub Pull Request"
    permission_level: PermissionLevel = PermissionLevel.USER

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """委托 Neo-MoFox 基类执行路由。"""

        return await super().execute(message_text)

    async def _reply(self, text: str) -> None:
        await send_text(text, stream_id=self.stream_id)

    def _identity(self) -> tuple[str, str]:
        """取得当前调用者的平台和平台用户 ID。"""

        if self._message is None:
            raise RuntimeError("无法读取当前消息的用户身份。")
        platform = str(self._message.platform or "").strip().lower()
        user_id = str(self._message.sender_id or "").strip()
        if not platform or not user_id:
            raise RuntimeError("无法读取当前平台或用户 ID，不能访问个人 Token。")
        return platform, user_id

    def _credential_name(self) -> tuple[str, str, str]:
        platform, user_id = self._identity()
        identity_hash = hashlib.sha256(
            f"{platform}\0{user_id}".encode("utf-8")
        ).hexdigest()
        return platform, user_id, f"token_{identity_hash}"

    @staticmethod
    async def _remove_legacy_shared_token() -> None:
        """删除旧版本使用的共享凭据，避免继续被多个用户共用。"""

        await storage_api.delete_json("gh_pr_manager", "credentials")

    async def _load_token(self) -> str | None:
        await self._remove_legacy_shared_token()
        platform, user_id, credential_name = self._credential_name()
        data = await storage_api.load_json("gh_pr_manager", credential_name)
        if not data or data.get("platform") != platform or str(data.get("user_id", "")) != user_id:
            return None
        token = data.get("gh_token") if data else None
        return str(token).strip() if token else None

    async def _client(self) -> GitHubClient:
        token = await self._load_token()
        if not token:
            raise RuntimeError("尚未设置 gh_token。请在私聊中执行 /gh token set <gh_token>。")
        return GitHubClient(token)

    @classmethod
    def match(cls, parts: list[str]) -> int:
        if parts and parts[0].lower() in {"gh", "github"}:
            return 1
        return 0

    @cmd_route()
    async def handle_help(self) -> tuple[bool, str]:
        await self._reply(_HELP)
        return True, "help"

    @cmd_route("help")
    async def handle_help_subcommand(self) -> tuple[bool, str]:
        return await self.handle_help()

    @cmd_route("token", "set")
    async def handle_set_token(self, token: str) -> tuple[bool, str]:
        """验证并安全保存 GitHub Token；绝不回显 Token。"""

        stream_info = await stream_api.get_stream_info(self.stream_id)
        chat_type = (stream_info or {}).get("chat_type")
        if chat_type != "private":
            message = "为避免凭据出现在群聊记录中，请在私聊中设置 gh_token。"
            await self._reply(message)
            return False, message

        normalized = token.strip()
        if len(normalized) < 20 or any(char.isspace() for char in normalized):
            message = "Token 格式无效，未保存。"
            await self._reply(message)
            return False, message
        try:
            platform, user_id, credential_name = self._credential_name()
            user = await GitHubClient(normalized).validate_token()
            await storage_api.save_json(
                "gh_pr_manager",
                credential_name,
                {"gh_token": normalized, "platform": platform, "user_id": user_id},
            )
        except GitHubAPIError as exc:
            message = f"GitHub Token 验证失败：{exc}。未保存。"
            await self._reply(message)
            return False, message
        except Exception as exc:
            message = f"保存 Token 失败：{exc}"
            await self._reply(message)
            return False, message
        message = (
            f"已验证并为 {platform}:{user_id} 保存个人 Token "
            f"（GitHub 用户：{user}）。Token 不会在回复中显示。"
        )
        await self._reply(message)
        return True, "token saved"

    @cmd_route("token", "clear")
    async def handle_clear_token(self) -> tuple[bool, str]:
        try:
            await self._remove_legacy_shared_token()
            _, _, credential_name = self._credential_name()
            removed = await storage_api.delete_json("gh_pr_manager", credential_name)
        except RuntimeError as exc:
            await self._reply(str(exc))
            return False, str(exc)
        message = "已删除你当前平台和用户 ID 对应的 GitHub Token。" if removed else "当前用户还没有保存 GitHub Token。"
        await self._reply(message)
        return True, "token cleared"

    @cmd_route("list")
    async def handle_list(self, repository: str, state: str = "open") -> tuple[bool, str]:
        try:
            owner, repo = repository.split("/", 1)
            if not owner or not repo or state not in {"open", "closed", "all"}:
                raise ValueError
            client = await self._client()
            pulls = await client.list_pulls(owner, repo, state)
        except ValueError:
            message = "格式：/gh list owner/repo [open|closed|all]"
            await self._reply(message)
            return False, message
        except Exception as exc:
            message = f"获取 PR 列表失败：{exc}"
            await self._reply(message)
            return False, message

        if not pulls:
            message = f"{owner}/{repo} 没有符合条件的 PR（state={state}）。"
        else:
            lines = [f"{owner}/{repo} 的 PR（显示最多 20 条）："]
            for pull in pulls:
                icon = "🟢" if pull.get("state") == "open" else "⚪"
                lines.append(
                    f"{icon} #{pull.get('number')} {pull.get('title', '')} "
                    f"— {((pull.get('user') or {}).get('login')) or 'unknown'}"
                )
            message = "\n".join(lines)
        await self._reply(message)
        return True, message

    @cmd_route("view")
    async def handle_view(self, pr_ref: str, file_path: str = "") -> tuple[bool, str]:
        try:
            ref = parse_pr_ref(pr_ref)
            client = await self._client()
            pull, files = await asyncio.gather(
                client.get_pull(ref), client.list_pull_files(ref)
            )
            screenshots = await screenshot_diff(
                repository=f"{ref.owner}/{ref.repo}",
                pull=pull,
                files=files,
                file_filter=file_path,
            )
        except Exception as exc:
            message = f"读取或截图 PR 失败：{exc}"
            await self._reply(message)
            return False, message

        overview = (
            f"{ref.owner}/{ref.repo}#{ref.number} · {pull.get('title', '')}\n"
            f"{pull.get('state', '')} · {len(files)} 个文件 · "
            f"+{pull.get('additions', 0)} / -{pull.get('deletions', 0)}\n"
            f"{pull.get('html_url', '')}"
        )
        await self._reply(overview)
        sent = 0
        for label, image_bytes in screenshots:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            if await send_image(
                encoded,
                stream_id=self.stream_id,
                processed_plain_text=f"{ref.owner}/{ref.repo}#{ref.number} diff：{label}",
            ):
                sent += 1
        message = f"已发送 {sent}/{len(screenshots)} 张无头浏览器 diff 截图。"
        if sent != len(screenshots):
            message += "部分图片未能通过当前平台发送。"
        await self._reply(message)
        return sent == len(screenshots), message

    @cmd_route("approve")
    async def handle_approve(self, pr_ref: str) -> tuple[bool, str]:
        try:
            ref = parse_pr_ref(pr_ref)
            client = await self._client()
            pull = await client.get_pull(ref)
            if pull.get("state") != "open" or pull.get("merged"):
                raise RuntimeError("只能审批仍处于打开状态的 PR。")
            result = await client.approve(ref)
        except Exception as exc:
            message = f"审批 PR 失败：{exc}"
            await self._reply(message)
            return False, message
        message = (
            f"已提交 {ref.owner}/{ref.repo}#{ref.number} 的 APPROVE 审批"
            f"（review id: {result.get('id', '已提交')}）。"
        )
        await self._reply(message)
        return True, message

    @cmd_route("close")
    async def handle_close(self, pr_ref: str) -> tuple[bool, str]:
        try:
            ref = parse_pr_ref(pr_ref)
            client = await self._client()
            pull = await client.get_pull(ref)
            if pull.get("merged"):
                raise RuntimeError("PR 已合并，不能关闭。")
            if pull.get("state") != "open":
                raise RuntimeError("PR 已经关闭。")
            result = await client.close_pull(ref)
        except Exception as exc:
            message = f"关闭 PR 失败：{exc}"
            await self._reply(message)
            return False, message
        message = f"已关闭 {ref.owner}/{ref.repo}#{ref.number}。\n{result.get('html_url', '')}"
        await self._reply(message)
        return True, message

    @cmd_route("merge")
    async def handle_merge(self, pr_ref: str, method: str = "merge") -> tuple[bool, str]:
        method = method.lower()
        if method not in {"merge", "squash", "rebase"}:
            message = "合并方式只能是 merge、squash 或 rebase。"
            await self._reply(message)
            return False, message
        try:
            ref = parse_pr_ref(pr_ref)
            client = await self._client()
            pull = await client.get_pull(ref)
            problems = await client.check_merge_requirements(ref, pull, method)
            if problems:
                message = "检查未通过，未执行合并：\n- " + "\n- ".join(problems)
                await self._reply(message)
                return False, message
            result = await client.merge(ref, method)
        except GitHubAPIError as exc:
            message = f"GitHub 拒绝合并（未绕过仓库规则）：{exc}"
            await self._reply(message)
            return False, message
        except (MergeBlockedError, ValueError, RuntimeError) as exc:
            message = f"检查未通过，未执行合并：{exc}"
            await self._reply(message)
            return False, message
        except Exception as exc:
            message = f"合并前检查失败，未执行合并：{exc}"
            await self._reply(message)
            return False, message

        message = (
            f"已合并 {ref.owner}/{ref.repo}#{ref.number}，方式：{method}。\n"
            f"{result.get('html_url', '')}"
        )
        await self._reply(message)
        return True, message
