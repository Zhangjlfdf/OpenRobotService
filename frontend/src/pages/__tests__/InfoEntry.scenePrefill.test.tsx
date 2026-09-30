// 录入信息页的「扫码带入项目id」链路（2026-09-30 用户口径）：
//   扫码跳转链接 …/info-entry?scene=xxx 里的 scene 就是项目id（wechat_qrcodes 第二列
//   scene_str，已无 project_id 列）——解析出来直接带入「项目id」输入框：
//   能查到行 → 编辑那行（锁死）；查不到行 → 按新录入处理（项目id 预填锁定）；
//   没带 scene → 管理端手动新建（可编辑、必填）。
//   该行已 published（录入+确认完成）→ 不停留本页，跳「我要摇人」(/call)，
//   scene/openid 原样带走；管理端不带 scene 的「编辑信息」链接不受影响。
//
// 测试策略（对齐仓库既有页面测试约定，见 CallView.vehicleScan.test.tsx）：
//   - api 层打桩：不触网
//   - tdesign 组件用轻量替身（Form→真 form、Button→真 button，能跑真实提交流程）
//   - ClearableInput 替身为原生 input，直接断言 value/disabled
//   - 路由用 MemoryRouter 真跑（useParams/useSearchParams 走真实实现）
import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

const mockFetchQrcode = vi.fn();
const mockFetchQrcodeByScene = vi.fn();
const mockCreateProjectInfo = vi.fn();
const mockUpdateProjectInfo = vi.fn();
const mockTransition = vi.fn();
const mockToast = vi.fn();

vi.mock('@/api/qrcode', () => ({
  fetchQrcode: (id: number) => mockFetchQrcode(id),
  fetchQrcodeByScene: (scene: string) => mockFetchQrcodeByScene(scene),
  createProjectInfo: (data: unknown) => mockCreateProjectInfo(data),
  updateProjectInfo: (id: number, data: unknown) => mockUpdateProjectInfo(id, data),
  qrcodeTransition: (id: number, action: string) => mockTransition(id, action),
}));

vi.mock('@/shared/components/ClearableInput', () => ({
  default: ({
    value,
    disabled,
    onChange,
    placeholder,
  }: {
    value?: string;
    disabled?: boolean;
    placeholder?: string;
    onChange?: (v: string) => void;
  }) => (
    <input
      placeholder={placeholder}
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

vi.mock('tdesign-mobile-react', () => ({
  Loading: () => <div>加载中</div>,
  Toast: (opts: { message?: string }) => mockToast(opts),
  Form: ({ onSubmit, children }: { onSubmit?: () => void; children?: ReactNode }) => (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit?.();
      }}
    >
      {children}
    </form>
  ),
  FormItem: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Button: ({
    children,
    onClick,
    loading,
    type,
  }: {
    children?: ReactNode;
    onClick?: () => void;
    loading?: boolean;
    type?: 'submit' | 'button';
  }) => (
    <button type={type} onClick={onClick} disabled={loading}>
      {children}
    </button>
  ),
}));

import InfoEntry from '../admin/InfoEntry';

const SCENE = 'P2026001';

const infoRow = {
  id: 9,
  scene_str: SCENE,
  status: 'entering',
  project_code: 'CODE-9',
  project_name: '项目九',
  project_location: '现场九',
  customer_name: '客户九',
  vehicle_model: 'XQE',
};

/** 「我要摇人」落点探针：断言 published 重定向发生、query（scene/openid）原样带过去 */
function CallProbe() {
  const { pathname, search } = useLocation();
  return (
    <div data-testid="call-page">
      {pathname}
      {search}
    </div>
  );
}

const renderInfoEntry = (entry: string) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/admin/info-entry" element={<InfoEntry />} />
        <Route path="/admin/info-entry/:id" element={<InfoEntry />} />
        <Route path="/call" element={<CallProbe />} />
      </Routes>
    </MemoryRouter>,
  );

const valueOf = (el: HTMLElement) => (el as HTMLInputElement).value;

describe('InfoEntry 扫码带入项目id', () => {
  beforeEach(() => {
    mockFetchQrcode.mockReset();
    mockFetchQrcodeByScene.mockReset();
    mockCreateProjectInfo.mockReset().mockResolvedValue({});
    mockUpdateProjectInfo.mockReset().mockResolvedValue({});
    mockTransition.mockReset().mockResolvedValue({ status: 'published' });
    mockToast.mockReset();
  });

  it('scene 能查到行：编辑那行，项目id=scene_str 且锁定；entering 时出「确认信息」并走 confirm', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(infoRow);
    renderInfoEntry(`/admin/info-entry?scene=${SCENE}&openid=oXk4js`);

    const pid = await screen.findByPlaceholderText('请输入项目id');
    await waitFor(() => expect(valueOf(pid)).toBe(SCENE));
    expect(pid).toBeDisabled();
    // scene 命中就走 by-scene（登录即可接口），不再按 id 查
    expect(mockFetchQrcodeByScene).toHaveBeenCalledWith(SCENE);
    expect(mockFetchQrcode).not.toHaveBeenCalled();
    // 行上的项目字段回填
    await waitFor(() => expect(valueOf(screen.getByPlaceholderText('请输入项目名'))).toBe('项目九'));

    // entering → 「确认信息」按钮；点击走 confirm（后端：录入行确认即发布）
    fireEvent.click(screen.getByText('确认信息'));
    await waitFor(() => expect(mockTransition).toHaveBeenCalledWith(9, 'confirm'));
    // 编辑已有行不发送 project_id（它就是 scene_str，不可改）
    expect(mockUpdateProjectInfo).not.toHaveBeenCalled();
  });

  it('scene 查不到行：按新录入处理，项目id 预填锁定；保存时 project_id 带入', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(null);
    renderInfoEntry(`/admin/info-entry?scene=${SCENE}`);

    const pid = await screen.findByPlaceholderText('请输入项目id');
    await waitFor(() => expect(valueOf(pid)).toBe(SCENE));
    expect(pid).toBeDisabled();
    // 新录入没有行可确认，不发「确认信息」按钮
    expect(screen.queryByText('确认信息')).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('请输入项目编号'), { target: { value: 'CODE-1' } });
    fireEvent.change(screen.getByPlaceholderText('请输入项目名'), { target: { value: '项目一' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() =>
      expect(mockCreateProjectInfo).toHaveBeenCalledWith(
        expect.objectContaining({ project_id: SCENE, project_code: 'CODE-1', project_name: '项目一' }),
      ),
    );
    expect(mockUpdateProjectInfo).not.toHaveBeenCalled();
  });

  it('没带 scene（管理端新建）：项目id 空、可编辑、必填校验拦下', async () => {
    renderInfoEntry('/admin/info-entry');

    const pid = await screen.findByPlaceholderText('请输入项目id');
    expect(valueOf(pid)).toBe('');
    expect(pid).not.toBeDisabled();
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText('请输入项目编号'), { target: { value: 'CODE-1' } });
    fireEvent.change(screen.getByPlaceholderText('请输入项目名'), { target: { value: '项目一' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ message: '请填写项目id' })));
    expect(mockCreateProjectInfo).not.toHaveBeenCalled();
  });

  it('非法 scene：不查接口、不预填，项目id 保持可编辑', async () => {
    renderInfoEntry('/admin/info-entry?scene=../etc/passwd');

    const pid = await screen.findByPlaceholderText('请输入项目id');
    expect(valueOf(pid)).toBe('');
    expect(pid).not.toBeDisabled();
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();
  });

  it('/:id 无 scene（二维码管理点「编辑信息」）：按 id 查行回填，项目id 锁死', async () => {
    mockFetchQrcode.mockResolvedValue(infoRow);
    renderInfoEntry('/admin/info-entry/9');

    const pid = await screen.findByPlaceholderText('请输入项目id');
    await waitFor(() => expect(valueOf(pid)).toBe(SCENE));
    expect(pid).toBeDisabled();
    expect(mockFetchQrcode).toHaveBeenCalledWith(9);
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();
  });

  it('扫码进入且该行已 published：不停留录入页，跳「我要摇人」并原样带上 scene/openid', async () => {
    // 真实扫码链接形如 …/info-entry/{id}?scene=…&openid=…（卡片生成于 entering 时期，
    // 点击时已有人确认过 = 状态机 published）→ 应直达 CallView（/call）
    mockFetchQrcodeByScene.mockResolvedValue({ ...infoRow, status: 'published' });
    renderInfoEntry(`/admin/info-entry/9?scene=${SCENE}&openid=oXk4js`);

    const probe = await screen.findByTestId('call-page');
    expect(probe.textContent).toContain(`scene=${SCENE}`);
    expect(probe.textContent).toContain('openid=oXk4js');
    // 不停留录入页：表单没渲染、无「确认信息」
    expect(screen.queryByPlaceholderText('请输入项目id')).not.toBeInTheDocument();
    expect(screen.queryByText('确认信息')).not.toBeInTheDocument();
    // scene 命中走 by-scene（登录即可接口），跳走前不再按 id 查
    expect(mockFetchQrcodeByScene).toHaveBeenCalledWith(SCENE);
    expect(mockFetchQrcode).not.toHaveBeenCalled();
  });

  it('/:id 无 scene（管理端「编辑信息」）且已 published：不跳转，仍打开那行编辑', async () => {
    mockFetchQrcode.mockResolvedValue({ ...infoRow, status: 'published' });
    renderInfoEntry('/admin/info-entry/9');

    const pid = await screen.findByPlaceholderText('请输入项目id');
    await waitFor(() => expect(valueOf(pid)).toBe(SCENE));
    expect(pid).toBeDisabled();
    expect(screen.queryByTestId('call-page')).not.toBeInTheDocument();
  });
});
