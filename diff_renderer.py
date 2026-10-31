"""通过 Playwright 将 PR diff 渲染成可发送的截图。"""

from __future__ import annotations

import html
import re
from typing import Any


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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


async def screenshot_diff(
    *, repository: str, pull: dict[str, Any], files: list[dict[str, Any]], file_filter: str = ""
) -> list[tuple[str, bytes]]:
    """将 diff 按文件及页切分，返回描述和 PNG 字节。"""

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

    pages: list[tuple[str, str, str]] = []
    for item in selected:
        file_name = str(item.get("filename", "(unknown file)"))
        patch = str(item.get("patch", ""))
        lines = patch.splitlines()
        chunks = [lines[i : i + 52] for i in range(0, len(lines), 52)] or [[]]
        for chunk_index, chunk in enumerate(chunks, start=1):
            pages.append(
                (
                    file_name,
                    str(item.get("status", "modified")),
                    "\n".join(chunk),
                )
            )

    if not pages:
        raise ValueError("该 PR 没有可显示的文件 diff。")

    screenshots: list[tuple[str, bytes]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1480, "height": 1100},
            device_scale_factor=1,
        )
        total = len(pages)
        for index, (file_name, status, patch) in enumerate(pages, start=1):
            await page.set_content(
                _page_html(
                    repository=repository,
                    number=int(pull.get("number", 0)),
                    title=str(pull.get("title", "")),
                    file_name=file_name,
                    status=status,
                    patch=patch,
                    page_number=index,
                    page_total=total,
                ),
                wait_until="domcontentloaded",
            )
            image = await page.screenshot(full_page=True, type="png")
            suffix = f" · {index}/{total}" if total > 1 else ""
            screenshots.append((f"{file_name}{suffix}", image))
        await browser.close()
    return screenshots
