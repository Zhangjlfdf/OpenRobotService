"""项目信息树节点 Service（逐节点 CRUD + 递归查树 + 删子树 + 移动）。

存储演示数据 a.json 中的 info_nodes：每节点一行，parent_id 递归成树。
与 ext_info 的区别：ext_info 存「开发者定义结构的值」，info_node 存
「结构本身是用户数据」——用户可自由增删节点、拖拽排序、编辑值，
每个节点独立 CRUD，不会因整文档读改写而互相覆盖。

每个写操作都在同一事务里追加一条操作记录（project_info_node_change，见
info_node_change_service）：时间 / 人员 / 节点 / 具体变动，供前端「编辑历史」展示；
本次调用未实际改变任何字段的更新不记流水。
"""
from datetime import datetime
from typing import Dict, List, Optional
import uuid

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.modules.admin.models_das.models import ProjectInfoNode, Project
from app.modules.admin.services import info_node_change_service as change_log
from app.modules.admin.services import info_node_mark_service as node_marks
from app.models.delivery import PROJECT_DELETED
from app.modules.admin.utils_das.config import DATABASE_URL
from sqlalchemy import create_engine

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _now_str() -> str:
    """与 delivery.py 一致，用字符串存时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _node_to_dict(node: ProjectInfoNode) -> Dict:
    return {
        "id": node.id,
        "project_id": node.project_id,
        "parent_id": node.parent_id,
        "title": node.title,
        "content_type": node.content_type,
        "value": node.value,
        "sort_order": node.sort_order,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
    }


class InfoNodeService:
    """项目信息树节点 CRUD。所有操作基于 project_info_node 邻接表。"""

    def __init__(self):
        self.engine = engine

    # ── 查询 ──────────────────────────────────────────

    def get_tree(self, project_id: str) -> List[Dict]:
        """获取项目信息树。一次查全量平铺行，Python 递归组装成树。

        递归 CTE 在 MySQL 8 可用，但项目量级（单项目百级节点）下
        一次 SELECT + 内存组装更简单，且避免版本兼容问题。
        """
        db = SessionLocal()
        try:
            nodes = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.project_id == project_id
            ).order_by(ProjectInfoNode.sort_order).all()

            if not nodes:
                return []

            # 构建 id→children 映射
            children_map: Dict[Optional[str], List[ProjectInfoNode]] = {}
            for n in nodes:
                children_map.setdefault(n.parent_id, []).append(n)

            def build(parent_id: Optional[str]) -> List[Dict]:
                result = []
                for n in children_map.get(parent_id, []):
                    d = _node_to_dict(n)
                    d["children"] = build(n.id)
                    result.append(d)
                return result

            return build(None)
        finally:
            db.close()

    def get_node(self, node_id: str) -> Optional[Dict]:
        """查单个节点（不含 children）。"""
        db = SessionLocal()
        try:
            node = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == node_id
            ).first()
            return _node_to_dict(node) if node else None
        finally:
            db.close()

    def _get_descendant_ids(self, db, node_id: str) -> List[str]:
        """递归 CTE 获取某节点及其所有后代 ID（含自身）。"""
        sql = text("""
            WITH RECURSIVE subtree AS (
                SELECT id FROM project_info_node WHERE id = :nid
                UNION ALL
                SELECT n.id FROM project_info_node n
                INNER JOIN subtree s ON n.parent_id = s.id
            )
            SELECT id FROM subtree
        """)
        rows = db.execute(sql, {"nid": node_id}).fetchall()
        return [r[0] for r in rows]

    # ── 创建 ──────────────────────────────────────────

    def create_node(self, project_id: str, node_data: Dict,
                    operator: Optional[str] = None,
                    operator_name: Optional[str] = None) -> Dict:
        """创建节点。node_data 需含 id（客户端生成 UUID）、title；
        可选 parent_id / content_type / value / sort_order。
        """
        db = SessionLocal()
        try:
            now = _now_str()
            node = ProjectInfoNode(
                id=node_data["id"],
                project_id=project_id,
                parent_id=node_data.get("parent_id"),
                title=node_data.get("title", "未命名节点"),
                content_type=node_data.get("content_type", "text"),
                value=node_data.get("value"),
                sort_order=node_data.get("sort_order", 0),
                created_at=now,
                updated_at=now,
            )
            db.add(node)
            change_log.add_change(
                db, project_id=project_id, action=change_log.ACTION_CREATE,
                node_id=node.id, parent_id=node.parent_id, node_title=node.title,
                detail=change_log.build_create_detail(node.title),
                operator=operator, operator_name=operator_name, created_at=now,
            )
            db.commit()
            db.refresh(node)
            return _node_to_dict(node)
        finally:
            db.close()

    # ── 更新 ──────────────────────────────────────────

    def update_node(self, node_id: str, update_data: Dict,
                    operator: Optional[str] = None,
                    operator_name: Optional[str] = None) -> Optional[Dict]:
        """更新节点可编辑字段（title / content_type / value / sort_order）。
        parent_id 变更走 move_node，不在此处理。
        """
        db = SessionLocal()
        try:
            node = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == node_id
            ).first()
            if not node:
                return None

            before = {field: getattr(node, field)
                      for field in ("title", "content_type", "value", "sort_order")}
            for field in ("title", "content_type", "value", "sort_order"):
                if field in update_data:
                    setattr(node, field, update_data[field])
            node.updated_at = _now_str()

            # 逐字段对比后才记流水：前端乐观更新失败重试等场景可能提交与现值相同的字段，
            # 这种「没有实际变动」的更新不入历史（避免刷屏）。
            detail = change_log.build_update_detail(before, update_data)
            if detail:
                change_log.add_change(
                    db, project_id=node.project_id, action=change_log.ACTION_UPDATE,
                    node_id=node.id, parent_id=node.parent_id, node_title=node.title,
                    detail=detail, operator=operator, operator_name=operator_name,
                )

            db.commit()
            db.refresh(node)
            return _node_to_dict(node)
        finally:
            db.close()

    def move_node(self, node_id: str, new_parent_id: Optional[str],
                  new_sort_order: int = 0,
                  operator: Optional[str] = None,
                  operator_name: Optional[str] = None) -> Optional[Dict]:
        """移动节点（改变父节点和/或排序）。不做环检测的简单实现——
        前端树形控件拖拽时不会把节点拖入自己的子树。
        """
        db = SessionLocal()
        try:
            node = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == node_id
            ).first()
            if not node:
                return None

            old_parent_id = node.parent_id
            old_sort_order = node.sort_order

            node.parent_id = new_parent_id
            node.sort_order = new_sort_order
            node.updated_at = _now_str()

            if new_parent_id != old_parent_id or new_sort_order != old_sort_order:
                def _title(parent_id: Optional[str]) -> Optional[str]:
                    if not parent_id:
                        return None
                    parent = db.query(ProjectInfoNode).filter(
                        ProjectInfoNode.id == parent_id
                    ).first()
                    return parent.title if parent else None

                detail = change_log.build_move_detail(
                    node.title, _title(old_parent_id), _title(new_parent_id),
                    same_parent=(new_parent_id == old_parent_id),
                )
                change_log.add_change(
                    db, project_id=node.project_id, action=change_log.ACTION_MOVE,
                    node_id=node.id, parent_id=node.parent_id, node_title=node.title,
                    detail=detail, operator=operator, operator_name=operator_name,
                )

            db.commit()
            db.refresh(node)
            return _node_to_dict(node)
        finally:
            db.close()

    # ── 删除 ──────────────────────────────────────────

    def delete_node(self, node_id: str, operator: Optional[str] = None,
                    operator_name: Optional[str] = None) -> bool:
        """删除节点及其全部子树。递归 CTE 找出所有后代 ID，批量删除。

        操作记录只有一条，挂在被删节点的上级节点上（parent_id）——节点删除后
        自身的历史已无从打开，前端在上级节点的「编辑历史」里展示这条删除记录。
        """
        db = SessionLocal()
        try:
            node = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == node_id
            ).first()
            if not node:
                return False

            ids = self._get_descendant_ids(db, node_id)
            if not ids:
                return False

            change_log.add_change(
                db, project_id=node.project_id, action=change_log.ACTION_DELETE,
                node_id=node.id, parent_id=node.parent_id, node_title=node.title,
                detail=change_log.build_delete_detail(node.title, len(ids) - 1),
                operator=operator, operator_name=operator_name,
            )
            # 被删节点的「关注」随节点一起清掉（树里已无此节点，星标点不开）
            node_marks.remove_marks(db, ids)
            db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id.in_(ids)
            ).delete(synchronize_session=False)
            db.commit()
            return True
        finally:
            db.close()

    # ── 批量导入 ────────────────────────────────────────

    def import_tree(self, project_id: str, nodes: List[Dict],
                    operator: Optional[str] = None,
                    operator_name: Optional[str] = None,
                    source: str = "import") -> int:
        """批量导入信息树（如从 a.json 的 info_nodes 导入）。
        节点需含 id/title/children/sort_order 等。
        先清空旧节点再导入。返回导入数量。

        source: import=外部导入；template=按模板重建（仅影响记录文案）。
        整树替换记一条项目级操作记录（node_id 为 NULL），不逐节点刷屏。
        """
        db = SessionLocal()
        try:
            removed = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.project_id == project_id
            ).count()

            # 清空旧节点（旧节点上的「关注」一并清掉，新树是全新的节点 id）
            db.query(ProjectInfoNode).filter(
                ProjectInfoNode.project_id == project_id
            ).delete(synchronize_session=False)
            node_marks.clear_project_marks(db, project_id)

            now = _now_str()
            flat = []

            def flatten(nodes_list: List[Dict], parent_id: Optional[str] = None):
                for n in nodes_list:
                    flat.append(ProjectInfoNode(
                        id=n["id"],
                        project_id=project_id,
                        parent_id=parent_id,
                        title=n.get("title", "未命名节点"),
                        content_type=n.get("content_type", "text"),
                        value=n.get("value"),
                        sort_order=n.get("sort_order", 0),
                        template_node_id=n.get("template_node_id"),
                        created_at=now,
                        updated_at=now,
                    ))
                    if n.get("children"):
                        flatten(n["children"], n["id"])

            flatten(nodes)
            db.bulk_save_objects(flat)
            change_log.add_change(
                db, project_id=project_id, action=change_log.ACTION_IMPORT,
                detail=change_log.build_import_detail(source, len(flat), removed),
                operator=operator, operator_name=operator_name,
            )
            db.commit()
            return len(flat)
        finally:
            db.close()

    def import_template(self, project_id: str, operator: Optional[str] = None,
                        operator_name: Optional[str] = None) -> int:
        """按项目模板重建信息树（替换现有全部节点）。

        模板来源与新建项目一致：project_type → {type}.yaml，缺省 default.yaml
        （project_templates/）。既服务「存量空项目一键初始化」，也保证前端
        空态按钮与后端新建项目走同一份结构定义，不会两套模板漂移。
        模板为空（或整个文件解析失败）时不改动现有节点，返回 0。
        """
        from app.modules.admin.services.project_service import (
            get_info_nodes_template, template_node_value,
        )

        db = SessionLocal()
        try:
            project = db.query(Project).filter(
                Project.id == project_id,
                Project.status != PROJECT_DELETED,
            ).first()
            if not project:
                raise LookupError("项目不存在")
            project_type = project.project_type
        finally:
            db.close()

        def build(nodes_tpl: List[Dict]) -> List[Dict]:
            rows = []
            for index, n in enumerate(nodes_tpl):
                content_type, value = template_node_value(n)
                row = {
                    "id": str(uuid.uuid4()),
                    "title": n.get("title", "未命名节点"),
                    "content_type": content_type,
                    "value": value,
                    "sort_order": n.get("sort_order", index),
                    # 数据库详情模板的节点带稳定 id：记到锚点，后续模板同步按它对齐
                    "template_node_id": n.get("id") if isinstance(n.get("id"), str) and n.get("id") else None,
                }
                children = build(n.get("children") or [])
                if children:
                    row["children"] = children
                rows.append(row)
            return rows

        rows = build(get_info_nodes_template(project_type))
        if not rows:
            return 0
        return self.import_tree(project_id, rows, operator=operator,
                                operator_name=operator_name, source="template")


info_node_service = InfoNodeService()
