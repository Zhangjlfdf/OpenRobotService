#!/usr/bin/env python3
"""部署通知卡片图渲染（企业微信「群机器人 image 消息」用）。

由 `notify.py` 在 `NOTIFY_STYLE=image` 时调用：把 `collect()` 的上下文渲染成一张
白底卡片 PNG，再以 base64 + md5 发到群里（企微 image 消息不支持跳转，因此
`notify.py` 会紧接着补一条只含 Actions 链接的文本消息）。

设计取舍：
- 图片宽度 960、按 2x 缩放输出，缩略图里字比企微模板卡片大 2~3 倍，且可点开看原图；
- 图标（勾/叉）用绘图 API 自绘，不依赖彩色 emoji 字体，Linux runner 同样可用；
- 字体优先用随包携带的子集（Noto Sans SC，OFL 许可证），找不到再退回系统字体；
  全部找不到时抛 `NotifyImageError`，由 notify.py 降级回模板卡片，保证通知不丢。
"""
import io
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W = 960
PAD = 48
BG = "#FFFFFF"
INK = "#1F2329"
MID = "#4E5969"
SUB = "#8A9099"
LINE = "#E6E8EB"
GREEN = "#07C160"
RED = "#F5222D"
ORANGE = "#FA8C16"

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# CI 侧 COMPONENTS 只会是 all / frontend / backend / ai（workflow 已做白名单校验）
COMPONENT_LABELS = {
    "all": "frontend + backend + AI",
    "frontend": "frontend",
    "backend": "backend",
    "ai": "AI",
}

# 提交/PR 标题里的 conventional 前缀对群里读者是噪音，展示时剥掉
CONVENTIONAL = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s*")


def _font_candidates():
    """(常规, 粗体) 候选对，按优先级排列；环境变量可覆盖。"""
    env_reg = os.environ.get("NOTIFY_FONT_REG", "").strip()
    env_bold = os.environ.get("NOTIFY_FONT_BOLD", "").strip()
    return [
        (env_reg, env_bold),
        (str(FONT_DIR / "NotoSansSC-Regular.ttf"), str(FONT_DIR / "NotoSansSC-Bold.ttf")),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
         "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    ]


def pick_fonts():
    """返回 (常规字体路径, 粗体字体路径或 None)。"""
    for regular, bold in _font_candidates():
        if regular and Path(regular).is_file():
            bold_path = bold if (bold and Path(bold).is_file()) else None
            return regular, bold_path
    raise NotifyImageError("未找到可用的中文字体（可用 NOTIFY_FONT_REG / NOTIFY_FONT_BOLD 指定）")


class NotifyImageError(RuntimeError):
    """图片渲染不可用（缺依赖 / 缺字体 / 尺寸超限），上游据此降级。"""


def component_text(raw):
    """`backend,ai` → `backend + AI`；未知取值原样保留。"""
    parts = [p for p in re.split(r"[,;\s]+", (raw or "all").strip()) if p]
    labels = []
    for part in parts or ["all"]:
        label = COMPONENT_LABELS.get(part, part)
        if label not in labels:
            labels.append(label)
    return " + ".join(labels)


def clean_title(text):
    """剥掉 `feat(ORS-907):` 这类提交前缀；剥空了就退回原文。"""
    text = " ".join((text or "").split())
    return CONVENTIONAL.sub("", text) or text


def quote_text(ctx):
    """卡片引用块文案：优先 PR 标题，拿不到时退回提交标题。

    回滚场景没有 PR 语义，且分支最新提交与「回滚到哪份备份」无关，因此不用提交标题硬凑。
    """
    quote = clean_title(ctx.get("pr_title") or "")
    if not quote and "回滚" not in (ctx.get("title") or ""):
        quote = clean_title(ctx.get("commit_msg") or "")
    return quote


def _dashed(draw, y, x0, x1, color=LINE, dash=10, gap=8):
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=color, width=2)
        x += dash + gap


def _wrap(draw, text, fnt, max_w):
    """按空格优先断行，单词本身超宽时再按字符硬切。"""
    lines, cur = [], ""
    for word in text.split(" "):
        candidate = f"{cur} {word}".strip()
        if draw.textlength(candidate, font=fnt) <= max_w:
            cur = candidate
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if draw.textlength(cur + ch, font=fnt) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    return lines


def _head_lines(draw, text, fnt, max_w, max_lines=2):
    lines = _wrap(draw, text, fnt, max_w)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        tail = lines[-1]
        while tail and draw.textlength(tail + "…", font=fnt) > max_w:
            tail = tail[:-1]
        lines[-1] = tail + "…"
    return lines


def _rows(ctx):
    """卡片键值对：告警优先，最长 5 行，避免整图太高。"""
    rows = [("部署组件", component_text(ctx.get("components")), INK)]
    if ctx.get("git_ref"):
        rows.append(("代码分支", f"{ctx['git_ref']} @ {ctx.get('sha', '')}".strip(), INK))
    if ctx.get("skip_gate"):
        rows.append(("测试门禁", "已跳过（紧急发布）", ORANGE))
    if ctx.get("auto_rollback"):
        rows.append(("自动回滚", "已触发，请人工确认服务", ORANGE))
    if ctx.get("backup_id"):
        rows.append(("回滚目标", str(ctx["backup_id"]), INK))
    if ctx.get("elapsed"):
        rows.append(("流水线耗时", str(ctx["elapsed"]), INK))
    return rows[:5]


def render_png(ctx):
    """渲染通知卡片图，返回 PNG 字节。"""
    regular_path, bold_path = pick_fonts()
    ok = bool(ctx.get("ok"))
    color = GREEN if ok else RED
    state = ctx.get("title") or ("部署成功" if ok else "部署失败")

    env_name = ctx.get("env_name") or "-"
    pr_no = str(ctx.get("pr_no") or "").strip()
    headline = f"#{pr_no} · {env_name} 环境" if pr_no else f"{env_name} 环境"

    quote = quote_text(ctx)

    actor = ctx.get("actor") or ""
    event = ctx.get("event") or ""
    sub = " · ".join(x for x in (actor, event) if x)

    font = lambda size, bold=False: ImageFont.truetype(  # noqa: E731
        bold_path if (bold and bold_path) else regular_path, size)
    bold_stroke = 0 if bold_path else 2      # 没有粗体文件时用描边模拟

    f_head = font(23)
    f_title = font(48, bold=True)
    f_desc = font(25)
    f_quote = font(29)
    f_label = font(24)
    f_value = font(26)
    f_link = font(27)

    rows = _rows(ctx)
    box = 68
    value_x = PAD + 300
    line_h, row_gap = 38, 26

    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    row_lines = [_wrap(probe, v, f_value, W - PAD - value_x) for _, v, _ in rows]

    img = Image.new("RGB", (W, 2600), BG)     # 先给足高度，画完按内容裁剪
    d = ImageDraw.Draw(img)
    y = PAD

    # 顶部：PR 号 + 环境（拿不到 PR 号时只显示环境）
    for line in _head_lines(d, headline, f_head, W - 2 * PAD, 2):
        d.text((PAD, y), line, font=f_head, fill=MID)
        y += 34
    y += 10
    _dashed(d, y, PAD, W - PAD)
    y += 40

    # 状态区：自绘圆角方块 + 勾/叉
    d.rounded_rectangle([PAD, y, PAD + box, y + box], radius=16, fill=color)
    if ok:
        d.line([(PAD + 17, y + 35), (PAD + 30, y + 48)], fill="#FFFFFF", width=7)
        d.line([(PAD + 30, y + 48), (PAD + 52, y + 15)], fill="#FFFFFF", width=7)
    else:
        d.line([(PAD + 20, y + 20), (PAD + 48, y + 48)], fill="#FFFFFF", width=7)
        d.line([(PAD + 48, y + 20), (PAD + 20, y + 48)], fill="#FFFFFF", width=7)

    tx = PAD + box + 24
    d.text((tx, y + 2), state, font=f_title, fill=INK,
           stroke_width=bold_stroke, stroke_fill=INK)
    if sub:
        d.text((tx, y + box), sub, font=f_desc, fill=SUB)
    y += box + 50
    d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
    y += 30

    # 引用块：本次部署带上了什么（左竖线 + 标题）
    if quote:
        lines = _head_lines(d, quote, f_quote, W - 2 * PAD - 28, 2)
        block_h = 40 * len(lines) + 10
        d.rectangle([PAD, y, PAD + 6, y + block_h], fill=color)
        for i, line in enumerate(lines):
            d.text((PAD + 26, y + i * 40), line, font=f_quote, fill=INK)
        y += block_h + 30
        d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
        y += 30

    # 键值对
    for (key, _, value_color), lines in zip(rows, row_lines):
        d.text((PAD, y + 4), key, font=f_label, fill=SUB)
        for i, line in enumerate(lines):
            d.text((value_x, y + i * line_h), line, font=f_value, fill=value_color)
        y += len(lines) * line_h + row_gap

    # 底部跳转提示（图片不可点，真实链接由随后的一条文本消息给出）
    if ctx.get("run_url"):
        y += 4
        d.line([(PAD, y), (W - PAD, y)], fill=LINE, width=2)
        y += 22
        d.text((PAD, y), "查看运行日志", font=f_link, fill=color)
        d.text((W - PAD - 16, y - 2), ">", font=f_link, fill=color)
        y += 46
    y += PAD

    img = img.crop((0, 0, W, y))
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)   # 2x 高清
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    if len(data) > 2 * 1024 * 1024:           # 企微 image 上限 2MB
        raise NotifyImageError(f"渲染结果过大（{len(data)} 字节），改用其他样式")
    return data
