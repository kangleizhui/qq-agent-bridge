"""后端工厂 + 注册表

qq-agent-bridge 只支持两个后端，都通过 OpenAI 兼容 HTTP API 远程连接：

  - hermes:   Hermes Gateway (端口 8642)，鉴权 = API key
  - openclaw: OpenClaw Gateway，鉴权 = token 或 password

链接 + 秘钥/密码就能接入，不需要在本机装 hermes/openclaw CLI。
"""
from typing import Dict, Any

from .base import Backend
from .hermes import HermesBackend
from .openclaw import OpenClawBackend

_REGISTRY = {
    "hermes": HermesBackend,
    "openclaw": OpenClawBackend,
}


def create_backend(backend_name: str, config: Dict[str, Any]) -> Backend:
    backend_name = (backend_name or "").lower().strip()
    if backend_name not in _REGISTRY:
        raise ValueError(
            f"未知后端 '{backend_name}'。可用: {', '.join(_REGISTRY.keys())}"
        )
    backend_config = config.get("backends", {}).get(backend_name, {})
    return _REGISTRY[backend_name](backend_config)


def list_backends() -> list:
    """供 WebUI 列出可选后端。"""
    return list(_REGISTRY.keys())
