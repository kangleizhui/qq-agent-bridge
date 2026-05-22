"""
WebUI HTTP 服务器
和 OneBot WS 服务器共用 aiohttp app，挂在不同路径上
"""
import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web

log = logging.getLogger(__name__)

# WebUI 静态文件目录
WEBUI_DIR = Path(__file__).parent / "webui_static"


class MessageLog:
    """环形消息日志，给 WebUI 展示"""

    def __init__(self, capacity: int = 200):
        self.capacity = capacity
        self.buffer: deque = deque(maxlen=capacity)

    def add(self, **kwargs: Any) -> None:
        entry = {"ts": time.time(), **kwargs}
        self.buffer.append(entry)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self.buffer)[-limit:]


class WebUIServer:
    """WebUI - 仪表盘 + 配置编辑器"""

    def __init__(
        self,
        bridge: Any,                # Bridge 实例
        username: str = "admin",
        password: str = "admin",
    ):
        self.bridge = bridge
        self.username = username
        self.password = password
        self.message_log = MessageLog()
        self._tokens: Dict[str, float] = {}      # token -> expire ts

    # ── 鉴权（最简版：登录拿 token，存在内存）──
    def _gen_token(self) -> str:
        import secrets
        token = secrets.token_urlsafe(24)
        self._tokens[token] = time.time() + 86400  # 24h
        return token

    def _check_token(self, request: web.Request) -> bool:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            exp = self._tokens.get(token)
            if exp and exp > time.time():
                return True
        # 也支持 cookie
        token = request.cookies.get("qab_token", "")
        if token:
            exp = self._tokens.get(token)
            if exp and exp > time.time():
                return True
        return False

    def _require_auth(self, handler):
        async def wrapper(request: web.Request):
            if not self._check_token(request):
                return web.json_response({"error": "unauthorized"}, status=401)
            return await handler(request)
        return wrapper

    # ── 路由 ────────────────────────────────
    def attach(self, app: web.Application, prefix: str = "/webui") -> None:
        app.router.add_get(prefix, self._index)
        app.router.add_get(f"{prefix}/", self._index)
        app.router.add_post(f"{prefix}/api/login", self._api_login)
        app.router.add_get(f"{prefix}/api/status", self._require_auth(self._api_status))
        app.router.add_get(f"{prefix}/api/messages", self._require_auth(self._api_messages))
        app.router.add_get(f"{prefix}/api/sessions", self._require_auth(self._api_sessions))
        app.router.add_post(f"{prefix}/api/sessions/reset", self._require_auth(self._api_reset_session))
        app.router.add_get(f"{prefix}/api/config", self._require_auth(self._api_get_config))
        app.router.add_post(f"{prefix}/api/config", self._require_auth(self._api_save_config))
        app.router.add_post(f"{prefix}/api/test", self._require_auth(self._api_test))

    # ── 页面 ────────────────────────────────
    async def _index(self, request: web.Request) -> web.Response:
        html_path = WEBUI_DIR / "index.html"
        if not html_path.exists():
            return web.Response(text="<h1>WebUI not installed</h1>", content_type="text/html")
        return web.FileResponse(html_path)

    # ── API ─────────────────────────────────
    async def _api_login(self, request: web.Request) -> web.Response:
        data = await request.json()
        if data.get("username") == self.username and data.get("password") == self.password:
            token = self._gen_token()
            resp = web.json_response({"ok": True, "token": token})
            resp.set_cookie("qab_token", token, max_age=86400, httponly=False)
            return resp
        await asyncio.sleep(1)
        return web.json_response({"ok": False, "error": "用户名或密码错误"}, status=401)

    async def _api_status(self, request: web.Request) -> web.Response:
        bridge = self.bridge
        return web.json_response({
            "backend": bridge.backend.name,
            "self_id": bridge.self_id,
            "onebot_connected": bridge.onebot.connected,
            "session_count": len(bridge.sessions._sessions),
            "uptime_seconds": int(time.time() - getattr(bridge, "_start_ts", time.time())),
            "version": "0.1.0",
        })

    async def _api_messages(self, request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", 50))
        return web.json_response({"messages": self.message_log.recent(limit)})

    async def _api_sessions(self, request: web.Request) -> web.Response:
        sessions = []
        for key, state in self.bridge.sessions._sessions.items():
            sessions.append({
                "key": key,
                "last_active": state.last_active,
                "turns": state.turns,
                "history_size": len(state.history),
            })
        sessions.sort(key=lambda x: x["last_active"], reverse=True)
        return web.json_response({"sessions": sessions})

    async def _api_reset_session(self, request: web.Request) -> web.Response:
        data = await request.json()
        key = data.get("key")
        if not key:
            return web.json_response({"error": "key required"}, status=400)
        if key in self.bridge.sessions._sessions:
            del self.bridge.sessions._sessions[key]
            return web.json_response({"ok": True})
        return web.json_response({"error": "session not found"}, status=404)

    async def _api_get_config(self, request: web.Request) -> web.Response:
        # 出于安全，密钥字段脱敏
        import copy
        cfg = copy.deepcopy(self.bridge.config)
        if "onebot" in cfg and cfg["onebot"].get("access_token"):
            cfg["onebot"]["access_token"] = "***"
        for backend_name, backend_cfg in cfg.get("backends", {}).items():
            if isinstance(backend_cfg, dict) and "api_key" in backend_cfg:
                backend_cfg["api_key"] = "***"
        return web.json_response(cfg)

    async def _api_save_config(self, request: web.Request) -> web.Response:
        # 实现：写回 config.yaml + 标记需要重启
        import yaml
        data = await request.json()
        config_path = os.environ.get("QAB_CONFIG", "./config/config.yaml")
        # TODO: 校验 + 合并已脱敏字段
        return web.json_response({"ok": False, "error": "在线改配置 v0.2 实现，请直接编辑 config.yaml + 重启服务"})

    async def _api_test(self, request: web.Request) -> web.Response:
        """让 webui 测试一下后端能不能调通"""
        data = await request.json()
        message = data.get("message", "你好")
        from .backends.base import ChatContext
        ctx = ChatContext(user_id="webui-test", chat_id="webui-test", chat_kind="user", session_key="webui-test")
        self.bridge.sessions.get(ctx)
        try:
            resp = await self.bridge.backend.ask(message, ctx)
            return web.json_response({"ok": True, "text": resp.text, "error": resp.error})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)
