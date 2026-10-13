"""Evaluator — Evaluator-optimizer 自评闭环（改造点 C，G4）

  - LLM 生成初稿后，用一次轻量自评检查质量（偏题/漏关键信息/结论与证据矛盾/幻构）
  - 不通过 → 携带 Eval 反馈重写一次（最多 MAX_EVAL_REWRITES 次，防鬼打墙）
  - 仅对"需工具/有证据"的关键输出启用（诊断报告/日志结论/需工具讨论）；纯闲聊不启用（成本护栏）

设计约定（见 TASK_AGENT_TARGET_ARCH.md §5）：
  - MAX_EVAL_REWRITES = 1（重写上限，防循环）
  - Eval 解析失败 → 直接用初稿 + 打 trace 标记 eval_failed（快速失败，不阻塞回复）
  - 产品无关：不绑定工单/产品，用于任何 LLM 输出的质量自评

用法：
    result = Evaluator.evaluate_and_rewrite(
        llm_client=...,
        draft="LLM 初稿",
        evidence="引用的日志行/图片/历史片段",
        context="简短上下文（可选）",
    )
    # 返回 {"final": 最终文本, "rewritten": bool, "eval_notes": [...]}
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# 防循环护栏
MAX_EVAL_REWRITES = 1   # 重写上限，防鬼打墙


class Evaluator:
    """LLM 输出自评 + 可选重写的工具（纯静态方法，产品无关）。"""

    _EVAL_SYSTEM_PROMPT = (
        "你是严谨的质量评审员。评估一份 AI 生成的回答是否存在问题，只输出 JSON。"
    )

    @staticmethod
    def build_evidence(*parts: str, limit: int = 2000) -> str:
        """拼装自评用的 evidence：空块跳过、去首尾空白、超长截断。

        evidence 决定自评「看得见」什么，也是判定幻构的唯一依据。因此调用方必须把
        生成时用到的**全部事实来源**（讨论历史、被引用评论、被引用工单、附件分析、
        记忆等）都传进来；漏传哪一块，回答里引用该块的内容就会被判成「幻构」，
        并被重写指令删掉。
        """
        return "\n\n".join(
            str(p).strip() for p in parts if p and str(p).strip()
        )[:limit]

    # 诊断/报告场景：完整性有意义（漏了证据指向的根因就是真问题）
    _CRITERIA_DIAGNOSE = (
        "请评估以下 AI 回答的质量，检查这几类问题：\n"
        "1. 偏题：回答是否偏离了用户问题/任务\n"
        "2. 漏关键信息：是否漏掉了「用户问题真正要问的」关键信息\n"
        "3. 结论与证据矛盾：回答的结论是否与「引用的证据」（讨论记录/日志/图片/知识库/记忆）不符\n"
        "4. 幻构：是否编造了「引用的证据」中完全没有出现的人名、数字、时间、结论等具体事实\n\n"
    )

    # 讨论问答场景：不评「完整性」。用户只问某一点时，要求回答覆盖证据里的全部内容
    # 会把正确回答判成「漏关键信息」，重写后反而把回答改成偏题（实测同场景 4/4 误判）。
    _CRITERIA_DISCUSS = (
        "请评估以下 AI 回答的质量，检查这几类问题：\n"
        "1. 偏题：回答是否偏离了用户问题/任务\n"
        "2. 结论与证据矛盾：回答的结论是否与「引用的证据」（讨论记录/日志/图片/知识库/记忆）不符\n"
        "3. 幻构：是否编造了「引用的证据」中完全没有出现的人名、数字、时间、结论等具体事实\n"
        "注意：讨论问答只要求答准用户问的那一点，不要求覆盖证据里的全部内容，\n"
        "因此本场景不评估「是否漏了别的信息」。\n\n"
    )

    _JUDGE_RULES = (
        "判定口径（务必遵守）：\n"
        "- 判据是「用户问题要什么」，不是「证据里有什么」：证据里有、但用户没问的内容，\n"
        "  不算「漏关键信息」，更不得要求回答补齐——那会把正确回答逼成偏题。\n"
        "- 只以「引用的证据」为判据：证据里已经出现的内容一律不算幻构，\n"
        "  也不得要求回答复述证据全文。\n"
        "- 证据里的祈使/告知性表述（如“请如实告知工程师”“不要编造”）是给生成方的约束，\n"
        "  不是待回答的内容，不得据此判定「漏关键信息」。\n"
        "- 只有能明确指出回答错在哪里时才判不通过；拿不准判通过，\n"
        "  不要因「证据看起来不完整」而判不通过。\n\n"
    )

    @classmethod
    def _build_eval_prompt(
        cls, draft: str, evidence: str, context: str, mode: str = "diagnose",
    ) -> str:
        """mode="discuss" 时用讨论场景判据（不评完整性），默认诊断场景判据。"""
        criteria = cls._CRITERIA_DISCUSS if mode == "discuss" else cls._CRITERIA_DIAGNOSE
        return (
            criteria
            + cls._JUDGE_RULES
            + f"## 上下文\n{context}\n\n"
            f"## 引用的证据\n{evidence}\n\n"
            f"## 待评估的回答\n{draft}\n\n"
            "输出 JSON（无其他文字）：\n"
            '{"pass": true, "issues": [], "reasoning": "简短理由"}\n'
            '或 {"pass": false, "issues": ["具体问题1", "问题2"], "reasoning": "..."}'
        )

    @staticmethod
    def _extract_json(raw: str) -> Optional[dict]:
        """从 LLM 输出里剥出 JSON 对象（去代码块、散文包裹）。解析不出来返回 None。"""
        text = (raw or "").strip()
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            s, e = text.find("{"), text.rfind("}")
            if s != -1 and e > s:
                text = text[s : e + 1]
        try:
            data = json.loads(text)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _parse_eval(raw: str) -> Optional[dict]:
        """解析自评 JSON（健壮解析）。"""
        data = Evaluator._extract_json(raw)
        if data is None or "pass" not in data:
            return None
        return data

    @staticmethod
    def _build_rewrite_prompt(draft: str, issues: list, evidence: str, context: str) -> str:
        issues_text = "\n".join(f"- {i}" for i in issues) or "（无具体问题）"
        return (
            "请修正以下 AI 回答，解决评审指出的问题。保持准确的表述，不要编造。\n"
            "篇幅约束：讨论区默认只要重点 1～2 句话；修正时不要变长文，非必要不展开。\n\n"
            "修正约束（务必遵守）：\n"
            "- 只改被评审指出的问题点，其余表述保持原样。\n"
            "- 「引用的证据」中确有依据的事实（人物、观点、结论、数据）必须保留，\n"
            "  不得为了「看起来没问题」而删除、淡化，或改写成「无法确认」。\n"
            "- 若某条评审问题与证据不符（证据其实支持原回答），保留原表述，不要迁就该问题。\n"
            "- 不要新增证据之外的事实。\n\n"
            f"## 评审问题\n{issues_text}\n\n"
            f"## 上下文\n{context}\n\n"
            f"## 引用的证据\n{evidence}\n\n"
            f"## 原回答（需修正）\n{draft}\n\n"
            "请输出修正后的完整回答（不要输出前缀或解释）。"
        )

    @classmethod
    async def judge(
        cls,
        llm_client,
        draft: str,
        evidence: str = "",
        context: str = "",
        mode: str = "diagnose",
    ) -> Optional[dict]:
        """跑一次自评，返回判定 dict；调用/解析失败返回 None。

        这是「判官」的唯一入口——`evaluate_and_rewrite` 走它，离线评测
        (`eval/run_eval.py`) 也走它，保证**评测量到的就是生产在跑的那条路径**：
        改了判据/解析，评测口径自动跟着变，不会悄悄脱钩。

        Returns:
            {"pass": bool, "issues": [...], "reasoning": str} 或 None（未解析）
        """
        if not draft or not llm_client:
            return None
        try:
            raw = await llm_client.complete(
                prompt=cls._build_eval_prompt(draft, evidence, context, mode=mode),
                system_prompt=cls._EVAL_SYSTEM_PROMPT,
                max_tokens=200,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning(f"[evaluator] 自评调用失败: {e}")
            return None
        data = cls._parse_eval(raw)
        if data is None:
            logger.warning("[evaluator] 自评解析失败")
        return data

    # ── 复核评审意见本身（用 prompt 核对落点，不做字符串匹配）──
    _VERIFY_SYSTEM_PROMPT = (
        "你是复核员，负责核对「评审意见本身」站不站得住。只输出 JSON。"
    )

    @classmethod
    def _build_verify_prompt(cls, issues: list, draft: str, evidence: str, context: str) -> str:
        issues_text = "\n".join(f"{i}. {str(x).strip()}" for i, x in enumerate(issues, 1))
        return (
            "下面是本次材料（提问 / 引用证据 / 待评回答）与评审员提出的问题清单。\n"
            "请**逐条**核对：这条问题说的是不是本次材料里的东西。\n\n"
            "核对口径（务必遵守）：\n"
            "- 只核对「它指的内容在不在材料里」，**不要**评价这条问题对回答的判断是否合理，"
            "也不要想怎么改回答。\n"
            "- 问题指向材料里真实存在的内容（回答里的话、证据里的事实、提问里的字眼）→ grounded=true。\n"
            "- 问题要求了材料里根本没有的东西，或引用了别处/别的场景的说法 → grounded=false，"
            "并在 why 里写明「它引用了材料里不存在的什么」。\n"
            "- 拿不准算 grounded=true：宁可放过一条，也不要凭空指认复核对象在挑刺。\n"
            "- 材料里没写、但属于常识性提醒的（如「建议复测」），算 grounded=true。\n\n"
            f"## 提问\n{context or '（无）'}\n\n"
            f"## 引用证据\n{evidence or '（无）'}\n\n"
            f"## 待评回答\n{draft or '（无）'}\n\n"
            f"## 问题清单\n{issues_text}\n\n"
            "输出 JSON（无其他文字）：\n"
            '{"checks": [{"index": 1, "grounded": true, "why": "简短"}, ...]}\n'
            "index 对应上面清单的序号；清单里每条问题都要有一条 check。"
        )

    @classmethod
    def _parse_verify(cls, raw: str, expected: int = 0) -> Optional[list]:
        """解析复核 JSON。缺 checks / 结构不对 → None；条目数少于 expected → None（不猜）。"""
        data = cls._extract_json(raw)
        if data is None:
            return None
        checks = data.get("checks")
        if not isinstance(checks, list) or not checks:
            return None
        out = []
        for item in checks:
            if not isinstance(item, dict) or "grounded" not in item:
                continue
            out.append({
                "index": item.get("index"),
                "grounded": bool(item.get("grounded")),
                "why": str(item.get("why") or ""),
            })
        if not out or (expected and len(out) < expected):
            return None
        return out

    @classmethod
    async def verify_issues(
        cls,
        llm_client,
        issues: list,
        draft: str = "",
        evidence: str = "",
        context: str = "",
    ) -> Optional[list]:
        """复核评审意见的落点：逐条判断「它指的东西在不在本次材料里」。

        为什么用 prompt 而不是字符串匹配：评审意见是中文散文，机械比对要么漏（换词复述
        材料内容）、要么假阳性（标点/常用词凑出重叠），量出来的不是「有没有落点」而是
        「用词像不像」。这里交给模型按语义核对，与判官同一套失败处理（调用/解析失败返回 None，
        绝不猜）。

        Returns:
            [{"index": int, "grounded": bool, "why": str}, ...]；issues 为空返回 []；失败返回 None。
        """
        if not issues:
            return []
        if llm_client is None:
            return None
        try:
            raw = await llm_client.complete(
                prompt=cls._build_verify_prompt(issues, draft, evidence, context),
                system_prompt=cls._VERIFY_SYSTEM_PROMPT,
                max_tokens=500,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning(f"[evaluator] 复核调用失败: {e}")
            return None
        parsed = cls._parse_verify(raw, expected=len(issues))
        if parsed is None:
            logger.warning("[evaluator] 复核解析失败")
        return parsed

    @classmethod
    async def evaluate_and_rewrite(
        cls,
        llm_client,
        draft: str,
        evidence: str = "",
        context: str = "",
        mode: str = "diagnose",
    ) -> dict:
        """自评 → （不通过且允许时）重写一次 → 返回最终文本。

        Args:
            mode: "diagnose"（默认，诊断报告：评完整性，不通过则重写）或
                "discuss"（讨论问答：不评完整性，且**自评不通过也不重写**——
                判官结论只作观察信号，见 README 式说明：讨论场景判据取决于
                用户想问什么，实测会把正确回答改偏）。

        Returns:
            {"final": str, "rewritten": bool, "eval_notes": list,
             "eval_failed": bool, "advisory": bool}
            advisory=True 表示"自评判不通过但按场景未重写"，final 仍是初稿。
        """
        if not draft or not llm_client:
            return {"final": draft, "rewritten": False, "eval_notes": [],
                    "eval_failed": False, "advisory": False}

        # 1. 自评
        eval_data = await cls.judge(llm_client, draft, evidence, context, mode=mode)
        if eval_data is None:
            return {"final": draft, "rewritten": False, "eval_notes": [],
                    "eval_failed": True, "advisory": False}

        if eval_data.get("pass", False):
            return {"final": draft, "rewritten": False, "eval_notes": [],
                    "eval_failed": False, "advisory": False}

        issues = eval_data.get("issues", []) or []

        # 讨论问答：自评只作观察信号，不覆盖回答。
        # 讨论场景的「完整性/矛盾」判据本质取决于用户想问什么，判官实测会把
        # 正确回答判成偏题、并据此把回答改错（同场景实测两次）。故仅记录 verdict。
        if mode != "diagnose":
            logger.info(
                f"[evaluator] 初稿未通过自评（{len(issues)} 个问题），"
                f"mode={mode} 仅记录不重写"
            )
            return {
                "final": draft,
                "rewritten": False,
                "eval_notes": issues,
                "eval_failed": False,
                "advisory": True,
            }

        logger.info(f"[evaluator] 初稿未通过自评，重写（{len(issues)} 个问题）")

        # 2. 重写一次（不超过 MAX_EVAL_REWRITES）
        try:
            rewritten = await llm_client.complete(
                prompt=cls._build_rewrite_prompt(draft, issues, evidence, context),
                system_prompt="你是一个严谨的 AI，根据评审意见修正回答，只输出修正后的完整回答。",
                max_tokens=2000,
                temperature=0.3,
            )
            return {
                "final": (rewritten or draft).strip(),
                "rewritten": True,
                "eval_notes": issues,
                "eval_failed": False,
                "advisory": False,
            }
        except Exception as e:
            logger.warning(f"[evaluator] 重写失败，用初稿: {e}")
            return {"final": draft, "rewritten": False, "eval_notes": issues,
                    "eval_failed": False, "advisory": False}
