"""GitHub REST API 封装与 PR 合并前置检查。"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
_PR_REF_RE = re.compile(
    r"^(?:https://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/pull/|#)([1-9][0-9]*)/?$",
    re.IGNORECASE,
)


class GitHubAPIError(RuntimeError):
    """GitHub API 返回错误。"""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class MergeBlockedError(RuntimeError):
    """PR 未满足可确认的合并前置要求。"""


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int

    @property
    def repo_path(self) -> str:
        return f"/repos/{quote(self.owner, safe='')}/{quote(self.repo, safe='')}"


def parse_pr_ref(value: str) -> PullRequestRef:
    """解析 owner/repo#123 或 GitHub PR URL。"""

    match = _PR_REF_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(
            "PR 格式应为 owner/repo#123，例如 octocat/Hello-World#42"
        )
    owner, repo, number = match.groups()
    return PullRequestRef(owner=owner, repo=repo, number=int(number))


class GitHubClient:
    """使用 Python 标准库访问 GitHub REST API。"""

    def __init__(self, token: str) -> None:
        self._token = token.strip()
        if not self._token:
            raise ValueError("gh_token 未设置")

    def _request_sync(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{API_ROOT}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "Neo-MoFox-gh-pr-manager",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=25) as response:
                body = response.read()
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                message = str(parsed.get("message", "GitHub API 请求失败"))
            except (json.JSONDecodeError, AttributeError):
                message = "GitHub API 请求失败"
            if self._token:
                message = message.replace(self._token, "[已隐藏]")
            raise GitHubAPIError(exc.code, f"HTTP {exc.code}: {message}") from None
        except (URLError, TimeoutError, OSError) as exc:
            # 不把 Request 或 headers 加入错误，避免异常上下文泄露授权头。
            reason = getattr(exc, "reason", None)
            if isinstance(reason, str) and len(reason) < 180:
                raise RuntimeError(f"连接 GitHub 失败：{reason}") from None
            raise RuntimeError("连接 GitHub 失败，请检查网络后重试") from None

    async def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        return await asyncio.to_thread(self._request_sync, method, path, payload)

    async def validate_token(self) -> str:
        user = await self.request("GET", "/user")
        return str(user.get("login", "GitHub 用户"))

    async def get_pull(self, ref: PullRequestRef) -> dict[str, Any]:
        return await self.request("GET", f"{ref.repo_path}/pulls/{ref.number}")

    async def list_pulls(
        self, owner: str, repo: str, state: str = "open"
    ) -> list[dict[str, Any]]:
        query = urlencode({"state": state, "per_page": 20, "page": 1})
        return await self.request(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls?{query}",
        )

    async def list_pull_files(self, ref: PullRequestRef) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for page in range(1, 4):
            query = urlencode({"per_page": 100, "page": page})
            batch = await self.request(
                "GET", f"{ref.repo_path}/pulls/{ref.number}/files?{query}"
            )
            if not isinstance(batch, list):
                break
            files.extend(batch)
            if len(batch) < 100:
                break
        return files

    async def list_reviews(self, ref: PullRequestRef) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        for page in range(1, 11):
            query = urlencode({"per_page": 100, "page": page})
            batch = await self.request(
                "GET", f"{ref.repo_path}/pulls/{ref.number}/reviews?{query}"
            )
            if not isinstance(batch, list):
                break
            reviews.extend(batch)
            if len(batch) < 100:
                break
        return reviews

    async def get_classic_protection(self, ref: PullRequestRef, branch: str) -> dict[str, Any] | None:
        path = (
            f"{ref.repo_path}/branches/{quote(branch, safe='')}/protection"
        )
        try:
            return await self.request("GET", path)
        except GitHubAPIError as exc:
            if exc.status == 404:
                return None
            raise

    async def get_branch_rules(
        self, ref: PullRequestRef, branch: str
    ) -> list[dict[str, Any]]:
        path = f"{ref.repo_path}/rules/branches/{quote(branch, safe='')}?per_page=100"
        result = await self.request("GET", path)
        return result if isinstance(result, list) else []

    async def check_named_statuses(
        self,
        ref: PullRequestRef,
        head_sha: str,
        required_contexts: set[str],
    ) -> list[str]:
        if not required_contexts:
            return []

        query = urlencode({"per_page": 100})
        statuses, check_data = await asyncio.gather(
            self.request("GET", f"{ref.repo_path}/commits/{quote(head_sha, safe='')}/status"),
            self.request(
                "GET",
                f"{ref.repo_path}/commits/{quote(head_sha, safe='')}/check-runs?{query}",
            ),
        )
        observed: dict[str, str] = {}
        for item in statuses.get("statuses", []):
            observed[str(item.get("context", ""))] = str(item.get("state", "pending"))
        for item in check_data.get("check_runs", []):
            name = str(item.get("name", ""))
            state = (
                str(item.get("conclusion", "pending"))
                if item.get("status") == "completed"
                else "pending"
            )
            if name:
                observed[name] = state

        failed = []
        for context in sorted(required_contexts):
            state = observed.get(context)
            if state != "success":
                failed.append(f"必需检查未通过：{context}（{state or '尚无结果'}）")
        return failed

    async def check_merge_requirements(
        self, ref: PullRequestRef, pull: dict[str, Any], merge_method: str
    ) -> list[str]:
        """读取经典保护规则与活动 Ruleset，并核实可验证的条件。"""

        base = pull.get("base", {})
        branch = str(base.get("ref", ""))
        head = pull.get("head", {})
        head_sha = str(head.get("sha", ""))
        if not branch or not head_sha:
            raise MergeBlockedError("无法读取目标分支或 PR head commit，停止合并。")

        protection, rules = await asyncio.gather(
            self.get_classic_protection(ref, branch),
            self.get_branch_rules(ref, branch),
        )

        required_reviews = 0
        require_last_push_approval = False
        required_contexts: set[str] = set()
        allowed_methods: set[str] | None = None
        merge_queue_required = False
        if protection:
            review_rule = protection.get("required_pull_request_reviews") or {}
            required_reviews = max(
                required_reviews,
                int(review_rule.get("required_approving_review_count") or 0),
            )
            require_last_push_approval = bool(
                review_rule.get("require_last_push_approval")
            )
            checks = protection.get("required_status_checks") or {}
            required_contexts.update(str(item) for item in checks.get("contexts", []))
            required_contexts.update(
                str(item.get("context"))
                for item in checks.get("checks", [])
                if item.get("context")
            )
            linear = protection.get("required_linear_history") or {}
            if linear.get("enabled"):
                allowed_methods = {"squash", "rebase"}

        for rule in rules:
            rule_type = rule.get("type")
            params = rule.get("parameters") or {}
            if rule_type == "pull_request":
                required_reviews = max(
                    required_reviews,
                    int(params.get("required_approving_review_count") or 0),
                )
                require_last_push_approval = require_last_push_approval or bool(
                    params.get("require_last_push_approval")
                )
                methods = set(params.get("allowed_merge_methods") or [])
                if methods:
                    allowed_methods = methods if allowed_methods is None else allowed_methods & methods
            elif rule_type == "required_status_checks":
                required_contexts.update(
                    str(item.get("context"))
                    for item in params.get("required_status_checks", [])
                    if item.get("context")
                )
            elif rule_type == "required_linear_history":
                linear_methods = {"squash", "rebase"}
                allowed_methods = (
                    linear_methods
                    if allowed_methods is None
                    else allowed_methods & linear_methods
                )
            elif rule_type == "merge_queue":
                merge_queue_required = True

        problems: list[str] = []
        if pull.get("merged"):
            problems.append("PR 已经合并。")
        elif pull.get("state") != "open":
            problems.append("PR 已关闭，不能合并。")
        if pull.get("draft"):
            problems.append("PR 仍处于 Draft 状态。")
        if pull.get("mergeable") is not True:
            problems.append("GitHub 尚未确认 PR 可合并，请稍后重试。")
        mergeable_state = pull.get("mergeable_state")
        if mergeable_state in {"dirty", "behind", "unknown"}:
            label = {
                "dirty": "存在合并冲突",
                "behind": "分支落后于目标分支",
                "unknown": "GitHub 尚未完成可合并性计算",
            }[str(mergeable_state)]
            problems.append(label + "。")
        if merge_queue_required:
            problems.append("目标分支要求使用 Merge Queue；直接合并已停止。")
        if allowed_methods is not None and merge_method not in allowed_methods:
            options = ", ".join(sorted(allowed_methods)) or "无"
            problems.append(
                f"仓库规则不允许 {merge_method} 合并方式（允许：{options}）。"
            )

        if required_reviews:
            reviews = await self.list_reviews(ref)
            latest_by_user: dict[str, dict[str, Any]] = {}
            for review in reviews:
                user = review.get("user") or {}
                login = str(user.get("login", ""))
                if not login:
                    continue
                current = latest_by_user.get(login)
                if current is None or str(review.get("submitted_at") or "") >= str(
                    current.get("submitted_at") or ""
                ):
                    latest_by_user[login] = review

            author = str((pull.get("user") or {}).get("login", ""))
            head_author = ""
            if require_last_push_approval:
                commits = await self.request(
                    "GET", f"{ref.repo_path}/pulls/{ref.number}/commits?per_page=100"
                )
                if commits:
                    head_author = str(
                        (((commits[-1].get("author") or {}).get("login")) or "")
                    )

            approved_writers: list[str] = []
            blocking_changes: list[str] = []
            for login, review in latest_by_user.items():
                if login == author:
                    continue
                permission = await self.request(
                    "GET",
                    f"{ref.repo_path}/collaborators/{quote(login, safe='')}/permission",
                )
                role = str(permission.get("permission", "none"))
                has_write = role in {"write", "maintain", "admin"}
                if not has_write:
                    continue
                state = str(review.get("state", "")).upper()
                review_sha = str(review.get("commit_id", ""))
                if state == "CHANGES_REQUESTED":
                    blocking_changes.append(login)
                elif state == "APPROVED" and review_sha == head_sha:
                    if not require_last_push_approval or login != head_author:
                        approved_writers.append(login)

            if blocking_changes:
                problems.append(
                    "仍有写入权限审阅者要求修改：" + ", ".join(sorted(blocking_changes))
                )
            if len(approved_writers) < required_reviews:
                problems.append(
                    f"需要 {required_reviews} 位不同的写入权限审阅者批准，"
                    f"目前确认 {len(approved_writers)} 位。"
                )

        problems.extend(await self.check_named_statuses(ref, head_sha, required_contexts))
        return problems

    async def approve(self, ref: PullRequestRef) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"{ref.repo_path}/pulls/{ref.number}/reviews",
            {"event": "APPROVE"},
        )

    async def merge(
        self, ref: PullRequestRef, method: str
    ) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"{ref.repo_path}/pulls/{ref.number}/merge",
            {"merge_method": method},
        )
