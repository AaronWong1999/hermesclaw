# HermesClaw - Hermes Agent + OpenClaw WeChat Dual-Agent Router

## HermesClaw - Hermes Agent + OpenClaw 微信双开工具

![MIT License](https://img.shields.io/badge/license-MIT-green)
![Author](https://img.shields.io/badge/Author-AaronYon-blue)
![Email](https://img.shields.io/badge/Email-aaronwong1999%40icloud.com-blue)

HermesClaw lets you run Hermes Agent and OpenClaw together in WeChat.
It solves the current gap where Hermes Agent does not natively support WeChat, and it solves the limitation where WeChat currently only has an OpenClaw path with no clean dual-agent routing.
HermesClaw keeps the Clawbot account as the single entrypoint, then routes messages to Hermes Agent, OpenClaw, or both.

HermesClaw 是一个在微信上能够同时双开运行 Hermes Agent 和 OpenClaw 的工具。
现在你可以在微信里同时调用 Hermes Agent 和 OpenClaw 了。
它解决了目前 Hermes Agent 暂不支持微信的问题，也解决了微信目前只支持 OpenClaw、没办法双开的限制。
HermesClaw 保留 Clawbot 账号作为两个智能体的统一入口，再按照你的要求智能路由。

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/AaronWong1999/hermesclaw/main/install.sh | bash
```

Run that one command to install.

运行这一行即可安装。

## AI Install

If you want an AI agent to finish the install, tell it to run the command above first, then keep reading the install output and fixing the environment until `/whoami` works in WeChat.

如果你想让 AI 直接帮你安装，就先让它执行上面的命令，然后持续读取安装输出并修环境，直到微信里的 `/whoami` 可以正常工作。

```text
Run: curl -fsSL https://raw.githubusercontent.com/AaronWong1999/hermesclaw/main/install.sh | bash

Then read README.md and install.sh from the installed HermesClaw directory. Detect whether Hermes, OpenClaw, clawbot/openclaw-weixin, python3, pip3, node, npx, and systemd are installed. Reuse existing config when found. If clawbot is missing, install it first. If Hermes or OpenClaw is missing, stop and print exact next actions instead of guessing. Only patch clawbot to HermesClaw after clawbot itself already works. Keep iterating until /whoami works in WeChat.
```

```text
先执行：curl -fsSL https://raw.githubusercontent.com/AaronWong1999/hermesclaw/main/install.sh | bash

然后读取安装后的 HermesClaw 目录里的 README.md 和 install.sh。检测 Hermes、OpenClaw、clawbot/openclaw-weixin、python3、pip3、node、npx、systemd 是否已安装。能复用现有配置就复用。缺 clawbot 就先安装 clawbot。缺 Hermes 或 OpenClaw 时不要瞎猜，直接输出明确下一步。只有在 clawbot 本身已经工作后，才把它改到 HermesClaw。一直修到微信里的 /whoami 可用为止。
```

The installer will:

安装脚本会：

1. Detect Hermes, OpenClaw, clawbot/openclaw-weixin, Python, and `npx`.
2. Reuse existing config when possible.
3. Install clawbot first if needed and possible.
4. Read the configured Hermes and OpenClaw endpoints, or fall back to the official defaults.
5. Read the iLink token from clawbot account config.
6. Patch clawbot to point at HermesClaw.
7. Write `.env` and install `hermesclaw.service`.

## How It Works

HermesClaw only does routing and forwarding. It does not rewrite agent memory, and it does not try to replace Hermes or OpenClaw behavior.

HermesClaw 只负责路由和转发，不会改写 agent 记忆，也不会试图替代 Hermes 或 OpenClaw 自己的行为。

The default route is Hermes.

默认路由是 Hermes。

Route commands:

路由指令：

| Command | Meaning |
| --- | --- |
| `/hermes` | Route to Hermes |
| `/openclaw` | Route to OpenClaw |
| `/both` | Route to both |
| `/whoami` | Show current route and status |

| 指令 | 含义 |
| --- | --- |
| `/hermes` | 切到 Hermes |
| `/openclaw` | 切到 OpenClaw |
| `/both` | 同时发给两边 |
| `/whoami` | 查看当前路由和状态 |

## Layout

The project stays minimal:

项目保持极简：

```text
hermesclaw.py
README.md
install.sh
LICENSE
```

## Media

HermesClaw forwards text, voice transcription, image, video, and file messages.

HermesClaw 支持转发文本、语音转写、图片、视频和文件消息。

Voice is forwarded as transcription text in this format:

语音会按下面这个格式转发转写文本：

```text
[The user sent a voice message. Here's what they said: "..."]
```

Images, videos, and files are downloaded into the HermesClaw project directory. Hermes receives local file paths instead of base64 blobs.

图片、视频和文件都会下载到 HermesClaw 项目目录。传给 Hermes 的是本地文件路径，不是 base64。

Raw audio bytes are not forwarded.

不会直接转发原始音频字节。

## Uninstall

Use AI for uninstall if the environment is non-standard.

如果环境不是标准安装，建议直接让 AI 来卸载和还原。

```text
Read README.md and inspect the current machine. Find the HermesClaw project directory, the hermesclaw systemd service, and any clawbot/openclaw-weixin account configs that were patched to point at HermesClaw. Stop and disable hermesclaw.service, remove the service file, restore any account config backups (*.json.bak) if they exist, and then remove the HermesClaw project directory only after showing what will be deleted.
```

```text
读取 README.md 并检查当前机器。找到 HermesClaw 项目目录、hermesclaw 的 systemd 服务，以及所有被改成指向 HermesClaw 的 clawbot/openclaw-weixin 账号配置。停止并禁用 hermesclaw.service，删除服务文件，如果存在 *.json.bak 就恢复对应账号配置，最后在展示将要删除的内容后再删除 HermesClaw 项目目录。
```

```bash
sudo systemctl stop hermesclaw
sudo systemctl disable hermesclaw
sudo rm -f /etc/systemd/system/hermesclaw.service
sudo systemctl daemon-reload
find "$HOME" -maxdepth 5 -type f -name "*.json.bak" -path "*/openclaw-weixin/accounts/*" -exec sh -c 'for f; do cp "$f" "${f%.bak}" && rm -f "$f"; done' sh {} +
rm -rf "$HOME/hermesclaw"
```

## License

MIT
