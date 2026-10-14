"""微信支付 Pydantic 模型。"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ============ 奖励 ============


class RewardOut(BaseModel):
    id: str
    amount: int = Field(..., description="奖励金额，单位：分")
    scene_name: str
    reward_desc: str
    status: str = Field(..., description="pending/withdrawing/success/expired")
    expire_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    # 当前有效转账单（用于前端决定按钮文案）
    bill_state: Optional[str] = None
    out_bill_no: Optional[str] = None

    class Config:
        from_attributes = True


class RewardListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[RewardOut]


class RewardStatsOut(BaseModel):
    pending_amount: int = Field(0, description="待提现总额（分）")
    pending_count: int = 0
    withdrawn_amount: int = Field(0, description="累计已到账（分）")


# ============ 提现 ============


class WithdrawOut(BaseModel):
    """提现/去确认结果。

    action:
    - confirm         有待确认转账单，用 package 调起 requestMerchantTransfer
    - processing      转账单处于其它非终态，提示稍后再试
    - need_real_name  收款人姓名未通过微信实名校验，需用户填写真实姓名后重新提交
    - success         已到账
    - expired         奖励已失效
    - rejected        不满足提现条件（金额/状态），见 message
    """

    action: str
    message: str = ""
    mch_id: str = ""
    app_id: str = ""
    package: str = ""
    out_bill_no: str = ""
    state: str = ""


class WithdrawIn(BaseModel):
    """提现请求体。

    user_name 仅在后端返回 `action=need_real_name` 后由用户填写并再次提交。
    微信受理该姓名后会被固化为 `users.name`，后续支付自动复用，无需再次填写。
    """

    user_name: str = Field("", max_length=64, description="收款人真实姓名，须与微信实名一致")


# ============ 内部：发放奖励 ============


class GrantRewardIn(BaseModel):
    user_id: str = Field(..., description="users.id")
    openid: str = Field("", description="收款人 openid，留空则回退用户绑定的 wechat_openid")
    amount: int = Field(..., ge=1, description="奖励金额，单位：分")
    scene_name: str = Field(..., description="报备信息①活动名称")
    reward_desc: str = Field(..., description="报备信息②奖励说明")
    remark: str = Field("", max_length=32, description="转账备注，≤32 字符")
    source_type: str = Field("", description="来源类型，如 sign_in / invite")
    source_id: Optional[str] = Field(None, description="来源业务 ID，防重复发放")
    expire_days: Optional[int] = Field(None, ge=1, description="有效期天数，留空用全局配置")


class GrantRewardOut(BaseModel):
    id: str
    user_id: str
    amount: int
    status: str
    expire_at: Optional[datetime] = None
    duplicated: bool = Field(False, description="True 表示同 (source_type, source_id) 已发放过，返回既有记录")


# ============ 内部：Go 服务推送的回调 ============


class TransferNotifyIn(BaseModel):
    """Go 服务验签解密后推送的商家转账回调内容。"""

    notify_id: str
    event_type: str = ""
    notify_create_time: str = ""
    original_type: str = ""
    mch_id: str = ""
    appid: str = ""
    out_bill_no: str
    transfer_bill_no: str = ""
    openid: str = ""
    state: str
    transfer_amount: int = 0
    transfer_remark: str = ""
    fail_reason: str = ""
    create_time: str = ""
    update_time: str = ""


class NotifyAckOut(BaseModel):
    code: str = "OK"
    message: str = ""
    duplicated: bool = False
