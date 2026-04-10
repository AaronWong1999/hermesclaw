# HermesClaw outreach leads — generated 2026-04-10T18:25+00:00

**Workflow**: for each lead below,
1. Read the issue (don't trust the excerpt blindly — context matters)
2. Edit the draft comment so it actually fits what the user is asking
3. Post manually on GitHub
4. Tick the `[ ] posted` checkbox so this entry doesn't reappear

**Pace yourself**: max 5–8 comments/day, spread across hours. Comment bursts trigger anti-spam — even with hand-typed text.

---

## [NousResearch/hermes-agent] [Feature]: WeChat messaging platform support

- **URL**: https://github.com/NousResearch/hermes-agent/issues/2422
- **Author**: @Vivien202  •  **State**: open  •  **Comments**: 0  •  **Created**: 2026-03-22

> ### Problem or Use Case WeChat just officially announced OpenClaw integration today via WeixinClawBot — users can now chat with OpenClaw directly in WeChat like texting a friend. https://x.com/Weixin_WeChat/status/2035537088314290236 Hermes already supports multiple platforms, an…

**Draft comment** (edit before posting):

```
Hey @Vivien202 —

saw you wrote: "WeChat just officially announced OpenClaw integration today via WeixinClawBot — users can now chat with OpenClaw directl…"

You linked the WeixinClawBot / clawbot integration in the issue — HermesClaw plugs into exactly that path.

I hit the same wall (Hermes Agent has no WeChat gateway out of the box) and ended up building a small bridge for it called HermesClaw. It piggybacks on the Clawbot / openclaw-weixin iLink path, so if you're already running OpenClaw on WeChat you don't need a second account. `/hermes` `/openclaw` `/both` switches the route per user.

Repo: https://github.com/AaronWong1999/hermesclaw — there's a one-line installer in the README. Happy to help if you get stuck setting it up; ping me here or file an issue on the HermesClaw repo.
```

- [ ] posted

---

## [NousResearch/hermes-agent] [Feature]: Can we integrate Feishu and WeChat robots?

- **URL**: https://github.com/NousResearch/hermes-agent/issues/5986
- **Author**: @myfxlogs  •  **State**: open  •  **Comments**: 1  •  **Created**: 2026-04-08

> ### Problem or Use Case Can we integrate Feishu and WeChat robots? ### Proposed Solution Can we integrate Feishu and WeChat robots? ### Alternatives Considered _No response_ ### Feature Type New tool ### Scope None ### Contribution - [ ] I'd like to implement this myself and subm…

**Draft comment** (edit before posting):

```
Hey @myfxlogs —

saw you wrote: "Can we integrate Feishu and WeChat robots?"

I hit the same wall (Hermes Agent has no WeChat gateway out of the box) and ended up building a small bridge for it called HermesClaw. It piggybacks on the Clawbot / openclaw-weixin iLink path, so if you're already running OpenClaw on WeChat you don't need a second account. `/hermes` `/openclaw` `/both` switches the route per user.

(For Feishu specifically, HermesClaw doesn't cover that — it's WeChat-only. For Feishu I'd file a separate request upstream or look at OpenClaw's Feishu channel.)

Repo: https://github.com/AaronWong1999/hermesclaw — there's a one-line installer in the README. Happy to help if you get stuck setting it up; ping me here or file an issue on the HermesClaw repo.
```

- [ ] posted

---

## [NousResearch/hermes-agent] hermes接入企业微信bot后相应慢，无法收发文件，找不到gateway模式运行日志。

- **URL**: https://github.com/NousResearch/hermes-agent/issues/6515
- **Author**: @OokamiForest  •  **State**: open  •  **Comments**: 0  •  **Created**: 2026-04-09

> 1. 接入企业微信bot后相应慢（用的minimax token plan）,可以优化吗？ 2. 聊天中发送的文档无法被存入Agent本地处理，产出文件无法通过聊天直接发送至用户。这个是hermes框架不支持还是Wecom企业微信的问题？ 3. gateway模式连接的日志在哪里？ 4. gateway模式运行的时候，是否有平台可以看到运行过程？ Eng Version: 1. After connecting to the WeCom bot, the response is slow (using the Minimax Token Plan). C…

**Draft comment** (edit before posting):

```
Hi @OokamiForest，

看到你提到「接入企业微信bot后相应慢（用的minimax token plan）,可以优化吗？」 ——

我前段时间也踩了一样的坑（Hermes Agent 不支持微信），就写了一个小工具叫 HermesClaw 来桥接：它复用 Clawbot / openclaw-weixin 已有的 iLink 通道，在同一个微信 Clawbot 账号上同时跑 Hermes 和 OpenClaw，用 `/hermes` `/openclaw` `/both` 切换路由。

提醒一下：HermesClaw 走的是个人微信（基于 Clawbot / openclaw-weixin 的 iLink 通道），不是企业微信。两边的协议不一样。如果你必须用企业微信，这个工具帮不上；但如果个人微信账号能满足你的场景，它正好对得上。

仓库在 https://github.com/AaronWong1999/hermesclaw，README 里有一行 `curl | bash` 安装。如果踩坑了 在这里 at 我或者去 HermesClaw 仓库提 issue 都行。
```

- [ ] posted

---
