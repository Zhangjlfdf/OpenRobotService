// 我要摇人页的「车体扫码进入」链路：
//   解析 scene → 查一次码记录 → 仅「已发布（出厂）」弹车辆信息确认 → 确认后另起空白会话
//   并把车辆上下文投给对话区；未携带 / 查无此码 / 非已发布 / 查询异常一律静默不打扰。
//
// 测试策略（对齐仓库既有页面测试约定）：
//   - api 层打桩：不触网
//   - ChatPanel / 抽屉 / 头像菜单 / 关注提醒 打桩：本用例只关心编排层，不跑真实对话组件
//   - tdesign 弹层用轻量替身（仓库约定：测试不渲染真实 Dialog/Popup，jsdom 下会挂起）
//   - 路由用 MemoryRouter 真跑（useSearchParams/useNavigate 都走真实实现，
//     这样「一次性摘掉 scene 参数」的行为才能被断言）
import { useState, type ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';

const mockFetchQrcodeByScene = vi.fn();

vi.mock('@/api/qrcode', () => ({
  fetchQrcodeByScene: (scene: string) => mockFetchQrcodeByScene(scene),
}));

vi.mock('@/api/ai', () => ({
  qaListTickets: vi.fn().mockResolvedValue({ data: { active_total: 0 } }),
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { username: string; isAdmin: boolean }) => unknown) =>
    selector({ username: 'bob', isAdmin: false }),
}));

vi.mock('@/shared/components/ChatPanel', () => ({ default: () => <div data-testid="chat-panel" /> }));
vi.mock('@/shared/components/ConversationDrawer', () => ({ default: () => null }));
vi.mock('@/shared/components/UserAvatarMenu', () => ({ default: () => null }));
vi.mock('@/shared/components/SubscriptionReminder', () => ({ default: () => null }));

interface BtnProps {
  content?: ReactNode;
  disabled?: boolean;
}

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => <div>{title}</div>,
  Dialog: ({
    visible,
    title,
    cancelBtn,
    confirmBtn,
    onCancel,
    onConfirm,
    children,
  }: {
    visible?: boolean;
    title?: ReactNode;
    cancelBtn?: BtnProps | null;
    confirmBtn?: BtnProps | null;
    onCancel?: () => void;
    onConfirm?: () => void;
    children?: ReactNode;
  }) => {
    if (!visible) return null;
    return (
      <div data-testid="vehicle-confirm-dialog">
        <div>{title}</div>
        <div>{children}</div>
        <button disabled={cancelBtn?.disabled} onClick={() => { if (!cancelBtn?.disabled) onCancel?.(); }}>
          {cancelBtn?.content}
        </button>
        <button disabled={confirmBtn?.disabled} onClick={() => { if (!confirmBtn?.disabled) onConfirm?.(); }}>
          {confirmBtn?.content}
        </button>
      </div>
    );
  },
}));

import CallView, { resolveSceneCode } from '../call/CallView';
import { useWorkbenchStore } from '@/stores/workbench';

const SCENE = 'proj_abc123def456';

const publishedRecord = {
  scene_str: SCENE,
  status: 'published',
  project_name: '项目A',
  customer_name: '客户A',
  vehicle_model: 'XQE',
};

/** 外层探针：暴露当前地址栏 query + 可手动卸载/重挂 CallView（模拟切 Tab） */
function Harness() {
  const location = useLocation();
  const [mounted, setMounted] = useState(true);
  return (
    <>
      <div data-testid="search">{location.search}</div>
      <button data-testid="remount" onClick={() => setMounted((v) => !v)}>
        remount
      </button>
      {mounted && <CallView />}
    </>
  );
}

const renderCall = (search = '') =>
  render(
    <MemoryRouter initialEntries={[`/call${search}`]}>
      <Harness />
    </MemoryRouter>,
  );

describe('resolveSceneCode（URL 场景值解析）', () => {
  it('取到合法 scene 并去空白', () => {
    expect(resolveSceneCode(new URLSearchParams(`scene=${SCENE}`))).toBe(SCENE);
    expect(resolveSceneCode(new URLSearchParams('scene=%20proj_x1%20'))).toBe('proj_x1');
  });

  it('未携带 / 空值 / 非法字符 / 超长一律返回 null', () => {
    expect(resolveSceneCode(new URLSearchParams(''))).toBeNull();
    expect(resolveSceneCode(new URLSearchParams('scene='))).toBeNull();
    expect(resolveSceneCode(new URLSearchParams('scene=proj%2Fadmin'))).toBeNull();
    expect(resolveSceneCode(new URLSearchParams(`scene=${'a'.repeat(65)}`))).toBeNull();
  });
});

describe('CallView 车体扫码进入', () => {
  beforeEach(() => {
    mockFetchQrcodeByScene.mockReset();
    useWorkbenchStore.setState({
      conversationId: 5,
      conversationTitle: '上一轮会话',
      pendingNewConversation: false,
      vehicleContext: null,
    });
  });

  it('未携带 scene：不发请求、不弹窗，现有对话行为不变', async () => {
    renderCall();

    await waitFor(() => expect(screen.getByTestId('chat-panel')).toBeInTheDocument());
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();
    expect(screen.queryByTestId('vehicle-confirm-dialog')).not.toBeInTheDocument();
  });

  it('非法 scene：同样不发请求（白名单在请求之前挡掉）', async () => {
    renderCall('?scene=../../etc/passwd');

    await waitFor(() => expect(screen.getByTestId('chat-panel')).toBeInTheDocument());
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();
  });

  it('码状态为「已发布」：查一次并弹出车辆信息确认，三行信息来自该码记录', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}`);

    expect(await screen.findByText('车辆信息确认')).toBeInTheDocument();
    expect(screen.getByText('项目A')).toBeInTheDocument();
    expect(screen.getByText('客户A')).toBeInTheDocument();
    expect(screen.getByText('XQE')).toBeInTheDocument();
    // 同一个 scene 只查一次（幂等）
    expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1);
    expect(mockFetchQrcodeByScene).toHaveBeenCalledWith(SCENE);
  });

  it('非「已发布」状态：静默降级，不弹窗', async () => {
    mockFetchQrcodeByScene.mockResolvedValue({ ...publishedRecord, status: 'confirming' });
    renderCall(`?scene=${SCENE}`);

    await waitFor(() => expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('vehicle-confirm-dialog')).not.toBeInTheDocument();
  });

  it('查无此码（api 返回 null）：静默降级，不弹窗', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(null);
    renderCall(`?scene=${SCENE}`);

    await waitFor(() => expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('vehicle-confirm-dialog')).not.toBeInTheDocument();
  });

  it('处理过就把 scene 从地址栏摘掉（保留其它参数），避免切 Tab 回来重弹', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}&openid=oXk4js`);

    await screen.findByText('车辆信息确认');

    await waitFor(() => expect(screen.getByTestId('search').textContent).not.toContain('scene='));
    // 其它参数原样保留（openid 后续还要用）
    expect(screen.getByTestId('search').textContent).toContain('openid=oXk4js');
  });

  it('卸载再挂载（等价于切 Tab 回来）不会重复查询、不会重弹弹窗', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}`);

    fireEvent.click(await screen.findByText('暂不'));
    await waitFor(() => expect(screen.queryByTestId('vehicle-confirm-dialog')).not.toBeInTheDocument());
    expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1);

    // 卸载 → 重新挂载，地址栏此时已不含 scene
    fireEvent.click(screen.getByTestId('remount'));
    fireEvent.click(screen.getByTestId('remount'));

    await waitFor(() => expect(screen.getByTestId('chat-panel')).toBeInTheDocument());
    expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('vehicle-confirm-dialog')).not.toBeInTheDocument();
  });

  it('点「确认」：另起空白会话 + 车辆上下文入 store + 弹窗关闭', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}`);

    fireEvent.click(await screen.findByText('确认'));

    const state = useWorkbenchStore.getState();
    // 「这次扫码专用」：清空当前会话，标记 pending 以拦住进入页的自动选最近会话
    expect(state.conversationId).toBeNull();
    expect(state.pendingNewConversation).toBe(true);
    expect(state.vehicleContext).toEqual({
      scene: SCENE,
      projectName: '项目A',
      customerName: '客户A',
      vehicleModel: 'XQE',
    });
    await waitFor(() => expect(screen.queryByTestId('vehicle-confirm-dialog')).not.toBeInTheDocument());
  });

  it('点「暂不」：只关弹窗，不写车辆上下文、不动当前会话', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}`);

    fireEvent.click(await screen.findByText('暂不'));

    const state = useWorkbenchStore.getState();
    expect(state.vehicleContext).toBeNull();
    expect(state.conversationId).toBe(5);
    await waitFor(() => expect(screen.queryByTestId('vehicle-confirm-dialog')).not.toBeInTheDocument());
  });

  it('缺项信息用占位符补位，不出现空白行', async () => {
    mockFetchQrcodeByScene.mockResolvedValue({
      ...publishedRecord,
      customer_name: null,
      vehicle_model: '   ',
    });
    renderCall(`?scene=${SCENE}`);

    expect(await screen.findByText('车辆信息确认')).toBeInTheDocument();
    expect(screen.getAllByText('—')).toHaveLength(2);
  });
});
