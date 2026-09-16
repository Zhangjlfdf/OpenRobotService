"""dataqa 会话/消息服务（AI 数据助手专用表，独立于 call 会话）。"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional, List

from app.models.dataqa import DataqaConversation, DataqaMessage
from app.modules.dataqa.schemas import (
    DataqaConversationCreate,
    DataqaConversationUpdate,
    DataqaMessageCreate,
)
from app.utils.database_utils import DatabaseUtils
from app.utils.data_utils import safe_json_dumps


class DataqaConversationService:
    @staticmethod
    async def create_conversation(db: AsyncSession, conversation: DataqaConversationCreate) -> DataqaConversation:
        conversation_data = conversation.dict()
        if conversation_data.get("metadata_") is not None:
            conversation_data["metadata_"] = safe_json_dumps(conversation_data["metadata_"])
        return await DatabaseUtils.create_and_commit(db, DataqaConversation, **conversation_data)

    @staticmethod
    async def get_conversation(db: AsyncSession, conversation_id: int) -> Optional[DataqaConversation]:
        # 逻辑删除过滤：软删会话对全部接口不可见（详情 404，重命名/删除拒绝）
        result = await db.execute(
            select(DataqaConversation).filter(
                DataqaConversation.id == conversation_id,
                DataqaConversation.is_deleted.is_(False),
            )
        )
        return result.scalars().first()

    @staticmethod
    async def get_conversations_by_user(db: AsyncSession, user_id: str, skip: int = 0, limit: int = 100) -> List[DataqaConversation]:
        result = await db.execute(
            select(DataqaConversation)
            .filter(
                DataqaConversation.user_id == user_id,
                DataqaConversation.is_deleted.is_(False),
            )
            .order_by(desc(DataqaConversation.updated_at))
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def update_conversation(db: AsyncSession, conversation_id: int, conversation: DataqaConversationUpdate) -> Optional[DataqaConversation]:
        # 复用 get_conversation 的软删过滤：已删会话不可再修改/重命名
        db_conversation = await DataqaConversationService.get_conversation(db, conversation_id)
        if not db_conversation:
            return None

        update_data = conversation.dict(exclude_unset=True)
        if update_data.get("metadata_") is not None:
            update_data["metadata_"] = safe_json_dumps(update_data["metadata_"])

        for field, value in update_data.items():
            setattr(db_conversation, field, value)

        return await DatabaseUtils.commit_and_refresh(db, db_conversation)

    @staticmethod
    async def delete_conversation(db: AsyncSession, conversation_id: int) -> bool:
        """逻辑删除：只打标记，不删 dataqa_conversations / dataqa_messages 任何数据。

        与 call 会话同构：数据保留供 AI 统计。原「显式删消息 + db.delete」物理删除已弃用。
        """
        conversation = await DataqaConversationService.get_conversation(db, conversation_id)
        if not conversation:
            return False

        conversation.is_deleted = True
        conversation.deleted_at = datetime.utcnow()
        await db.commit()
        return True


class DataqaMessageService:
    @staticmethod
    async def create_message(db: AsyncSession, message: DataqaMessageCreate) -> DataqaMessage:
        max_sequence = await db.scalar(
            select(func.max(DataqaMessage.sequence))
            .filter(DataqaMessage.conversation_id == message.conversation_id)
        )

        message_data = message.dict()
        message_data["sequence"] = (max_sequence or 0) + 1

        # 追加消息同时刷新所属会话 updated_at：列表按 updated_at 倒序展示，
        # 不刷新会导致在旧会话继续对话时列表排序停滞在创建时刻。
        conv = await db.get(DataqaConversation, message.conversation_id)
        if conv:
            conv.updated_at = func.now()

        if message_data.get("metadata_") is not None:
            message_data["metadata_"] = safe_json_dumps(message_data["metadata_"])

        return await DatabaseUtils.create_and_commit(db, DataqaMessage, **message_data)

    @staticmethod
    async def get_messages_by_conversation(db: AsyncSession, conversation_id: int) -> List[DataqaMessage]:
        result = await db.execute(
            select(DataqaMessage)
            .filter(DataqaMessage.conversation_id == conversation_id)
            .order_by(DataqaMessage.sequence)
        )
        return result.scalars().all()

    @staticmethod
    async def delete_messages_by_conversation(db: AsyncSession, conversation_id: int) -> int:
        result = await db.execute(
            select(DataqaMessage).filter(DataqaMessage.conversation_id == conversation_id)
        )
        messages = result.scalars().all()

        for message in messages:
            await db.delete(message)

        return len(messages)
