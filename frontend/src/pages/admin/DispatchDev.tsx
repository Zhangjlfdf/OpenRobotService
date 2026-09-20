// 派单开发者模式：看问题簇、重建簇、一键补索引、转派指标、派单测试。
// 入口在「其他」，权限 frontend:admin:dispatch-dev:show（admin 直通仍可见）。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import ReactECharts from '@/shared/components/ReactECharts';
import UiAtlasPanel from '@/pages/admin/UiAtlasPanel';

export const PERM_DISPATCH_DEV = 'frontend:admin:dispatch-dev:show';

function pct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${(Number(v) * 100).toFixed(1)}%`;
}

function formatPytestTime(ts: number | string | null | undefined): string {
  if (ts == null) return '';
  const t = Number(ts);
  if (!Number.isFinite(t)) return '';
  const diff = Math.max(0, Math.floor((Date.now() - t * 1000) / 1000));
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

function TicketLink({ taskId, title }: { taskId: number; title?: string }) {
  const label = title ? `#${taskId} ${title}` : `#${taskId}`;
  return (
    <Link
      className="dispatch-dev__ticket-link"
      to={`/tasks/${taskId}`}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      title="在新标签打开工单详情"
    >
      {label || `（无标题）#${taskId}`}
    </Link>
  );
}

interface ClusterPerson { engineer_id: string; name: string; count: number }
interface ClusterTicket { ticket_id: string; title: string; engineer_id: string; engineer_name?: string }
interface Cluster {
  id: number;
  titles: string[];
  ticket_count: number;
  people: ClusterPerson[];
  tickets: ClusterTicket[];
}
interface ClusterPoint {
  ticket_id: string;
  title: string;
  engineer_id: string;
  engineer_name?: string;
  cluster_id: number;
  x: number;
  y: number;
}
interface ClusterSnap {
  ready: boolean;
  ticket_total: number;
  clustered: number;
  noise: number;
  clusters: Cluster[];
  points?: ClusterPoint[];
  params?: Record<string, number | string | undefined>;
  error?: string;
}
interface HistoryTicket {
  ticket_id: string;
  title: string;
  engineer_id: string;
  engineer_name: string;
  task_type: string;
  robot_type: string;
  fault_code: string;
  closed_at: string;
}
interface HistorySnap {
  mysql_total: number;
  qdrant_collection: string;
  qdrant_points: number;
  tickets: HistoryTicket[];
  params?: Record<string, number | string | undefined>;
}
interface Overview { clusters: ClusterSnap; history: HistorySnap; reassign?: ReassignSnap }
interface ReindexResult { total: number; indexed: number; skipped: number; collection: string }
interface ReassignMetrics {
  signal_total: number;
  signal_tickets: number;
  redispatch_total: number;
  redispatch_tickets?: number;
  redispatch_pending?: number;
  redispatch_inaccurate?: number;
  redispatch_skipped?: number;
  unlabeled_total: number;
  reassign_log_total: number;
  reassign_total: number;
  reassign_tickets: number;
  ai_assign_total: number;
  ai_assign_tickets: number;
  by_kind: Record<string, number>;
  by_source: Record<string, number>;
  classified: number;
  misassign_events: number;
  misassign_tickets: number;
  inaccurate_events?: number;
  inaccurate_tickets?: number;
  misassign_rate_of_signal: number | null;
  misassign_rate_of_reassign: number | null;
  misassign_rate_of_ai_assign: number | null;
  ticket_misassign_rate_of_ai: number | null;
  redispatch_inaccurate_rate_of_reviewed?: number | null;
  inaccurate_rate_of_ai_assign?: number | null;
  ticket_inaccurate_rate_of_ai?: number | null;
}
interface ReassignSample {
  id: number;
  task_id: number;
  title: string;
  kind: string;
  source: string;
  channel?: string;
  confidence: number;
  reason: string;
  created_at: string;
}
interface TicketListItem {
  task_id: number;
  title: string;
  kind?: string;
  channel?: string;
  created_at?: string;
  reason?: string;
  tag?: string;
}
interface WeeklyBucket {
  week: string;
  label: string;
  week_start: string;
  metrics: ReassignMetrics;
}
type TicketListKey = 'misassign' | 'redispatch_inaccurate' | 'inaccurate' | 'signal';
interface UnlabeledHop {
  id: number;
  created_at: string;
  from_id: string;
  from_name: string;
  to_id: string;
  to_name: string;
  reason: string;
  description: string;
  kind?: string;
  channel?: string;
  reviewable?: boolean;
}
interface UnlabeledItem {
  id: number;
  task_id: number;
  title: string;
  reason: string;
  description: string;
  created_at: string;
  from_id: string;
  from_name: string;
  to_id: string;
  to_name: string;
}
interface UnlabeledGroup {
  task_id: number;
  title: string;
  hops: UnlabeledHop[];
}
interface RedispatchItem {
  id: number;
  task_id: number;
  title: string;
  reason: string;
  description: string;
  created_at: string;
  operator_name: string;
  preferred_id: string;
  preferred_name: string;
}
interface ReassignSnap {
  metrics?: ReassignMetrics;
  unlabeled?: number;
  llm?: { called: number; failed: number; pending: number };
  persisted?: number;
  samples?: ReassignSample[];
  ticket_lists?: Partial<Record<TicketListKey, TicketListItem[]>>;
  weekly?: WeeklyBucket[];
  unlabeled_items?: UnlabeledItem[];
  unlabeled_groups?: UnlabeledGroup[];
  redispatch_items?: RedispatchItem[];
  note?: string;
  error?: string;
}

const KIND_LABEL: Record<string, string> = {
  misassign: '派错了',
  stage: '阶段转派',
  other: '其它',
};

function hopKindLabel(hop: UnlabeledHop): string {
  if (hop.kind && KIND_LABEL[hop.kind]) return KIND_LABEL[hop.kind];
  if (hop.channel === 'redispatch') return '重新派单';
  if (hop.channel === 'skipped') return '已跳过';
  return '未标类型';
}

function formatHopTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${m}月${day}日 ${hh}:${mm}`;
}

function unlabeledGroupsFromSnap(reassign: ReassignSnap | null): UnlabeledGroup[] {
  if (reassign?.unlabeled_groups?.length) return reassign.unlabeled_groups;
  const items = reassign?.unlabeled_items || [];
  const map = new Map<number, UnlabeledGroup>();
  const out: UnlabeledGroup[] = [];
  for (const item of items) {
    let g = map.get(item.task_id);
    if (!g) {
      g = { task_id: item.task_id, title: item.title, hops: [] };
      map.set(item.task_id, g);
      out.push(g);
    }
    g.hops.push({
      ...item,
      reviewable: true,
    });
  }
  return out;
}

const CLUSTER_COLORS = [
  '#227197',
  '#e37318',
  '#2ba471',
  '#7b61ff',
  '#d4537e',
  '#c9a227',
  '#0f9d91',
  '#c45c26',
];
const NOISE_COLOR = '#c9d4d9';

function clusterColor(id: number): string {
  if (id < 0) return NOISE_COLOR;
  return CLUSTER_COLORS[id % CLUSTER_COLORS.length];
}

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

// ── 派单测试面板 ──

interface TestScenario {
  label?: string;
  title: string;
  desc?: string;
  problem_description?: string;
  robot_type?: string;
  fault_code?: string;
  dispatch_hint?: string;
  preferred_assignee?: string;
  preferred_assignee_remark?: string;
  prev_assignee?: string;
  contact?: string;
  creator?: string;
  repeat?: number;
  expected_branch?: string;
  note?: string;
}
interface BranchChild {
  id: string;
  step?: string;
  title: string;
  desc?: string;
  tests?: string[];
  test_status?: 'passed' | 'failed' | 'untested';
  example?: TestScenario;
}
interface BranchNode {
  id: string;
  step: string;
  title: string;
  desc: string;
  children: BranchChild[];
}
interface BranchTreeData {
  branches: BranchNode[];
  total_passed: number;
  total_failed: number;
  covered_by_run?: number;
  pytest_ran_at?: number | string | null;
  pytest_cache_hit?: boolean;
  cache_age_seconds?: number;
  ttl_seconds?: number;
}
interface HitPathNode {
  step: string;
  branch: string;
  id: string;
}
interface TestRunResult {
  engineer_id: string;
  engineer_name: string;
  confidence_score: number;
  decision_type: string;
  reasoning: string;
  preferred_id?: string | null;
  matched_pref?: boolean | null;
  profile?: Record<string, unknown> | null;
  candidates?: Array<Record<string, unknown>> | null;
}
interface TestRunData {
  result: TestRunResult;
  hit_path: HitPathNode[];
  candidate_count: number;
}

const STATUS_COLOR: Record<string, string> = {
  passed: '#2ba471',
  failed: '#e34d4d',
  untested: '#c9d4d9',
};
const STATUS_LABEL: Record<string, string> = {
  passed: '已通过',
  failed: '失败',
  untested: '未测',
};

const PRESET_TICKETS: TestScenario[] = [
  { label: '指定人', title: '指定处理人：张三', desc: '车辆在站点停下不动' },
  { label: '模糊(severe)', title: '坏了', desc: '不知道啥情况', dispatch_hint: 'severe' },
  { label: '故障码', title: '车辆报错', desc: '车辆无法启动', fault_code: 'E1001' },
  { label: '车型+故障', title: 'S20 车辆故障', desc: '车停了不动', robot_type: 'S20', fault_code: 'E2002' },
];

// 一键跑全部场景：覆盖所有派单分支
const ALL_SCENARIOS: TestScenario[] = [
  // Step 0 — 指定人
  { label: 'Step0 命中指定人', title: '指定处理人：张三', desc: '车辆在站点停下不动' },
  { label: 'Step0 未命中指定人', title: '指定处理人：不存在的ID', desc: '车辆在站点停下不动' },
  // 倾向人
  { label: '倾向人连续确认', title: '车辆故障', desc: '车不动了', preferred_assignee: '1' },
  { label: '倾向人画像不完整', title: '车辆故障', desc: '车不动了', preferred_assignee: '999' },
  // Step 1 — 部门收紧
  { label: 'Step1 部门硬过滤', title: 'S20 车辆故障', desc: '车停了不动', robot_type: 'S20', fault_code: 'E2002' },
  { label: 'Step1 无部门信号', title: '车辆报错', desc: '车辆无法启动', fault_code: 'E1001' },
  // Step 2 — severe 跳过
  { label: 'Step2 severe 跳Step7', title: '坏了', desc: '不知道啥情况', dispatch_hint: 'severe' },
  { label: 'Step2 正常流程', title: '车辆故障', desc: '车不动了' },
  // Step 3 — 三路召回
  { label: 'Step3 画像召回', title: 'S20 车辆故障', desc: '车停了不动', robot_type: 'S20', fault_code: 'E2002' },
  { label: 'Step3 相似工单', title: '车辆无法启动', desc: '故障码 E1001，启动不了' },
  { label: 'Step3 问题簇', title: '车辆在站点停下不动', desc: '到站后不动，指示灯闪红灯' },
  // Step 4 — 精排
  { label: 'Step4 倾向人保底', title: '车辆故障', desc: '车不动了', preferred_assignee: '1' },
  // Step 6 — LLM 决策
  { label: 'Step6 LLM成功', title: 'S20 车辆故障', desc: '车停了不动，故障码E2002', robot_type: 'S20', fault_code: 'E2002' },
  // Step 7 — 兜底
  { label: 'Step7 派对接人', title: '车辆故障', desc: '车不动了', contact: '对接人' },
  { label: 'Step7 无法指派', title: '未知问题', desc: '不清楚什么情况' },
  // 重派场景
  { label: '重派场景', title: '车辆故障', desc: '车不动了', prev_assignee: '1', preferred_assignee: '2' },
];

function DispatchTestPanel() {
  const [branches, setBranches] = useState<BranchTreeData | null>(null);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [branchesRefreshing, setBranchesRefreshing] = useState(false);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<TestRunData | null>(null);
  const [runError, setRunError] = useState('');

  const [title, setTitle] = useState('车辆在站点停下不动');
  const [desc, setDesc] = useState('车辆到站后不动了，指示灯闪红灯');
  const [robotType, setRobotType] = useState('');
  const [faultCode, setFaultCode] = useState('');
  const [dispatchHint, setDispatchHint] = useState('');
  const [preferredAssignee, setPreferredAssignee] = useState('');
  const [remark, setRemark] = useState('');
  const [prevAssignee, setPrevAssignee] = useState('');
  const [contact, setContact] = useState('');
  const [creator, setCreator] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchProgress, setBatchProgress] = useState<{ current: number; total: number; label: string }>({ current: 0, total: 0, label: '' });

  const loadBranches = useCallback(async () => {
    setBranchesLoading(true);
    try {
      const data = unwrap<BranchTreeData>(await request('/dispatch-dev/test-branches', { skipCache: true }));
      setBranches(data);
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '加载分支失败', theme: 'error' });
    } finally {
      setBranchesLoading(false);
    }
  }, []);

  const refreshTests = useCallback(async () => {
    // 手动触发 pytest 全量跑一次；耗时 30~120 秒，前端按钮要禁用 + loading 文案。
    setBranchesRefreshing(true);
    try {
      const data = unwrap<BranchTreeData>(await request('/dispatch-dev/test-branches/refresh', {
        method: 'POST',
        timeout: 180000,
      }));
      setBranches(data);
      if (data?.pytest_cache_hit) {
        Toast({ message: `复用上次结果（${data.cache_age_seconds ?? '?'}s 前）`, theme: 'success' });
      } else {
        Toast({ message: `测试结果已刷新：通过 ${data?.total_passed ?? 0} / 失败 ${data?.total_failed ?? 0}`, theme: 'success' });
      }
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '刷新测试结果失败', theme: 'error' });
    } finally {
      setBranchesRefreshing(false);
    }
  }, []);

  useEffect(() => { loadBranches(); }, [loadBranches]);

  const applyPreset = (preset: TestScenario) => {
    setTitle(preset.title || '');
    setDesc(preset.problem_description || preset.desc || '');
    setRobotType(preset.robot_type || '');
    setFaultCode(preset.fault_code || '');
    setDispatchHint(preset.dispatch_hint || '');
    setPreferredAssignee(preset.preferred_assignee || '');
    setRemark(preset.preferred_assignee_remark || '');
    setPrevAssignee(preset.prev_assignee || '');
    setContact(preset.contact || '');
    setCreator(preset.creator || '');
  };

  const payloadFromScenario = (scenario: TestScenario): Record<string, string> => {
    const payload: Record<string, string> = {
      title: scenario.title,
      problem_description: scenario.problem_description || scenario.desc || '',
    };
    if (scenario.robot_type) payload.robot_type = scenario.robot_type;
    if (scenario.fault_code) payload.fault_code = scenario.fault_code;
    if (scenario.dispatch_hint) payload.dispatch_hint = scenario.dispatch_hint;
    if (scenario.preferred_assignee) payload.preferred_assignee = scenario.preferred_assignee;
    if (scenario.preferred_assignee_remark) payload.preferred_assignee_remark = scenario.preferred_assignee_remark;
    if (scenario.prev_assignee) payload.prev_assignee = scenario.prev_assignee;
    if (scenario.contact) payload.contact = scenario.contact;
    if (scenario.creator) payload.creator = scenario.creator;
    return payload;
  };

  const currentPayload = (): Record<string, string> => {
    const payload: Record<string, string> = { title, problem_description: desc };
    if (robotType) payload.robot_type = robotType;
    if (faultCode) payload.fault_code = faultCode;
    if (dispatchHint) payload.dispatch_hint = dispatchHint;
    if (preferredAssignee) payload.preferred_assignee = preferredAssignee;
    if (remark) payload.preferred_assignee_remark = remark;
    if (prevAssignee) payload.prev_assignee = prevAssignee;
    if (contact) payload.contact = contact;
    if (creator) payload.creator = creator;
    return payload;
  };

  const runPayload = async (payload: Record<string, string>, repeat = 1) => {
    let last: TestRunData | null = null;
    for (let i = 0; i < Math.max(1, repeat); i += 1) {
      last = unwrap<TestRunData>(await request('/dispatch-dev/test-run', {
        method: 'POST',
        timeout: 120000,
        body: JSON.stringify(payload),
      }));
    }
    return last;
  };

  const runTest = async () => {
    setRunning(true);
    setRunError('');
    setRunResult(null);
    try {
      const data = await runPayload(currentPayload());
      if (data) setRunResult(data);
      // 跑完自动刷新分支覆盖树
      loadBranches();
    } catch (e) {
      setRunError(e instanceof Error ? e.message : '模拟派单失败');
    } finally {
      setRunning(false);
    }
  };

  const runScenario = async (scenario: TestScenario) => {
    applyPreset(scenario);
    setRunning(true);
    setRunError('');
    setRunResult(null);
    try {
      const data = await runPayload(payloadFromScenario(scenario), scenario.repeat || 1);
      if (data) setRunResult(data);
      await loadBranches();
    } catch (e) {
      setRunError(e instanceof Error ? e.message : '模拟派单失败');
    } finally {
      setRunning(false);
    }
  };

  const batchRunAll = async () => {
    const realProfileScenarios = (branches?.branches || []).flatMap((node) =>
      (node.children || [])
        .filter((child) => child.example)
        .map((child) => ({ ...(child.example as TestScenario), label: `${node.step} ${child.title}` })),
    );
    const scenarios = realProfileScenarios.length ? realProfileScenarios : ALL_SCENARIOS;
    setBatchRunning(true);
    setRunError('');
    setRunResult(null);
    setBatchProgress({ current: 0, total: scenarios.length, label: '' });
    try {
      for (let i = 0; i < scenarios.length; i++) {
        const s = scenarios[i];
        setBatchProgress({ current: i + 1, total: scenarios.length, label: s.label || s.title });
        try {
          await runPayload(payloadFromScenario(s), s.repeat || 1);
        } catch {
          // 单个场景失败不中断，继续跑下一个
        }
      }
      // 全部跑完后刷新分支覆盖
      await loadBranches();
      Toast({ message: '全部场景跑完，分支覆盖已更新', theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '批量跑单失败', theme: 'error' });
    } finally {
      setBatchRunning(false);
      setBatchProgress({ current: 0, total: 0, label: '' });
    }
  };

  const branchNodes = branches?.branches || [];
  const passedCount = branches?.total_passed ?? 0;
  const failedCount = branches?.total_failed ?? 0;
  const runCovered = branches?.covered_by_run ?? 0;
  const totalBranches = branchNodes.reduce((n, b) => n + (b.children?.length || 0), 0);
  const coveredBranches = branchNodes.reduce((n, b) => n + (b.children || []).filter(c => c.test_status === 'passed').length, 0);
  const coveragePct = totalBranches > 0 ? Math.round((coveredBranches / totalBranches) * 100) : 0;

  return (
    <div className="dispatch-test">
      {/* 分支树 */}
      <section className="dispatch-dev__card">
        <div className="dispatch-dev__head">
          <span className="dispatch-dev__title">派单分支覆盖</span>
          <div className="dispatch-dev__head-actions">
            <button
              type="button"
              className="dispatch-dev__btn"
              disabled={batchRunning || running}
              onClick={batchRunAll}
            >
              {batchRunning ? `跑场景中… (${batchProgress.current}/${batchProgress.total})` : '一键跑全部场景'}
            </button>
            <button
              type="button"
              className="dispatch-dev__btn dispatch-dev__btn--ghost"
              disabled={branchesRefreshing}
              onClick={refreshTests}
              title="跑一遍 pytest，更新每个分支的通过失败状态（耗时 30~120s）"
            >
              {branchesRefreshing ? '跑测试中…' : '刷新测试结果'}
            </button>
            <button
              type="button"
              className="dispatch-dev__btn dispatch-dev__btn--ghost"
              disabled={branchesLoading}
              onClick={loadBranches}
              title="秒级刷新：重新拉分支树（不跑 pytest）"
            >
              {branchesLoading ? '刷新中…' : '刷新分支树'}
            </button>
          </div>
        </div>
        <p className="dispatch-dev__hint">
          派单流程共 8 个 Step、{totalBranches} 条分支。绿色=已通过、灰色=未测、红色=失败。
          点「一键跑全部场景」会优先遍历每条分支上的真实画像示例；也可以在单个分支点「载入例子」后手动改字段。
          {' '}「刷新测试结果」会跑一遍 pytest（耗时 30~120 秒），用结果标记每条分支。
          {branches?.pytest_ran_at
            ? `（当前测试结果更新于 ${formatPytestTime(branches.pytest_ran_at)}）`
            : '（尚未跑过 pytest，所有分支显示为未测）'}
        </p>
        {batchRunning ? (
          <div className="dispatch-test__batch-progress">
            <Loading text={`跑场景 ${batchProgress.current}/${batchProgress.total}：${batchProgress.label}`} />
          </div>
        ) : null}
        <div className="dispatch-test__summary">
          <span>pytest 通过 {passedCount}</span>
          <span>失败 {failedCount}</span>
          <span>跑单覆盖 {runCovered} 条</span>
          <span>分支覆盖 {coveredBranches}/{totalBranches} ({coveragePct}%)</span>
        </div>
        {branchesLoading && !branches ? (
          <div className="dispatch-dev__empty"><Loading text="加载分支…" /></div>
        ) : (
          <div className="dispatch-test__tree">
            {branchNodes.map((node) => (
              <div key={node.id} className="dispatch-test__branch">
                <div className="dispatch-test__branch-head">
                  <span className="dispatch-test__step">{node.step}</span>
                  <span className="dispatch-test__branch-title">{node.title}</span>
                </div>
                <p className="dispatch-test__branch-desc">{node.desc}</p>
                <div className="dispatch-test__children">
                  {(node.children || []).map((child) => {
                    const status = child.test_status || 'untested';
                    return (
                      <div key={child.id} className={`dispatch-test__child dispatch-test__child--${status}`}>
                        <i className="dispatch-test__dot" style={{ background: STATUS_COLOR[status] }} />
                        <span className="dispatch-test__child-title">{child.title}</span>
                        <em className="dispatch-test__child-status">{STATUS_LABEL[status]}</em>
                        {child.example ? (
                          <>
                            <button
                              type="button"
                              className="dispatch-test__mini-btn"
                              onClick={() => applyPreset(child.example as TestScenario)}
                              title={child.example.note || '载入这个分支的真实画像示例'}
                            >
                              载入例子
                            </button>
                            <button
                              type="button"
                              className="dispatch-test__mini-btn"
                              disabled={running || batchRunning}
                              onClick={() => runScenario(child.example as TestScenario)}
                              title={child.example.note || '直接跑这个分支的真实画像示例'}
                            >
                              跑此例
                            </button>
                          </>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 模拟提单 */}
      <section className="dispatch-dev__card">
        <div className="dispatch-dev__head">
          <span className="dispatch-dev__title">模拟提单</span>
        </div>
        <p className="dispatch-dev__hint">
          填写工单信息后点「跑派单」，会走一次完整派单流程，并显示命中了哪条分支。
        </p>
        <div className="dispatch-test__presets">
          {PRESET_TICKETS.map((p) => (
            <button
              key={p.label}
              type="button"
              className="dispatch-dev__btn dispatch-dev__btn--ghost"
              onClick={() => applyPreset(p)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="dispatch-test__form">
          <label>
            <span>标题</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="工单标题" />
          </label>
          <label>
            <span>问题描述</span>
            <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} placeholder="问题描述" />
          </label>
          <label>
            <span>车型</span>
            <input value={robotType} onChange={(e) => setRobotType(e.target.value)} placeholder="如 S20" />
          </label>
          <label>
            <span>故障码</span>
            <input value={faultCode} onChange={(e) => setFaultCode(e.target.value)} placeholder="如 E1001" />
          </label>
          <label>
            <span>信息充分性</span>
            <select value={dispatchHint} onChange={(e) => setDispatchHint(e.target.value)}>
              <option value="">信息充分（默认）</option>
              <option value="lacking">lacking（信息不足）</option>
              <option value="severe">severe（严重不足）</option>
            </select>
          </label>
          <button
            type="button"
            className="dispatch-dev__btn dispatch-dev__btn--ghost dispatch-test__toggle-advanced"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? '收起高级选项' : '展开高级选项'}
          </button>
          {showAdvanced ? (
            <>
              <label>
                <span>倾向处理人 ID</span>
                <input value={preferredAssignee} onChange={(e) => setPreferredAssignee(e.target.value)} placeholder="users.id" />
              </label>
              <label>
                <span>重派备注</span>
                <input value={remark} onChange={(e) => setRemark(e.target.value)} placeholder="希望派给熟悉…的人" />
              </label>
              <label>
                <span>原处理人 ID</span>
                <input value={prevAssignee} onChange={(e) => setPrevAssignee(e.target.value)} placeholder="users.id" />
              </label>
              <label>
                <span>联系人</span>
                <input value={contact} onChange={(e) => setContact(e.target.value)} placeholder="对接人" />
              </label>
              <label>
                <span>提单人 ID</span>
                <input value={creator} onChange={(e) => setCreator(e.target.value)} placeholder="users.id" />
              </label>
            </>
          ) : null}
        </div>
        <button
          type="button"
          className="dispatch-dev__btn dispatch-test__run-btn"
          disabled={running}
          onClick={runTest}
        >
          {running ? '派单中…' : '跑派单'}
        </button>
      </section>

      {/* 派单结果 */}
      {runError ? (
        <section className="dispatch-dev__card">
          <div className="dispatch-dev__head"><span className="dispatch-dev__title">派单失败</span></div>
          <p className="dispatch-dev__hint dispatch-dev__hint--error">{runError}</p>
        </section>
      ) : runResult ? (
        <section className="dispatch-dev__card">
          <div className="dispatch-dev__head">
            <span className="dispatch-dev__title">派单结果</span>
            <span className="dispatch-dev__hint" style={{ margin: 0 }}>
              候选 {runResult.candidate_count} 人
            </span>
          </div>
          {/* 命中路径 */}
          <div className="dispatch-test__hit-path">
            <span className="dispatch-test__sub">命中分支</span>
            <div className="dispatch-test__path-chain">
              {runResult.hit_path.map((node, idx) => (
                <span key={idx} className="dispatch-test__path-node">
                  <em>{node.step}</em>
                  <span>{node.branch}</span>
                  {idx < runResult.hit_path.length - 1 ? <i className="dispatch-test__arrow">→</i> : null}
                </span>
              ))}
            </div>
          </div>
          {/* 结果详情 */}
          <div className="dispatch-test__result">
            <div className="dispatch-test__result-row">
              <span>指派工程师</span>
              <strong>{runResult.result.engineer_name || '（未指派）'}</strong>
              <em>{runResult.result.engineer_id || '—'}</em>
            </div>
            <div className="dispatch-test__result-row">
              <span>决策类型</span>
              <strong>{runResult.result.decision_type || '—'}</strong>
            </div>
            <div className="dispatch-test__result-row">
              <span>置信度</span>
              <strong>{(runResult.result.confidence_score * 100).toFixed(1)}%</strong>
            </div>
            {runResult.result.reasoning ? (
              <div className="dispatch-test__result-row">
                <span>理由</span>
                <span className="dispatch-test__reasoning">{runResult.result.reasoning}</span>
              </div>
            ) : null}
            {runResult.result.matched_pref != null ? (
              <div className="dispatch-test__result-row">
                <span>倾向人命中</span>
                <strong>{runResult.result.matched_pref ? '是' : '否'}</strong>
              </div>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}

export default function DispatchDev() {
  const navigate = useNavigate();
  const allowed = useAuthStore((s) => s.hasPermission(PERM_DISPATCH_DEV));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [clusters, setClusters] = useState<ClusterSnap | null>(null);
  const [history, setHistory] = useState<HistorySnap | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [indexHint, setIndexHint] = useState('');
  const [reassign, setReassign] = useState<ReassignSnap | null>(null);
  const [reviewingId, setReviewingId] = useState<number | null>(null);
  const [mergeInput, setMergeInput] = useState('');
  const [assignInput, setAssignInput] = useState('');
  const [minSizeInput, setMinSizeInput] = useState('');
  const [savingParams, setSavingParams] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [listKey, setListKey] = useState<TicketListKey | null>(null);
  const [mainTab, setMainTab] = useState<'metrics' | 'atlas' | 'test'>('metrics');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = unwrap<Overview>(await request('/dispatch-dev/overview', { skipCache: true }));
      setClusters(data.clusters);
      setHistory(data.history);
      setReassign(data.reassign || null);
      const p = data.clusters?.params || data.history?.params || {};
      if (p.cluster_merge != null) setMergeInput(String(p.cluster_merge));
      if (p.cluster_assign != null) setAssignInput(String(p.cluster_assign));
      if (p.cluster_min_size != null) setMinSizeInput(String(p.cluster_min_size));
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!allowed) {
      navigate('/admin/entries', { replace: true });
      return;
    }
    load();
  }, [allowed, load, navigate]);

  const rebuild = async () => {
    setRebuilding(true);
    try {
      const data = unwrap<ClusterSnap>(await request('/dispatch-dev/clusters/rebuild', {
        method: 'POST',
        timeout: 180000,
      }));
      setClusters(data);
      const p = data.params || {};
      if (p.cluster_merge != null) setMergeInput(String(p.cluster_merge));
      if (p.cluster_assign != null) setAssignInput(String(p.cluster_assign));
      if (p.cluster_min_size != null) setMinSizeInput(String(p.cluster_min_size));
      Toast({ message: data.ready ? `已重建 ${data.clusters.length} 个簇` : '已重建，当前没有簇', theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '重建失败', theme: 'error' });
    } finally {
      setRebuilding(false);
    }
  };

  const reindex = async () => {
    setReindexing(true);
    setIndexHint('');
    try {
      const data = unwrap<{ index: ReindexResult; history: HistorySnap }>(
        await request('/dispatch-dev/history/reindex', { method: 'POST', timeout: 600000 }),
      );
      setHistory(data.history);
      const ix = data.index || { total: 0, indexed: 0, skipped: 0, collection: '' };
      setIndexHint(`写入 ${ix.indexed} / ${ix.total}，失败 ${ix.skipped}，集合 ${ix.collection || '无'}`);
      Toast({ message: '补索引完成', theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '补索引失败', theme: 'error' });
    } finally {
      setReindexing(false);
    }
  };

  const reviewItem = async (logId: number, kind: string) => {
    setReviewingId(logId);
    try {
      const data = unwrap<ReassignSnap>(
        await request('/dispatch-dev/reassign-review', {
          method: 'POST',
          body: JSON.stringify({ log_id: logId, kind }),
        }),
      );
      setReassign(data);
      const toast = kind === 'skipped'
        ? '已跳过'
        : kind === 'inaccurate'
          ? '已计入不准确'
          : `已标成${KIND_LABEL[kind] || kind}`;
      Toast({ message: toast, theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '审核失败', theme: 'error' });
    } finally {
      setReviewingId(null);
    }
  };

  const saveClusterParams = async () => {
    setSavingParams(true);
    try {
      const data = unwrap<ClusterSnap>(
        await request('/dispatch-dev/clusters/params', {
          method: 'POST',
          timeout: 180000,
          body: JSON.stringify({
            ...(Number.isFinite(Number(mergeInput)) && Number(mergeInput) >= 0.1
              ? { cluster_merge: Number(mergeInput) } : {}),
            ...(Number.isFinite(Number(assignInput)) && Number(assignInput) >= 0.1
              ? { cluster_assign: Number(assignInput) } : {}),
            ...(Number(minSizeInput) >= 2 ? { cluster_min_size: Number(minSizeInput) } : {}),
          }),
        }),
      );
      setClusters(data);
      const p = data.params || {};
      if (p.cluster_merge != null) setMergeInput(String(p.cluster_merge));
      if (p.cluster_assign != null) setAssignInput(String(p.cluster_assign));
      if (p.cluster_min_size != null) setMinSizeInput(String(p.cluster_min_size));
      Toast({ message: data.ready ? `已按新门槛重建 ${data.clusters.length} 个簇` : '已保存并重建', theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '保存失败', theme: 'error' });
    } finally {
      setSavingParams(false);
    }
  };

  const scatterOption = useMemo(() => {
    const pts = clusters?.points || [];
    const clusterList = clusters?.clusters || [];
    const series = clusterList.map((c) => ({
      name: `簇${c.id + 1}`,
      type: 'scatter' as const,
      symbolSize: 14,
      itemStyle: { color: clusterColor(c.id) },
      data: pts.filter((p) => p.cluster_id === c.id).map((p) => ({
        value: [p.x, p.y],
        cluster_id: p.cluster_id,
        title: p.title,
        person: p.engineer_name || p.engineer_id,
      })),
    }));
    const noise = pts.filter((p) => p.cluster_id < 0);
    if (noise.length) {
      series.push({
        name: '未进簇',
        type: 'scatter',
        symbolSize: 10,
        itemStyle: { color: NOISE_COLOR },
        data: noise.map((p) => ({
          value: [p.x, p.y],
          cluster_id: -1,
          title: p.title,
          person: p.engineer_name || p.engineer_id,
        })),
      });
    }
    return {
      color: CLUSTER_COLORS,
      tooltip: {
        trigger: 'item',
        formatter: (item: { data?: { title?: string; person?: string }; seriesName?: string }) => {
          const d = item?.data || {};
          return `${item?.seriesName || ''}<br/>${d.title || '（无标题）'}<br/>${d.person || ''}`;
        },
      },
      legend: {
        type: 'scroll',
        top: 0,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: '#888d8f', fontSize: 11 },
      },
      grid: { left: 12, right: 12, top: 32, bottom: 8, containLabel: true },
      xAxis: { type: 'value', min: -1.15, max: 1.15, splitLine: { lineStyle: { color: '#f1f4f4' } }, axisLabel: { show: false } },
      yAxis: { type: 'value', min: -1.15, max: 1.15, splitLine: { lineStyle: { color: '#f1f4f4' } }, axisLabel: { show: false } },
      series,
    };
  }, [clusters]);

  const weeklyOption = useMemo(() => {
    const weeks = reassign?.weekly || [];
    const labels = weeks.map((w) => w.label);
    const aiCounts = weeks.map((w) => Number(w.metrics?.ai_assign_total ?? 0));
    const countLabels = aiCounts.map((n) => `${n}次`);
    const seriesOf = (getter: (m: ReassignMetrics) => number | null | undefined, name: string, color: string) => ({
      name,
      type: 'line' as const,
      xAxisIndex: 0,
      smooth: true,
      symbol: 'circle',
      symbolSize: 7,
      lineStyle: { width: 2, color },
      itemStyle: { color },
      data: weeks.map((w) => {
        const v = getter(w.metrics);
        return v == null ? null : Number((Number(v) * 100).toFixed(2));
      }),
    });
    return {
      color: ['#227197', '#e37318', '#2ba471'],
      tooltip: {
        trigger: 'axis',
        formatter: (
          params: Array<{ dataIndex?: number; marker?: string; seriesName?: string; value?: number | null }>,
        ) => {
          const idx = params?.[0]?.dataIndex ?? 0;
          const head = `${weeks[idx]?.label || ''}<br/>AI 派单 ${aiCounts[idx] ?? 0} 次`;
          const rows = (params || [])
            .filter((p) => p.seriesName)
            .map((p) => (
              `${p.marker || ''}${p.seriesName || ''} ${p.value == null ? '—' : `${p.value}%`}`
            )).join('<br/>');
          return `${head}<br/>${rows}`;
        },
      },
      legend: {
        top: 0,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: '#888d8f', fontSize: 11 },
        data: ['错派率', '重派不准确率', '不准确率'],
      },
      grid: { left: 36, right: 12, top: 36, bottom: 58, containLabel: false },
      xAxis: [
        {
          type: 'category',
          data: labels,
          axisLabel: { color: '#888d8f', fontSize: 10, rotate: labels.length > 6 ? 30 : 0 },
          axisLine: { lineStyle: { color: '#e8eaea' } },
        },
        {
          type: 'category',
          data: countLabels,
          position: 'bottom',
          offset: labels.length > 6 ? 30 : 22,
          axisTick: { show: false },
          axisLine: { show: false },
          axisLabel: { color: '#227197', fontSize: 11, fontWeight: 600, interval: 0 },
        },
      ],
      yAxis: {
        type: 'value',
        min: 0,
        max: 100,
        axisLabel: { color: '#888d8f', fontSize: 10, formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#f1f4f4' } },
      },
      series: [
        seriesOf((m) => m.misassign_rate_of_signal ?? m.misassign_rate_of_reassign, '错派率', '#227197'),
        seriesOf((m) => m.redispatch_inaccurate_rate_of_reviewed, '重派不准确率', '#e37318'),
        seriesOf((m) => m.inaccurate_rate_of_ai_assign, '不准确率', '#2ba471'),
        {
          type: 'bar' as const,
          xAxisIndex: 1,
          data: aiCounts.map(() => 0),
          barWidth: 1,
          silent: true,
          tooltip: { show: false },
          legendHoverLink: false,
          itemStyle: { opacity: 0 },
          emphasis: { disabled: true },
        },
      ],
    };
  }, [reassign?.weekly]);

  if (!allowed) return null;

  const params = clusters?.params || history?.params || {};
  const unlabeledGroups = unlabeledGroupsFromSnap(reassign);
  const unlabeledRemain = unlabeledGroups.reduce(
    (n, g) => n + g.hops.filter((h) => h.reviewable !== false).length,
    0,
  );
  const hasScatter = (clusters?.points || []).length > 0;

  return (
    <div className="dispatch-dev">
      <div className="dispatch-dev__tabs">
        <button
          type="button"
          className={`dispatch-dev__tab${mainTab === 'metrics' ? ' is-active' : ''}`}
          onClick={() => setMainTab('metrics')}
        >
          派单调试
        </button>
        <button
          type="button"
          className={`dispatch-dev__tab${mainTab === 'test' ? ' is-active' : ''}`}
          onClick={() => setMainTab('test')}
        >
          派单测试
        </button>
        <button
          type="button"
          className={`dispatch-dev__tab${mainTab === 'atlas' ? ' is-active' : ''}`}
          onClick={() => setMainTab('atlas')}
        >
          界面图鉴
        </button>
      </div>
      {mainTab === 'atlas' ? (
        <UiAtlasPanel />
      ) : mainTab === 'test' ? (
        <DispatchTestPanel />
      ) : loading ? (
        <div className="dispatch-dev__empty"><Loading text="加载中..." /></div>
      ) : error ? (
        <div className="dispatch-dev__empty">{error}</div>
      ) : (
        <>
          <section className="dispatch-dev__card">
            <div className="dispatch-dev__head">
              <span className="dispatch-dev__title">转派指标</span>
            </div>
            <p className="dispatch-dev__hint">
              转派弹窗三个类型单独计错派率。重新派单（提单人 / 处理人 / 管理员让 AI 再派）测试期要人工审核：算不准确计入不准确率并进入派单学习（原处理人 ×0.7），测试操作点「测试不算」。
              点下面的比率可看对应工单清单；工单号可新标签打开详情。
            </p>
            {reassign?.error ? (
              <p className="dispatch-dev__hint">{reassign.error}</p>
            ) : (
              <>
                <div className="dispatch-dev__stats">
                  <button
                    type="button"
                    className={`dispatch-dev__stat-btn${listKey === 'signal' ? ' is-active' : ''}`}
                    onClick={() => setListKey((k) => (k === 'signal' ? null : 'signal'))}
                  >
                    有类型转派 {reassign?.metrics?.signal_total ?? reassign?.metrics?.reassign_total ?? 0} 次 / {reassign?.metrics?.signal_tickets ?? 0} 张
                  </button>
                  <span>AI 派单 {reassign?.metrics?.ai_assign_total ?? 0} 次</span>
                  <button
                    type="button"
                    className={`dispatch-dev__stat-btn${listKey === 'misassign' ? ' is-active' : ''}`}
                    onClick={() => setListKey((k) => (k === 'misassign' ? null : 'misassign'))}
                  >
                    弹窗派错了 {reassign?.metrics?.misassign_events ?? 0} 次 / {reassign?.metrics?.misassign_tickets ?? 0} 张
                  </button>
                  <span>未标类型 {reassign?.metrics?.unlabeled_total ?? reassign?.unlabeled ?? 0}</span>
                </div>
                <div className="dispatch-dev__stats">
                  <span>重新派单 {reassign?.metrics?.redispatch_total ?? 0} 次 / {reassign?.metrics?.redispatch_tickets ?? 0} 张</span>
                  <span>待审 {reassign?.metrics?.redispatch_pending ?? 0}</span>
                  <button
                    type="button"
                    className={`dispatch-dev__stat-btn${listKey === 'redispatch_inaccurate' ? ' is-active' : ''}`}
                    onClick={() => setListKey((k) => (k === 'redispatch_inaccurate' ? null : 'redispatch_inaccurate'))}
                  >
                    不准确 {reassign?.metrics?.redispatch_inaccurate ?? 0}
                  </button>
                  <span>测试不算 {reassign?.metrics?.redispatch_skipped ?? 0}</span>
                </div>
                <div className="dispatch-dev__rates">
                  <button
                    type="button"
                    className={`dispatch-dev__rate-btn${listKey === 'misassign' ? ' is-active' : ''}`}
                    onClick={() => setListKey((k) => (k === 'misassign' ? null : 'misassign'))}
                  >
                    <strong>{pct(reassign?.metrics?.misassign_rate_of_signal ?? reassign?.metrics?.misassign_rate_of_reassign)}</strong>
                    <span>错派率（/有类型转派）</span>
                    <em>点开看工单</em>
                  </button>
                  <button
                    type="button"
                    className={`dispatch-dev__rate-btn${listKey === 'redispatch_inaccurate' ? ' is-active' : ''}`}
                    onClick={() => setListKey((k) => (k === 'redispatch_inaccurate' ? null : 'redispatch_inaccurate'))}
                  >
                    <strong>{pct(reassign?.metrics?.redispatch_inaccurate_rate_of_reviewed)}</strong>
                    <span>重派不准确率（/已审核）</span>
                    <em>点开看工单</em>
                  </button>
                  <button
                    type="button"
                    className={`dispatch-dev__rate-btn${listKey === 'inaccurate' ? ' is-active' : ''}`}
                    onClick={() => setListKey((k) => (k === 'inaccurate' ? null : 'inaccurate'))}
                  >
                    <strong>{pct(reassign?.metrics?.inaccurate_rate_of_ai_assign)}</strong>
                    <span>不准确率（派错了+重派不准确 / AI 派单）</span>
                    <em>点开看工单</em>
                  </button>
                </div>
                <div className="dispatch-dev__stats">
                  {(['misassign', 'stage', 'other'] as const).map((k) => (
                    <span key={k}>{KIND_LABEL[k]} {reassign?.metrics?.by_kind?.[k] ?? 0}</span>
                  ))}
                </div>
                {listKey ? (
                  <div className="dispatch-dev__drill">
                    <div className="dispatch-dev__drill-head">
                      <span>
                        {{
                          misassign: '错派工单',
                          redispatch_inaccurate: '重派不准确工单',
                          inaccurate: '不准确工单（派错了 + 重派不准确）',
                          signal: '有类型转派工单',
                        }[listKey]}
                        {' · '}
                        {reassign?.ticket_lists?.[listKey]?.length ?? 0} 张
                      </span>
                      <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => setListKey(null)}>收起</button>
                    </div>
                    {(reassign?.ticket_lists?.[listKey] || []).length === 0 ? (
                      <div className="dispatch-dev__empty-row">这一类暂时没有工单</div>
                    ) : (
                      <ul className="dispatch-dev__list">
                        {(reassign?.ticket_lists?.[listKey] || []).map((t) => (
                          <li key={`${listKey}-${t.task_id}`}>
                            <TicketLink taskId={t.task_id} title={t.title || '（无标题）'} />
                            <em>{t.tag || KIND_LABEL[t.kind || ''] || t.kind || t.channel || '—'}</em>
                            <span>{formatHopTime(t.created_at || '')}{t.reason ? ` · ${t.reason}` : ''}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : null}
                <div className="dispatch-dev__chart">
                  <span className="dispatch-dev__sub">按周趋势（最近 {reassign?.weekly?.length ?? 0} 周；折线为比率 %，横轴下方为当周 AI 派单次数）</span>
                  {(reassign?.weekly || []).length === 0 ? (
                    <div className="dispatch-dev__empty-row">还没有带时间的转派 / 派单记录，趋势图暂时为空</div>
                  ) : (
                    <ReactECharts option={weeklyOption} style={{ height: 320 }} notMerge />
                  )}
                </div>
              </>
            )}
          </section>

          <section className="dispatch-dev__card">
            <div className="dispatch-dev__head">
              <span className="dispatch-dev__title">重新派单审核</span>
              <span className="dispatch-dev__hint" style={{ margin: 0 }}>
                剩 {reassign?.redispatch_items?.length ?? reassign?.metrics?.redispatch_pending ?? 0} 条
              </span>
            </div>
            <p className="dispatch-dev__hint">
              提单人、处理人、管理员点「重新派单」都会出现在这里。线上这通常表示对原派单不满意；测试操作点「测试不算」。
              标成不准确计入指标，并进入派单学习（相似单上的原处理人总分 ×0.7，不从候选人剔除）。测试不算不学。测试期结束后可以改成一点重新派单就自动算不准确。
            </p>
            {(reassign?.redispatch_items || []).length === 0 ? (
              <div className="dispatch-dev__empty-row">没有待审核的重新派单</div>
            ) : (
              <ul className="dispatch-dev__list">
                {(reassign?.redispatch_items || []).map((item) => (
                  <li key={item.id} className="dispatch-dev__review">
                    <strong><TicketLink taskId={item.task_id} title={item.title || '（无标题）'} /></strong>
                    <em>{item.operator_name} → 倾向 {item.preferred_name}</em>
                    <span>{item.description || ''}</span>
                    {item.reason ? (
                      <p className="dispatch-dev__hop-reason">备注：{item.reason}</p>
                    ) : (
                      <p className="dispatch-dev__hop-reason dispatch-dev__hop-reason--empty">（无备注）</p>
                    )}
                    <div className="dispatch-dev__review-btns">
                      <button
                        type="button"
                        className="dispatch-dev__btn"
                        disabled={reviewingId === item.id}
                        onClick={() => reviewItem(item.id, 'inaccurate')}
                      >
                        算不准确
                      </button>
                      <button
                        type="button"
                        className="dispatch-dev__btn dispatch-dev__btn--ghost"
                        disabled={reviewingId === item.id}
                        onClick={() => reviewItem(item.id, 'skipped')}
                      >
                        测试不算
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="dispatch-dev__card">
            <div className="dispatch-dev__head">
              <span className="dispatch-dev__title">未标类型审核</span>
              <span className="dispatch-dev__hint" style={{ margin: 0 }}>
                剩 {unlabeledRemain} 条
              </span>
            </div>
            <p className="dispatch-dev__hint">
              一张单转过多次会整链列出来，每一次未标转派都要单独点。已经有类型的那几跳只作对照，不能改。
              标成三个固定类型后计入指标；标成「派错了」也会进入派单学习。继续处理改人、实在看不出来的点「跳过」，不进错派率。
            </p>
            {unlabeledGroups.length === 0 ? (
              <div className="dispatch-dev__empty-row">没有待审核的未标转派</div>
            ) : (
              <ul className="dispatch-dev__list">
                {unlabeledGroups.map((g) => (
                  <li key={g.task_id} className="dispatch-dev__review">
                    <strong><TicketLink taskId={g.task_id} title={g.title || '（无标题）'} /></strong>
                    <span>共 {g.hops.length} 次转派，其中 {g.hops.filter((h) => h.reviewable !== false).length} 次未标</span>
                    <ul className="dispatch-dev__hops">
                      {g.hops.map((hop, idx) => (
                        <li key={hop.id} className="dispatch-dev__hop">
                          <em>
                            第 {idx + 1} 次 · {hop.from_name} → {hop.to_name}
                            <span className="dispatch-dev__hop-tag">{hopKindLabel(hop)}</span>
                          </em>
                          <span>{formatHopTime(hop.created_at)}{hop.description ? ` · ${hop.description}` : ''}</span>
                          {hop.reason ? (
                            <p className="dispatch-dev__hop-reason">转派原因：{hop.reason}</p>
                          ) : (
                            <p className="dispatch-dev__hop-reason dispatch-dev__hop-reason--empty">（当时没有填写转派原因）</p>
                          )}
                          {hop.reviewable !== false ? (
                            <div className="dispatch-dev__review-btns">
                              {(['misassign', 'stage', 'other'] as const).map((k) => (
                                <button
                                  key={k}
                                  type="button"
                                  className="dispatch-dev__btn"
                                  disabled={reviewingId === hop.id}
                                  onClick={() => reviewItem(hop.id, k)}
                                >
                                  {KIND_LABEL[k]}
                                </button>
                              ))}
                              <button
                                type="button"
                                className="dispatch-dev__btn dispatch-dev__btn--ghost"
                                disabled={reviewingId === hop.id}
                                onClick={() => reviewItem(hop.id, 'skipped')}
                              >
                                跳过
                              </button>
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="dispatch-dev__card">
            <div className="dispatch-dev__head">
              <span className="dispatch-dev__title">相似工单 · 索引</span>
              <div className="dispatch-dev__head-actions">
                <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => setHistoryOpen((v) => !v)}>
                  {historyOpen ? '收起列表' : `展开列表（${history?.tickets?.length ?? 0}）`}
                </button>
                <button type="button" className="dispatch-dev__btn" disabled={reindexing} onClick={reindex}>
                  {reindexing ? '补索引中…' : '一键补索引'}
                </button>
              </div>
            </div>
            <p className="dispatch-dev__hint">
              已解决 / 已关闭且有处理人的单会写入 Qdrant，供相似工单召回。工单多时要等一会儿。
            </p>
            <div className="dispatch-dev__stats">
              <span>库里 {history?.mysql_total ?? 0} 张</span>
              <span>已索引 {history?.qdrant_points ?? 0} 条</span>
              <span>集合 {history?.qdrant_collection || '尚未创建'}</span>
            </div>
            {indexHint ? <p className="dispatch-dev__hint">{indexHint}</p> : null}
            {historyOpen ? (
              <ul className="dispatch-dev__list">
                {(history?.tickets || []).length === 0 ? (
                  <li className="dispatch-dev__empty-row">暂无已解决 / 已关闭工单</li>
                ) : (
                  (history?.tickets || []).map((t) => (
                    <li key={t.ticket_id || t.title}>
                      {t.ticket_id && /^\d+$/.test(String(t.ticket_id)) ? (
                        <TicketLink taskId={Number(t.ticket_id)} title={t.title || '（无标题）'} />
                      ) : (
                        <strong>{t.title || '（无标题）'}</strong>
                      )}
                      <em>{t.engineer_name || t.engineer_id || '无人'}</em>
                      <span>
                        {[t.task_type, t.robot_type, t.fault_code].filter(Boolean).join(' · ') || '—'}
                      </span>
                    </li>
                  ))
                )}
              </ul>
            ) : null}
          </section>

          <section className="dispatch-dev__card">
            <div className="dispatch-dev__head">
              <span className="dispatch-dev__title">问题簇</span>
              <button type="button" className="dispatch-dev__btn" disabled={rebuilding || savingParams} onClick={rebuild}>
                {rebuilding ? '重建中…' : '重建簇'}
              </button>
            </div>
            <p className="dispatch-dev__hint">
              已解决/已关闭工单全量自动聚簇。改门槛后点「保存并重建」，立刻按新值聚，不用改 yaml。
            </p>
            <div className="dispatch-dev__params">
              <label>
                合并门槛
                <input
                  type="number"
                  min={0.1}
                  max={0.99}
                  step={0.01}
                  value={mergeInput}
                  onChange={(e) => setMergeInput(e.target.value)}
                />
              </label>
              <label>
                进簇门槛
                <input
                  type="number"
                  min={0.1}
                  max={0.99}
                  step={0.01}
                  value={assignInput}
                  onChange={(e) => setAssignInput(e.target.value)}
                />
              </label>
              <label>
                最小团
                <input
                  type="number"
                  min={2}
                  max={20}
                  step={1}
                  value={minSizeInput}
                  onChange={(e) => setMinSizeInput(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="dispatch-dev__btn"
                disabled={savingParams || rebuilding}
                onClick={saveClusterParams}
              >
                {savingParams ? '保存重建中…' : '保存并重建'}
              </button>
            </div>
            <p className="dispatch-dev__hint">
              合并、进簇都可以调到 0.10～0.99。点「保存并重建」后看下面「当前生效」；没保存成功数字不会变。
              合并：两张历史单有多像才并成一团。进簇：新单离簇中心多近才算这个簇。最小团：少于这么张丢掉。
            </p>
            <div className="dispatch-dev__stats">
              <span>当前生效 合并 {params.cluster_merge ?? '—'}</span>
              <span>进簇 {params.cluster_assign ?? '—'}</span>
              <span>最小团 {params.cluster_min_size ?? '—'}</span>
              <span>簇 {clusters?.clusters.length ?? 0} 个</span>
              <span>进簇 {clusters?.clustered ?? 0}</span>
              <span>未进簇 {clusters?.noise ?? 0}</span>
              <span>窗口 {clusters?.ticket_total ?? 0}</span>
            </div>
            <div className="dispatch-dev__chart">
              <span className="dispatch-dev__sub">向量分布（PCA 二维，轴无业务含义）</span>
              {hasScatter ? (
                <ReactECharts
                  option={scatterOption}
                  style={{ height: 300 }}
                  notMerge
                  onEvents={{
                    click: (item: { data?: { cluster_id?: number } }) => {
                      const cid = item?.data?.cluster_id;
                      if (typeof cid === 'number' && cid >= 0) setOpenId(cid);
                    },
                  }}
                />
              ) : (
                <div className="dispatch-dev__empty-row">再点一次「重建簇」后才能看到分布图</div>
              )}
            </div>
            {(clusters?.clusters || []).length === 0 ? (
              <div className="dispatch-dev__empty-row">
                {clusters?.error
                  ? clusters.error
                  : clusters?.ready
                    ? `窗口 ${clusters?.ticket_total ?? 0} 张，当前门槛下没聚出簇。把合并门槛再降一点，或把最小团降到 2，再点保存并重建。`
                    : '还没有簇缓存，点「重建簇」'}
              </div>
            ) : (
              (clusters?.clusters || []).map((c) => {
                const open = openId === c.id;
                return (
                  <div key={c.id} className="dispatch-dev__cluster" style={{ borderColor: clusterColor(c.id) }}>
                    <button type="button" className="dispatch-dev__cluster-head" onClick={() => setOpenId(open ? null : c.id)}>
                      <span className="dispatch-dev__cluster-title">
                        <i className="dispatch-dev__dot" style={{ background: clusterColor(c.id) }} />
                        #{c.id + 1} {c.titles.slice(0, 2).join(' / ') || '（无标题）'}
                      </span>
                      <span className="dispatch-dev__cluster-meta">
                        {c.ticket_count} 张 · {c.people.map((p) => `${p.name}×${p.count}`).join('、') || '无人'}
                        {open ? ' ▴' : ' ▾'}
                      </span>
                    </button>
                    {open ? (
                      <ul className="dispatch-dev__list">
                        {c.tickets.map((t, i) => (
                          <li key={`${t.ticket_id}-${i}`}>
                            {t.ticket_id && /^\d+$/.test(String(t.ticket_id)) ? (
                              <TicketLink taskId={Number(t.ticket_id)} title={t.title || '（无标题）'} />
                            ) : (
                              <strong>{t.title || '（无标题）'}</strong>
                            )}
                            <em>{t.engineer_name || t.engineer_id || '无人'}</em>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                );
              })
            )}
          </section>
        </>
      )}
    </div>
  );
}
