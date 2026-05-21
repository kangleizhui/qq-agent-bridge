"""
OneBot v11 反向 WebSocket 服务端
NapCat 作为客户端连进来，我们接收事件、发送 action
"""
import asyncio
import json
import logging
from typing import Optional, Dict, Any, Callable, Awaitable
import uuid

from aiohttp import web, WSMsgType

log = logging.getLogger(__name__)


class OneBotServer:
    """OneBot v11 reverse WS server。"""

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        access_token: str,
        on_message: Callable[[Dict[str, Any]], Awaitable[None]],
    ):
        self.host = host
        self.port = port
        self.path = path
        self.access_token = access_token
        self.on_message = on_message

        self._ws: Optional[web.WebSocketResponse] = None
        self._echo_waiters: Dict[str, asyncio.Future] = {}
        self._runner: Optional[web.AppRunner] = None

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get(self.path, self._handle_ws)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info("OneBot WS server listening on ws://%s:%d%s", self.host, self.port, self.path)

    async def stop(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        # 鉴权
        auth = request.headers.get("Authorization", "")
        token = request.headers.get("X-Self-ID", "")  # 有些实现把 token 放 Auth header
        if self.access_token:
            expected = f"Bearer {self.access_token}"
            qs_token = request.query.get("access_token", "")
            if auth != expected and qs_token != self.access_token:
                log.warning("OneBot WS rejected: bad token")
                return web.Response(status=401, text="unauthorized")

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._ws = ws
        log.info("OneBot client connected: self_id=%s", token)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        log.warning("invalid json from onebot: %s", msg.data[:200])
                        continue
                    await self._route(data)
                elif msg.type == WSMsgType.ERROR:
                    log.error("ws error: %s", ws.exception())
        finally:
            self._ws = None
            log.info("OneBot client disconnected")
        return ws

    async def _route(self, data: Dict[str, Any]) -> None:
        # action 响应（带 echo 字段）
        if "echo" in data and data["echo"] in self._echo_waiters:
            self._echo_waiters[data["echo"]].set_result(data)
            return
        # 事件
        post_type = data.get("post_type")
        if post_type == "message":
            asyncio.create_task(self.on_message(data))
        # meta_event 心跳等忽略

    async def call_action(self, action: str, params: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        if not self.connected:
            raise RuntimeError("OneBot client not connected")
        echo = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._echo_waiters[echo] = fut
        try:
            await self._ws.send_str(json.dumps({"action": action, "params": params, "echo": echo}))
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._echo_waiters.pop(echo, None)

    # ── 便利方法 ────────────────────────────────
    async def send_private_msg(self, user_id: int | str, text: str) -> None:
        await self.call_action("send_private_msg", {"user_id": int(user_id), "message": text})

    async def send_group_msg(self, group_id: int | str, text: str, at_user: Optional[str] = None) -> None:
        message = text
        if at_user:
            message = f"[CQ:at,qq={at_user}] {text}"
        await self.call_action("send_group_msg", {"group_id": int(group_id), "message": message})
