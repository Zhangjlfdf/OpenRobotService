// 二维码管理相关 API —— 对接 admin 模块 /api/admin/qrcodes*
//
// 注：此前本文件误用 `import { request } from './client'`（该导出不存在）且路径带了
// `/api/v1` 前缀（后端实际挂在 /api/admin 下，settings.API_V1_STR='/api'），
// 页面接口全不可达；现按仓库统一写法改用 createRequest(API_CONFIG.ADMIN.BASE_URL)。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, '二维码');

export interface QrcodeItem {
  id: number;
  scene_str: string;
  name: string;
  description?: string;
  ticket?: string;
  url?: string;
  qrcode_image_url?: string;
  type: 'temporary' | 'permanent';
  expire_seconds?: number;
  status: string;
  batch_id?: string;
  /** 所属项目ID（录入信息行：业务键；普通码行：project.id 关联；没有则为 null） */
  project_id?: string | null;
  /** 项目名：录入信息行用自己的；普通码行是后端按 project_id 联查下发的所属项目名 */
  project_name?: string | null;
  /** 项目编号（录入信息行；普通码行为 null） */
  project_code?: string | null;
  /** 项目地点（录入信息行） */
  project_location?: string | null;
  /** 客户名（录入信息行） */
  customer_name?: string | null;
  /** 车型（录入信息行） */
  vehicle_model?: string | null;
  redirect_url?: string;
  created_by?: string;
  published_by?: string;
  deprecated_by?: string;
  ticket_created_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface QrcodeListResult {
  total: number;
  items: QrcodeItem[];
}

export interface QrcodeStats {
  status: Record<string, number>;
  type: Record<string, number>;
  total: number;
  permanent_quota_remaining: number;
}

export type QrcodeStatus = 'init' | 'entering' | 'confirming' | 'published' | 'deprecated';
export type QrcodeType = 'temporary' | 'permanent';

export const QRCODE_STATUS_LABELS: Record<QrcodeStatus, { label: string; color: string }> = {
  init:       { label: '初始化', color: '#888d8f' },
  entering:   { label: '录入中', color: '#5aa9cd' },
  confirming: { label: '确认中', color: '#d4a843' },
  published:  { label: '已发布', color: '#2d9d5c' },
  deprecated: { label: '已弃用', color: '#c94a4a' },
};

export async function fetchQrcodes(params: {
  status?: string;
  qrcode_type?: string;
  keyword?: string;
  batch_id?: string;
  project_id?: string;
  skip?: number;
  limit?: number;
}): Promise<QrcodeListResult> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.append(k, String(v));
  });
  const query = qs.toString();
  return request(`/qrcodes${query ? `?${query}` : ''}`);
}

export async function fetchQrcode(id: number): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}`);
}

export async function fetchQrcodeStats(): Promise<QrcodeStats> {
  return request('/qrcodes/stats/summary');
}

export async function createQrcode(data: {
  scene_str: string;
  name?: string;
  description?: string;
  qrcode_type?: QrcodeType;
  redirect_url?: string;
  project_id?: string;
}): Promise<QrcodeItem> {
  return request('/qrcodes', { method: 'POST', body: JSON.stringify(data) });
}

export async function batchCreateQrcodes(data: {
  scene_list: string[];
  name_prefix?: string;
  qrcode_type?: QrcodeType;
  redirect_url?: string;
  /** 整批统一关联的项目ID（project.id），不传则不与项目关联 */
  project_id?: string;
}): Promise<{ batch_id: string; created: string[]; skipped: Array<{ scene: string; reason: string }>; created_count: number; skipped_count: number; project_id?: string | null }> {
  return request('/qrcodes/batch', { method: 'POST', body: JSON.stringify(data) });
}

export async function generateQrcodeTicket(id: number): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}/generate`, { method: 'POST' });
}

export async function batchGenerateTickets(data: {
  batch_id?: string;
  qid_list?: number[];
  only_init?: boolean;
}): Promise<{ total: number; success: Array<{ id: number; scene_str: string }>; failed: Array<{ id: number; scene_str: string; reason: string }> }> {
  return request('/qrcodes/batch-generate', { method: 'POST', body: JSON.stringify(data) });
}

export async function updateQrcode(id: number, data: {
  name?: string;
  description?: string;
  redirect_url?: string;
  /** 传空串清除项目关联；不传则不修改 */
  project_id?: string;
}): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}

/** 状态流转动作。录入信息行（project_code 非空）点「确认」= 确认即发布，
 *  后端直接落到 published（不经 confirming），见 InfoEntry.tsx。 */
export async function qrcodeTransition(id: number, action: 'confirm' | 'publish' | 'deprecate'): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}/${action}`, { method: 'POST' });
}

export async function deleteQrcode(id: number): Promise<{ ok: boolean }> {
  return request(`/qrcodes/${id}`, { method: 'DELETE' });
}

// ── 录入信息（项目信息登记）──
// 六个字段 = wechat_qrcodes 一行（和行 id 同行存），见 pages/admin/InfoEntry.tsx。
// 项目id 唯一不可改（留空可后补）；项目编号唯一可改；重复/改动由后端 400 拦下。

export interface ProjectInfoPayload {
  /** 项目id（唯一；留空可后补一次，存过不可改） */
  project_id?: string;
  /** 项目编号（唯一，可改） */
  project_code: string;
  /** 项目名 */
  project_name: string;
  project_location?: string;
  customer_name?: string;
  vehicle_model?: string;
}

/** 登记一条项目信息（落成 wechat_qrcodes 新行，init 状态） */
export async function createProjectInfo(data: ProjectInfoPayload): Promise<QrcodeItem> {
  return request('/qrcodes/project-info', { method: 'POST', body: JSON.stringify(data) });
}

/** 更新一条项目信息（项目id 只允许「空 → 有」补填；其余字段传了才改） */
export async function updateProjectInfo(id: number, data: Partial<ProjectInfoPayload>): Promise<QrcodeItem> {
  return request(`/qrcodes/${id}/project-info`, { method: 'PUT', body: JSON.stringify(data) });
}
