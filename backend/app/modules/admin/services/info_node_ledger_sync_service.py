"""企业微信台账（本地 project 表镜像）→ 项目信息节点 同步预览服务。

与「文件导入」共用同一套匹配内核（info_node_import_service 的 flatten_tree / match_items）：
台账的每一列（列名 → 值）当成一条待导入条目，按标题与信息树节点匹配，产出三类预览：
  将填写   —— 节点现在是空的，勾选后填入；
  将覆盖   —— 节点已有内容且与台账不一致（**矛盾**），勾选后才覆盖；
  未匹配   —— 台账里有这一列、树里没有对应节点（**缺少的节点**），勾选后新建。

匹配不只按列名：台账的称呼与信息树节点常对不上，所以先把**值**拿去认节点——
值正好是某个下拉节点的可选项时（列名「项目生命周期」、值「售前方案」对节点「项目特性 /
时间线 / 大节点」），就认到那个节点上，不另建节点（见 _pin_option_values）。
本模块只算不写：用户在前端勾选确认后，由前端逐节点走既有 CRUD 落库（与文件导入同一条路径）。

台账来源 = **本地 project 表里本项目那一行**。企业微信智能表格的台账由
app/integrations/sources/wecom/adapter.py 的 map_wecom_record_to_project 同步进这张表，
这里按 PROJECT_LEDGER_FIELDS 反向还原成「台账列名 → 值」再比对：读的就是那份台账的数据，
但不依赖 AI 服务与企业微信凭据，整个比对都在本地库上完成。

异常约定（接口层映射）：LookupError → 404（项目不存在）；ValueError → 400（还没有信息节点）。
"""
from typing import Any, Dict, List, Optional, Tuple

from app.modules.admin.services import info_node_import_service as import_service

# 台账列名 → project 表字段。**与 app/integrations/sources/wecom/adapter.py 的
# map_wecom_record_to_project 一一对应**（那边是把台账写进这张表，这里是把表读回台账形态）：
# 列名照抄企微智能表格，顺序也按台账来。改台账列名时两处要一起改，否则「同步」会拿一张
# 对不上的表去比对。
#
# 只收「原样落到字段上」的列：adapter 里换过词的列不进这张表。典型是「项目类型」——它同时
# 落进 project_type（原文）与 category_basis（CATEGORY_MAP 换成另一套词：普通项目 →
# 重要不紧急）；category_basis 这种加工过的值不是台账原文，同步进信息节点只会误导人，
# 所以这里只认 project_type。
PROJECT_LEDGER_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("项目名称", "name"),
    ("项目编号", "code"),
    ("承接描述", "description"),
    ("调度对接人", "contact_person"),
    ("项目生命周期", "status"),
    ("预计走向", "expected_trend"),
    ("人员计划", "personnel_plan"),
    ("预计AGV下线时间", "deployment_date"),
    ("部署版本", "deployment_version"),
    ("更新时间", "recent_delivery_date"),
    ("车型&车数", "recent_delivery_content"),
    ("最终交付时间", "final_delivery_date"),
    ("任务执行情况", "task_execution_status"),
    ("内部编号", "internal_code"),
    ("项目区域", "project_region"),
    ("总车数", "total_vehicle_count"),
    ("控制器选择", "controller_vendor"),
    ("服务器部署", "server_deployment_status"),
    ("特别关注", "special_attention"),
    ("风险和任务描述", "risk_task_description"),
    ("项目管理策略", "management_strategy"),
    ("风险承接", "risk_carrying_type"),
    ("项目类型", "project_type"),
    ("业绩核算期", "settlement_period"),
    ("销售", "sales"),
    ("售前方案", "pre_sales"),
    ("项目经理", "project_manager"),
    ("实施工程师", "field_engineer"),
    ("是否承接", "undertake_status"),
)

# 这些值不是台账原文，是 adapter 对「台账里这一列没值」的兜底（`_to_str(...) or "待开始"`）：
# 把兜底值当台账同步进节点等于凭空造数据。「是否承接」同样有兜底（空 → 是），但台账里
# 「是」本来就是最常见的真值，无从区分，只能照原样带上——预览本来就要用户点头才写。
ADAPTER_DEFAULTS: Dict[str, Tuple[str, ...]] = {"status": ("待开始",)}

# 与文件导入同一套标题判等口径（去空白与常见标点、统一小写），两处不能漂移。
_norm = import_service._norm

# 台账里用于「定位是哪一条记录」的列：它们是台账自身的主键列，不是项目信息，
# 不参与节点匹配（树里没有、也不该有同名节点；参与了只会变成两条噪音「缺少的节点」）。
LOCATOR_FIELDS = ("项目编号", "项目名称")

# 「按下拉选项认领」的取值门槛：规范化后至少 2 个字。单字值（是/否/高）满树的下拉都有，
# 即便某一刻只有一个下拉装着它，也不该拿它当归属依据。
MIN_OPTION_VALUE_LEN = 2

# 「相近标题」判据：两个标题（规范化后）一方包含另一方，且短的一方至少 2 个字。
# 台账列名常是节点名加了限定词（「实施工程师」对「实施」、「项目区域」对「项目区域/地点」），
# 靠序列相似度认这种关系会误伤——「是否承接」与「是否对接」相似度 0.75 但毫不相干，
# 而包含关系不会：短标题的所有字都出现在长标题里，才是同一个东西的两种说法。
MIN_CONTAINMENT_LEN = 2


def _value_text(value: Any) -> str:
    """台账字段值 → 纯文本（口径对齐 wecom adapter 的 _to_str）。

    企微 flatten_value 对图片/附件/位置等字段保留原结构（list of dict / dict），
    这类值落进文本节点只会是一串 JSON，没有意义 → 丢弃；list 取首个非空字符串
    （选项/成员类字段的形态）。布尔按中文「是/否」——节点内容是给人看的。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return ""
    return ""


def _ledger_values(row: Any) -> Dict[str, Any]:
    """project 行 → 台账 values（列名 → 值），没值的列直接不出现。"""
    values: Dict[str, Any] = {}
    for title, attr in PROJECT_LEDGER_FIELDS:
        value = getattr(row, attr, None)
        if value is None:
            continue
        if str(value).strip() in ADAPTER_DEFAULTS.get(attr, ()):
            continue
        values[title] = value
    return values


def _ledger_items(values: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
    """台账 values（字段标题 → 值）→ match_items 认的条目列表。

    空值字段与定位列不参与：空值没什么可同步的，定位列不是项目信息。
    台账只有列名，没有大模型那种「我认到哪个节点」的判断，故 node_title / quantity /
    suggested_parent_path 一律为空（未匹配条目的建议归属另行补，见 _enrich_unmatched）。
    """
    items: List[Dict[str, Optional[str]]] = []
    for field, raw in values.items():
        title = str(field or "").strip()
        if not title or title in LOCATOR_FIELDS:
            continue
        value = _value_text(raw)
        if not value:
            continue
        items.append({
            "title": title,
            "value": value,
            "node_title": None,
            "quantity": None,
            "suggested_parent_path": None,
        })
    return items


def _find_exact_node(flat: List[Dict], title: str) -> Optional[Dict]:
    """树里与台账列名同名的节点（规范化后完全相等），没有则 None。"""
    target = _norm(title)
    if not target:
        return None
    return next((n for n in flat if _norm(n.get("title")) == target), None)


def _find_value_group(flat: List[Dict], title: str) -> Optional[Dict]:
    """台账列名该落在哪个**分组节点**里：先找同名分组，再退一步找「包含它」的分组。

    如台账「项目区域」对树里分组「项目区域/地点」：列名是分组名去掉了限定词，值本来就该
    落在分组的某个子节点里。只认「台账列名 ⊂ 分组名」这一个方向（分组名 = 列名 + 限定词）；
    分组名更短的那种包含关系不认——那更像是另一件事，不猜。
    """
    exact = _find_exact_node(flat, title)
    if exact is not None and not import_service.is_fillable_node(exact):
        return exact

    target = _norm(title)
    if len(target) < MIN_CONTAINMENT_LEN:
        return None
    best: Optional[Dict] = None
    best_len = 0
    for node in flat:
        node_norm = _norm(node.get("title"))
        # 必须比列名更长（多了限定词）才算「分组名 = 列名 + 限定词」，同名的上面已处理
        if import_service.is_fillable_node(node) or len(node_norm) <= len(target):
            continue
        if target in node_norm and len(node_norm) > best_len:
            best, best_len = node, len(node_norm)
    return best


def _dropdown_child_taking(flat: List[Dict], group: Dict, value: str) -> Optional[Dict]:
    """分组节点下「唯一一个」装得下该值的下拉子节点（多个/没有都不猜）。

    「唯一」是关键：两个下拉都装得下这个值（比如都是「是/否」型）时无从判断值是给谁的，
    宁可不动，交回未匹配让用户自己决定。
    """
    hits = [
        node for node in flat
        if node["parent_id"] == group["id"]
        and node["content_type"] == "select"
        and node["options"]
        and import_service.is_fillable_node(node)
        and import_service.snap_select_value(value, node["options"]) is not None
    ]
    return hits[0] if len(hits) == 1 else None


def _option_taker(flat: List[Dict], value: str) -> Optional[Dict]:
    """树里「唯一一个」可选项**精确等于**该值的下拉节点（没有、或有多个都返回 None）。

    只认精确命中，不走 snap_select_value 的相似度与车型放宽：这一层是在没有标题线索时
    单凭值去认节点，证据本来就弱，宁可少认几个。「唯一」是硬门槛——车型1/2/3 装着同一套
    型号，值只说明「是这一款车」，说明不了属于哪一辆，这种情况交回未匹配让用户决定。
    """
    target = _norm(value)
    if len(target) < MIN_OPTION_VALUE_LEN:
        return None
    found: Optional[Dict] = None
    for node in flat:
        if node["content_type"] != "select" or not node["options"]:
            continue
        if not import_service.is_fillable_node(node):
            continue
        if not any(_norm(option) == target for option in node["options"]):
            continue
        if found is not None:
            return None          # 第二个装得下这个值的下拉 → 无从判断，不猜
        found = node
    return found


def _pin_option_values(flat: List[Dict], items: List[Dict[str, Optional[str]]]) -> None:
    """台账某一列的**值**正好是某个下拉节点的可选项时，把这个条目指到那个节点上。

    节点名与台账列名对不上也认：台账「项目生命周期」的值是「售前方案」，树里没有叫这个名的
    节点，但「项目特性 / 时间线 / 大节点」是下拉、选项里就有「售前方案」——同一个阶段在台账
    与信息树里各有一套说法，值能对上就说明说的是同一件事，没有理由再另建一个节点（这正是
    以前「同步」把「项目生命周期」「承接描述」堆进「导入信息」根节点的由来）。
    做法与 _pin_group_values 一致：给条目补 node_title（match_items 认它），取值校验、
    填/覆盖分桶、落库都照旧走同一条路径。

    先于 _pin_group_values 跑：按值精确命中可选项，比按列名包含关系去认要硬。
    """
    for item in items:
        if item.get("node_title"):
            continue
        node = _option_taker(flat, item["value"] or "")
        if node is not None:
            item["node_title"] = node["title"]


def _pin_group_values(flat: List[Dict], items: List[Dict[str, Optional[str]]]) -> None:
    """台账列名落在一个分组节点上时，把这个值指到分组下装得下它的那个下拉里。

    如台账「项目区域」=「大陆（China Mainland）」：树里「基础信息 / 项目区域/地点」是分组，
    它的下拉子节点「区域选项」正好有这一项——这个值本来就该填在那里，而不是另建一个
    同名节点。做法是给条目补上 node_title（match_items 认它，等同大模型说「我认到哪个
    节点」），后续的取值校验、填/覆盖分桶全部照旧走 match_items，不另开一条落库路径。
    """
    for item in items:
        if item.get("node_title"):
            continue             # 已按值认到下拉上的不重算（_pin_option_values 的证据更硬）
        group = _find_value_group(flat, item["title"] or "")
        if group is None:
            continue
        child = _dropdown_child_taking(flat, group, item["value"] or "")
        if child is not None:
            item["node_title"] = child["title"]


def _enrich_unmatched(flat: List[Dict], row: Dict) -> None:
    """给未匹配条目补「建议归属」与「为什么没匹配上」的备注（就地把 row 补全，不改匹配结果）。

    四种情形（都只是预览里的提示；用户不勾选就不会写库）：
      1. 树里有同名分组节点（如「服务器部署」是分组）→ 建议作为它的子节点新建，
         并说明原因——这个值本来就该填在它下面的某个字段里；
      2. 树里有同名节点但装不下这个值（下拉的可选项里没有它，如「项目类型」没有「普通项目」）
         → 不给归属（前端落到「导入信息」兜底），并说清是选项的问题，补选项比新建节点合适；
      3. 没有同名节点，但有个标题包含关系相近的（台账列名带限定词：实施工程师 vs 实施）
         → 建议挂到相近节点所在的层级下，免得整批堆到「导入信息」根节点下；
         相近的本身就是分组时（项目区域 vs 项目区域/地点），建议就落在分组里面。
    """
    title = row.get("title") or ""
    target = _norm(title)
    if not target:
        return

    same = _find_exact_node(flat, title)
    if same is not None:
        if not import_service.is_fillable_node(same):
            row["suggested_parent_id"] = same["id"]
            row["suggested_parent_path"] = same["path"]
            row["note"] = f"树里已有同名分组节点「{same['title']}」，勾选后在它下面新建这个值"
        elif same.get("content_type") == "select":
            row["note"] = (
                f"树里已有同名节点「{same['title']}」，但它是下拉、可选项里没有这个值——"
                "先到编辑页给它补上选项，比新建一个同名节点合适"
            )
        else:
            row["note"] = f"树里已有同名节点「{same['title']}」，但当前装不下这个值，请人工确认"
        return

    best: Optional[Dict] = None
    best_len = 0
    for node in flat:
        node_norm = _norm(node.get("title"))
        if len(node_norm) < MIN_CONTAINMENT_LEN or len(target) < MIN_CONTAINMENT_LEN:
            continue
        if node_norm in target or target in node_norm:
            # 同级取更具体的那个（包含关系下，标题更长 = 限定更细）
            if len(node_norm) > best_len:
                best, best_len = node, len(node_norm)
    if best is None:
        return

    if not import_service.is_fillable_node(best):
        # 相近的是个分组：值就该新建在它里面（比挂到它的父级更贴近）
        row["suggested_parent_id"], row["suggested_parent_path"] = best["id"], best["path"]
        row["note"] = f"树里最接近的是分组节点「{best['title']}」，勾选后在它下面新建这个值"
        return

    parent_id = best.get("parent_id")
    if not parent_id:
        row["suggested_parent_id"], row["suggested_parent_path"] = best["id"], best["path"]
        return
    parent = next((n for n in flat if n["id"] == parent_id), None)
    if parent is not None:
        row["suggested_parent_id"], row["suggested_parent_path"] = parent["id"], parent["path"]


def _load_local_context(project_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict]]:
    """项目行 → (项目摘要, 台账 values, 信息树平铺)，一次连接里读完。"""
    from app.modules.admin.models_das.models import Project
    from app.modules.admin.services.info_node_service import SessionLocal, info_node_service

    db = SessionLocal()
    try:
        row = db.query(Project).filter(Project.id == project_id).first()
        if row is None:
            raise LookupError("项目不存在")
        project = {
            "id": str(row.id),
            "code": row.code or str(row.id),
            "name": row.name or "",
        }
        # 在会话没关之前就把值取出来（关了之后实例会 detached，属性可能读不到）
        values = _ledger_values(row)
    finally:
        db.close()

    return project, values, import_service.flatten_tree(info_node_service.get_tree(project_id))


def build_sync_preview(project_id: str) -> Dict[str, Any]:
    """台账 ↔ 本项目信息树 的比对结果（三类预览，不落库）。

    异常约定（接口层映射）：LookupError → 404；ValueError → 400。
    """
    project, values, flat = _load_local_context(project_id)
    if not flat:
        raise ValueError("该项目还没有信息节点，请先在编辑页新建节点后再同步")

    items = _ledger_items(values)
    # 先按「值 = 某个下拉的可选项」认节点（项目生命周期 售前方案 → 项目特性/时间线/大节点），
    # 再按「列名落在分组节点上」（项目区域 → 项目区域/地点）兜一层，最后走统一匹配
    _pin_option_values(flat, items)
    _pin_group_values(flat, items)
    buckets = import_service.match_items(flat, items)
    for row in buckets["unmatched"]:
        _enrich_unmatched(flat, row)

    return {
        "project_id": project["id"],
        "project_name": project["name"],
        "project_code": project["code"],
        # 台账「更新时间」列（镜像落在 project.recent_delivery_date 上）：给用户一个台账新鲜度的判断依据
        "ledger_updated_at": _value_text(values.get("更新时间")) or None,
        # 参与比对的字段数（本项目有值的台账列，不含项目编号/项目名称这两个定位列）
        "field_count": len(items),
        # 镜像的台账列总数：说明「台账还有多少列本项目没值」
        "mirror_field_total": len(PROJECT_LEDGER_FIELDS),
        **buckets,
    }
