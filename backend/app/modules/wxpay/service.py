"""微信支付业务编排：奖励记账 + 提现三分支决策 + 回调落库。

三分支决策（方案 4.3 / 8.4.1）是防重复出资的核心，务必保持「先查单再决策」：

    A. 已有 WAIT_USER_CONFIRM 且 package 未过期  -> 复用 package_info（不建新单）
    B. 已有其它非终态单                          -> 先查单刷新，再按刷新结果分流
    C. 无单 / 已到终态                          -> 用【新的 out_bill_no】发起转账

🔴 同一笔奖励上同时存在两笔非终态转账单 = 重复出资。
   并发由 reward_record 行锁（SELECT ... FOR UPDATE）串行化。
"""

import secrets
import string
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.wxpay.client import WxpayServiceError, wxpay_client
from app.modules.wxpay.models import (
    RewardRecord,
    RewardStatus,
    TransferBill,
    TransferBillState,
    WxpayNotifyLog,
    derive_reward_status,
)
from app.modules.wxpay.schemas import (
    GrantRewardIn,
    RewardOut,
    RewardStatsOut,
    TransferNotifyIn,
    WithdrawOut,
)
from app.services.logging import get_logger

logger = get_logger(__name__)

# package_info 有效期（官方：24 小时未确认自动关单退款）
PACKAGE_TTL = timedelta(hours=24)
# 转账单查询有效期（官方：仅支持 30 天内）
BILL_QUERY_WINDOW = timedelta(days=30)

_ALNUM = string.ascii_uppercase + string.digits


def gen_out_bill_no() -> str:
    """生成商户单号：只能数字+大小写字母，≤32 字符，每次新建必须全新。"""
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    rand = "".join(secrets.choice(_ALNUM) for _ in range(6))
    return f"RP{ts}{rand}"[:32]


def _now() -> datetime:
    """UTC 当前时间（DB 会话时区已强制为 UTC，见 app/core/db.py）。"""
    return datetime.utcnow()


class RewardService:
    """奖励记录：发放与查询。发放时**不创建转账单**。"""

    @staticmethod
    async def grant(
        db: AsyncSession, payload: GrantRewardIn
    ) -> Tuple[RewardRecord, bool]:
        """发放奖励（纯记账）。返回 (记录, 是否重复发放)。"""
        if payload.source_id:
            existed = (
                await db.execute(
                    select(RewardRecord).where(
                        RewardRecord.source_type == payload.source_type,
                        RewardRecord.source_id == payload.source_id,
                    )
                )
            ).scalar_one_or_none()
            if existed:
                return existed, True

        openid = payload.openid
        if not openid:
            openid = await RewardService._resolve_openid(db, payload.user_id)

        days = payload.expire_days or int(settings.WXPAY_REWARD_EXPIRE_DAYS or 30)
        record = RewardRecord(
            id=uuid.uuid4().hex,
            user_id=payload.user_id,
            openid=openid or "",
            amount=int(payload.amount),
            scene_name=payload.scene_name,
            reward_desc=payload.reward_desc,
            remark=(payload.remark or "")[:32],
            source_type=payload.source_type or "",
            source_id=payload.source_id,
            status=RewardStatus.PENDING,
            expire_at=_now() + timedelta(days=days),
        )
        db.add(record)
        try:
            await db.commit()
        except IntegrityError:
            # 并发下撞 (source_type, source_id) 唯一约束
            await db.rollback()
            existed = (
                await db.execute(
                    select(RewardRecord).where(
                        RewardRecord.source_type == payload.source_type,
                        RewardRecord.source_id == payload.source_id,
                    )
                )
            ).scalar_one_or_none()
            if existed:
                return existed, True
            raise
        await db.refresh(record)
        return record, False

    @staticmethod
    async def _resolve_openid(db: AsyncSession, user_id: str) -> Optional[str]:
        from app.models.identity import UserDB

        row = (
            await db.execute(select(UserDB.wechat_openid).where(UserDB.id == user_id))
        ).scalar_one_or_none()
        return row

    @staticmethod
    async def get(db: AsyncSession, reward_id: str, user_id: str) -> Optional[RewardRecord]:
        return (
            await db.execute(
                select(RewardRecord).where(
                    RewardRecord.id == reward_id, RewardRecord.user_id == user_id
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_rewards(
        db: AsyncSession, user_id: str, status: Optional[str], page: int, size: int
    ) -> Tuple[List[RewardRecord], int]:
        conds = [RewardRecord.user_id == user_id]
        if status:
            conds.append(RewardRecord.status == status)

        total = (
            await db.execute(select(func.count(RewardRecord.id)).where(*conds))
        ).scalar() or 0

        rows = (
            await db.execute(
                select(RewardRecord)
                .where(*conds)
                .order_by(RewardRecord.created_at.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).scalars().all()
        return list(rows), int(total)

    @staticmethod
    async def stats(db: AsyncSession, user_id: str) -> RewardStatsOut:
        pending_amt = case(
            (RewardRecord.status == RewardStatus.PENDING, RewardRecord.amount), else_=0
        )
        pending_cnt = case((RewardRecord.status == RewardStatus.PENDING, 1), else_=0)
        success_amt = case(
            (RewardRecord.status == RewardStatus.SUCCESS, RewardRecord.amount), else_=0
        )
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(pending_amt), 0),
                    func.coalesce(func.sum(pending_cnt), 0),
                    func.coalesce(func.sum(success_amt), 0),
                ).where(RewardRecord.user_id == user_id)
            )
        ).one()
        return RewardStatsOut(
            pending_amount=int(row[0] or 0),
            pending_count=int(row[1] or 0),
            withdrawn_amount=int(row[2] or 0),
        )

    @staticmethod
    async def to_out(db: AsyncSession, reward: RewardRecord) -> RewardOut:
        """附带当前有效转账单状态（供前端决定按钮文案）。"""
        bill = await WithdrawService.latest_open_bill(db, reward.id)
        return RewardOut(
            id=reward.id,
            amount=reward.amount,
            scene_name=reward.scene_name,
            reward_desc=reward.reward_desc,
            status=reward.status,
            expire_at=reward.expire_at,
            withdrawn_at=reward.withdrawn_at,
            created_at=reward.created_at,
            bill_state=bill.state if bill else None,
            out_bill_no=bill.out_bill_no if bill else None,
        )


class WithdrawService:
    """立即提现 / 去确认（三分支决策）。"""

    @staticmethod
    async def latest_open_bill(
        db: AsyncSession, reward_id: str
    ) -> Optional[TransferBill]:
        """取该奖励最新一笔非终态转账单；无则返回 None。"""
        return (
            await db.execute(
                select(TransferBill)
                .where(
                    TransferBill.reward_id == reward_id,
                    TransferBill.is_terminal.is_(False),
                )
                .order_by(TransferBill.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def withdraw(db: AsyncSession, reward_id: str, user_id: str) -> WithdrawOut:
        if not getattr(settings, "WXPAY_ENABLED", False):
            return WithdrawOut(action="rejected", message="微信支付功能未启用")

        # 行锁：同一 reward 的并发提现串行化，保证「至多一笔非终态转账单」
        reward = (
            await db.execute(
                select(RewardRecord)
                .where(
                    RewardRecord.id == reward_id, RewardRecord.user_id == user_id
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if reward is None:
            return WithdrawOut(action="rejected", message="奖励不存在")

        now = _now()

        if reward.status == RewardStatus.SUCCESS:
            return WithdrawOut(action="success", message="已到账")
        if reward.status == RewardStatus.EXPIRED:
            return WithdrawOut(action="expired", message="奖励已失效")

        # 过期判定与回写
        if reward.expire_at and reward.expire_at < now:
            reward.status = RewardStatus.EXPIRED
            await db.commit()
            return WithdrawOut(action="expired", message="奖励已失效")

        min_fen = int(getattr(settings, "WXPAY_MIN_WITHDRAW_FEN", 100) or 0)
        if reward.amount < min_fen:
            return WithdrawOut(action="rejected", message=f"未满 {min_fen // 100} 元，暂不可提现")
        max_fen = int(getattr(settings, "WXPAY_MAX_TRANSFER_FEN", 0) or 0)
        if max_fen and reward.amount > max_fen:
            return WithdrawOut(action="rejected", message="单笔金额超过上限，请联系客服")

        bill = await WithdrawService.latest_open_bill(db, reward.id)

        # 分支 B：非 WAIT_USER_CONFIRM 或 package 已过期 -> 先查单刷新
        if bill is not None:
            reusable = (
                TransferBillState.can_reuse_package(bill.state)
                and bill.package_expire_at is not None
                and bill.package_expire_at > now
            )
            if not reusable:
                bill = await WithdrawService._refresh_bill(db, bill)

        # 分支 A：复用 package_info
        if bill is not None and TransferBillState.can_reuse_package(bill.state):
            if bill.package_expire_at and bill.package_expire_at > now and bill.package_info:
                reward.status = RewardStatus.WITHDRAWING
                await db.commit()
                return WithdrawOut(
                    action="confirm",
                    message="请完成收款确认",
                    mch_id=str(getattr(settings, "WXPAY_MCH_ID", "") or ""),
                    app_id=str(settings.WXPAY_APP_ID_EFFECTIVE or ""),
                    package=bill.package_info,
                    out_bill_no=bill.out_bill_no,
                    state=bill.state or "",
                )

        # 分支 B 续：刷新后仍非终态
        if bill is not None and not TransferBillState.is_terminal(bill.state):
            reward.status = RewardStatus.WITHDRAWING
            await db.commit()
            return WithdrawOut(
                action="processing",
                message="转账处理中，请稍后重试",
                out_bill_no=bill.out_bill_no,
                state=bill.state or "",
            )

        # 分支 C：无单或已终态 -> 用新的 out_bill_no 发起转账
        return await WithdrawService._create_bill(db, reward)

    @staticmethod
    async def _refresh_bill(db: AsyncSession, bill: TransferBill) -> Optional[TransferBill]:
        """查单刷新转账单。

        微信侧查不到（404）说明未受理成功，删除占位后返回 None，交由上层重新建单。
        """
        try:
            data = await wxpay_client.query_transfer(bill.out_bill_no)
        except WxpayServiceError as e:
            if e.status_code == 404:
                logger.warning(
                    f"[wxpay] 转账单在微信侧不存在，删除占位: out_bill_no={bill.out_bill_no}"
                )
                await db.delete(bill)
                return None
            bill.last_query_at = _now()
            bill.query_count = (bill.query_count or 0) + 1
            await db.commit()
            return bill

        state = data.get("state") or bill.state
        bill.state = state
        bill.transfer_bill_no = data.get("transfer_bill_no") or bill.transfer_bill_no
        bill.fail_reason = data.get("fail_reason") or ""
        bill.wx_update_time = data.get("update_time") or ""
        bill.is_terminal = TransferBillState.is_terminal(state)
        bill.last_query_at = _now()
        bill.query_count = (bill.query_count or 0) + 1
        await db.commit()
        return bill

    @staticmethod
    async def _create_bill(db: AsyncSession, reward: RewardRecord) -> WithdrawOut:
        """发起转账（必须先落占位单，保证回调到达时能定位到 reward）。"""
        now = _now()
        out_bill_no = gen_out_bill_no()
        bill = TransferBill(
            id=uuid.uuid4().hex,
            reward_id=reward.id,
            out_bill_no=out_bill_no,
            appid=str(settings.WXPAY_APP_ID_EFFECTIVE or ""),
            mchid=str(getattr(settings, "WXPAY_MCH_ID", "") or ""),
            openid=reward.openid,
            amount=reward.amount,
            state="",
            is_terminal=False,
            package_expire_at=now + PACKAGE_TTL,
        )
        db.add(bill)
        await db.flush()

        try:
            data = await wxpay_client.create_transfer(
                out_bill_no=out_bill_no,
                openid=reward.openid,
                amount=reward.amount,
                remark=reward.remark or reward.reward_desc,
                scene_name=reward.scene_name,
                reward_desc=reward.reward_desc,
                perception=str(getattr(settings, "WXPAY_USER_RECV_PERCEPTION", "") or ""),
                scene_id=str(getattr(settings, "WXPAY_TRANSFER_SCENE_ID", "") or ""),
            )
        except WxpayServiceError as e:
            # 明确 4xx 且非系统错误 => 微信侧未受理，删除占位允许重试
            if (not e.system_error) and 400 <= e.status_code < 500:
                logger.warning(
                    f"[wxpay] 发起转账被拒绝，占位单已删除: "
                    f"out_bill_no={out_bill_no} code={e.code} msg={e}"
                )
                await db.delete(bill)
                reward.status = RewardStatus.PENDING
                await db.commit()
                return WithdrawOut(action="rejected", message=e.message or "发起转账失败")
            # 其余（网络异常/系统错误）保留占位，等兜底查单刷新，绝不换单重试
            logger.error(
                f"[wxpay] 发起转账异常，占位单保留待查单: "
                f"out_bill_no={out_bill_no} request_id={e.request_id} err={e}"
            )
            reward.status = RewardStatus.WITHDRAWING
            await db.commit()
            return WithdrawOut(
                action="processing",
                message="转账处理中，请稍后重试",
                out_bill_no=out_bill_no,
            )

        state = data.get("state") or ""
        bill.state = state
        bill.transfer_bill_no = data.get("transfer_bill_no") or ""
        bill.package_info = data.get("package_info") or ""
        bill.wx_create_time = data.get("create_time") or ""
        bill.is_terminal = TransferBillState.is_terminal(state)

        reward.status = derive_reward_status(state)
        if reward.status == RewardStatus.SUCCESS:
            reward.withdrawn_at = now
        await db.commit()

        if bill.package_info:
            return WithdrawOut(
                action="confirm",
                message="请完成收款确认",
                mch_id=str(getattr(settings, "WXPAY_MCH_ID", "") or ""),
                app_id=str(settings.WXPAY_APP_ID_EFFECTIVE or ""),
                package=bill.package_info,
                out_bill_no=out_bill_no,
                state=state,
            )
        return WithdrawOut(
            action="processing",
            message="转账处理中，请稍后重试",
            out_bill_no=out_bill_no,
            state=state,
        )


class NotifyService:
    """接收 Go 服务推送的回调结果并落库（方案 8.4.2）。"""

    @staticmethod
    async def handle(db: AsyncSession, payload: TransferNotifyIn) -> Tuple[bool, str]:
        """处理转账回调。返回 (是否重复通知, 说明)。"""
        existed = (
            await db.execute(
                select(WxpayNotifyLog.id).where(
                    WxpayNotifyLog.notify_id == payload.notify_id
                )
            )
        ).scalar_one_or_none()
        if existed:
            return True, "duplicated"

        log = WxpayNotifyLog(
            id=uuid.uuid4().hex,
            notify_id=payload.notify_id,
            event_type=payload.event_type,
            resource_type="encrypt-resource",
            biz_no=payload.out_bill_no,
            decrypted=payload.model_dump(),
            process_result="DONE",
            processed_at=_now(),
        )
        db.add(log)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return True, "duplicated"

        bill = (
            await db.execute(
                select(TransferBill).where(
                    TransferBill.out_bill_no == payload.out_bill_no
                )
            )
        ).scalar_one_or_none()

        if bill is None:
            # 正常流程中占位单先于建单落库，走到这里说明数据异常；
            # 必须返回 2xx 避免微信无限重试，同时留痕告警由巡检任务兜底。
            logger.error(
                f"[wxpay] 回调对应的转账单不存在: out_bill_no={payload.out_bill_no} "
                f"notify_id={payload.notify_id}"
            )
            await db.commit()
            return False, "bill_not_found"

        state = (payload.state or "").upper()
        if bill.is_terminal:
            # 终态不回退，防止乱序通知把 SUCCESS 打回
            logger.warning(
                f"[wxpay] 转账单已终态，忽略回调: out_bill_no={payload.out_bill_no} "
                f"current={bill.state} incoming={state}"
            )
            await db.commit()
            return False, "already_terminal"

        bill.state = state
        bill.is_terminal = TransferBillState.is_terminal(state)
        bill.fail_reason = payload.fail_reason or ""
        bill.transfer_bill_no = payload.transfer_bill_no or bill.transfer_bill_no
        bill.wx_update_time = payload.update_time or ""
        bill.last_query_at = _now()

        reward = (
            await db.execute(
                select(RewardRecord).where(RewardRecord.id == bill.reward_id)
            )
        ).scalar_one_or_none()
        if reward is not None:
            if state == TransferBillState.SUCCESS:
                reward.status = RewardStatus.SUCCESS
                reward.withdrawn_at = _now()
            elif bill.is_terminal:
                reward.status = RewardStatus.PENDING
            else:
                reward.status = RewardStatus.WITHDRAWING

        await db.commit()
        logger.info(
            f"[wxpay] 转账回调已处理: out_bill_no={payload.out_bill_no} "
            f"state={state} amount={payload.transfer_amount}"
        )
        return False, "done"


async def expire_overdue_rewards(db: AsyncSession) -> int:
    """超期未提现的奖励置为已失效。返回影响行数。"""
    rows = (
        await db.execute(
            select(RewardRecord).where(
                RewardRecord.status == RewardStatus.PENDING,
                RewardRecord.expire_at.isnot(None),
                RewardRecord.expire_at < _now(),
            )
        )
    ).scalars().all()
    for r in rows:
        r.status = RewardStatus.EXPIRED
    if rows:
        await db.commit()
    return len(rows)


async def reconcile_open_bills(db: AsyncSession, limit: int = 200) -> int:
    """兜底查单：刷新非终态转账单（微信不保证回调送达，这是必选项）。"""
    rows = (
        await db.execute(
            select(TransferBill)
            .where(
                TransferBill.is_terminal.is_(False),
                TransferBill.created_at >= _now() - BILL_QUERY_WINDOW,
            )
            .order_by(TransferBill.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    refreshed = 0
    for bill in rows:
        try:
            data = await wxpay_client.query_transfer(bill.out_bill_no)
        except WxpayServiceError as e:
            if e.status_code == 404:
                # 微信侧无单：删除占位，奖励回退为待提现
                reward = (
                    await db.execute(
                        select(RewardRecord).where(RewardRecord.id == bill.reward_id)
                    )
                ).scalar_one_or_none()
                if reward is not None and reward.status == RewardStatus.WITHDRAWING:
                    reward.status = RewardStatus.PENDING
                await db.delete(bill)
                await db.commit()
                refreshed += 1
                continue
            logger.warning(f"[wxpay] 兜底查单失败: {bill.out_bill_no} err={e}")
            continue

        state = data.get("state") or bill.state
        bill.state = state
        bill.transfer_bill_no = data.get("transfer_bill_no") or bill.transfer_bill_no
        bill.fail_reason = data.get("fail_reason") or ""
        bill.wx_update_time = data.get("update_time") or ""
        bill.is_terminal = TransferBillState.is_terminal(state)
        bill.last_query_at = _now()
        bill.query_count = (bill.query_count or 0) + 1

        reward = (
            await db.execute(
                select(RewardRecord).where(RewardRecord.id == bill.reward_id)
            )
        ).scalar_one_or_none()
        if reward is not None:
            if state == TransferBillState.SUCCESS:
                reward.status = RewardStatus.SUCCESS
                reward.withdrawn_at = reward.withdrawn_at or _now()
            elif bill.is_terminal:
                if reward.status == RewardStatus.WITHDRAWING:
                    reward.status = RewardStatus.PENDING
            else:
                reward.status = RewardStatus.WITHDRAWING
        await db.commit()
        refreshed += 1
    return refreshed


async def check_consistency(db: AsyncSession) -> Dict[str, int]:
    """一致性巡检：奖励=提现中 但无「非终态」转账单 -> 回退为待提现。"""
    rows = (
        await db.execute(
            select(RewardRecord).where(RewardRecord.status == RewardStatus.WITHDRAWING)
        )
    ).scalars().all()

    fixed = 0
    stuck = 0
    for reward in rows:
        bill = await WithdrawService.latest_open_bill(db, reward.id)
        if bill is None:
            reward.status = RewardStatus.PENDING
            fixed += 1
        elif bill.created_at and bill.created_at < _now() - timedelta(hours=48):
            logger.warning(
                f"[wxpay] 转账单长时间未终态，需人工介入: "
                f"out_bill_no={bill.out_bill_no} state={bill.state}"
            )
            stuck += 1
    if rows:
        await db.commit()
    return {"withdrawing": len(rows), "fixed": fixed, "stuck": stuck}
