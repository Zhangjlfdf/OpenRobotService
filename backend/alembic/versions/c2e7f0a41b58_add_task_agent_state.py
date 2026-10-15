"""add task_agent_state

工单的 Agent 状态表：U老师 对这张工单的结构化工作记忆（九栏）+ 覆盖水位线。
单列一张表而不是塞 tasks.metadata_info —— AI 侧 upsert_task 会整体重建 metadata_info。

启动时 create_all 可能已经建过表（部署链路的 schema_sync 只管列/索引、不管整表），
这里先查再建，避免重复执行报错。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "c2e7f0a41b58"
down_revision: Union[str, None] = "b8c1d4e7f203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "task_agent_state"


def _table_exists() -> bool:
    bind = op.get_bind()
    result = bind.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :t"
    ), {"t": TABLE}).scalar()
    return bool(result)


def upgrade() -> None:
    if _table_exists():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("task_id", sa.BigInteger(), nullable=False, comment="任务ID"),
        sa.Column("state", sa.JSON(), nullable=True, comment="九栏结构化状态（键随迭代增删）"),
        sa.Column("covered_comment_id", sa.BigInteger(), nullable=False,
                  server_default="0", comment="水位线：状态已覆盖到这条 task_comments.id（含）"),
        sa.Column("rounds", sa.Integer(), nullable=False, server_default="0",
                  comment="刷新次数（观察用）"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", name="uq_task_agent_state_task"),
    )
    op.create_index("ix_task_agent_state_task_id", TABLE, ["task_id"])


def downgrade() -> None:
    if not _table_exists():
        return
    op.drop_table(TABLE)
