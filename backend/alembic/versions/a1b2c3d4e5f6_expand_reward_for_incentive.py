"""奖励机制扩展：RewardConfig 新表 + RewardRecord 扩展字段 + 唯一约束改三元组

Revision ID: a1b2c3d4e5f6
Revises: f2f1bc37b495
Create Date: 2026-10-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f2f1bc37b495'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. 新建 reward_config 表 ──
    op.create_table(
        'reward_config',
        sa.Column('config_key', sa.String(length=64), primary_key=True, comment='配置键，如 task_archive / audit_policy'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true(), comment='是否启用'),
        sa.Column('config', sa.JSON(), nullable=False, comment='规则 JSON，结构由 config_key 决定'),
        sa.Column('updated_by', sa.String(length=64), nullable=True, comment='最近修改人 users.id'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), comment='更新时间（UTC）'),
    )

    # ── 2. RewardRecord 加 4 列 ──
    op.add_column('reward_record', sa.Column('role', sa.String(length=32), nullable=True, comment='奖励角色: reporter/assignee/reviewer/liker/ranker/manual'))
    op.add_column('reward_record', sa.Column('audit_by', sa.String(length=64), nullable=True, comment='审核人 users.id（自动通过时为 AUTO）'))
    op.add_column('reward_record', sa.Column('audit_at', sa.DateTime(), nullable=True, comment='审核时间'))
    op.add_column('reward_record', sa.Column('audit_remark', sa.String(length=255), nullable=True, comment='审核备注（驳回原因或风控标记）'))

    # ── 3. 旧唯一约束 → 新三元组约束 ──
    op.drop_constraint('uq_reward_source', 'reward_record', type_='unique')

    # ── 4. 历史数据回填 role ──
    op.execute("UPDATE reward_record SET role='manual' WHERE role IS NULL")

    # ── 5. 新唯一约束 ──
    op.create_unique_constraint('uq_reward_source_role', 'reward_record', ['source_type', 'source_id', 'role'])

    # ── 6. 新索引 ──
    op.create_index('ix_reward_audit_status', 'reward_record', ['status'], unique=False)
    op.create_index('ix_reward_task_role', 'reward_record', ['source_id', 'role'], unique=False)
    op.create_index('ix_reward_user_created', 'reward_record', ['user_id', 'created_at'], unique=False)

    # ── 7. 种子数据：4 行默认配置 ──
    op.execute("""
        INSERT INTO reward_config (config_key, enabled, config) VALUES
        ('task_archive', true, '{"reporter_fen":200,"assignee_fen":300,"reviewer_fen":100}'),
        ('monthly_rank', true, '{"top3_fen":10000,"top4_10_fen":5000}'),
        ('quality_like', true, '{"per_like_fen":20,"daily_limit":5}'),
        ('audit_policy', true, '{"auto_pass_max_fen":500,"force_audit_user_total_count":10,"force_audit_user_daily_fen":2000,"enabled_scenes":["task_archive","quality_like","monthly_rank"]}')
    """)


def downgrade() -> None:
    # 种子数据先清
    op.execute("DELETE FROM reward_config WHERE config_key IN ('task_archive','monthly_rank','quality_like','audit_policy')")

    # 删索引
    op.drop_index('ix_reward_user_created', table_name='reward_record')
    op.drop_index('ix_reward_task_role', table_name='reward_record')
    op.drop_index('ix_reward_audit_status', table_name='reward_record')

    # 恢复旧唯一约束
    op.drop_constraint('uq_reward_source_role', 'reward_record', type_='unique')
    op.create_unique_constraint('uq_reward_source', 'reward_record', ['source_type', 'source_id'])

    # 删列
    op.drop_column('reward_record', 'audit_remark')
    op.drop_column('reward_record', 'audit_at')
    op.drop_column('reward_record', 'audit_by')
    op.drop_column('reward_record', 'role')

    # 删表
    op.drop_table('reward_config')
