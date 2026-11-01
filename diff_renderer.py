"""通过 Playwright 将 PR diff 渲染成可发送的截图。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_DIFF_LINES_PER_IMAGE = 72
_MAX_IMAGE_ATTACHMENTS = 12


@dataclass(frozen=True)
class PRViewImages:
    """截图和 diff 截图分页信息。"""

    images: list[tuple[str, bytes]]
    diff_pages_total: int
    diff_pages_rendered: int


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _render_rows(patch: str) -> str:
    rows: list[str] = []
    old_line: int | None = None
    new_line: int | None = None
    for index, line in enumerate(patch.splitlines(), start=1):
        hunk = _HUNK_RE.match(line)
        if hunk:
            old_line, new_line = int(hunk.group(1)), int(hunk.group(2))
            rows.append(
                '<div class="line hunk"><span></span><span></span>'
                f'<code>{_escape(line)}</code></div>'
            )
            continue
        if line.startswith("+") and not line.startswith("+++"):
            kind = "add"
            old_number, new_number = "", str(new_line or "")
            if new_line is not None:
                new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            kind = "delete"
            old_number, new_number = str(old_line or ""), ""
            if old_line is not None:
                old_line += 1
        else:
            kind = "context" if not line.startswith("\\") else "meta"
            old_number = str(old_line or "")
            new_number = str(new_line or "")
            if old_line is not None and not line.startswith("\\"):
                old_line += 1
            if new_line is not None and not line.startswith("\\"):
                new_line += 1
        rows.append(
            f'<div class="line {kind}"><span class="ln">{old_number}</span>'
            f'<span class="ln">{new_number}</span><code>{_escape(line)}</code></div>'
        )
    if not rows:
        rows.append('<div class="empty">GitHub 没有提供文本 patch（可能是二进制文件或 diff 太大）。</div>')
    return "\n".join(rows)


def _page_html(
    *,
    repository: str,
    number: int,
    title: str,
    file_name: str,
    status: str,
    patch: str,
    page_number: int,
    page_total: int,
) -> str:
    rows = _render_rows(patch)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 26px; background: #0d1117; color: #e6edf3; font: 14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
.card {{ max-width: 1450px; margin: 0 auto; border: 1px solid #30363d; border-radius: 12px; overflow: hidden; background: #161b22; }}
.head {{ padding: 20px 24px; border-bottom: 1px solid #30363d; background: #161b22; }}
.eyebrow {{ color: #8b949e; font-size: 12px; letter-spacing: .03em; }}
h1 {{ margin: 6px 0 12px; font-size: 20px; line-height: 1.3; }}
.meta {{ color: #8b949e; font-size: 13px; }}
.file {{ padding: 14px 24px; background: #1f2937; color: #c9d1d9; border-bottom: 1px solid #30363d; font: 13px ui-monospace,SFMono-Regular,monospace; word-break: break-all; }}
.diff {{ padding: 6px 0 10px; overflow: hidden; font: 12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace; }}
.line {{ display: grid; grid-template-columns: 48px 48px minmax(0,1fr); min-height: 19px; }}
.line code {{ padding: 0 12px; white-space: pre-wrap; overflow-wrap: anywhere; }}
.ln {{ color: #6e7681; text-align: right; padding: 0 8px; user-select: none; border-right: 1px solid rgba(110,118,129,.15); }}
.add {{ background: rgba(46,160,67,.2); }} .add code {{ color: #aff5b4; }}
.delete {{ background: rgba(248,81,73,.18); }} .delete code {{ color: #ffdcd7; }}
.hunk {{ background: rgba(56,139,253,.18); color: #a5d6ff; }}
.meta {{ color: #8b949e; }}
.empty {{ padding: 24px; color: #8b949e; }}
.foot {{ padding: 12px 24px; color: #8b949e; border-top: 1px solid #30363d; font-size: 12px; }}
</style></head><body><main class="card">
<header class="head"><div class="eyebrow">{_escape(repository)} · Pull Request #{number} · {page_number}/{page_total}</div>
<h1>{_escape(title)}</h1><div class="meta">{_escape(status)}</div></header>
<div class="file">{_escape(file_name)}</div><section class="diff">{rows}</section>
<footer class="foot">GitHub PR diff · rendered by headless Chromium</footer>
</main></body></html>"""


def _file_tree_rows(files: list[dict[str, Any]]) -> str:
    root: dict[str, Any] = {"directories": {}, "files": []}
    status_labels = {
        "added": "A",
        "modified": "M",
        "removed": "D",
        "renamed": "R",
        "copied": "C",
        "changed": "M",
    }
    for item in files:
        path = str(item.get("filename", "(unknown file)")).replace("\\", "/")
        parts = [part for part in path.split("/") if part]
        if not parts:
            continue
        node = root
        for directory in parts[:-1]:
            directories = node["directories"]
            node = directories.setdefault(directory, {"directories": {}, "files": []})
        node["files"].append(item)

    rows: list[str] = []

    def render(node: dict[str, Any], depth: int) -> None:
        for name in sorted(node["directories"], key=str.casefold):
            rows.append(
                f'<div class="tree-row folder" style="padding-left:{14 + depth * 18}px">'
                f'<span class="chevron">⌄</span><span class="folder-icon">▰</span>'
                f'<span class="name">{_escape(name)}</span></div>'
            )
            render(node["directories"][name], depth + 1)
        for item in sorted(
            node["files"],
            key=lambda file_item: str(file_item.get("filename", "")).casefold(),
        ):
            filename = str(item.get("filename", "(unknown file)")).replace("\\", "/")
            name = filename.rsplit("/", 1)[-1]
            status = str(item.get("status", "modified")).lower()
            badge = status_labels.get(status, "M")
            rows.append(
                f'<div class="tree-row file" style="padding-left:{34 + depth * 18}px">'
                f'<span class="file-icon">▤</span><span class="name">{_escape(name)}</span>'
                f'<span class="badge {badge.lower()}">{badge}</span></div>'
            )

    render(root, 0)
    return "\n".join(rows) or '<div class="empty">没有变更文件</div>'


def _file_tree_html(
    *, repository: str, pull: dict[str, Any], files: list[dict[str, Any]]
) -> str:
    additions = sum(int(item.get("additions") or 0) for item in files)
    deletions = sum(int(item.get("deletions") or 0) for item in files)
    rows = _file_tree_rows(files)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 18px; width: 720px; background: #0d1117; color: #e6edf3; font: 13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
.sidebar {{ border: 1px solid #30363d; border-radius: 8px; overflow: hidden; background: #0d1117; }}
.repo {{ padding: 14px 16px 4px; color: #8b949e; font-size: 12px; }}
h1 {{ margin: 2px 16px 10px; font-size: 16px; line-height: 1.35; overflow-wrap: anywhere; }}
.summary {{ padding: 9px 16px; border-top: 1px solid #30363d; border-bottom: 1px solid #30363d; color: #8b949e; font-size: 12px; }}
.tree {{ padding: 7px 0; }}
.tree-row {{ min-height: 30px; padding-right: 12px; display: flex; align-items: center; gap: 8px; white-space: nowrap; }}
.tree-row:hover {{ background: #161b22; }}
.chevron {{ width: 10px; color: #8b949e; font-size: 12px; }}
.folder-icon {{ color: #79c0ff; font-size: 14px; }}
.file-icon {{ width: 18px; color: #8b949e; text-align: center; }}
.name {{ flex: 1; overflow: hidden; text-overflow: ellipsis; }}
.badge {{ min-width: 18px; height: 18px; border-radius: 9px; text-align: center; font: 10px/18px ui-monospace,monospace; color: #8b949e; background: #21262d; }}
.badge.a {{ color: #3fb950; }} .badge.m {{ color: #d29922; }} .badge.d {{ color: #f85149; }} .badge.r {{ color: #a371f7; }}
.empty {{ padding: 18px; color: #8b949e; }}
</style></head><body><main class="sidebar">
<div class="repo">{_escape(repository)} · Pull Request #{int(pull.get('number', 0))}</div>
<h1>{_escape(str(pull.get('title', '')))}</h1>
<div class="summary">Files changed · {len(files)} · +{additions} / -{deletions}</div>
<nav class="tree">{rows}</nav>
</main></body></html>"""


def _description_html(*, repository: str, pull: dict[str, Any]) -> str:
    body = str(pull.get("body") or "").strip() or "（无 PR 说明）"
    author = str((pull.get("user") or {}).get("login") or "unknown")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px; width: 920px; background: #0d1117; color: #e6edf3; font: 14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
.card {{ border: 1px solid #30363d; border-radius: 10px; overflow: hidden; background: #161b22; }}
.header {{ padding: 18px 22px; border-bottom: 1px solid #30363d; }}
.eyebrow {{ color: #8b949e; font-size: 12px; }}
h1 {{ margin: 6px 0; font-size: 20px; line-height: 1.35; overflow-wrap: anywhere; }}
.author {{ color: #8b949e; font-size: 12px; }}
.body {{ padding: 20px 22px; white-space: pre-wrap; overflow-wrap: anywhere; }}
</style></head><body><main class="card">
<header class="header"><div class="eyebrow">{_escape(repository)} · Pull Request #{int(pull.get('number', 0))} · PR 说明</div>
<h1>{_escape(str(pull.get('title', '')))}</h1><div class="author">{_escape(author)}</div></header>
<section class="body">{_escape(body)}</section>
</main></body></html>"""


async def screenshot_pr_view(
    *,
    repository: str,
    pull: dict[str, Any],
    files: list[dict[str, Any]],
    file_filter: str = "",
    include_description_image: bool = False,
    start_page: int = 1,
) -> PRViewImages:
    """渲染文件树、可选 PR 说明和从指定页开始的 diff 截图。"""

    if start_page < 1:
        raise ValueError("Diff 起始页必须大于或等于 1。")

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Playwright。请安装插件依赖，并执行 `playwright install chromium`。"
        ) from exc

    selected = files
    if file_filter:
        needle = file_filter.replace("\\", "/").strip("/")
        selected = [
            item
            for item in files
            if str(item.get("filename", "")).replace("\\", "/") == needle
            or str(item.get("filename", "")).replace("\\", "/").endswith("/" + needle)
        ]
        if not selected:
            raise ValueError(f"PR 中找不到匹配的文件：{file_filter}")

    diff_pages_total = 0
    for item in selected:
        lines = str(item.get("patch", "")).splitlines()
        chunk_count = max(
            1, (len(lines) + _DIFF_LINES_PER_IMAGE - 1) // _DIFF_LINES_PER_IMAGE
        )
        diff_pages_total += chunk_count

    if start_page > diff_pages_total and diff_pages_total:
        raise ValueError(f"起始页超出范围；该 PR 当前筛选结果共 {diff_pages_total} 页。")

    max_diff_images = _MAX_IMAGE_ATTACHMENTS - 1 - int(include_description_image)
    pages: list[tuple[int, str, str, str]] = []
    current_page = 0
    end_page = start_page + max_diff_images
    for item in selected:
        file_name = str(item.get("filename", "(unknown file)"))
        lines = str(item.get("patch", "")).splitlines()
        chunk_count = max(
            1, (len(lines) + _DIFF_LINES_PER_IMAGE - 1) // _DIFF_LINES_PER_IMAGE
        )
        for chunk_index in range(chunk_count):
            current_page += 1
            if current_page < start_page:
                continue
            if current_page >= end_page:
                break
            start = chunk_index * _DIFF_LINES_PER_IMAGE
            chunk = lines[start : start + _DIFF_LINES_PER_IMAGE]
            pages.append(
                (
                    current_page,
                    file_name,
                    str(item.get("status", "modified")),
                    "\n".join(chunk),
                )
            )
        if current_page >= end_page:
            break

    if not pages:
        raise ValueError("该 PR 没有可显示的文件 diff。")

    screenshots: list[tuple[str, bytes]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1480, "height": 1100},
            device_scale_factor=1,
        )
        try:
            await page.set_viewport_size({"width": 760, "height": 1100})
            await page.set_content(
                _file_tree_html(repository=repository, pull=pull, files=files),
                wait_until="domcontentloaded",
            )
            tree_image = await page.screenshot(full_page=True, type="png")
            screenshots.append(("GitHub 风格文件树", tree_image))

            if include_description_image:
                await page.set_viewport_size({"width": 960, "height": 1100})
                await page.set_content(
                    _description_html(repository=repository, pull=pull),
                    wait_until="domcontentloaded",
                )
                description_image = await page.screenshot(full_page=True, type="png")
                screenshots.append(("PR 说明", description_image))

            await page.set_viewport_size({"width": 1480, "height": 1100})
            total = diff_pages_total
            for page_number, file_name, status, patch in pages:
                await page.set_content(
                    _page_html(
                        repository=repository,
                        number=int(pull.get("number", 0)),
                        title=str(pull.get("title", "")),
                        file_name=file_name,
                        status=status,
                        patch=patch,
                        page_number=page_number,
                        page_total=total,
                    ),
                    wait_until="domcontentloaded",
                )
                image = await page.screenshot(full_page=True, type="png")
                suffix = f" · {page_number}/{total}" if total > 1 else ""
                screenshots.append((f"{file_name}{suffix}", image))
        finally:
            await browser.close()
    return PRViewImages(
        images=screenshots,
        diff_pages_total=diff_pages_total,
        diff_pages_rendered=len(pages),
    )
