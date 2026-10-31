"""Neo-MoFox GitHub Pull Request 管理插件。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin

from .command import GitHubPRCommand


@register_plugin
class GitHubPRManagerPlugin(BasePlugin):
    """管理 GitHub PR 的命令插件。"""

    plugin_name: str = "gh_pr_manager"
    configs: list[type] = []

    def get_components(self) -> list[type]:
        return [GitHubPRCommand]
