"""
CLI 子进程后端基类 —— Hermes / OpenClaude / Claude Code 都基于这个

设计思路：
  - 每个 session 启动一个独立的 agent CLI 子进程
  - 通过 stdin 喂消息，从 stdout 读回复
  - 用 PTY 模式跑（这些 CLI 通常需要伪终端）
  - 多轮对话靠子进程持续存活
"""
import asyncio
import logging
import os
import pty
import re
import select
import shlex
import signal
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from pathlib import Path

from .base import Backend, ChatContext, AgentResponse

log = logging.getLogger(__name__)

# ANSI 颜色码清除
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


@dataclass
class CLISession:
    """一个 agent CLI 子进程实例。"""
    pid: int
    master_fd: int
    proc: subprocess.Popen
    workdir: str
    busy: asyncio.Lock = field(default_factory=asyncio.Lock)


class CLIBackend(Backend):
    """所有 agent CLI 后端的通用实现。子类只需指定 binary 和 extra_args。"""

    name = "cli"
    default_binary = ""
    default_args: list = []
    # 用于判定"agent 输出结束"的提示符正则（子类按需覆盖）
    prompt_pattern = re.compile(r"(\n[>$#❯]\s*$|\n\? )")

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.binary = config.get("binary") or self.default_binary
        self.extra_args = config.get("extra_args", self.default_args)
        self.workdir = Path(os.path.expanduser(config.get("workdir", "~/qq-agent-workspace")))
        self.workdir.mkdir(parents=True, exist_ok=True)
        # session_key -> CLISession
        self._sessions: Dict[str, CLISession] = {}
        self._global_lock = asyncio.Lock()

    async def _spawn_session(self, session_key: str) -> CLISession:
        """启动一个 CLI 子进程。"""
        cmd = [self.binary, *self.extra_args]
        log.info("[%s] spawning: %s (cwd=%s)", self.name, " ".join(shlex.quote(c) for c in cmd), self.workdir)

        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            cmd,
            cwd=self.workdir,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            env={**os.environ, "TERM": "xterm-256color"},
        )
        os.close(slave_fd)

        sess = CLISession(pid=proc.pid, master_fd=master_fd, proc=proc, workdir=str(self.workdir))
        # 等几秒让 agent 启动完毕，把启动横幅吃掉
        await asyncio.sleep(2.0)
        self._drain(sess, timeout=1.0)
        return sess

    def _drain(self, sess: CLISession, timeout: float = 0.5) -> str:
        """从 PTY 读取所有当前可读数据。"""
        output = []
        end = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = end - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            ready, _, _ = select.select([sess.master_fd], [], [], min(remaining, 0.1))
            if not ready:
                # 没数据来了，再等一小会儿确认确实结束
                ready2, _, _ = select.select([sess.master_fd], [], [], 0.3)
                if not ready2:
                    break
                continue
            try:
                chunk = os.read(sess.master_fd, 4096)
                if not chunk:
                    break
                output.append(chunk.decode("utf-8", errors="replace"))
            except OSError:
                break
        return "".join(output)

    async def _send_and_read(self, sess: CLISession, message: str, timeout: float = 90.0) -> str:
        """写入消息，读完整回复。"""
        # 清掉残留
        self._drain(sess, timeout=0.2)

        os.write(sess.master_fd, (message + "\n").encode("utf-8"))

        # 等回复（启发式：先 sleep 1s，再轮询读到一段时间没新内容为止）
        await asyncio.sleep(1.0)

        output = []
        idle_count = 0
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            chunk = self._drain(sess, timeout=2.0)
            if chunk:
                output.append(chunk)
                idle_count = 0
            else:
                idle_count += 1
                # 连续 2 次 2 秒都没新输出，判定输出结束
                if idle_count >= 2:
                    break
            await asyncio.sleep(0.5)

        text = strip_ansi("".join(output))
        # 去掉用户输入的回显（PTY 模式 stdin 会回显）
        lines = text.splitlines()
        if lines and message[:50] in lines[0]:
            lines = lines[1:]
        # 去掉末尾的 prompt
        while lines and self.prompt_pattern.search("\n" + lines[-1]):
            lines.pop()
        return "\n".join(lines).strip()

    async def ask(self, message: str, ctx: ChatContext) -> AgentResponse:
        key = ctx.session_key
        async with self._global_lock:
            sess = self._sessions.get(key)
            if sess is None or sess.proc.poll() is not None:
                sess = await self._spawn_session(key)
                self._sessions[key] = sess

        async with sess.busy:
            try:
                text = await self._send_and_read(sess, message)
                if not text:
                    text = "（无回复 —— agent 可能在沉思，再试一次？）"
                return AgentResponse(text=text)
            except Exception as e:
                log.exception("CLIBackend.ask failed")
                return AgentResponse(text="", error=f"{type(e).__name__}: {e}")

    async def reset_session(self, ctx: ChatContext) -> None:
        key = ctx.session_key
        async with self._global_lock:
            sess = self._sessions.pop(key, None)
        if sess:
            try:
                os.killpg(os.getpgid(sess.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.close(sess.master_fd)
            except OSError:
                pass

    async def shutdown(self) -> None:
        for key in list(self._sessions.keys()):
            await self.reset_session(ChatContext(user_id="", chat_id="", chat_kind="user", session_key=key))


class HermesBackend(CLIBackend):
    name = "hermes"
    default_binary = "hermes"
    default_args = []  # mode 在子类里处理


class OpenClaudeBackend(CLIBackend):
    name = "openclaude"
    default_binary = "openclaude"
    default_args = []


class ClaudeCodeBackend(CLIBackend):
    name = "claude-code"
    default_binary = "claude"
    default_args = ["--no-tty"]
