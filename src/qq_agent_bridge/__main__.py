"""
qq-agent-bridge 主程序

启动顺序：
  1. 加载 config.yaml
  2. 创建后端（hermes/openclaude/claude-code/openai）
  3. 创建 aiohttp app，OneBot WS + WebUI 共用同一个 app（同端口）
  4. 等 NapCat 连接
  5. 收到消息 → 权限检查 → 路由到后端 → 回复
"""
import asyncio
import logging
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Any

import yaml
from aiohttp import web

from .backends import create_backend
from .backends.base import ChatContext
from .session import SessionManager
from .permissions import PermissionChecker
from .onebot import OneBotServer
from .webui import WebUIServer

log = logging.getLogger("qq-agent-bridge")

CQ_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]")
CQ_GENERIC_RE = re.compile(r"\[CQ:[^\]]+\]")


def parse_message(raw: Any) -> str:
    """OneBot message 字段可能是 string 或 array 格式"""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts = []
        for seg in raw:
            t = seg.get("type")
            if t == "text":
                parts.append(seg.get("data", {}).get("text", ""))
            elif t == "at":
                parts.append(f"[CQ:at,qq={seg.get('data', {}).get('qq')}]")
            elif t == "image":
                parts.append("[图片]")
            elif t == "face":
                parts.append("[表情]")
            elif t == "reply":
                pass  # 忽略
        return "".join(parts).strip()
    return str(raw).strip()


class Bridge:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.self_id = str(config["onebot"]["self_id"])
        self._start_ts = time.time()

        backend_name = config.get("backend", "openai")
        log.info("初始化后端: %s", backend_name)
        self.backend = create_backend(backend_name, config)

        self.sessions = SessionManager(
            scope=config.get("session", {}).get("scope", "per_chat"),
            timeout=config.get("session", {}).get("timeout", 1800),
            history_turns=config.get("session", {}).get("history_turns", 10),
        )
        self.perms = PermissionChecker(config.get("permissions", {}))

        self.group_require_at = self.perms.group_require_at
        self._semaphore = asyncio.Semaphore(config.get("advanced", {}).get("max_concurrent", 5))

        # ── 共用 aiohttp app（OneBot WS + WebUI 同端口）──
        self.app = web.Application()

        # OneBot WS
        self.onebot = OneBotServer(
            host=config["onebot"].get("ws_host", "0.0.0.0"),
            port=config["onebot"].get("ws_port", 8080),
            path=config["onebot"].get("ws_path", "/onebot/v11/ws"),
            access_token=config["onebot"].get("access_token", ""),
            on_message=self._handle_message,
        )

        # WebUI
        webui_cfg = config.get("webui", {})
        self.webui = WebUIServer(
            bridge=self,
            username=webui_cfg.get("username", "admin"),
            password=webui_cfg.get("password", "admin"),
        )

    async def start(self) -> None:
        await self.backend.start()

        # 注册路由到共用 app
        self.app.router.add_get(self.onebot.path, self.onebot._handle_ws)
        self.webui.attach(self.app, prefix="/webui")

        # 启动 HTTP 服务
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner,
            self.onebot.host,
            self.onebot.port,
        )
        await site.start()
        log.info(
            "服务已启动 → http://%s:%d  (OneBot WS: %s | WebUI: /webui)",
            self.onebot.host, self.onebot.port, self.onebot.path,
        )

    async def stop(self) -> None:
        if hasattr(self, "_runner"):
            await self._runner.cleanup()
        await self.backend.shutdown()

    async def _handle_message(self, event: Dict[str, Any]) -> None:
        async with self._semaphore:
            await self._handle_one(event)

    async def _handle_one(self, event: Dict[str, Any]) -> None:
        message_type = event.get("message_type")  # "private" or "group"
        raw_msg = event.get("message", "")
        text = parse_message(raw_msg)
        user_id = str(event.get("user_id", ""))
        sender = event.get("sender", {})
        nickname = sender.get("card") or sender.get("nickname") or user_id

        if message_type == "private":
            chat_id = user_id
            chat_kind = "user"
            cleaned_text = text
        elif message_type == "group":
            chat_id = str(event.get("group_id"))
            chat_kind = "group"
            # 检查是否 @ 我
            at_targets = CQ_AT_RE.findall(text)
            at_me = self.self_id in at_targets
            if self.group_require_at and not at_me:
                return  # 没 @ 我，不理
            # 去掉所有 [CQ:...]
            cleaned_text = CQ_GENERIC_RE.sub("", text).strip()
        else:
            return

        if not cleaned_text:
            return

        ctx = ChatContext(
            user_id=user_id,
            chat_id=chat_id,
            chat_kind=chat_kind,
            user_nickname=nickname,
        )

        # 内置命令
        if cleaned_text.strip() in ("/reset", "/clear", "/重置"):
            self.sessions.reset(ctx)
            await self.backend.reset_session(ctx)
            await self._reply(ctx, "✅ 会话已重置")
            return
        if cleaned_text.strip() in ("/help", "/帮助"):
            await self._reply(ctx, self._help_text())
            return
        if cleaned_text.strip() in ("/backend", "/status"):
            await self._reply(ctx, f"当前后端: {self.backend.name}")
            return

        # 权限
        if not self.perms.allowed(ctx):
            log.info("拒绝 user=%s chat=%s: not whitelisted", user_id, chat_id)
            return

        # 准备 session
        self.sessions.get(ctx)

        log.info("→ [%s] %s (%s): %s", chat_kind, nickname, user_id, cleaned_text[:80])
        self.webui.message_log.add(direction="in", user=nickname, text=cleaned_text[:500])

        resp = await self.backend.ask(cleaned_text, ctx)

        if resp.error:
            self.webui.message_log.add(direction="err", text=resp.error[:500])
            await self._reply(ctx, f"❌ {resp.error}")
            return

        if not resp.text:
            return

        self.sessions.record(ctx, cleaned_text, resp.text)
        self.webui.message_log.add(direction="out", text=resp.text[:500])
        await self._reply(ctx, resp.text)

    async def _reply(self, ctx: ChatContext, text: str) -> None:
        # 长文本切片
        threshold = self.config.get("advanced", {}).get("long_output_threshold", 2000)
        mode = self.config.get("advanced", {}).get("long_output", "split")
        chunks = [text]
        if mode == "split" and len(text) > threshold:
            chunks = [text[i:i+threshold] for i in range(0, len(text), threshold)]
        elif mode == "truncate" and len(text) > threshold:
            chunks = [text[:threshold] + "\n…(已截断)"]

        for chunk in chunks:
            try:
                if ctx.chat_kind == "user":
                    await self.onebot.send_private_msg(ctx.chat_id, chunk)
                else:
                    await self.onebot.send_group_msg(ctx.chat_id, chunk, at_user=ctx.user_id)
            except Exception as e:
                log.error("发送失败: %s", e)
                return
            await asyncio.sleep(0.3)

    def _help_text(self) -> str:
        return (
            f"qq-agent-bridge | 后端: {self.backend.name}\n"
            "命令:\n"
            "  /reset    重置当前会话\n"
            "  /backend  查看后端\n"
            "  /help     帮助\n"
            "群里 @ 我才会回复。"
        )


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def main_async(config_path: str) -> None:
    config = load_config(config_path)
    setup_logging(config.get("advanced", {}).get("log_level", "INFO"))
    log.info("qq-agent-bridge 启动中… config=%s", config_path)

    bridge = Bridge(config)
    await bridge.start()

    stop_event = asyncio.Event()

    def _shutdown() -> None:
        log.info("收到关闭信号")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    await stop_event.wait()
    log.info("正在停止…")
    await bridge.stop()
    log.info("已停止")


def main() -> None:
    config_path = os.environ.get("QAB_CONFIG", "./config/config.yaml")
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    if not Path(config_path).exists():
        print(f"配置文件不存在: {config_path}", file=sys.stderr)
        print("从 config/config.example.yaml 复制一份再改即可。", file=sys.stderr)
        sys.exit(1)
    asyncio.run(main_async(config_path))


if __name__ == "__main__":
    main()
