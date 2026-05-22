"""
OpenClaw 远程后端 —— 通过 OpenClaw Gateway 的 OpenAI 兼容 HTTP API

OpenClaw 自带 OpenAI 兼容端点（需在 OpenClaw 那边启用）：

  ~/.openclaw/config.json5:
    gateway: {
      auth: { mode: "token", token: "your-secret" },
      http: { endpoints: { chatCompletions: { enabled: true } } },
    }

不依赖本地装 OpenClaw，可以连远程任意 OpenClaw Gateway 实例。
"""
import logging
from typing import Dict, Any, List

import httpx

from .base import Backend, ChatContext, AgentResponse
from ._openai_compat import (
    OpenAICompatCall, call_chat_completions, expand_env, health_check,
)

log = logging.getLogger(__name__)


class OpenClawBackend(Backend):
    """
    远程 OpenClaw 后端。

    配置示例（config.yaml）：

        backend: openclaw
        backends:
          openclaw:
            base_url: http://1.2.3.4:5757/v1   # OpenClaw Gateway，必须带 /v1
            api_key: ${OPENCLAW_TOKEN}         # 或直接明文（gateway.auth.token / .password）
            model: openclaw                    # 或 openclaw/<agentId> 路由到特定 agent
            system_prompt: ""                  # 留空，OpenClaw agent 自己有 system prompt
            temperature: 0.7
            max_tokens: 4000
            timeout: 120
    """
    name = "openclaw"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        base_url = expand_env(config.get("base_url", "")).rstrip("/")
        # 自动补 /v1（OpenClaw 端点是 /v1/chat/completions）
        if base_url and not base_url.endswith("/v1"):
            base_url = base_url + "/v1"
        self.base_url = base_url
        self.api_key = expand_env(config.get("api_key", ""))
        # OpenClaw 的 model 字段是 agent target："openclaw" / "openclaw/<agentId>"
        self.model = config.get("model", "openclaw")
        # OpenClaw agent 已经有自己的 system prompt，默认不再叠加
        self.system_prompt = config.get("system_prompt", "")
        self.temperature = float(config.get("temperature", 0.7))
        self.max_tokens = int(config.get("max_tokens", 4000))
        self.timeout = float(config.get("timeout", 120))

        if not self.base_url:
            raise ValueError("openclaw backend: base_url 未设置")
        if not self.api_key:
            log.warning("openclaw backend: api_key 为空 —— 仅在 OpenClaw 端 auth.mode=none 时可用")

        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def ask(self, message: str, ctx: ChatContext) -> AgentResponse:
        messages: List[Dict[str, str]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(ctx.history)
        messages.append({"role": "user", "content": message})

        # OpenClaw 支持的额外 header（来自 https://docs.openclaw.ai/gateway/openai-http-api）：
        #   x-openclaw-session-key: 全权控制 session 路由（跨调用复用上下文）
        #   x-openclaw-message-channel: 设置合成 ingress channel（QQ 即 qqbot）
        extra_headers: Dict[str, str] = {
            "x-openclaw-message-channel": "qqbot",
        }
        if ctx.session_key:
            extra_headers["x-openclaw-session-key"] = ctx.session_key

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
        url = expand_env(base_url).rstrip("/")
        if url and not url.endswith("/v1"):
            url = url + "/v1"
        return await health_check(url, expand_env(api_key))
