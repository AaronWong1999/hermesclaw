# HermesClaw Troubleshooting

## Hermes Agent splits long messages into multiple WeChat messages

**Symptom**: Hermes 把一条长文按换行拆成多条微信消息发送。

**Root Cause**: Hermes Agent 的微信适配器 (`gateway/platforms/weixin.py`) 默认按换行拆分消息。

**Fix**:

```bash
cd ~/hermesclaw
bash scripts/fix_hermes_splitting.sh
sudo systemctl restart hermes
```

或者手动修改 `~/.hermes/hermes-agent/gateway/platforms/weixin.py`：

```python
# 替换 _split_delivery_units_for_weixin 函数为：
def _split_delivery_units_for_weixin(content: str) -> List[str]:
    """Return content as a single unit; splitting is handled by length limits only."""
    return [content] if content.strip() else []

# 替换 _split_text_for_weixin_delivery 函数为：
def _split_text_for_weixin_delivery(content: str, max_length: int) -> List[str]:
    """Split content only when it exceeds max_length; no newline-based splitting."""
    if len(content) <= max_length:
        return [content]
    return _pack_markdown_blocks_for_weixin(content, max_length) or [content]
```

---

## OpenClaw sends "Edit failed" errors

**Symptom**: OpenClaw 发送 `[OpenClaw] ⚠️ 📝 Edit: in ~/.openclaw/openclaw.json failed`。

**Root Cause**: 不是权限问题。OpenClaw Agent 尝试自动修改配置文件但 `oldText` 与实际内容不匹配。通常是因为配置中包含无效的 model（如 `manifest/auto`）。

**Fix**:

1. 检查配置：`cat ~/.openclaw/openclaw.json | python3 -m json.tool`
2. 确保 `agents.defaults.model.primary` 指向有效模型，如 `openrouter/openai/gpt-oss-120b:free`
3. 移除无效的 model 配置（如 `manifest/auto`）
4. 重启 OpenClaw：`systemctl --user restart openclaw`

---

## BrokenPipeError in logs

**Symptom**: 日志出现 `BrokenPipe on write-back (benign): ilink/bot/sendmessage`

**Root Cause**: Gateway 在 HermesClaw 写回响应前断开连接。**消息已成功发送**，只是响应回写失败。

**Action**: 无需处理，这是正常现象。

---

## One gateway gets 403 errors

**Symptom**: 某个 Gateway 报 403 错误。

**Root Cause**: Gateway 直连了 iLink 而不是通过 HermesClaw 代理。

**Fix**:

**OpenClaw**: 检查 `~/.openclaw/**/openclaw-weixin/accounts/*.json` 中 `"baseUrl"` 是否为 `http://127.0.0.1:19999`

**Hermes**: 检查 `~/.hermes/.env` 中 `WEIXIN_BASE_URL` 是否为 `http://127.0.0.1:19998`

重新运行安装脚本可自动修复。

---

## Service won't start

**Check logs**: `journalctl -u hermesclaw -n 50 --no-pager`

**Common causes**:
- 缺少 `ILINK_TOKEN` 环境变量
- 端口 19998/19999 被占用
- 缺少 Python 依赖 (`requests`, `python-dotenv`)

---

## Getting Help

1. 查看日志：`journalctl -u hermesclaw -f --no-pager`
2. 检查服务状态：`sudo systemctl status hermesclaw`
3. 测试命令：在微信发送 `/whoami` 应显示当前路由状态
4. 提交 GitHub Issue：附上相关日志片段
