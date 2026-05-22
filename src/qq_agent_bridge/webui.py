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
        app.router.add_get(f"{prefix}/api/config/raw", self._require_auth(self._api_get_config_raw))
        app.router.add_post(f"{prefix}/api/config", self._require_auth(self._api_save_config))
        app.router.add_post(f"{prefix}/api/test", self._require_auth(self._api_test))
        app.router.add_post(f"{prefix}/api/test-connection", self._require_auth(self._api_test_connection))
        app.router.add_post(f"{prefix}/api/restart", self._require_auth(self._api_restart))
        app.router.add_post(f"{prefix}/api/reload", self._require_auth(self._api_reload))
        app.router.add_get(f"{prefix}/api/napcat-snippet", self._require_auth(self._api_napcat_snippet))
        app.router.add_get(f"{prefix}/api/backends", self._require_auth(self._api_list_backends))

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
        """保存配置到 config.yaml（合并：脱敏字段不覆盖）"""
        import yaml
        data = await request.json()
        section = data.get("section")  # backend / onebot / permissions / webui
        payload = data.get("payload", {})
        if section not in ("backend", "onebot", "permissions", "webui"):
            return web.json_response({"ok": False, "error": "section must be backend/onebot/permissions/webui"}, status=400)

        config_path = os.environ.get("QAB_CONFIG", "./config/config.yaml")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                current = yaml.safe_load(f) or {}
        except FileNotFoundError:
            return web.json_response({"ok": False, "error": f"config file not found: {config_path}"}, status=500)

        if section == "backend":
            current["backend"] = payload.get("name", current.get("backend", "openai"))
            bname = current["backend"]
            current.setdefault("backends", {})
            old_backend = current["backends"].get(bname, {})
            new_backend = payload.get("config", old_backend)
            # 如果传来的 api_key 是 ***，保留旧的
            if new_backend.get("api_key") == "***" and old_backend.get("api_key"):
                new_backend["api_key"] = old_backend["api_key"]
            current["backends"][bname] = new_backend
        elif section == "onebot":
            old_token = current.get("onebot", {}).get("access_token", "")
            current.setdefault("onebot", {}).update(payload)
            # 如果传来的 access_token 是 ***，保留旧的
            if current["onebot"].get("access_token") == "***" and old_token:
                current["onebot"]["access_token"] = old_token
        elif section == "permissions":
            current["permissions"] = payload
        elif section == "webui":
            current["webui"] = payload

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(current, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        self.bridge.config = current
        return web.json_response({"ok": True, "message": "配置已保存，需重启或热重载生效"})

    async def _api_get_config_raw(self, request: web.Request) -> web.Response:
        """返回完整配置（脱敏）—— 前端表单用"""
        return web.json_response(self._masked_config())

    def _masked_config(self) -> dict:
        import copy
        cfg = copy.deepcopy(self.bridge.config)
        if "onebot" in cfg and cfg["onebot"].get("access_token"):
            cfg["onebot"]["access_token"] = "***"
        for bname, bcfg in cfg.get("backends", {}).items():
            if isinstance(bcfg, dict) and "api_key" in bcfg:
                bcfg["api_key"] = "***"
        return cfg

    async def _api_restart(self, request: web.Request) -> web.Response:
        """重启进程（systemd 拉起 / 直接 os.execv）"""
        import sys
        log.warning("WebUI 触发进程重启")
        # 先写配置落盘
        os._exit(0)  # systemd 会自动拉起；如果不是 systemd 则进程直接退出
        return web.json_response({"ok": True})  # unreachable

    async def _api_reload(self, request: web.Request) -> web.Response:
        """热重载后端 + 权限 + 会话配置（OneBot 连接不断）"""
        try:
            await self.bridge.backend.shutdown()
            from .backends import create_backend
            from .permissions import PermissionChecker
            from .session import SessionManager
            cfg = self.bridge.config
            self.bridge.backend = create_backend(cfg.get("backend", "openai"), cfg)
            await self.bridge.backend.start()
            self.bridge.perms = PermissionChecker(cfg.get("permissions", {}))
            self.bridge.group_require_at = self.bridge.perms.group_require_at
            # 会话配置也重建（scope/timeout 改了能生效）
            self.bridge.sessions = SessionManager(
                scope=cfg.get("session", {}).get("scope", "per_chat"),
                timeout=cfg.get("session", {}).get("timeout", 1800),
                history_turns=cfg.get("session", {}).get("history_turns", 10),
            )
            log.info("WebUI 热重载完成: backend=%s, allow_all=%s, group_require_at=%s",
                     self.bridge.backend.name, self.bridge.perms.allow_all, self.bridge.perms.group_require_at)
            return web.json_response({"ok": True, "backend": self.bridge.backend.name})
        except Exception as e:
            log.exception("热重载失败")
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _api_napcat_snippet(self, request: web.Request) -> web.Response:
        """生成 NapCat 反向 WS 配置片段"""
        cfg = self.bridge.config.get("onebot", {})
        snippet = {
            "name": "to-qq-agent-bridge",
            "enable": True,
            "url": f"ws://{cfg.get('ws_host', '127.0.0.1')}:{cfg.get('ws_port', 8080)}{cfg.get('ws_path', '/onebot/v11/ws')}",
            "messagePostFormat": "array",
            "reportSelfMessage": False,
            "token": cfg.get("access_token", ""),
            "reconnectInterval": 5000,
            "heartInterval": 30000,
            "debug": False,
        }
        return web.json_response({"snippet": snippet, "config_path_hint": "/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/onebot11_<BOT_QQ>.json"})

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

    async def _api_test_connection(self, request: web.Request) -> web.Response:
        """测试 base_url + api_key 能否连通（探测 /v1/models），不需要保存配置。"""
        data = await request.json()
        backend_name = (data.get("backend") or "").lower().strip()
        base_url = data.get("base_url", "").strip()
        api_key = data.get("api_key", "").strip()

        # 如果传来的 api_key 是 ***（脱敏），用当前实例的真实 key
        if api_key == "***":
            current = self.bridge.config.get("backends", {}).get(backend_name, {})
            api_key = current.get("api_key", "")

        if not base_url:
            return web.json_response({"ok": False, "message": "base_url 必填"}, status=400)

        from .backends import _REGISTRY
        cls = _REGISTRY.get(backend_name)
        if cls is None or not hasattr(cls, "test_connection"):
            return web.json_response(
                {"ok": False, "message": f"后端 '{backend_name}' 不支持连接测试"}, status=400
            )

        try:
            ok, msg = await cls.test_connection(base_url, api_key)
            return web.json_response({"ok": ok, "message": msg})
        except Exception as e:
            return web.json_response({"ok": False, "message": f"测试失败: {type(e).__name__}: {e}"}, status=500)

    async def _api_list_backends(self, request: web.Request) -> web.Response:
        """列出所有可用后端 + 元数据（供 WebUI 后端选择下拉框）。"""
        from .backends import list_backends
        backends_meta = {
            "hermes": {
                "label": "Hermes（远程 Gateway）",
                "description": "连接远程 Hermes Gateway 的 OpenAI 兼容 API（端口 8642）",
                "fields": [
                    {"name": "base_url", "label": "Hermes Gateway URL", "placeholder": "http://1.2.3.4:8642/v1", "required": True},
                    {"name": "api_key", "label": "API Key", "placeholder": "Hermes 那边 platforms.api_server.extra.key", "required": True, "sensitive": True},
                    {"name": "model", "label": "Model 名", "placeholder": "hermes-agent", "default": "hermes-agent"},
                    {"name": "system_prompt", "label": "System Prompt", "default": "你是一个 QQ 助手。", "textarea": True},
                    {"name": "temperature", "label": "Temperature", "type": "number", "default": 0.7, "step": 0.1},
                    {"name": "max_tokens", "label": "Max Tokens", "type": "number", "default": 2000},
                ],
                "setup_hint": (
                    "在远程 Hermes 服务器：\n"
                    "1. config.yaml 启用 api_server platform（端口 8642）\n"
                    "2. 配置 platforms.api_server.extra.key 作为 API Key\n"
                    "3. 重启 hermes-gateway"
                ),
            },
            "openclaw": {
                "label": "OpenClaw（远程 Gateway）",
                "description": "连接远程 OpenClaw Gateway 的 OpenAI 兼容 API",
                "fields": [
                    {"name": "base_url", "label": "OpenClaw Gateway URL", "placeholder": "http://1.2.3.4:5757", "required": True, "hint": "自动补 /v1"},
                    {"name": "api_key", "label": "Gateway Token / Password", "placeholder": "gateway.auth.token", "required": True, "sensitive": True},
                    {"name": "model", "label": "Agent Target", "placeholder": "openclaw 或 openclaw/<agentId>", "default": "openclaw"},
                    {"name": "temperature", "label": "Temperature", "type": "number", "default": 0.7, "step": 0.1},
                    {"name": "max_tokens", "label": "Max Tokens", "type": "number", "default": 4000},
                ],
                "setup_hint": (
                    "在远程 OpenClaw 服务器：\n"
                    "1. ~/.openclaw/config.json5 启用：\n"
                    '   gateway: { auth: { mode: "token", token: "your-secret" },\n'
                    "             http: { endpoints: { chatCompletions: { enabled: true } } } }\n"
                    "2. 重启 openclaw gateway"
                ),
            },
        }
        return web.json_response({
            "backends": list_backends(),
            "current": self.bridge.config.get("backend", ""),
            "meta": backends_meta,
        })
