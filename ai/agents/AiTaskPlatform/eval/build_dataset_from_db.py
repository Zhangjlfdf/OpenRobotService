#!/usr/bin/env python3
"""从生产库抽取**真实**评测用例：提问 / 事后重建的材料 / AI 当时的回答。

为什么要从库里抽：手写用例已被实测证明**没有区分力**（判据换回诊断口径、甚至抽掉
`_JUDGE_RULES` 都照样通过，见 README「为什么」）。真实材料才带真实的模糊、噪音与表述
方式；而现在的量法（判定稳定性 + 落点复核）**不需要人工标注期望**，所以真实三元组
可以直接用。

三元组怎么来的：
  - **提问**：`task_comments` 里人工账号发的、内容含 `@U` 的评论（用户真的这样问的）；
  - **草稿**：同一工单里该提问之后**第一条** `created_by='U老师'` 的评论（AI 当时真答的）；
  - **材料（证据）**：事后按时间**重建**——提问**之前**的讨论史 + 工单基本信息 +
    附件分析摘要。⚠️ 真实 prompt 没落库，所以这是**近似材料**；刻意排除提问之后的评论，
    否则 AI 的回答本身会混进证据，判官就成了抄答案。

脱敏口径：**真人名默认保留**——生产里判官看到的材料就是真名，换成「工程师A/B」等于在评一个
生产里不会出现的输入；要给外部看的版本才加 `--anonymize`（同 `users.json` 那批导出的纪律）。
微信 ID（`wechat_xxx`）与手机号**始终隐去**：那是指纹不是名字。

**未实名账号**（users 里 `name` 为空 → 映射只能给出 `wechat_{openid[:10]}`）单独处理：一律写成
「（微信用户N）」，同人同号、不同人不同号。不编名字（编出来会和真人重名），也不写 openid。
生产里这类账号显示的是 `wechat_oD5oY3abc`，这是**刻意的偏差**——只影响身份呈现，不影响
「谁说了什么」的可读性。映射整体加载失败时，所有作者按同一条路降级并打警告。

用法:
    python -m ai.agents.AiTaskPlatform.eval.build_dataset_from_db --limit 24
    python -m ai.agents.AiTaskPlatform.eval.build_dataset_from_db --limit 24 --anonymize
    python -m ai.agents.AiTaskPlatform.eval.build_dataset_from_db --ticket 640 --limit 3
    python -m ai.agents.AiTaskPlatform.eval.build_dataset_from_db --since 2026-09-01 --limit 40
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = Path(__file__).resolve().parent
_DATASETS = _HERE / "datasets"
_PROJECT_ROOT = _HERE.parents[3]
for _p in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)  # backend 也要在：作者名映射来自 app.services.user_service

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_PROJECT_ROOT / "ai" / ".env")

AI_AUTHOR = "U老师"          # AI 的讨论回复作者（产品人格）
HISTORY_ITEMS = 8            # 重建材料时取提问之前最近多少条讨论
MAX_QUERY_CHARS = 600
MAX_DRAFT_CHARS = 1200
MAX_EVIDENCE_CHARS = 6000
ANON_TICKET_BASE = 20000     # 匿名工单号起始值


def _fetch_pairs(ticket: str = "", limit: int = 0) -> list:
    """取 (提问, AI 回答) 对：每个含 @U 的提问配它之后第一条 U老师 回复。"""
    from sqlalchemy import text

    from ai.core.database import engine

    sql = """
        SELECT q.task_id, q.id AS q_id, q.created_at AS q_at, q.created_by AS q_by, q.content AS q_text,
               r.id AS r_id, r.created_at AS r_at, r.content AS r_text
        FROM task_comments q
        JOIN task_comments r ON r.id = (
            SELECT r2.id FROM task_comments r2
            WHERE r2.task_id = q.task_id AND r2.created_by = :ai
              AND r2.created_at > q.created_at
            ORDER BY r2.created_at ASC, r2.id ASC LIMIT 1
        )
        WHERE q.created_by <> :ai AND q.content LIKE '%@U%'
        {ticket_filter}
        ORDER BY q.created_at DESC, q.id DESC
        {limit_clause}
    """.format(
        ticket_filter="AND q.task_id = :tid" if ticket else "",
        limit_clause="LIMIT :lim" if limit else "",
    )
    params: dict = {"ai": AI_AUTHOR}
    if ticket:
        params["tid"] = int(ticket)
    if limit:
        params["lim"] = int(limit)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def _fetch_task(task_id) -> dict:
    from sqlalchemy import text

    from ai.core.database import engine

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, title, description, task_type, status, metadata_info, attachment_analysis "
            "FROM tasks WHERE id = :tid"
        ), {"tid": task_id}).mappings().first()
    return dict(row) if row else {}


def _fetch_history(task_id, before, limit: int = HISTORY_ITEMS) -> list:
    """提问之前最近的 limit 条讨论（**不含提问之后的任何内容**）。"""
    from sqlalchemy import text

    from ai.core.database import engine

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT created_by, content, created_at FROM task_comments "
            "WHERE task_id = :tid AND created_at < :before AND COALESCE(content,'') <> '' "
            "ORDER BY created_at DESC, id DESC LIMIT :lim"
        ), {"tid": task_id, "before": before, "lim": limit}).mappings().all()
    return list(reversed([dict(r) for r in rows]))


def _names_from_map(umap: dict) -> dict:
    """从 users 映射里挑出**真名**。

    未实名的账号在映射里显示的是它的 username（`wechat_{openid[:10]}`，见
    `user_service._load_user_map_from_db` 的 `name or username`），那是**指纹不是名字**，
    不能混进「人名」集合，也不能当成人名去替换正文——它由 `_Anonymizer` 的微信占位接管。
    """
    names = {}
    for _key, name in umap.items():
        real = str(name or "").strip()
        if real and 1 < len(real) <= 20 and not real.startswith("wechat_") and real not in names:
            names[real] = ""
    return names


def _all_people() -> dict:
    """从 users 映射取**全量真名** → 用于替换正文里被提到的真人（不只是作者）。

    只登记「作者」不够：讨论正文里会直接写到别人的名字（实测漏出过一位同事的姓名）。
    """
    try:
        from app.services.user_service import user_service
        umap = user_service.get_user_map() or {}
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 用户映射加载失败，人名脱敏降级: {e}", file=sys.stderr)
        return {}
    return _names_from_map(umap)


_degraded_warned = False


def _real_name(created_by: str) -> str:
    """用生产同一套映射把作者解析成真名；解析不出来就原样返回（调用方按未实名处理）。"""
    global _degraded_warned
    try:
        from ai.agents.AiTaskPlatform.contexts.comments import author_display_name
        return str(author_display_name(created_by) or created_by or "")
    except Exception as e:  # noqa: BLE001
        if not _degraded_warned:
            _degraded_warned = True
            print(f"  ⚠️ 用户姓名映射不可用，作者名一律降级为微信占位: {e}", file=sys.stderr)
        return str(created_by or "")


class _Anonymizer:
    """两种模式，别混：

    - **保真（默认）**：人名、工单号**原样保留**——生产里判官看到的就是这些，换成标签
      等于在评一个生产里不会出现的输入。只隐去微信 ID / 电话（那是指纹不是名字）。
    - **脱敏**（`--anonymize`）：人名→工程师A/B/…（含正文里提到的）、工单号→重编号。

    两种模式共同的一条：**未实名账号**（映射解析不出真名，显示名还是 `wechat_…`）
    一律写成「（微信用户N）」——同人同号、不同人不同号。不编名字（编出来会与真人重名），
    也不写 openid（那是指纹）。生产里这类账号显示成 `wechat_oD5oY3abc`，是**刻意的偏差**。
    """

    _WECHAT_ID = re.compile(r"wechat_[A-Za-z0-9_\-]{2,}")
    _PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

    def __init__(self, all_names: Optional[dict] = None, anonymize_names: bool = False):
        self.anonymize_names = bool(anonymize_names)
        self._labels: dict[str, str] = {}
        self._real2label: dict[str, str] = {}
        self._tickets: dict[str, str] = {}
        self._wx: dict[str, str] = {}
        # 预登记全量真名：正文里提到的同事名（不是作者）也要换掉
        for real in sorted(all_names or {}):
            self._assign(real)

    @staticmethod
    def _label(i: int) -> str:
        letter = chr(ord("A") + (i - 1) % 26)
        return f"工程师{letter}" if i <= 26 else f"工程师{letter}{i // 26 + 1}"

    def _assign(self, real: str) -> str:
        label = self._real2label.get(real)
        if label is None:
            label = self._label(len(self._real2label) + 1)
            self._real2label[real] = label
        return label

    @staticmethod
    def _wx_body(raw_id: str) -> str:
        """openid 本体（去掉 `wechat_` 前缀）。"""
        key = str(raw_id or "").strip()
        return key[len("wechat_"):] if key.startswith("wechat_") else key

    def _wx_placeholder(self, raw_id: str) -> str:
        """未实名账号 → 「（微信用户N）」，同人同号。

        同一个账号有两种写法：`created_by` 里是 `wechat_{openid[:10]}`（username），
        而 users.id 本身可以是**裸 openid**。按前缀/openid 头 10 位认人，避免同一个人
        在作者位和正文里拿到两个号。
        """
        key = str(raw_id or "").strip()
        body = self._wx_body(key)
        for known, placeholder in self._wx.items():
            kbody = self._wx_body(known)
            if key.startswith(known) or known.startswith(key):
                return placeholder
            if len(body) >= 10 and kbody.startswith(body[:10]):
                return placeholder
        placeholder = f"（微信用户{len(self._wx) + 1}）"
        self._wx[key] = placeholder
        return placeholder

    @staticmethod
    def _is_unverified(raw: str, real: str) -> bool:
        """没有真名：映射没这个人、或映射给的还是 openid 形式的 username。"""
        return not real or real == raw or real.startswith("wechat_")

    def name(self, created_by: str) -> str:
        raw = str(created_by or "").strip()
        if not raw:
            return "（未知）"
        if raw == AI_AUTHOR:
            return AI_AUTHOR
        if raw in self._labels:
            return self._labels[raw]
        real = _real_name(raw) or raw
        if self._is_unverified(raw, real):
            label = self._wx_placeholder(raw)   # 未实名：两种模式都不编名字、不写指纹
        elif self.anonymize_names:
            label = self._assign(real)
        else:
            label = real
        self._labels[raw] = label
        return label

    def ticket(self, task_id) -> str:
        key = str(task_id)
        if not self.anonymize_names:
            return f"#{key}"  # 保真：讨论正文里也可能引用工单号，两边要一致
        if key not in self._tickets:
            self._tickets[key] = f"#{ANON_TICKET_BASE + len(self._tickets) + 1}"
        return self._tickets[key]

    def scrub(self, text: str) -> str:
        """统一脱敏出口：脱敏模式下换真人名；微信 ID / 电话两种模式都隐去。

        正文里的 openid 必须换成**与作者位同一个**占位（同人同号），所以：
        整串匹配 + 词边界，绝不按「已登记串是子串」去替换——那样会把更长的 openid
        拦腰截断（实测 `wechat_oD5oY3abc` 命中后残出 `defgh`）。
        """
        out = str(text or "")
        if self.anonymize_names:
            for real, label in self._real2label.items():
                if real in out:
                    out = out.replace(real, label)
        # 已有的裸 openid（users.id 形式）整串换掉；`wechat_…` 交给下面的正则整串吃掉
        for known in sorted(self._wx, key=len, reverse=True):
            if not known.startswith("wechat_") and len(known) >= 10:
                out = re.sub(rf"(?<![A-Za-z0-9_\-]){re.escape(known)}(?![A-Za-z0-9_\-])",
                             self._wx[known], out)
        out = self._WECHAT_ID.sub(lambda m: self._wx_placeholder(m.group(0)), out)
        out = self._PHONE.sub("（电话已隐去）", out)
        return out

    @property
    def people(self) -> int:
        return len(self._real2label)


def _json_field(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)) and value:
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _ticket_block(task: dict, anon: "_Anonymizer") -> str:
    from ai.agents.AiTaskPlatform.contexts.comments import _plain_comment_text

    meta = _json_field(task.get("metadata_info"))
    diag = meta.get("diagnosis") if isinstance(meta.get("diagnosis"), dict) else {}
    lines = [f"- 工单 {anon.ticket(task.get('id'))}《{task.get('title') or '(无标题)'}》",
             f"  状态: {task.get('status') or '未知'}；类型: {task.get('task_type') or 'problem'}"]
    if meta.get("robot_type"):
        lines.append(f"  车型: {meta['robot_type']}")
    if meta.get("fault_code"):
        lines.append(f"  故障码: {meta['fault_code']}")
    desc = _plain_comment_text(task.get("description"), limit=300)
    if desc:
        lines.append(f"  描述: {desc}")
    if isinstance(diag, dict) and diag.get("problem_summary"):
        lines.append(f"  提单诊断: {_plain_comment_text(diag['problem_summary'], limit=200)}")
    att = _json_field(task.get("attachment_analysis"))
    if isinstance(att, dict) and att.get("summary"):
        lines.append(f"  附件分析: {_plain_comment_text(att['summary'], limit=400)}")
    return anon.scrub("\n".join(lines))


def _is_system_notice(text: str) -> bool:
    """后端自动生成的通知（不是人说的话）——归档/派审/指派这类。

    实测踩过：归档工单的最后 8 条讨论全是「开始归档工单，状态变更为「归档中」」，
    这种材料喂给判官毫无意义，必须滤掉。
    """
    t = str(text or "").strip()
    if not t:
        return True
    return bool(re.search(
        r"开始归档工单|提交了归档报告|审核通过归档报告|系统派审|状态变更为「|重新指派给|已归档",
        t,
    ))


def build(limit: int = 24, ticket: str = "", since: str = "",
          anonymize: bool = False, per_ticket: int = 1) -> tuple:
    """返回 (dataset, 参与脱敏的人数)。

    真人名**默认保留**：生产里判官看到的材料就是真名（`author_display_name` 解析出来的），
    换成「工程师A/B」等于在评一个生产里不会出现的输入（讨论里靠人名区分谁说了什么，
    这条信息本身是判据的一部分）。要给外部看的版本才加 `--anonymize`。
    无论哪种模式，微信 ID / 电话都隐去——那是指纹，不是名字。

    `per_ticket` 默认 1：**同一张工单只取 1 条**。实测不限时 20 条里 8 条来自同一张查日志的工单，
    那不是 8 个独立观测，样本被一张工单的问答风格占满（`--ticket` 指定单张工单时不限）。

    两趟：先登记全量真名（保证正文里提到的同事名也能换），再拼材料。
    候选比 limit 多取几倍，滤掉「讨论史里没有真人发言」的工单后再截断。
    """
    fetch = 0 if ticket else max(limit * 8, limit)
    pairs = _fetch_pairs(ticket=ticket, limit=fetch)
    anon = _Anonymizer(_all_people() if anonymize else {}, anonymize_names=anonymize)

    # 第一趟：登记作者 + 按时间戳重建讨论史（只保留提问之前的真人发言）
    prepared = []
    per_ticket_seen: dict = {}
    for row in pairs:
        q_at = row.get("q_at")
        if since and q_at and str(q_at)[:10] < since:
            continue
        tid_key = str(row["task_id"])
        if per_ticket and per_ticket_seen.get(tid_key, 0) >= per_ticket:
            continue
        raw_history = _fetch_history(row["task_id"], q_at, limit=HISTORY_ITEMS * 3)
        history = [h for h in raw_history if not _is_system_notice(h.get("content"))]
        if len(history) < 2:
            continue  # 讨论史里没有实质对话 → 这种材料没意义
        per_ticket_seen[tid_key] = per_ticket_seen.get(tid_key, 0) + 1
        for h in history[-HISTORY_ITEMS:]:
            anon.name(h.get("created_by"))
        anon.name(row.get("q_by"))
        anon.ticket(row["task_id"])
        prepared.append((row, history[-HISTORY_ITEMS:]))
        if len(prepared) >= limit:
            break

    # 第二趟：拼用例
    from ai.agents.AiTaskPlatform.contexts.comments import _plain_comment_text

    cases = []
    for row, history in prepared:
        query = _plain_comment_text(row.get("q_text"), limit=MAX_QUERY_CHARS)
        draft = _plain_comment_text(row.get("r_text"), limit=MAX_DRAFT_CHARS)
        if not query or not draft:
            continue
        tid = anon.ticket(row["task_id"])
        lines = [_ticket_block(_fetch_task(row["task_id"]), anon)]
        if history:
            hist = "\n".join(
                f"【{anon.name(h.get('created_by'))}】"
                f"{anon.scrub(_plain_comment_text(h.get('content'), limit=300))}"
                for h in history
            )
            lines.append(f"  讨论史（本次提问之前的最近 {len(history)} 条，已滤掉归档类系统通知）:\n{hist}")
        cases.append({
            "case_id": f"real-{tid.lstrip('#')}",
            "mode": "discuss",
            "context": f"工单 {tid} 讨论区，用户问：{anon.scrub(query)}",
            "evidence": anon.scrub("\n".join(lines))[:MAX_EVIDENCE_CHARS],
            "draft": anon.scrub(draft),
            "why": f"真实用例（提问时间 {row.get('q_at')}）：材料按提问之前重建，回答是 AI 当时真答的那条",
        })

    dataset = {
        "kind": "evaluator",
        "description": (
            "**真实生产用例**：从 task_comments 抽的 (提问, 材料, AI 当时的回答) 三元组。"
            "材料按提问之前重建（真实 prompt 未落库，属近似）；"
            + ("人名已匿名化、工单号已重编号。"
               if anonymize else "人名与工单号保真（与生产一致），微信 ID 与电话已隐去。")
            + "**不标期望通过/不通过**——标准随提问变，见 README。"
        ),
        "source": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "extractor": "ai/agents/AiTaskPlatform/eval/build_dataset_from_db.py",
            "material_note": "证据=工单基本信息 + 提问之前的讨论史 + 附件分析摘要（重建近似）",
            "anonymized": ("人名→工程师A/B/…；工单号→重编号；微信 ID 与电话隐去" if anonymize
                           else "保真：人名与工单号原样（与生产一致）；微信 ID 与电话隐去"),
            "people_present": anon.people,
            "tickets": len({c["case_id"] for c in cases}),
        },
        "cases": cases,
    }
    return dataset, anon.people


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="从生产库抽取真实评测用例")
    ap.add_argument("--limit", type=int, default=24, help="取多少条（按提问时间倒序）")
    ap.add_argument("--ticket", default="", help="只取某个工单号")
    ap.add_argument("--since", default="", help="只要这个日期之后的提问（YYYY-MM-DD）")
    ap.add_argument("--anonymize", action="store_true",
                    help="人名换成「工程师A/B/…」（默认保留真名，与生产一致）；微信ID/电话始终隐去")
    ap.add_argument("--per-ticket", type=int, default=None,
                    help="同一张工单最多取几条（默认 1；--ticket 指定单张工单时不限）")
    ap.add_argument("--out", default=str(_DATASETS / "discuss_real_cases.json"))
    args = ap.parse_args(argv)

    per_ticket = args.per_ticket if args.per_ticket is not None else (0 if args.ticket else 1)
    try:
        dataset, people = build(limit=args.limit, ticket=args.ticket, since=args.since,
                                anonymize=args.anonymize, per_ticket=per_ticket)
    except Exception as e:  # noqa: BLE001
        print(f"✗ 抽取失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    cases = dataset["cases"]
    if not cases:
        print("✗ 没抽到用例（检查该时间范围/工单里有没有 @U 提问）", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    lens = sorted(len(c["draft"]) for c in cases)
    mode = "匿名化人名" if args.anonymize else "保留真名（与生产一致）"
    tid = dataset["source"]["tickets"]
    print(f"✓ 写出 {len(cases)} 条 → {out}")
    print(f"  {mode}；{tid} 张工单；微信ID/电话已隐去；"
          f"回答长度 min={lens[0]} 中位={lens[len(lens) // 2]} max={lens[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
