"""权限系统：白名单 / 黑名单"""
from typing import Dict, Any, Set

from .backends.base import ChatContext


class PermissionChecker:
    def __init__(self, config: Dict[str, Any]):
        self.owners: Set[str] = {str(x) for x in config.get("owners", [])}
        self.allow_all = bool(config.get("allow_all", False))
        self.whitelist_users: Set[str] = {str(x) for x in config.get("whitelist_users", [])}
        self.whitelist_groups: Set[str] = {str(x) for x in config.get("whitelist_groups", [])}
        self.blacklist_users: Set[str] = {str(x) for x in config.get("blacklist_users", [])}
        self.blacklist_groups: Set[str] = {str(x) for x in config.get("blacklist_groups", [])}
        self.group_require_at = bool(config.get("group_require_at", True))

    def is_owner(self, user_id: str) -> bool:
        return str(user_id) in self.owners

    def allowed(self, ctx: ChatContext) -> bool:
        uid = str(ctx.user_id)
        # 1. 主人永远放行
        if uid in self.owners:
            return True
        # 2. 黑名单永远禁止
        if uid in self.blacklist_users:
            return False
        if ctx.chat_kind == "group" and str(ctx.chat_id) in self.blacklist_groups:
            return False
        # 3. allow_all 模式
        if self.allow_all:
            return True
        # 4. 白名单检查
        if uid in self.whitelist_users:
            return True
        if ctx.chat_kind == "group" and str(ctx.chat_id) in self.whitelist_groups:
            return True
        return False
