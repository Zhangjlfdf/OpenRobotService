// 微信支付 · 商家转账 —— 用户端「我的历史奖励」接口封装
//
// 业务形态（无卡包 / 无余额账户）：奖励发放只在商户侧记账（reward_record），
// 用户点「立即提现」时才真正向微信发起转账单；因此列表读的是奖励，不是转账单。
// 对应后端 backend/app/modules/wxpay/api/rewards.py（JWT 登录态）。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.WXPAY.BASE_URL, 'Wxpay');

/** 奖励状态（与后端 RewardStatus 对齐） */
export type RewardStatus = 'pending' | 'withdrawing' | 'success' | 'expired';

export interface RewardItem {
  id: string;
  /** 奖励金额，单位：分 */
  amount: number;
  /** 报备信息①活动名称 */
  scene_name: string;
  /** 报备信息②奖励说明 */
  reward_desc: string;
  status: RewardStatus;
  expire_at?: string | null;
  withdrawn_at?: string | null;
  created_at?: string | null;
  /** 当前有效转账单状态（微信侧 state，用于前端判断按钮文案） */
  bill_state?: string | null;
  out_bill_no?: string | null;
}

export interface RewardListResp {
  total: number;
  page: number;
  size: number;
  items: RewardItem[];
}

export interface RewardStats {
  /** 待提现总额（分） */
  pending_amount: number;
  /** 待提现笔数 */
  pending_count: number;
  /** 累计已到账（分） */
  withdrawn_amount: number;
}

/**
 * 提现结果动作：
 * - confirm    有待确认转账单，用 mch_id/app_id/package 调起 requestMerchantTransfer
 * - processing 转账单处于其它非终态（含刚发起未返回 package），提示稍后查看
 * - success    已到账
 * - expired    奖励已失效
 * - rejected   不满足提现条件（金额/状态/微信拒绝），见 message
 */
export type WithdrawAction = 'confirm' | 'processing' | 'success' | 'expired' | 'rejected';

export interface WithdrawResp {
  action: WithdrawAction;
  message: string;
  mch_id: string;
  app_id: string;
  /** 微信返回的 package_info，仅在 confirm 时有值 */
  package: string;
  out_bill_no: string;
  state: string;
}

/**
 * 我的历史奖励列表。
 *
 * 始终 skipCache：奖励状态由微信回调异步扭转（提现中 → 已到账），
 * 命中 5 分钟 GET 缓存会让用户看到「明明已到账却还显示提现中」。
 */
export function fetchRewards(params?: {
  status?: RewardStatus | '';
  page?: number;
  size?: number;
}): Promise<RewardListResp> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  qs.set('page', String(params?.page ?? 1));
  qs.set('size', String(params?.size ?? 20));
  return request<RewardListResp>(`/?${qs.toString()}`, { skipCache: true });
}

/** 奖励汇总（待提现 / 累计已到账） */
export function fetchRewardStats(): Promise<RewardStats> {
  return request<RewardStats>('/stats', { skipCache: true });
}

/**
 * 立即提现 / 去确认。
 *
 * 后端内部走三分支决策：复用 package → 查单提示处理中 → 换新单号建单，
 * 保证同一笔奖励至多有一笔非终态转账单（防重复出资），前端无需自行判重。
 */
export function withdrawReward(rewardId: string): Promise<WithdrawResp> {
  return request<WithdrawResp>(`/${encodeURIComponent(rewardId)}/withdraw`, { method: 'POST' });
}

/** 分 → 元（保留两位） */
export function fenToYuan(fen: number): string {
  return ((fen || 0) / 100).toFixed(2);
}
