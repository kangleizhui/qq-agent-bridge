"""会话管理：决定哪个聊天用哪个 session，超时清理"""
import time
from typing import Dict, List
from dataclasses import dataclass, field

from .backends.base import ChatContext


@dataclass
class SessionState:
    key: str
    last_active: float = field(default_factory=time.time)
    history: List[Dict[str, str]] = field(default_factory=list)  # OpenAI 格式
    turns: int = 0


class SessionManager:
    def __init__(self, scope: str = "per_chat", timeout: int = 1800, history_turns: int = 10):
        self.scope = scope  # per_user | per_group | per_chat
        self.timeout = timeout
        self.max_history = history_turns * 2  # user+assistant
        self._sessions: Dict[str, SessionState] = {}

    def session_key(self, user_id: str, chat_id: str, chat_kind: str) -> str:
        if self.scope == "per_user":
            return f"user:{user_id}"
        if self.scope == "per_group":
            return f"group:{chat_id}" if chat_kind == "group" else f"user:{user_id}"
        # per_chat (默认)
        if chat_kind == "group":
            return f"group:{chat_id}:{user_id}"  # 群里每个人独立会话
        return f"private:{user_id}"

    def get(self, ctx: ChatContext) -> SessionState:
        self._gc()
        key = self.session_key(ctx.user_id, ctx.chat_id, ctx.chat_kind)
        ctx.session_key = key
        state = self._sessions.get(key)
        if state is None:
            state = SessionState(key=key)
            self._sessions[key] = state
        state.last_active = time.time()
        ctx.history = list(state.history)
        return state

    def record(self, ctx: ChatContext, user_msg: str, assistant_msg: str) -> None:
        state = self._sessions.get(ctx.session_key)
        if not state:
            return
        state.history.append({"role": "user", "content": user_msg})
        state.history.append({"role": "assistant", "content": assistant_msg})
        # 截断到 max_history
        if len(state.history) > self.max_history:
            state.history = state.history[-self.max_history:]
        state.turns += 1
        state.last_active = time.time()

    def reset(self, ctx: ChatContext) -> bool:
        key = self.session_key(ctx.user_id, ctx.chat_id, ctx.chat_kind)
        if key in self._sessions:
            del self._sessions[key]
            return True
        return False

    def _gc(self) -> None:
        now = time.time()
        expired = [k for k, s in self._sessions.items() if now - s.last_active > self.timeout]
        for k in expired:
            del self._sessions[k]
