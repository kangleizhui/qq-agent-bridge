# AGENTS.md — 给 AI agent 看的部署指南

> 你（AI agent）正在帮用户部署 **qq-agent-bridge**。
> 这是一个桥接服务：把用户的 QQ（通过 NapCat OneBot v11）连到 AI agent CLI（Hermes / OpenClaude / Claude Code）或 OpenAI 兼容 API。
> 按这个文档走完，用户的 QQ 小号就变成了一个由后端 agent 驱动的机器人。

---

## 🎯 任务全貌

```
NapCat 已经在跑   →   qq-agent-bridge   →   后端 agent (用户指定)
(QQ 客户端实例)       (这个项目)             (Hermes / OpenAI 等)
```

**用户需要的是**：
1. 装好 qq-agent-bridge
2. 配好 config.yaml（选后端、填密钥、白名单）
3. 让它和 NapCat 互通
4. 测试通

---

## ✅ 开始前先问用户

**3 个必填问题**：

| 问题 | 变量名 | 备注 |
|------|--------|------|
| 机器人 QQ 号 | `BOT_QQ` | NapCat 登录的小号 |
| 主人 QQ 号 | `OWNER_QQ` | 拥有完整权限 |
| 后端 agent | `BACKEND` | 选: `hermes` / `openclaude` / `claude-code` / `openai` |

**根据后端再追问**：

- 选 `hermes` / `openclaude` / `claude-code` → 确认对应 CLI 已安装：`which hermes` / `which openclaude` / `which claude`
- 选 `openai` → 问用户 API 提供商（DeepSeek/火山/月之暗面/OpenAI…），拿到 `base_url`、`api_key`、`model` 名

---

## 🔍 前置环境

```bash
# Python 3.10+
python3 --version

# NapCat 在跑（已经接好 OneBot v11）
ss -tlnp | grep -E ":(3000|3001|6099)\b"

# pip / git
which pip git
```

如果 NapCat 还没装，先让用户去：https://github.com/kangleizhui/hermes-qq-bot （那个项目的 install.sh 会装 NapCat）

---

## 🚀 部署步骤

### Step 1: 克隆 + 安装

```bash
cd /root
git clone https://github.com/kangleizhui/qq-agent-bridge.git
cd qq-agent-bridge

# 推荐 venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Step 2: 生成配置文件

```bash
cp config/config.example.yaml config/config.yaml
```

用 sed / Python 把这些值替换进去：

| 字段 | 替换成 |
|------|--------|
| `onebot.self_id` | `<BOT_QQ>` |
| `onebot.access_token` | 用 `openssl rand -hex 16` 随机生成 |
| `backend` | `<用户选的后端>` |
| `permissions.owners` | `["<OWNER_QQ>"]` |
| `backends.<backend>.*` | 用户提供的具体配置（base_url/api_key/model 等）|

> ⚠️ `openai` 后端的 `api_key` 强烈建议写成 `${VAR_NAME}` 然后用环境变量注入，别明文。

### Step 3: 配置 NapCat 反向 WS 连过来

让 NapCat 主动连到这个桥的 WS 端口。编辑 NapCat 配置：

```bash
NAPCAT_CFG="/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/onebot11_<BOT_QQ>.json"
```

在 `network.websocketClients` 数组里加一项：
```json
{
    "name": "to-qq-agent-bridge",
    "enable": true,
    "url": "ws://127.0.0.1:8080/onebot/v11/ws",
    "messagePostFormat": "array",
    "reportSelfMessage": false,
    "token": "<跟 config.yaml 里 access_token 一致>",
    "reconnectInterval": 5000,
    "heartInterval": 30000,
    "debug": false
}
```

重启 NapCat：
```bash
sudo systemctl restart napcat
```

### Step 4: 起 bridge 服务

```bash
cd /root/qq-agent-bridge
source .venv/bin/activate
qq-agent-bridge config/config.yaml
```

日志里应该看到：
```
OneBot WS server listening on ws://0.0.0.0:8080/onebot/v11/ws
OneBot client connected: self_id=<BOT_QQ>
```

如果没看到 `client connected`，说明 NapCat 没连过来 —— 检查端口/token/url。

### Step 5: 注册成 systemd service（持久化）

```bash
sudo tee /etc/systemd/system/qq-agent-bridge.service >/dev/null <<EOF
[Unit]
Description=qq-agent-bridge
After=network.target napcat.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/qq-agent-bridge
ExecStart=/root/qq-agent-bridge/.venv/bin/qq-agent-bridge /root/qq-agent-bridge/config/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now qq-agent-bridge
sudo systemctl status qq-agent-bridge --no-pager
```

### Step 6: 让用户实测

告诉用户：用主号给机器人 `<BOT_QQ>` 发"你好"试试。

应该收到后端 agent 的回复。

---

## ⚠️ 关键踩坑

### 1. CLI 后端（hermes/openclaude/claude-code）需要 PTY

这些 CLI 通常在伪终端环境下才能正常运行。我们的 `CLIBackend` 默认用 `pty.openpty()`，但**注意**：

- agent CLI 必须能**无交互启动**（不要求扫码/登录）。先在终端跑一遍 `hermes --version` 之类，确认能跑。
- 第一次启动慢（5-15 秒），第一条消息会等久一点。
- 子进程会在 `workdir` 下跑，那里是 agent 的工作空间。

### 2. openai 后端的 API key 不要硬编码

用 `${OPENAI_API_KEY}` 引用环境变量。然后在 systemd unit 加：
```ini
Environment=OPENAI_API_KEY=sk-xxx
```
或者用 `EnvironmentFile=/root/qq-agent-bridge/.env`

### 3. 群里 @ 才回复

默认 `permissions.group_require_at: true`。如果用户希望群里"不 @ 也回"，改成 `false`，但小心被群友刷屏。

### 4. 长输出处理

CLI 后端 agent 可能产生几千字的回复。配置：
```yaml
advanced:
  long_output: "split"           # split / truncate / file
  long_output_threshold: 2000
```

---

## 🐛 排错速查

| 症状 | 检查 |
|------|------|
| `OneBot client not connected` | NapCat 的 websocketClients 配置 + token |
| 后端无响应 | 终端跑一遍 `<binary> --help` 确认 CLI 能起 |
| `unauthorized` (401) | access_token 两边对不上 |
| 群消息没反应 | 群在白名单里吗？群里是否 @ 了机器人？ |
| `BOT_QQ` 收消息发不出 | 检查 NapCat 的 OneBot HTTP/WS server 是否启用 |

```bash
# 看 bridge 日志
sudo journalctl -u qq-agent-bridge -n 100 --no-pager

# 看 NapCat 日志
sudo journalctl -u napcat -n 100 --no-pager
```

---

## ✅ 完成确认清单

- [ ] `sudo systemctl is-active qq-agent-bridge` → `active`
- [ ] `sudo systemctl is-active napcat` → `active`
- [ ] `config/config.yaml` 已生成且 backend 选好
- [ ] NapCat 的 `websocketClients` 已配置反向 WS
- [ ] 用户用主号给 BOT_QQ 发消息能秒回

全部 ✅ → 部署完成，告诉用户：
> "现在用主号给小号 `<BOT_QQ>` 发条消息试试，后端是 `<BACKEND>` 🎉"

---

## 🤝 配套项目

如果用户还没装 NapCat：
- → 先去 https://github.com/kangleizhui/hermes-qq-bot 跑那个项目的 `install.sh`（专门负责 NapCat 部署）
- → 然后回来跑这个 qq-agent-bridge（专门负责 AI 后端路由）

两个项目互补：**hermes-qq-bot** 是 NapCat 部署 + 默认 Hermes 集成；**qq-agent-bridge** 是后端灵活切换。
