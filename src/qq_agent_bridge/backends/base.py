"""
后端 adapter 抽象基类
所有 AI 后端（Hermes/OpenClaude/Claude Code/OpenAI）都实现这个接口
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class ChatContext:
    """单次对话的上下文。"""
    user_id: str               # 发消息的 QQ 号
    chat_id: str               # 私聊=对方QQ；群聊=群号
    chat_kind: str             # "user" or "group"
    user_nickname: str = ""
    session_key: str = ""      # 会话隔离 key（由 SessionManager 生成）
    history: List[Dict[str, str]] = field(default_factory=list)  # OpenAI 格式历史


@dataclass
class AgentResponse:
    """后端 agent 的回复。"""
    text: str
    images: List[str] = field(default_factory=list)   # 本地路径或 URL
    files: List[str] = field(default_factory=list)
    error: Optional[str] = None


class Backend(ABC):
    """所有 AI 后端的基类。"""

    name: str = "abstract"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    async def ask(self, message: str, ctx: ChatContext) -> AgentResponse:
        """处理一条消息并返回回复。"""
        ...

    async def start(self) -> None:
        """启动后端（连接、健康检查等）。可选。"""
        pass

    async def shutdown(self) -> None:
        """优雅关闭。可选。"""
        pass

    async def reset_session(self, ctx: ChatContext) -> None:
        """重置某个会话（用户发 /reset 时调用）。"""
        pass
