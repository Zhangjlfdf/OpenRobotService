"""项目信息树「详情模板」服务 —— 管理员在编辑页维护模板，保存后同步到所有项目。

背景：模板原先是代码目录里的 YAML（config/project_templates/default.yaml），改模板要改代码发版。
本服务把模板搬进数据库（project_info_template 表，当前只有一套，id='default'），
首次读取时用 YAML 模板补种（保留现有默认结构），之后凭前端编辑页维护。

同步锚点：project_info_node.template_node_id ↔ 模板节点 id。
  - 存量项目节点没有锚点 → 同步时先按「标题路径」回填一次；
  - 回填不上的节点视为用户自建，同步不动它（也不会被模板删除带走）；
  - 模板删掉的节点 → 带锚点的项目节点连其子树一起删（前端确认弹窗里已明示）。

节点对上后的同步规则：
  - 标题 / 父子关系 / 同级顺序 / 内容类型 一律以模板为准；
  - 文本类节点已填内容保留；内容类型变了（如 text→select）按模板重置值；
  - 下拉节点的选项以模板为准，已选项仍在新选项里则保留。

接口层（api/info_nodes.py）：
  GET  /info-nodes/template              读模板（仅管理员）
  POST /info-nodes/template {nodes,dry_run}  dry_run=预览影响；否则保存并同步（仅管理员）
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.models.delivery import PROJECT_DELETED
from app.modules.admin.models_das.models import Project, ProjectInfoNode, ProjectInfoTemplate
from app.modules.admin.services import info_node_change_service as change_log
from app.modules.admin.services import info_node_mark_service as node_marks
from app.modules.admin.services.info_node_service import SessionLocal

TEMPLATE_ID = "default"
TEMPLATE_NAME = "项目详情模板"
MAX_TEMPLATE_DEPTH = 4  # 与前端 PROJECT_INFO_MAX_DEPTH / 导入服务 MAX_NODE_DEPTH 一致
ALLOWED_CONTENT_TYPES = ("text", "select", "file", "image")
MAX_TITLE_LEN = 255
MAX_PREVIEW_DETAILS = 20  # 预览里最多列出的项目数（其余只给汇总）

_NORM_STRIP_RE = re.compile(r"[\s　()（）\[\]【】<>《》\"'“”‘’,，。.：:；;、·|/\\\-—_~]+")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm_title(text: Any) -> str:
    """标题/路径规范化（去空白与常见标点、统一小写），用于按路径回填锚点。"""
    return _NORM_STRIP_RE.sub("", str(text or "")).lower()


def _select_state(value: Any) -> Tuple[str, List[str]]:
    """项目节点 value → (selected, options)；坏数据按空处理。"""
    parsed: Any = None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = None
    if not isinstance(parsed, dict):
        return "", []
    selected = parsed.get("selected") if isinstance(parsed.get("selected"), str) else ""
    options = [o for o in parsed.get("options", []) if isinstance(o, str)] if isinstance(parsed.get("options"), list) else []
    return selected, options


def _node_content_type(node: Dict) -> str:
    """模板节点内容类型：显式 content_type 优先，其次有 options 即 select，否则 text。"""
    ct = node.get("content_type")
    if isinstance(ct, str) and ct in ALLOWED_CONTENT_TYPES:
        return ct
    return "select" if node.get("options") else "text"


def normalize_template_nodes(nodes: Any, depth: int = 1, seen_ids: Optional[set] = None) -> List[Dict]:
    """校验并规范化模板节点树（保存前调用）。

    规则（与模板的两条硬约束一致）：标题非空、id 唯一、内容类型合法、
    下拉节点必须是末级、整树最深 4 层。返回规范化后的新树（sort_order 按顺序重排）。
    不合法直接抛 ValueError（接口层转 400，带用户可读信息）。
    """
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("模板不能为空，至少保留一个节点")
    if depth > MAX_TEMPLATE_DEPTH:
        raise ValueError(f"模板最多 {MAX_TEMPLATE_DEPTH} 层，节点层级过深")

    seen = seen_ids if seen_ids is not None else set()
    normalized: List[Dict] = []
    for index, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            raise ValueError("模板节点格式不正确")
        title = str(raw.get("title") or "").strip()
        if not title:
            raise ValueError(f"第 {depth} 层第 {index + 1} 个节点标题不能为空")
        node_id = raw.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError(f"模板节点「{title}」缺少 id")
        if node_id in seen:
            raise ValueError(f"模板节点 id 重复：{node_id}")
        seen.add(node_id)

        content_type = _node_content_type(raw)
        children = raw.get("children") or []
        if content_type == "select" and children:
            raise ValueError(f"下拉节点「{title}」不能有子节点（下拉必须是末级）")

        options: List[str] = []
        if content_type == "select":
            raw_options = raw.get("options") or []
            if not isinstance(raw_options, list):
                raise ValueError(f"下拉节点「{title}」的选项格式不正确")
            options = [str(o).strip() for o in raw_options if str(o).strip()]

        node: Dict[str, Any] = {
            "id": node_id,
            "title": title[:MAX_TITLE_LEN],
            "content_type": content_type,
            "sort_order": index,
            "children": normalize_template_nodes(children, depth + 1, seen) if children else [],
        }
        if content_type == "select":
            node["options"] = options
        if "value" in raw and raw.get("value") is not None:
            node["value"] = raw.get("value")
        normalized.append(node)
    return normalized


def flatten_template(nodes: List[Dict]) -> List[Dict]:
    """模板树 → 平铺列表（父节点在前，保序）。含 parent_id / depth / path"""
    flat: List[Dict] = []

    def walk(items: List[Dict], parent_id: Optional[str], parent_path: List[str], depth: int) -> None:
        for item in items:
            titles = parent_path + [item.get("title", "")]
            flat.append({
                "id": item.get("id"),
                "parent_id": parent_id,
                "title": item.get("title", ""),
                "content_type": _node_content_type(item),
                "options": [str(o) for o in (item.get("options") or [])] if _node_content_type(item) == "select" else [],
                "sort_order": item.get("sort_order"),
                "depth": depth,
                "path": " / ".join(titles),
                "value": item.get("value"),
            })
            walk(item.get("children") or [], item.get("id"), titles, depth + 1)

    walk(nodes, None, [], 1)
    return flat


def _project_paths(project_nodes: List[Dict]) -> Dict[str, str]:
    """项目节点 → 标题路径（内存递归，带环保护）。"""
    by_id = {n["id"]: n for n in project_nodes}
    cache: Dict[str, str] = {}

    def path_of(node_id: str, guard: set) -> str:
        if node_id in cache:
            return cache[node_id]
        if node_id in guard:
            return ""
        guard.add(node_id)
        node = by_id.get(node_id)
        if node is None:
            return ""
        parent_id = node.get("parent_id")
        prefix = path_of(parent_id, guard) + " / " if parent_id else ""
        result = prefix + (node.get("title") or "")
        cache[node_id] = result
        return result

    return {n["id"]: path_of(n["id"], set()) for n in project_nodes}


def compute_sync_plan(template_flat: List[Dict], project_nodes: List[Dict]) -> Dict[str, Any]:
    """纯函数：模板 + 一个项目的现有节点 → 该项目要执行的动作清单（不碰数据库）。

    返回 {link, creates, updates, delete_ids}：
      link      [{project_node_id, template_node_id}] 存量节点按标题路径回填锚点
      creates   [{node_id(新UUID), template_node_id, parent_id, title, content_type, options, sort_order}]
      updates   [{node_id, template_node_id, changes: {字段: 新值}}]
      delete_ids 集合：锚点已被模板删除的项目节点及其子树（锚点仍有效的节点会被移走，不删）
    """
    tpl_by_id = {t["id"]: t for t in template_flat}
    proj_by_id = {p["id"]: p for p in project_nodes}

    # 1) 锚点回填：没有锚点的存量节点，按规范化标题路径对上模板节点（先到先得）
    linked: Dict[str, str] = {p["id"]: p["template_node_id"] for p in project_nodes if p.get("template_node_id")}
    claimed = set(linked.values())
    tpl_by_path: Dict[str, List[Dict]] = {}
    for t in template_flat:
        tpl_by_path.setdefault(_norm_title(t["path"]), []).append(t)

    proj_paths = _project_paths(project_nodes)
    link: List[Dict] = []
    for p in project_nodes:
        if p.get("template_node_id"):
            continue
        key = _norm_title(proj_paths.get(p["id"], ""))
        for candidate in tpl_by_path.get(key, []):
            if candidate["id"] not in claimed:
                link.append({"project_node_id": p["id"], "template_node_id": candidate["id"]})
                claimed.add(candidate["id"])
                linked[p["id"]] = candidate["id"]
                break

    # 锚点 → 项目节点映射（同一锚点多个节点时取第一个，其余按用户节点处理）
    mapping: Dict[str, str] = {}
    for p in project_nodes:
        tpl_id = linked.get(p["id"])
        if tpl_id and tpl_id in tpl_by_id:
            mapping.setdefault(tpl_id, p["id"])

    # 2) 逐模板节点（父在前）：有对应项目节点 → 算差异；没有 → 新建
    creates: List[Dict] = []
    updates: List[Dict] = []
    for t in template_flat:
        parent_proj = mapping.get(t["parent_id"]) if t["parent_id"] else None
        proj_id = mapping.get(t["id"])
        if proj_id is None:
            new_id = str(uuid.uuid4())
            mapping[t["id"]] = new_id
            creates.append({
                "node_id": new_id,
                "template_node_id": t["id"],
                "parent_id": parent_proj,
                "title": t["title"],
                "content_type": t["content_type"],
                "options": t["options"],
                "sort_order": t["sort_order"] if t["sort_order"] is not None else 0,
                "value": t.get("value"),
            })
            continue

        current = proj_by_id.get(proj_id) or {}
        changes: Dict[str, Any] = {}
        if (current.get("title") or "") != t["title"]:
            changes["title"] = t["title"]
        if (current.get("parent_id") or None) != (parent_proj or None):
            changes["parent_id"] = parent_proj
        if current.get("sort_order") != t["sort_order"]:
            changes["sort_order"] = t["sort_order"] if t["sort_order"] is not None else 0
        if (current.get("content_type") or "text") != t["content_type"]:
            # 内容类型变了：值按模板重置（旧格式的内容不再适用）
            changes["content_type"] = t["content_type"]
            if t["content_type"] == "select":
                changes["value"] = json.dumps({"selected": "", "options": t["options"]}, ensure_ascii=False)
            else:
                changes["value"] = t.get("value")
        elif t["content_type"] == "select":
            selected, options = _select_state(current.get("value"))
            if options != t["options"]:
                changes["value"] = json.dumps(
                    {"selected": selected if selected in t["options"] else "", "options": t["options"]},
                    ensure_ascii=False,
                )
        if changes:
            updates.append({"node_id": proj_id, "template_node_id": t["id"], "changes": changes})

    # 3) 模板已删除的锚点：删其项目节点及子树；锚点仍有效的节点会被移动到模板位置，跳过不删
    children_map: Dict[str, List[str]] = {}
    for p in project_nodes:
        if p.get("parent_id"):
            children_map.setdefault(p["parent_id"], []).append(p["id"])

    alive = set(tpl_by_id)
    delete_ids: set = set()

    def collect(node_id: str) -> None:
        for child in children_map.get(node_id, []):
            child_tpl = linked.get(child)
            if child_tpl and child_tpl in alive:
                collect(child)  # 锚点有效：不删（会被同步移走），继续看它下面
                continue
            delete_ids.add(child)
            collect(child)

    for p in project_nodes:
        tpl_id = p.get("template_node_id")
        if tpl_id and tpl_id not in alive:
            delete_ids.add(p["id"])
            collect(p["id"])

    return {"link": link, "creates": creates, "updates": updates, "delete_ids": delete_ids}


class InfoTemplateService:
    """详情模板的读取 / 保存 / 同步。所有写操作都由管理员端点触发。"""

    # ── 模板读取 / 保存 ──────────────────────────────

    def get_template_nodes(self) -> List[Dict]:
        """数据库模板节点树；没有模板行时返回空列表（调用方回退 YAML）。"""
        db = SessionLocal()
        try:
            row = db.query(ProjectInfoTemplate).filter(ProjectInfoTemplate.id == TEMPLATE_ID).first()
            if row is None or not row.nodes:
                return []
            data = json.loads(row.nodes)
            return data if isinstance(data, list) else []
        finally:
            db.close()

    def get_template(self) -> Dict[str, Any]:
        """模板完整信息（首次访问用 YAML 模板补种，保证模板节点 id 从此刻起稳定）。"""
        db = SessionLocal()
        try:
            row = db.query(ProjectInfoTemplate).filter(ProjectInfoTemplate.id == TEMPLATE_ID).first()
            source = "db"
            if row is None:
                nodes = self._seed_from_yaml()
                row = ProjectInfoTemplate(
                    id=TEMPLATE_ID, name=TEMPLATE_NAME,
                    nodes=json.dumps(nodes, ensure_ascii=False),
                    updated_at=_now_str(), updated_by="system",
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                source = "yaml"
            project_count = db.query(Project).filter(Project.status != PROJECT_DELETED).count()
            return {
                "id": row.id,
                "name": row.name or TEMPLATE_NAME,
                "nodes": json.loads(row.nodes or "[]"),
                "updated_at": row.updated_at,
                "updated_by": row.updated_by,
                "project_count": project_count,
                "source": source,
            }
        finally:
            db.close()

    def _seed_from_yaml(self) -> List[Dict]:
        """YAML 默认模板 → 带稳定 id 的模板节点树（补种用）。"""
        from app.modules.admin.services.project_service import get_info_nodes_template_from_yaml

        def build(items: List[Dict]) -> List[Dict]:
            rows: List[Dict] = []
            for index, n in enumerate(items):
                if not isinstance(n, dict):
                    continue
                content_type = _node_content_type(n)
                row: Dict[str, Any] = {
                    "id": str(uuid.uuid4()),
                    "title": str(n.get("title") or "未命名节点")[:MAX_TITLE_LEN],
                    "content_type": content_type,
                    "sort_order": index,
                    "children": build(n.get("children") or []),
                }
                if content_type == "select":
                    row["options"] = [str(o) for o in (n.get("options") or [])]
                if n.get("value") is not None:
                    row["value"] = n.get("value")
                rows.append(row)
            return rows

        return build(get_info_nodes_template_from_yaml(None))

    def save_template(self, nodes: Any, username: str = "") -> Dict[str, Any]:
        """校验并保存模板（不同步）。返回保存后的模板信息。"""
        normalized = normalize_template_nodes(nodes)
        db = SessionLocal()
        try:
            row = db.query(ProjectInfoTemplate).filter(ProjectInfoTemplate.id == TEMPLATE_ID).first()
            if row is None:
                row = ProjectInfoTemplate(id=TEMPLATE_ID, name=TEMPLATE_NAME)
                db.add(row)
            row.nodes = json.dumps(normalized, ensure_ascii=False)
            row.updated_at = _now_str()
            row.updated_by = username or "admin"
            db.commit()
            db.refresh(row)
            return {"id": row.id, "name": row.name, "updated_at": row.updated_at, "updated_by": row.updated_by}
        finally:
            db.close()

    # ── 同步 ─────────────────────────────────────────

    def _plan_all(self, nodes: Any) -> Dict[str, Any]:
        """对全部未删除项目计算同步计划（模板节点已校验规范化）。"""
        normalized = normalize_template_nodes(nodes)
        flat = flatten_template(normalized)

        db = SessionLocal()
        try:
            projects = db.query(Project).filter(Project.status != PROJECT_DELETED).all()
            project_infos = [(p.id, p.name or p.id) for p in projects]
            nodes_by_project: Dict[str, List[Dict]] = {}
            for project_id, _ in project_infos:
                rows = db.query(ProjectInfoNode).filter(ProjectInfoNode.project_id == project_id).all()
                nodes_by_project[project_id] = [{
                    "id": n.id,
                    "parent_id": n.parent_id,
                    "title": n.title,
                    "content_type": n.content_type,
                    "value": n.value,
                    "sort_order": n.sort_order,
                    "template_node_id": n.template_node_id,
                } for n in rows]
        finally:
            db.close()

        details: List[Dict] = []
        totals = {"projects": len(project_infos), "changed_projects": 0, "added": 0, "updated": 0, "deleted": 0}
        plans: Dict[str, Dict] = {}
        for project_id, project_name in project_infos:
            plan = compute_sync_plan(flat, nodes_by_project[project_id])
            plans[project_id] = plan
            changed = len(plan["creates"]) + len(plan["updates"]) + len(plan["link"]) + len(plan["delete_ids"])
            if not changed:
                continue
            totals["changed_projects"] += 1
            totals["added"] += len(plan["creates"])
            totals["updated"] += len(plan["updates"]) + len(plan["link"])
            totals["deleted"] += len(plan["delete_ids"])
            if len(details) < MAX_PREVIEW_DETAILS:
                details.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "added": len(plan["creates"]),
                    "updated": len(plan["updates"]) + len(plan["link"]),
                    "deleted": len(plan["delete_ids"]),
                })
        return {"normalized": normalized, "plans": plans, "totals": totals, "details": details}

    def preview_sync(self, nodes: Any) -> Dict[str, Any]:
        """预览（dry-run）：只算影响面，不写库。"""
        result = self._plan_all(nodes)
        return {"dry_run": True, **result["totals"], "details": result["details"]}

    def save_and_sync(self, nodes: Any, username: str = "", operator_name: str = "") -> Dict[str, Any]:
        """保存模板并把变更同步到所有项目。返回保存信息 + 同步统计。"""
        result = self._plan_all(nodes)
        template_info = self.save_template(result["normalized"], username)

        totals = dict(result["totals"])
        totals["failed_projects"] = []
        now = _now_str()

        for project_id, plan in result["plans"].items():
            if not (plan["creates"] or plan["updates"] or plan["link"] or plan["delete_ids"]):
                continue
            db = SessionLocal()
            try:
                self._apply_plan(db, project_id, plan, now, username, operator_name)
                db.commit()
            except Exception as exc:  # 单个项目失败不拖垮整体，最后汇总上报
                db.rollback()
                totals["failed_projects"].append({"project_id": project_id, "error": str(exc)[:200]})
            finally:
                db.close()

        return {"dry_run": False, **template_info, **totals, "details": result["details"]}

    def _apply_plan(self, db, project_id: str, plan: Dict[str, Any], now: str,
                    operator: str = "", operator_name: str = "") -> None:
        """在一个事务里执行某个项目的同步计划（并记一条项目级操作记录）。"""
        rows = db.query(ProjectInfoNode).filter(ProjectInfoNode.project_id == project_id).all()
        by_id = {n.id: n for n in rows}

        # 1) 删除：模板已删除锚点的节点及子树（锚点有效的节点不在此列，会被下面移走）
        node_marks.remove_marks(db, plan["delete_ids"])
        for node_id in plan["delete_ids"]:
            obj = by_id.get(node_id)
            if obj is not None:
                db.delete(obj)
                by_id.pop(node_id, None)

        # 2) 回填锚点
        for item in plan["link"]:
            obj = by_id.get(item["project_node_id"])
            if obj is not None:
                obj.template_node_id = item["template_node_id"]
                obj.updated_at = now

        # 3) 更新（先于新建也无妨：父节点映射用的是同步后的 id）
        for item in plan["updates"]:
            obj = by_id.get(item["node_id"])
            if obj is None:
                continue
            for field, value in item["changes"].items():
                setattr(obj, field, value)
            obj.updated_at = now

        # 4) 新建（父节点在前，parent 已在 plan 里解析成项目节点 id）
        for item in plan["creates"]:
            content_type = item["content_type"]
            if content_type == "select":
                value = json.dumps({"selected": "", "options": item["options"]}, ensure_ascii=False)
            else:
                value = item.get("value")
            db.add(ProjectInfoNode(
                id=item["node_id"],
                project_id=project_id,
                parent_id=item["parent_id"],
                title=item["title"],
                content_type=content_type,
                value=value,
                sort_order=item["sort_order"],
                template_node_id=item["template_node_id"],
                created_at=now,
                updated_at=now,
            ))

        # 5) 操作记录：整树级同步记一条项目级流水（不逐节点刷屏）
        change_log.add_change(
            db, project_id=project_id, action=change_log.ACTION_SYNC,
            detail=change_log.build_sync_detail(
                len(plan["creates"]), len(plan["updates"]) + len(plan["link"]),
                len(plan["delete_ids"]),
            ),
            operator=operator or None, operator_name=operator_name or operator or None,
            created_at=now,
        )


info_template_service = InfoTemplateService()
