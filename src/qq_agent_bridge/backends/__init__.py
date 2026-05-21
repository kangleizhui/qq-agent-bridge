"""后端工厂 + 注册表"""
from typing import Dict, Any

from .base import Backend
from .openai_backend import OpenAIBackend
from .cli_backend import HermesBackend, OpenClaudeBackend, ClaudeCodeBackend

_REGISTRY = {
    "openai": OpenAIBackend,
    "hermes": HermesBackend,
    "openclaude": OpenClaudeBackend,
    "claude-code": ClaudeCodeBackend,
}


def create_backend(backend_name: str, config: Dict[str, Any]) -> Backend:
    backend_name = backend_name.lower().strip()
    if backend_name not in _REGISTRY:
        raise ValueError(
            f"未知后端 '{backend_name}'。可用: {', '.join(_REGISTRY.keys())}"
        )
    backend_config = config.get("backends", {}).get(backend_name, {})
    return _REGISTRY[backend_name](backend_config)
