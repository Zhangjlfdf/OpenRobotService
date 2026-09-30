#!/usr/bin/env python3
"""部署 / 回滚结果通知（企业微信或飞书群机器人）。

由 GitHub Actions 通过环境变量驱动（见 .github/workflows/deploy-split.yml）：

- 支持同时推送到多个群：`NOTIFY_WEBHOOKS` 用逗号分隔多个 Webhook，每项可写
  `<url>|<policy>|<群名>`（后两段可省略，例 `url1|always|研发群,url2|failure|运维群`）；
  未配置时回退到单个 `NOTIFY_WEBHOOK`；
- 未配置任何 Webhook 时静默跳过，不影响部署结果；
- Webhook 主机做域名白名单校验，避免误配成内网地址产生 SSRF；
- 任何发送失败都只打印告警并以 0 退出——通知不应影响部署判定；
- 日志只打印 Webhook 主机名，绝不回显完整 URL（其路径含机器人密钥）。

消息样式（NOTIFY_STYLE，仅企微生效；飞书始终发纯文本）：
  card      企微模板卡片 text_notice（默认）：状态色标 + 关键信息键值对 + 日志跳转
  markdown  markdown_v2：状态图标标题 + 元信息引用块 + 检查项表格
            （markdown_v2 不支持 <font> 彩色标签，勿引入）
  image     自绘卡片图（见 deploy/notify_image.py）：字号比模板卡片大 2~3 倍，
            标题取当前分支最近一条已合并 PR，可点开看原图；
            企微 image 消息不支持跳转，因此紧接着补一条只含 Actions 链接的文本消息。
降级链：image → card → markdown_v2。渲染不可用（缺 Pillow / 缺中文字体 / 体积超限）
或任一形态被企微拒收（errcode != 0）时逐级降级，保证通知不丢。

环境变量：
  NOTIFY_WEBHOOKS  多群推送：`<url>|<policy>|<群名>[, ...]`（policy、群名均可省略）
                   policy = always（默认，每条都发）| failure（仅失败/自动回滚）
                            | success（仅成功）| off（永久禁发，仅留档 URL）
                   群名只是日志标签，用来一眼看出哪个群发送/跳过了
                   注：没写进本变量的群本来就不会收到通知（不在名单 = 不发）
  NOTIFY_WEBHOOK   单群 Webhook（旧变量，作为 NOTIFY_WEBHOOKS 的回退，等价于 always）
  NOTIFY_PROVIDER  wecom（默认）| feishu
  NOTIFY_STYLE     card（默认）| markdown（仅企微）
  NOTIFY_STATUS    success | failure | cancelled（用于判定成功与否）
  NOTIFY_TITLE     动作名，如「部署」「回滚」
  ENV_NAME         目标环境 test|prod
  COMPONENTS       组件（部署时有意义）
  GIT_REF          代码分支
  COMMIT_SHA       提交号
  COMMIT_MSG       提交标题（plan job 解析）
  EVENT_NAME       触发方式 push | workflow_dispatch | schedule
  RUN_STARTED_AT   run 开始时间 ISO8601（用于估算流水线耗时）
  HEALTH_URLS      健康检查地址（逗号分隔，仅展示端点概况）
  BACKUP_ID        回滚目标备份 id（rollback 场景）
  SKIP_GATE        "true" 表示已跳过测试门禁
  AUTO_ROLLBACK    "yes" 表示已自动回滚
  ACTOR            触发人
  RUN_URL          Actions 运行链接
  PR_NO            部署分支上最近一条已合并 PR 的编号（plan job 解析，可空）
  PR_TITLE         同一 PR 的标题，image 样式用它当卡片主旨（可空）
"""
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

ALLOWED_HOSTS = ("qyapi.weixin.qq.com", "open.feishu.cn")
POLICIES = ("always", "failure", "success", "off")
STYLES = ("card", "markdown", "image")
EVENT_LABELS = {"workflow_dispatch": "手动触发", "push": "推送触发", "schedule": "定时触发"}


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def masked(url):
    """只保留主机名与路径前缀，避免 Webhook 密钥泄漏进日志。"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}{parsed.path[:12]}..."


def clip(text, limit):
    """按 UTF-8 字节截断（企业微信字段长度按字节计），超长补 …。"""
    text = " ".join((text or "").split())
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[: max(limit - 3, 0)].decode("utf-8", "ignore") + "…"


def elapsed_text():
    """run 开始到现在的人类可读耗时；拿不到开始时间就返回空串。"""
    started = env("RUN_STARTED_AT")
    if not started:
        return ""
    try:
        begin = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return ""
    secs = max(int((datetime.now(timezone.utc) - begin).total_seconds()), 0)
    if secs < 60:
        return f"{secs} 秒"
    return f"{secs // 60} 分 {secs % 60:02d} 秒"


def health_summary():
    """健康检查地址只展示端点概况，避免整串 URL 刷屏。"""
    urls = [u.strip() for u in env("HEALTH_URLS").split(",") if u.strip()]
    if not urls:
        return ""
    hosts = [urlparse(u).netloc or u for u in urls]
    shown = "、".join(hosts[:3]) + ("…" if len(hosts) > 3 else "")
    return f"{len(urls)} 个端点（{shown}）"


def collect():
    ok = env("NOTIFY_STATUS", "success") == "success"
    event = env("EVENT_NAME")
    return {
        "ok": ok,
        "icon": "✅" if ok else "❌",
        "title": f"{env('NOTIFY_TITLE', '部署')}{'成功' if ok else '失败'}",
        "env_name": env("ENV_NAME", "-"),
        "components": env("COMPONENTS", "all"),
        "git_ref": env("GIT_REF"),
        "sha": env("COMMIT_SHA")[:8],
        "commit_msg": env("COMMIT_MSG"),
        "actor": env("ACTOR"),
        "event": EVENT_LABELS.get(event, event),
        "elapsed": elapsed_text(),
        "skip_gate": env("SKIP_GATE") == "true",
        "auto_rollback": env("AUTO_ROLLBACK") == "yes",
        "backup_id": env("BACKUP_ID"),
        "health": health_summary(),
        "run_url": env("RUN_URL"),
        "pr_no": env("PR_NO"),
        "pr_title": env("PR_TITLE"),
    }


def who(c):
    return f"{c['actor']} · {c['event']}" if c["event"] else c["actor"]


def build_card(c):
    """企微模板卡片：状态色标 + 键值对（最多 6 组）+ 日志跳转。"""
    rows = []
    if c["auto_rollback"]:
        rows.append(("自动回滚", "⚠️ 已触发，请人工确认服务"))
    if not c["ok"]:
        rows.append(("处理建议", "查看 Actions 日志定位失败原因"))
    if c["skip_gate"]:
        rows.append(("测试门禁", "⚠️ 已跳过（紧急发布）"))
    if c["git_ref"]:
        rows.append(("代码分支", clip(f"{c['git_ref']} @ {c['sha']}", 120)))
    if c["commit_msg"]:
        rows.append(("提交信息", clip(c["commit_msg"], 120)))
    if c["actor"]:
        rows.append(("操作人", clip(who(c), 120)))
    if c["elapsed"]:
        rows.append(("流水线耗时", c["elapsed"]))
    if c["backup_id"]:
        rows.append(("回滚目标", clip(c["backup_id"], 120)))
    rows = rows[:6]

    card = {
        "card_type": "text_notice",
        "source": {"desc": "ORS-907 发布通知", "desc_color": 3 if c["ok"] else 2},
        "main_title": {
            "title": clip(f"{c['icon']} {c['title']}", 60),
            "desc": clip(f"{c['env_name']} · {c['components']}", 30),
        },
        "card_action": {"type": 1, "url": c["run_url"] or "https://github.com/"},
    }
    if rows:
        card["horizontal_content_list"] = [{"keyname": k, "value": v} for k, v in rows]
    if c["git_ref"]:
        card["sub_title_text"] = clip(f"{c['git_ref']} @ {c['sha']}", 60)
    if c["run_url"]:
        card["jump_list"] = [{"type": 1, "url": c["run_url"], "title": "查看运行日志"}]
    return {"msgtype": "template_card", "template_card": card}


def build_markdown(c):
    """企微 markdown_v2：状态图标标题 + 元信息引用块 + 检查项表格。

    注意：markdown_v2 不支持旧版 markdown 的 <font color> 彩色标签，
    状态靠图标（✅/❌/⚠️）与文字表达。
    """
    meta = [f"**{c['env_name']}** · {c['components']}"]
    if c["git_ref"]:
        meta.append(f"分支 `{c['git_ref']}` @ `{c['sha']}`")
    if c["actor"]:
        meta.append(f"操作人 {who(c)}")
    if c["elapsed"]:
        meta.append(f"耗时 {c['elapsed']}")

    lines = [f"# {c['icon']} {c['title']}", ""]
    lines += [f"> {m}" for m in meta]

    checks = [("测试门禁", "⚠️ 已跳过" if c["skip_gate"] else "✅ 已通过")]
    if c["auto_rollback"]:
        checks.append(("自动回滚", "⚠️ 已触发，请人工确认"))
    if c["backup_id"]:
        checks.append(("回滚目标", clip(c["backup_id"], 60)))
    if c["health"]:
        checks.append(("健康检查", clip(c["health"], 80)))
    lines += ["", "| 检查项 | 结果 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in checks]

    if c["commit_msg"]:
        lines += ["", f"提交：{clip(c['commit_msg'], 100)}"]
    if not c["ok"]:
        lines += ["", "⚠️ 请到 Actions 日志查看失败原因"]
    if c["run_url"]:
        lines += ["", f"[查看运行日志]({c['run_url']})"]
    return {"msgtype": "markdown_v2", "markdown_v2": {"content": "\n".join(lines)}}


def build_text(c):
    """纯文本（飞书 / 兜底）。"""
    scope = c["env_name"] if c["components"] == "all" else f"{c['env_name']} / {c['components']}"
    lines = [f"{c['icon']} {c['title']}（{scope}）"]
    if c["git_ref"]:
        lines.append(f"分支: {c['git_ref']} @ {c['sha']}")
    if c["commit_msg"]:
        lines.append(f"提交: {clip(c['commit_msg'], 100)}")
    if c["actor"]:
        lines.append(f"操作人: {who(c)}")
    if c["elapsed"]:
        lines.append(f"耗时: {c['elapsed']}")
    if c["skip_gate"]:
        lines.append("⚠️ 已跳过测试门禁（紧急发布，请事后补测）")
    if c["auto_rollback"]:
        lines.append("⚠️ 健康检查未通过，已自动回滚到部署前版本，请人工确认服务状态")
    if c["backup_id"]:
        lines.append(f"回滚目标: {c['backup_id']}")
    if not c["ok"]:
        lines.append("请到 Actions 日志查看失败原因")
    if c["run_url"]:
        lines.append(f"详情: {c['run_url']}")
    return "\n".join(lines)


def render_image(c):
    """渲染通知卡片图（PNG 字节）。

    Pillow 或中文字体缺失时抛异常，由 deliver() 降级为模板卡片——通知不因环境缺失而丢。
    """
    import notify_image
    return notify_image.render_png(c)


def build_image(png):
    """企微群机器人图片消息：base64 + md5（无需先上传素材，上限 2MB）。"""
    return {
        "msgtype": "image",
        "image": {
            "base64": base64.b64encode(png).decode("ascii"),
            "md5": hashlib.md5(png, usedforsecurity=False).hexdigest(),
        },
    }


def build_link_text(c):
    """图片不可点击，补一条只含运行链接的文本消息。"""
    return {"msgtype": "text", "text": {"content": f"查看运行日志：{c['run_url']}"}}


def post(webhook, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read(2000).decode("utf-8", "replace")


def wecom_accepted(body):
    try:
        return json.loads(body).get("errcode") == 0
    except (ValueError, AttributeError):
        return False


def follow_up_link(webhook, c):
    """图片消息不能点击，补发一条只含运行链接的文本；失败只告警。"""
    if not c["run_url"]:
        return
    try:
        post(webhook, build_link_text(c))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  运行链接补发失败（不影响通知主体）: {exc}")


def deliver(provider, webhook, c, style):
    """发送；图片/卡片被拒或渲染不可用时逐级降级（image → card → markdown）。
    返回实际使用的样式。"""
    if provider == "feishu":
        status, body = post(webhook, {"msg_type": "text", "content": {"text": build_text(c)}})
        return "text", status, body

    if style == "image":
        try:
            png = render_image(c)
        except Exception as exc:      # 缺 Pillow / 缺中文字体 / 体积超限：降级但不中断
            print(f"  图片渲染不可用（{exc}），降级为模板卡片")
        else:
            status, body = post(webhook, build_image(png))
            if wecom_accepted(body):
                follow_up_link(webhook, c)
                return "image", status, body
            print(f"  图片样式被拒（{body[:120]}），降级为模板卡片")

    want_card = style in ("card", "image")
    status, body = post(webhook, build_card(c) if want_card else build_markdown(c))
    used = "card" if want_card else "markdown"
    if used == "card" and not wecom_accepted(body):
        print(f"  卡片样式被拒（{body[:120]}），自动降级 markdown_v2 重发")
        used = "markdown"
        status, body = post(webhook, build_markdown(c))
    return used, status, body


def allowed_webhook(url):
    """校验 https + 域名白名单，避免误配成内网地址产生 SSRF。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_HOSTS)


def parse_targets(raw):
    """解析 `NOTIFY_WEBHOOKS`：`<url>|<policy>|<群名>[, ...]`（后两段可省略）。"""
    targets = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]   # Webhook URL 本身不含 `|`
        url = parts[0]
        policy = (parts[1] if len(parts) > 1 and parts[1] else "always").lower()
        label = parts[2] if len(parts) > 2 and parts[2] else ""
        if policy not in POLICIES:
            print(f"未识别的通知策略（{policy}），按 always 处理")
            policy = "always"
        targets.append((url, policy, label))
    return targets


def wants(policy, ok):
    """按目标策略判断本次结果是否需要通知。"""
    return policy == "always" or policy == ("success" if ok else "failure")


def main():
    ok = env("NOTIFY_STATUS", "success") == "success"

    raw = env("NOTIFY_WEBHOOKS")
    if raw:
        targets = parse_targets(raw)
    else:
        single = env("NOTIFY_WEBHOOK")
        if not single:
            print("未配置 NOTIFY_WEBHOOKS / NOTIFY_WEBHOOK，跳过通知")
            return 0
        targets = [(single, "always", "")]

    if not targets:
        print("NOTIFY_WEBHOOKS 未解析出有效目标，跳过通知")
        return 0

    provider = env("NOTIFY_PROVIDER", "wecom").lower()
    if provider not in ("wecom", "feishu"):
        print(f"NOTIFY_PROVIDER 取值非法（{provider}），按 wecom 处理")
        provider = "wecom"

    style = env("NOTIFY_STYLE", "card").lower()
    if style not in STYLES:
        print(f"NOTIFY_STYLE 取值非法（{style}），按 card 处理")
        style = "card"

    c = collect()
    sent = skipped = 0
    for index, (url, policy, label) in enumerate(targets, 1):
        name = label or f"#{index}"
        if not allowed_webhook(url):
            print(f"[{name}] 不在白名单（仅允许 https 的企业微信 / 飞书域名），"
                  f"跳过 {masked(url)}")
            skipped += 1
            continue
        if policy == "off":
            print(f"[{name}] 已禁用（策略 off），跳过 {masked(url)}")
            skipped += 1
            continue
        if not wants(policy, ok):
            print(f"[{name}] 策略为 {policy}，本次结果为 "
                  f"{'成功' if ok else '失败'}，按策略跳过 {masked(url)}")
            skipped += 1
            continue
        try:
            used, status, body = deliver(provider, url, c, style)
            print(f"[{name}] 已发送（{provider}/{used} → {masked(url)}）: "
                  f"HTTP {status} {body[:200]}")
            sent += 1
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[{name}] 发送失败（{masked(url)}，不影响部署结果）: {exc}")
    print(f"通知完成：目标 {len(targets)} 个，已发送 {sent} 个，跳过 {skipped} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
