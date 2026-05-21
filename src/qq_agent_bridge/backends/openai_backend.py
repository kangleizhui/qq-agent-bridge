"""
OpenAI 兼容后端 —— 支持 DeepSeek、火山引擎、月之暗面、Together 等所有 OpenAI 格式 API
最简单的后端，纯 HTTP 调用，无 subprocess。
"""
import os
import asyncio
import logging
import re
from typing import Dict, Any, List

import httpx

from .base import Backend, ChatContext, AgentResponse

log = logging.getLogger(__name__)


def expand_env(s: str) -> str:
    """${VAR_NAME} → 环境变量值"""
    if not isinstance(s, str):
        return s
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), s)


class OpenAIBackend(Backend):
    name = "openai"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = expand_env(config.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = expand_env(config.get("api_key", ""))
        self.model = config.get("model", "gpt-4o-mini")
        self.system_prompt = config.get("system_prompt", "你是一个 QQ 机器人。")
        self.temperature = float(config.get("temperature", 0.7))
        self.max_tokens = int(config.get("max_tokens", 2000))
        self.timeout = float(config.get("timeout", 60))

        if not self.api_key:
            raise ValueError("openai backend: api_key 未设置（可用 ${ENV_VAR} 引用环境变量）")

        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def ask(self, message: str, ctx: ChatContext) -> AgentResponse:
        messages: List[Dict[str, str]] = [{"role": "system", "content": self.system_prompt}]
        # 历史（OpenAI 格式由 SessionManager 维护）
        messages.extend(ctx.history)
        messages.append({"role": "user", "content": message})

        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                },
            )
            if resp.status_code != 200:
                log.error("openai api error %s: %s", resp.status_code, resp.text[:500])
                return AgentResponse(text="", error=f"API 错误 {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            return AgentResponse(text=text)

        except httpx.TimeoutException:
            return AgentResponse(text="", error="后端响应超时，稍后再试。")
        except Exception as e:
            log.exception("openai backend failed")
            return AgentResponse(text="", error=f"调用失败: {type(e).__name__}: {e}")

    async def shutdown(self) -> None:
        await self._client.aclose()
