#!/usr/bin/env python3
"""部署 / 回滚结果通知（企业微信或飞书群机器人）。

由 GitHub Actions 通过环境变量驱动（见 .github/workflows/deploy.yml、rollback.yml）：

- 未配置 `NOTIFY_WEBHOOK` 时静默跳过，不影响部署结果；
- Webhook 主机做域名白名单校验，避免误配成内网地址产生 SSRF；
- 任何发送失败都只打印告警并以 0 退出——通知不应影响部署判定；
- 日志只打印 Webhook 主机名，绝不回显完整 URL（其路径含机器人密钥）。

环境变量：
  NOTIFY_WEBHOOK   群机器人 Webhook（必填，未配置则跳过）
  NOTIFY_PROVIDER  wecom（默认）| feishu
  NOTIFY_STATUS    success | failure | cancelled（用于判定成功与否）
  NOTIFY_TITLE     动作名，如「部署」「回滚」
  ENV_NAME         目标环境 test|prod
  COMPONENTS       组件（部署时有意义）
  GIT_REF          代码分支
  COMMIT_SHA       提交号
  SKIP_GATE        "true" 表示已跳过测试门禁
  AUTO_ROLLBACK    "yes" 表示已自动回滚
  ACTOR            触发人
  RUN_URL          Actions 运行链接
"""
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

ALLOWED_HOSTS = ("qyapi.weixin.qq.com", "open.feishu.cn")


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def masked(url):
    """只保留主机名与路径前缀，避免 Webhook 密钥泄漏进日志。"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path[:12]}..."


def build_message():
    ok = env("NOTIFY_STATUS", "success") == "success"
    action = env("NOTIFY_TITLE", "部署")
    icon = "✅" if ok else "❌"

    scope = env("ENV_NAME") or "-"
    if env("COMPONENTS"):
        scope += f" / {env('COMPONENTS')}"
    lines = [f"{icon} {action}{'成功' if ok else '失败'}（{scope}）"]

    if env("GIT_REF"):
        lines.append(f"分支: {env('GIT_REF')} @ {env('COMMIT_SHA')[:8]}")
    if env("SKIP_GATE") == "true":
        lines.append("⚠️ 已跳过测试门禁（紧急发布，请事后补测）")
    if env("AUTO_ROLLBACK") == "yes":
        lines.append("⚠️ 健康检查未通过，已自动回滚到部署前版本，请人工确认服务状态")
    if not ok:
        lines.append("请到 Actions 日志查看失败原因")
    if env("ACTOR"):
        lines.append(f"操作人: {env('ACTOR')}")
    if env("RUN_URL"):
        lines.append(f"详情: {env('RUN_URL')}")
    return "\n".join(lines)


def send(provider, webhook, text):
    """按平台格式 POST。企业微信走 markdown，飞书走纯文本。"""
    if provider == "feishu":
        payload = {"msg_type": "text", "content": {"text": text}}
    else:
        payload = {"msgtype": "markdown", "markdown": {"content": text}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read(2000).decode("utf-8", "replace")


def main():
    webhook = env("NOTIFY_WEBHOOK")
    if not webhook:
        print("未配置 NOTIFY_WEBHOOK，跳过通知")
        return 0

    parsed = urlparse(webhook)
    host = (parsed.hostname or "").lower()
    allowed = any(host == d or host.endswith("." + d) for d in ALLOWED_HOSTS)
    if parsed.scheme != "https" or not allowed:
        print("NOTIFY_WEBHOOK 不在白名单（仅允许 https 的企业微信 / 飞书域名），跳过通知")
        return 0

    provider = env("NOTIFY_PROVIDER", "wecom").lower()
    if provider not in ("wecom", "feishu"):
        print(f"NOTIFY_PROVIDER 取值非法（{provider}），按 wecom 处理")
        provider = "wecom"

    text = build_message()
    try:
        status, body = send(provider, webhook, text)
        print(f"通知已发送（{provider} → {masked(webhook)}）: HTTP {status} {body[:200]}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"通知发送失败（不影响部署结果）: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
