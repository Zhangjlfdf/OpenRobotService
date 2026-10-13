# -*- coding: utf-8 -*-
"""车型定制模式：扫码入口的模式确认接口（XQE 试点）。

链路：车体二维码（跳转 URL 带 scene = str(qrcodes.id)）→ 前端「我要摇人」界面
弹信息确认弹窗（项目名/客户名/车型取扫码查到的录入信息行）→ 用户确认后前端调
POST /mode/confirm → 此处按 scene 精确取 wechat_qrcodes 里的那一行：
  - 该行存在且 status=published → 该 session 注册为车型定制模式
    （memory.metadata["vehicle_mode"]），返回该车型 SOP 手册文档清单
    （md 链接，前端拉正文渲染）
  - scene 非法 / 查无此码 / 非 published → code=1 报错拦住
    （实验阶段不降级常规模式）

车型/项目名/客户名一律以库内那行为准（请求里的同名字段只作兜底）；同车型多台车
的 scene（行 id）各不相同，按主键精确取行才不串车。

铁律：定制模式是「加法」——常规链路（不调本接口的会话）一个字节不变；
后续 pipeline 的检索域限定 / prompt 注入 / 错误码直查全部以
metadata["vehicle_mode"] 存在为唯一开关。

幂等：同一 session 重复调用 = 覆盖重注册（前端刷新即重调，天然安全）。
"""
import time
import asyncio
import random
import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ai.core.logging import get_logger
from ai.core.memory import MemoryManager

logger = get_logger(__name__)

# ---- 开场大方向引导题（0930 定稿：服务端直出，不经 LLM）----
OPENING_QUESTION = "您遇到了什么问题？"
OPENING_HINT = "若是其他情况，请在下方输入框描述"
_OPENING_KEEP_SUBSTR = "故障码"   # 保底类目（重要大类，随机时必选）
_OPENING_LIMIT = 5


def parse_fork_tree_choices(model: str, kb_root: Optional[Path] = None) -> List[str]:
    """读 kb/company/{车型}/ 下文件名含「分叉树」的 md，解析顶层 ## 标题。

    只收**带数字序号前缀**的顶层节点（`## 1. xxx`）——分叉树大方向类目天然
    编号；「附：…」「待用户确认」等文档管理章节无序号，天然排除（v0.1 实测
    混进过「待用户确认」）。剥序号后返回类目列表；文件不存在/无标题返回 []
    （开场题是可选能力，空库自动退化为纯输入框，不阻塞模式注册）。
    kb_root 参数供测试注入临时目录。
    """
    sub = model_to_subpath(model)
    if not sub:
        return []
    root = (kb_root or _default_kb_root()) / "company" / sub
    tree = None
    if root.is_dir():
        for f in sorted(root.glob("*.md")):
            if "分叉树" in f.stem:
                tree = f
                break
    if tree is None:
        return []
    try:
        lines = tree.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        logger.warning(f"[vehicle_mode] 分叉树读取失败: {tree.name}: {e}")
        return []
    out: List[str] = []
    for ln in lines:
        m = re.match(r"^##\s+(\d+)\s*[.、．]\s*(.+?)\s*$", ln)
        if not m:
            continue
        t = m.group(2).strip()
        if t and t not in out:
            out.append(t)
    return out


def build_opening(top_levels: List[str], rng: Optional[random.Random] = None) -> Optional[Dict]:
    """开场题数据：故障码类保底 + 其余随机抽满 5（0930 定稿）。

    随机是用户拍板（固定前 5 会让其余类永无曝光）；故障码是重要大类保底
    不随机。不足 2 类不出题（None）。
    """
    if not top_levels:
        return None
    rng = rng or random
    keep = [c for c in top_levels if _OPENING_KEEP_SUBSTR in c]
    rest = [c for c in top_levels if _OPENING_KEEP_SUBSTR not in c]
    rng.shuffle(rest)
    choices = (keep + rest)[:_OPENING_LIMIT]
    if len(choices) < 2:
        return None
    return {"question": OPENING_QUESTION, "choices": choices, "hint": OPENING_HINT}


class ModeConfirmRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID（前端先建会话再调本接口绑定模式）")
    scene: str = Field(..., min_length=1, max_length=32, description="扫码场景值（= str(wechat_qrcodes.id)，纯数字），据此精确取扫的那一行")
    model: str = Field(..., min_length=1, max_length=64, description="车型（用户已在弹窗确认的展示值；实际以库内行为准）")
    project_name: str = Field(default="", max_length=128, description="项目名（弹窗展示值；库内非空时以库内为准）")
    customer_name: str = Field(default="", max_length=128, description="客户名（弹窗展示值；库内非空时以库内为准）")
    vehicle_code: str = Field(default="", max_length=64, description="唯一车号（扫码链路不使用，保留字段兼容其他调用方）")


# ============================================================
# 扫码行查询（数据源：wechat_qrcodes 录入信息行）
# ============================================================
# scene 严格为纯数字串（= str(qrcodes.id)，0930 起的微信号场景值口径）：非数字
# 直接拒绝，不做归一也不做模糊匹配——只按主键命中「扫的那一行」。同车型多台车各
# 自 id 独立，按车型/项目名兜底匹配会让不同车串味，一律不做。
_SCENE_RE = re.compile(r"^\d+$")

# 只有已发布（= 车辆已出厂）的码才允许进入定制模式
_STATUS_PUBLISHED = "published"


async def _lookup_qrcode(scene: str):
    """按主键精确取 scene 对应的二维码行；查无此码返回 None。

    只按 id 匹配，车型/项目/客户仅用于展示、不参与匹配。status 校验留给调用方，
    便于把「查无此码」与「码不可用」报成不同文案（两者都不降级）。
    """
    from ai.core.database import WechatQrcode, engine
    from sqlalchemy.orm import Session as DBSession

    qr_id = int(scene)

    def _query():
        with DBSession(engine) as s:
            return s.query(WechatQrcode).filter(WechatQrcode.id == qr_id).first()

    return await asyncio.to_thread(_query)


# ============================================================
# 车型 → 知识库目录映射 + 手册清单
# ============================================================
def model_to_subpath(model: str) -> str:
    """车型 → company 域下的知识库子目录名。XQE → XQE；未来新车型零改动。

    车型知识不建独立顶层域（0930 用户定稿）：kb/company/{车型}/ 入库归
    company 域，payload sub_domain="{车型}"，检索按此过滤（车型目录平铺，无子层级）。"""
    return (model or "").strip().upper()


def model_to_domain(model: str) -> str:
    """车型 → 所属 kb 域名。车型知识统一挂 company 域。"""
    return "company"


# 引导设施文档（不走 SOP 外显按钮）：分叉树=对话引导题的内容源、故障码表=
# 输码直查的内容源——它们的入口在对话里，不作为文档气泡重复露出（0930 定稿：
# 除引导设施外，一个文件一个 SOP 气泡按钮）
_GUIDE_DOC_KEYWORDS = ("分叉树", "故障码表")


def list_manual_docs(model: str, kb_root: Optional[Path] = None,
                     media_prefix: str = "/api/ai/media",
                     exclude_guide_docs: bool = False) -> List[Dict[str, str]]:
    """扫 kb/company/{车型}/*.md 生成手册文档清单（title + 正文 URL）。

    exclude_guide_docs=True 时排除引导设施文档（分叉树/故障码表）——confirm
    响应的 manual_docs 用于 SOP 外显气泡，只出可在线查看的操作文档。
    kb 目录/车型目录不存在 → 返回空清单（手册是可选能力，不阻塞模式注册）。
    URL 走 run.py 已挂的静态路由 {media_prefix}/kb/**（前端 fetch 后自行渲染）。
    kb_root 参数供测试注入临时目录。
    """
    sub = model_to_subpath(model)
    if not sub:
        return []
    root = (kb_root or _default_kb_root()) / "company" / sub
    if not root.is_dir():
        return []
    docs: List[Dict[str, str]] = []
    for f in sorted(root.glob("*.md")):
        if exclude_guide_docs and any(k in f.stem for k in _GUIDE_DOC_KEYWORDS):
            continue
        docs.append({
            "title": f.stem,
            "path": f"company/{sub}/{f.name}",
            "url": f"{media_prefix}/kb/company/{sub}/{f.name}",
        })
    return docs


def _default_kb_root() -> Path:
    from ai.config import _KB_DIR
    return _KB_DIR


# ============================================================
# 模式注册主流程
# ============================================================
async def register_mode(req: ModeConfirmRequest,
                        memory_manager: Optional[MemoryManager] = None) -> dict:
    """按 scene 取录入信息行 → session 注册定制模式 → 返回手册清单。

    返回：{code: 0, data: {confirmed, model, domain, manual_docs, opening}} 或 {code: 1, message}。
    memory_manager 参数供测试注入 mock；缺省用真实 get_memory_manager()。
    """
    scene = (req.scene or "").strip()
    if not _SCENE_RE.match(scene):
        logger.warning(
            f"[vehicle_mode] 场景值非法拦截: scene={scene!r}, session={req.session_id}",
        )
        return {"code": 1, "message": "扫码信息无效，请重新扫码或联系管理员"}

    row = await _lookup_qrcode(scene)
    if row is None or (row.status or "").strip() != _STATUS_PUBLISHED:
        # 实验阶段：查无此码 / 码不可用都报错拦住，不降级常规模式（前端展示提示）
        logger.warning(
            f"[vehicle_mode] 二维码不可用拦截: scene={scene} "
            f"status={getattr(row, 'status', None)!r}, session={req.session_id}",
        )
        return {"code": 1, "message": "该二维码未录入信息或不可用，请联系管理员"}

    # 车型以库内那行为准（录入界面必填，正常不会为空）；为空时用弹窗确认值兜底。
    # 归一到大写（= 磁盘目录名）：下游拿 model 拼检索 filter 的 sub_domain
    # （pipeline._vehicle_mode_domains）与 prompt 里的「车型 xxx」，都要与 kb
    # 入库同形才对得上，小写会过滤成空集。
    model_key = model_to_subpath(
        (row.vehicle_model or "").strip() or (req.model or "").strip())
    if not model_key:
        logger.warning(
            f"[vehicle_mode] 车型为空拦截: scene={scene}, session={req.session_id}",
        )
        return {"code": 1, "message": "该二维码未录入车型信息，请联系管理员"}

    # 项目名/客户名同口径：库内非空以库内为准，为空用弹窗确认值兜底
    project_name = (row.project_name or "").strip() or (req.project_name or "").strip()
    customer_name = (row.customer_name or "").strip() or (req.customer_name or "").strip()

    domain = model_to_domain(model_key)
    mode_info = {
        "model": model_key,
        "domain": domain,
        # scene = 行 id：本会话绑定的是「哪一台车」的唯一身份，供追溯
        "scene": scene,
        # 扫码链路无车号概念（车号字段保留给其他调用方；空则不注入 prompt 车号段）
        "vehicle_code": (req.vehicle_code or "").strip(),
        "project_name": project_name,
        "customer_name": customer_name,
        "location": (row.project_location or "").strip(),
        "registered_at": int(time.time()),
    }

    mm = memory_manager or await _default_memory_manager()
    memory = await mm.get_memory(req.session_id)
    # SOP 外显清单：排除引导设施文档（分叉树/故障码表的入口在对话里）
    manual_docs = list_manual_docs(model_key, exclude_guide_docs=True)
    opening = build_opening(parse_fork_tree_choices(model_key))
    if opening:
        # 开场类目随模式入 metadata：pipeline 识别「用户点开场气泡」（query
        # == 类目原文）做零 LLM 短接，不必再读分叉树文件
        mode_info["opening_choices"] = opening["choices"]
    # 覆盖重注册 = 幂等（前端刷新即重调本接口）
    memory.metadata["vehicle_mode"] = mode_info
    await mm.save_memory(memory)

    logger.info(
        f"[vehicle_mode] 定制模式注册: session={req.session_id} scene={scene} "
        f"model={model_key} domain={domain} manual={len(manual_docs)}篇 "
        f"opening={len(opening['choices']) if opening else 0}类",
    )
    return {
        "code": 0,
        "data": {
            "confirmed": True,
            "model": model_key,
            "domain": domain,
            "manual_docs": manual_docs,
            "opening": opening,
        },
    }


async def _default_memory_manager() -> MemoryManager:
    from ai.core import get_memory_manager
    return await get_memory_manager()


def register_vehicle_mode_routes(qa_router) -> None:
    """把模式确认路由挂到 qa_router（router.py 一行调用，其余逻辑全部隔离在本模块）。"""
    from starlette.concurrency import run_in_threadpool  # noqa: F401（预留同步重活用）

    @qa_router.post("/mode/confirm", summary="车型定制模式确认（扫码入口：校验车辆档案并注册会话模式）")
    async def mode_confirm(req: ModeConfirmRequest):
        return await register_mode(req)
