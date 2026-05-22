"""
OpenAI 兼容 HTTP 调用器 —— Hermes Gateway 和 OpenClaw Gateway 共用的底层

两边都通过 `POST /v1/chat/completions` 暴露 agent，鉴权都是 `Authorization: Bearer <key>`。
差异只在：
  - URL 路径前缀（Hermes 一般是 :8642/v1，OpenClaw 是 gateway 端口）
  - model 字段语义（Hermes 任意；OpenClaw 必须 "openclaw" 或 "openclaw/<agentId>"）
  - 可能的额外 header（OpenClaw 支持 x-openclaw-session-key 等）

这个模块只负责 HTTP 调用本身，不关心是谁。
"""
import os
import re
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

import httpx

log = logging.getLogger(__name__)


def expand_env(s: str) -> str:
    """${VAR_NAME} → 环境变量值，方便 api_key 写成 ${HERMES_KEY}"""
    if not isinstance(s, str):
        return s
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), s)


@dataclass
class OpenAICompatCall:
    """单次 HTTP 调用的所有参数。"""
    base_url: str
    api_key: str
    model: str
    messages: List[Dict[str, str]]
    temperature: float = 0.7
    max_tokens: int = 2000
    timeout: float = 120.0
    extra_headers: Dict[str, str] = field(default_factory=dict)


async def call_chat_completions(
    client: httpx.AsyncClient,
    req: OpenAICompatCall,
) -> tuple[str, Optional[str]]:
    """
    POST /v1/chat/completions，返回 (text, error)。
    text 为空 + error 为 None 是不可能的；要么 text 非空（成功），要么 error 非空（失败）。
    """
    url = f"{req.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {req.api_key}",
        "Content-Type": "application/json",
        **req.extra_headers,
    }
    payload = {
        "model": req.model,
        "messages": req.messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
    }

    try:
        resp = await client.post(url, headers=headers, json=payload, timeout=req.timeout)
        if resp.status_code != 200:
            log.error("HTTP %s from %s: %s", resp.status_code, url, resp.text[:500])
            return "", f"后端 API 错误 {resp.status_code}: {resp.text[:200]}"

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return "", f"后端返回空 choices: {str(data)[:200]}"
        msg = choices[0].get("message", {})
        text = (msg.get("content") or "").strip()
        if not text:
            return "", f"后端返回空 content: {str(data)[:200]}"
        return text, None
    except httpx.TimeoutException:
        return "", "后端响应超时，稍后再试。"
    except httpx.ConnectError as e:
        return "", f"无法连接到后端 ({url}): {e}"
    except Exception as e:
        log.exception("call_chat_completions failed")
        return "", f"调用失败: {type(e).__name__}: {e}"


async def health_check(base_url: str, api_key: str, timeout: float = 10.0) -> tuple[bool, str]:
    """
    探测 /v1/models 接口是否可达 + 鉴权是否通过。
    用于 WebUI 上的"测试连接"按钮。
    返回 (ok, message)。
    """
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("data") or []
            return True, f"✓ 连接成功（{len(models)} 个 model 可用）"
        elif resp.status_code in (401, 403):
            return False, f"✗ 鉴权失败 ({resp.status_code})：检查 api_key / token / password"
        else:
            return False, f"✗ HTTP {resp.status_code}: {resp.text[:200]}"
    except httpx.ConnectError as e:
        return False, f"✗ 连接失败: {e}"
    except httpx.TimeoutException:
        return False, "✗ 探测超时"
    except Exception as e:
        return False, f"✗ {type(e).__name__}: {e}"
