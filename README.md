# qq-agent-bridge 🌉

> **一座桥，把 QQ 连到任何 AI Agent CLI。** 
> 配置文件改一行 → 切换 Hermes / OpenClaude / Claude Code / OpenAI 兼容 API。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![OneBot v11](https://img.shields.io/badge/OneBot-v11-green)](https://onebot.dev)

```
        QQ (NapCat)
            │ OneBot v11 WS
            ▼
  ┌──────────────────────────┐
  │   qq-agent-bridge        │   ← 这里
  │   消息路由 + 权限 + 会话  │
  └──────────┬───────────────┘
             │
   ┌─────────┼─────────┐──────────┐
   ▼         ▼         ▼          ▼
 Hermes  OpenClaude  Claude     OpenAI
                     Code       兼容API
                                (DeepSeek/火山/月之暗面…)
```

---

## ⚡ 30 秒上手

```bash
# 1. 拿代码
git clone https://github.com/kangleizhui/qq-agent-bridge.git
cd qq-agent-bridge

# 2. 装依赖（推荐用 venv）
pip install -e .

# 3. 改配置
cp config/config.example.yaml config/config.yaml
# 编辑：填 self_id（机器人 QQ）、access_token、选 backend、owner

# 4. 启动
qq-agent-bridge config/config.yaml
```

然后让 NapCat 反向连过来（NapCat 配置里加一个 WebSocket Client，URL 填 `ws://127.0.0.1:8080/onebot/v11/ws`），即可。

---

## 🎯 它解决什么问题？

**问题**：你有一个 AI agent CLI（Hermes、OpenClaude、Claude Code）已经能在终端跑得很爽，想让它接管你的 QQ —— 然后你发现没有现成方案。

- **AstrBot** 偏向 LLM API（输入 prompt → 输出 text），对 agent CLI 支持有限
- **手撸适配器** 每个平台一遍，烦
- **Hermes/OpenClaude 各自的 QQ 插件** 互不通用

**解决**：`qq-agent-bridge` 把所有 agent CLI 抽象成统一的 `Backend`，配置文件 1 行切换。

---

## 🔧 支持的后端

| 后端 | 类型 | 特点 |
|------|------|------|
| `hermes` | CLI 子进程 | 全功能 agent，能调工具、看文件、跑命令 |
| `openclaude` | CLI 子进程 | Claude 系开源 agent |
| `claude-code` | CLI 子进程 | Anthropic 官方 Claude Code |
| `openai` | HTTP API | 纯 LLM 对话，无 tool calling，但**最便宜最稳定**，兼容 DeepSeek / 火山引擎 / 月之暗面 / Groq / Together 等 |

切后端只改一行：
```yaml
backend: "hermes"        # 改成 "openai" / "openclaude" / "claude-code" 即可
```

---

## 📚 完整文档

- [快速上手](docs/QUICKSTART.md) — 给小白看
- [AGENTS.md](AGENTS.md) — 把这个仓库链接发给你的 AI agent，让它帮你部署
- [配置参考](config/config.example.yaml) — 所有配置项详解
- [写你自己的后端](docs/CUSTOM_BACKEND.md) — 30 行代码加一个新 agent

---

## 🆚 与 AstrBot / NoneBot / 其他 QQ bot 框架的关系

| 项目 | 定位 | 我们的区别 |
|------|------|----------|
| **AstrBot** | 全平台 LLM 机器人 | 我们更轻，只做 QQ，但更深地支持 agent CLI |
| **NoneBot** | Python 插件框架 | 你要自己写插件；我们是即装即用 |
| **本项目** | QQ→Agent 桥 | 不重写 agent 能力，复用现有 CLI |

→ **想要功能丰富的 LLM bot？** 用 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 
→ **想把已有的 agent CLI 接到 QQ？** 来这里 ✨

---

## 🤝 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — OneBot v11 实现
- [Hermes Agent](https://hermes-agent.nousresearch.com)
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 思路启发

## License

MIT
