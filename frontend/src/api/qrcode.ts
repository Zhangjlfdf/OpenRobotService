// 二维码管理相关 API
import { request } from './client';

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
  skip?: number;
  limit?: number;
}): Promise<QrcodeListResult> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.append(k, String(v));
  });
  const query = qs.toString();
  return request.get(`/api/v1/admin/qrcodes${query ? `?${query}` : ''}`);
}

export async function fetchQrcode(id: number): Promise<QrcodeItem> {
  return request.get(`/api/v1/admin/qrcodes/${id}`);
}

export async function fetchQrcodeStats(): Promise<QrcodeStats> {
  return request.get('/api/v1/admin/qrcodes/stats/summary');
}

export async function createQrcode(data: {
  scene_str: string;
  name?: string;
  description?: string;
  qrcode_type?: QrcodeType;
  redirect_url?: string;
}): Promise<QrcodeItem> {
  return request.post('/api/v1/admin/qrcodes', data);
}

export async function batchCreateQrcodes(data: {
  scene_list: string[];
  name_prefix?: string;
  qrcode_type?: QrcodeType;
  redirect_url?: string;
}): Promise<{ batch_id: string; created: string[]; skipped: Array<{ scene: string; reason: string }>; created_count: number; skipped_count: number }> {
  return request.post('/api/v1/admin/qrcodes/batch', data);
}

export async function generateQrcodeTicket(id: number): Promise<QrcodeItem> {
  return request.post(`/api/v1/admin/qrcodes/${id}/generate`);
}

export async function batchGenerateTickets(data: {
  batch_id?: string;
  qid_list?: number[];
  only_init?: boolean;
}): Promise<{ total: number; success: Array<{ id: number; scene_str: string }>; failed: Array<{ id: number; scene_str: string; reason: string }> }> {
  return request.post('/api/v1/admin/qrcodes/batch-generate', data);
}

export async function updateQrcode(id: number, data: {
  name?: string;
  description?: string;
  redirect_url?: string;
}): Promise<QrcodeItem> {
  return request.put(`/api/v1/admin/qrcodes/${id}`, data);
}

export async function qrcodeTransition(id: number, action: 'confirm' | 'publish' | 'deprecate'): Promise<QrcodeItem> {
  return request.post(`/api/v1/admin/qrcodes/${id}/${action}`);
}

export async function deleteQrcode(id: number): Promise<{ ok: boolean }> {
  return request.delete(`/api/v1/admin/qrcodes/${id}`);
}
