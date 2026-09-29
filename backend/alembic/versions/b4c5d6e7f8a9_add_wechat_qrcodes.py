"""add wechat_qrcodes table

微信公众号带参数二维码实体表：存储 scene_str、ticket、status、batch_id 等字段。
与 WechatService.create_qrcode_ticket 配合使用。

幂等：应用启动时 Base.metadata.create_all 已经会建表，
走过启动的库再跑本迁移会撞 "table already exists"，所以先查再建。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'wechat_qrcodes'


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
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True, comment='主键'),
        sa.Column('scene_str', sa.String(length=64), nullable=False, comment='场景值'),
        sa.Column('name', sa.String(length=128), nullable=False, server_default='', comment='二维码名称/用途'),
        sa.Column('description', sa.Text(), nullable=True, comment='用途说明'),
        sa.Column('ticket', sa.String(length=256), nullable=True, comment='微信 ticket'),
        sa.Column('url', sa.String(length=512), nullable=True, comment='微信短链'),
        sa.Column('qrcode_image_url', sa.String(length=512), nullable=True, comment='可访问图片地址'),
        sa.Column('type', sa.String(length=16), nullable=False, server_default='permanent', comment='temporary/permanent'),
        sa.Column('expire_seconds', sa.Integer(), nullable=True, comment='临时码有效期(秒)'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='init', comment='状态'),
        sa.Column('batch_id', sa.String(length=64), nullable=True, comment='批次ID'),
        sa.Column('redirect_url', sa.String(length=512), nullable=True, comment='扫码跳转URL'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人'),
        sa.Column('published_by', sa.String(length=64), nullable=True, comment='发布人'),
        sa.Column('deprecated_by', sa.String(length=64), nullable=True, comment='弃用人'),
        sa.Column('ticket_created_at', sa.DateTime(), nullable=True, comment='ticket创建时间'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), comment='记录创建时间'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), comment='记录更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scene_str', name='uq_wechat_qrcodes_scene_str'),
    )
    op.create_index('ix_wechat_qrcodes_scene_str', TABLE, ['scene_str'])
    op.create_index('ix_wechat_qrcodes_status', TABLE, ['status'])
    op.create_index('ix_wechat_qrcodes_batch_id', TABLE, ['batch_id'])
    op.create_index('ix_wechat_qrcodes_status_type', TABLE, ['status', 'type'])


def downgrade() -> None:
    if not _table_exists():
        return
    op.drop_index('ix_wechat_qrcodes_status_type', table_name=TABLE)
    op.drop_index('ix_wechat_qrcodes_batch_id', table_name=TABLE)
    op.drop_index('ix_wechat_qrcodes_status', table_name=TABLE)
    op.drop_index('ix_wechat_qrcodes_scene_str', table_name=TABLE)
    op.drop_table(TABLE)
