"""
Hermes 远程后端 —— 通过 Hermes Gateway 的 OpenAI 兼容 HTTP API (端口 8642)

只需要：
  - base_url: 例如 http://your-hermes-server:8642/v1
  - api_key: 在 Hermes config.yaml 的 platforms.api_server.extra.key 配置

不依赖本地装 Hermes，可以连远程任意 Hermes Gateway 实例。
"""
import logging
from typing import Dict, Any, List

import httpx

from .base import Backend, ChatContext, AgentResponse
from ._openai_compat import (
    OpenAICompatCall, call_chat_completions, expand_env, health_check,
)

log = logging.getLogger(__name__)


class HermesBackend(Backend):
    """
    远程 Hermes 后端。

    配置示例（config.yaml）：

        backend: hermes
        backends:
          hermes:
            base_url: http://101.32.98.240:8642/v1
            api_key: ${HERMES_API_KEY}    # 或直接明文
            model: hermes-agent           # Hermes 的默认 model 名
            system_prompt: 你是一个 QQ 助手。
            temperature: 0.7
            max_tokens: 2000
            timeout: 120
    """
    name = "hermes"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = expand_env(
            config.get("base_url", "http://127.0.0.1:8642/v1")
        ).rstrip("/")
        self.api_key = expand_env(config.get("api_key", ""))
        # Hermes 的 /v1/models 里会列出 "hermes-agent"；默认用这个名字
        self.model = config.get("model", "hermes-agent")
        self.system_prompt = config.get("system_prompt", "你是一个 QQ 助手。")
        self.temperature = float(config.get("temperature", 0.7))
        self.max_tokens = int(config.get("max_tokens", 2000))
        self.timeout = float(config.get("timeout", 120))

        if not self.api_key:
            log.warning("hermes backend: api_key 为空 —— 仅在 Hermes 端 auth 关闭时可用")

        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def ask(self, message: str, ctx: ChatContext) -> AgentResponse:
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        messages.extend(ctx.history)
        messages.append({"role": "user", "content": message})

        # Hermes 支持 X-Hermes-Session-Id 来跨调用复用 session
        extra_headers = {}
        if ctx.session_key:
            extra_headers["X-Hermes-Session-Id"] = ctx.session_key

        text, err = await call_chat_completions(
            self._client,
            OpenAICompatCall(
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout,
                extra_headers=extra_headers,
            ),
        )
        if err:
            return AgentResponse(text="", error=err)
        return AgentResponse(text=text)

    async def shutdown(self) -> None:
        await self._client.aclose()

    @staticmethod
    async def test_connection(base_url: str, api_key: str) -> tuple[bool, str]:
        """供 WebUI 测试连接按钮调用。"""
        return await health_check(expand_env(base_url), expand_env(api_key))
