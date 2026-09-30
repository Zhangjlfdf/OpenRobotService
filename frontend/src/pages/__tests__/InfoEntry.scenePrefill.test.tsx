// 录入信息页的「扫码 scene=str(id)」链路（2026-09-30 用户口径）：
//   扫码跳转链接 …/info-entry?scene=xxx 里的 scene 即 str(id)（项目id 就是行 id，
//   不单独占列、不随表单提交、界面也不再显示）——能查到行 → 编辑那行；
//   查不到行 → 按新录入处理；没带 scene → 管理端手动新建。
//   该行已 published（录入+确认完成）→ 不停留本页，跳「我要摇人」(/call)，
//   scene/openid 原样带走；管理端不带 scene 的「编辑信息」链接不受影响。
//   项目名称（表单第一位）：候选来自 project 表（下拉模糊匹配），选中带出项目编号
//   且编号锁定只读；手改名称=新项目 → 编号解锁、手动填写，保存时由后端补进 project
//   表（提示「保存时将自动新建项目」）。
//
// 测试策略（对齐仓库既有页面测试约定，见 CallView.vehicleScan.test.tsx）：
//   - api 层打桩：不触网
//   - tdesign 组件用轻量替身（Form→真 form、Button→真 button，能跑真实提交流程）
//   - ClearableInput 替身为原生 input，直接断言 value/disabled，并透传 onFocus/onBlur
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
const mockGetProjects = vi.fn();
const mockToast = vi.fn();

vi.mock('@/api/qrcode', () => ({
  fetchQrcode: (id: number) => mockFetchQrcode(id),
  fetchQrcodeByScene: (scene: string) => mockFetchQrcodeByScene(scene),
  createProjectInfo: (data: unknown) => mockCreateProjectInfo(data),
  updateProjectInfo: (id: number, data: unknown) => mockUpdateProjectInfo(id, data),
  qrcodeTransition: (id: number, action: string) => mockTransition(id, action),
}));

// 项目名候选来自 project 表（GET /projects/），打桩避免触网
vi.mock('@/api/projects', () => ({
  getProjects: (...args: unknown[]) => mockGetProjects(...args),
}));

vi.mock('@/shared/components/ClearableInput', () => ({
  default: ({
    value,
    disabled,
    onChange,
    placeholder,
    onFocus,
    onBlur,
  }: {
    value?: string;
    disabled?: boolean;
    placeholder?: string;
    onChange?: (v: string) => void;
    onFocus?: () => void;
    onBlur?: () => void;
  }) => (
    <input
      placeholder={placeholder}
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange?.(e.target.value)}
      onFocus={() => onFocus?.()}
      onBlur={() => onBlur?.()}
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

// scene 即 str(id)：行 9 的场景值就是 '9'（2026-09-30 口径）
const SCENE = '9';

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

const NAME_PLACEHOLDER = '可匹配已有项目或输入新项目名称';
const CODE_PLACEHOLDER = '请输入项目编号';

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
    mockGetProjects.mockReset().mockResolvedValue([]);
    mockToast.mockReset();
  });

  it('scene 能查到行：编辑那行，字段回填；entering 时出「确认信息」并走 confirm', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(infoRow);
    renderInfoEntry(`/admin/info-entry?scene=${SCENE}&openid=oXk4js`);

    // scene 命中就走 by-scene（登录即可接口），不再按 id 查
    expect(mockFetchQrcodeByScene).toHaveBeenCalledWith(SCENE);
    expect(mockFetchQrcode).not.toHaveBeenCalled();
    // 行上的项目字段回填（界面不再有项目id 输入框）
    await waitFor(() => expect(valueOf(screen.getByPlaceholderText(CODE_PLACEHOLDER))).toBe('CODE-9'));
    expect(valueOf(screen.getByPlaceholderText(NAME_PLACEHOLDER))).toBe('项目九');
    expect(screen.queryByPlaceholderText('保存后自动生成')).not.toBeInTheDocument();

    // entering → 「确认信息」按钮；点击走 confirm（后端：录入行确认即发布）
    fireEvent.click(screen.getByText('确认信息'));
    await waitFor(() => expect(mockTransition).toHaveBeenCalledWith(9, 'confirm'));
    // 编辑已有行不发送 project_id（它就是行 id，不可改）
    expect(mockUpdateProjectInfo).not.toHaveBeenCalled();
  });

  it('scene 查不到行：按新录入处理，空表单；保存不带 project_id', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(null);
    renderInfoEntry(`/admin/info-entry?scene=${SCENE}`);

    await waitFor(() => expect(screen.getByPlaceholderText(NAME_PLACEHOLDER)).toHaveValue(''));
    // 表单顺序：项目名称在前、项目编号在后（2026-09-30 用户口径）
    const nameEl = screen.getByPlaceholderText(NAME_PLACEHOLDER);
    const codeEl = screen.getByPlaceholderText(CODE_PLACEHOLDER);
    expect(nameEl.compareDocumentPosition(codeEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // 新项目的编号手填，不锁定
    expect(codeEl).not.toBeDisabled();
    // 新录入没有行可确认，不发「确认信息」按钮
    expect(screen.queryByText('确认信息')).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(CODE_PLACEHOLDER), { target: { value: 'CODE-1' } });
    fireEvent.change(screen.getByPlaceholderText(NAME_PLACEHOLDER), { target: { value: '项目一' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(mockCreateProjectInfo).toHaveBeenCalled());
    const payload = mockCreateProjectInfo.mock.calls[0][0] as Record<string, unknown>;
    expect(payload).toMatchObject({ project_code: 'CODE-1', project_name: '项目一' });
    expect(payload.project_id).toBeUndefined();
    expect(mockUpdateProjectInfo).not.toHaveBeenCalled();
  });

  it('没带 scene（管理端新建）：空表单；填编号+项目名即可保存，不带 project_id', async () => {
    renderInfoEntry('/admin/info-entry');

    await waitFor(() => expect(screen.getByPlaceholderText(NAME_PLACEHOLDER)).toHaveValue(''));
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(CODE_PLACEHOLDER), { target: { value: 'CODE-1' } });
    fireEvent.change(screen.getByPlaceholderText(NAME_PLACEHOLDER), { target: { value: '项目一' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(mockCreateProjectInfo).toHaveBeenCalled());
    const payload = mockCreateProjectInfo.mock.calls[0][0] as Record<string, unknown>;
    expect(payload).toMatchObject({ project_code: 'CODE-1', project_name: '项目一' });
    expect(payload.project_id).toBeUndefined();
  });

  it('非法 scene：不查接口、不预填，表单保持空', async () => {
    renderInfoEntry('/admin/info-entry?scene=../etc/passwd');

    await waitFor(() => expect(screen.getByPlaceholderText(NAME_PLACEHOLDER)).toHaveValue(''));
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();
  });

  it('/:id 无 scene（二维码管理点「编辑信息」）：按 id 查行回填', async () => {
    mockFetchQrcode.mockResolvedValue(infoRow);
    renderInfoEntry('/admin/info-entry/9');

    await waitFor(() => expect(valueOf(screen.getByPlaceholderText(CODE_PLACEHOLDER))).toBe('CODE-9'));
    expect(mockFetchQrcode).toHaveBeenCalledWith(9);
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();
  });

  it('项目名称支持模糊选择：输入触发候选，点选自动带出项目编号且编号锁定', async () => {
    mockGetProjects.mockResolvedValue([
      { id: 'CODE-10', project_code: 'CODE-10', name: '项目十' },
      { id: 'CODE-11', project_code: 'CODE-11', name: '项目十一' },
    ]);
    renderInfoEntry('/admin/info-entry');

    const nameInput = screen.getByPlaceholderText(NAME_PLACEHOLDER);
    fireEvent.focusIn(nameInput);
    fireEvent.change(nameInput, { target: { value: '项目十' } });

    // 模糊匹配按子串过滤：两条都命中；点第一条 → 项目名称+项目编号一起带出
    await screen.findByText('项目十');
    fireEvent.mouseDown(screen.getByText('项目十'));

    expect(valueOf(nameInput)).toBe('项目十');
    const codeInput = screen.getByDisplayValue('CODE-10');
    expect(codeInput).toBeDisabled(); // 编号来自 project 表（不是手填），锁住不让改
    // 选中已有项目不提示「自动新建」
    expect(screen.queryByText('不在项目表中，保存时将自动新建项目')).not.toBeInTheDocument();

    // 手动改名 = 新项目：编号解锁，交回手动填写
    fireEvent.change(nameInput, { target: { value: '项目十改' } });
    expect(screen.getByDisplayValue('CODE-10')).not.toBeDisabled();
  });

  it('项目名称不在 project 表：提示保存时自动新建；编号可手填并照常提交', async () => {
    mockGetProjects.mockResolvedValue([{ id: 'CODE-10', project_code: 'CODE-10', name: '项目十' }]);
    renderInfoEntry('/admin/info-entry');

    const nameInput = screen.getByPlaceholderText(NAME_PLACEHOLDER);
    fireEvent.focusIn(nameInput);
    fireEvent.change(nameInput, { target: { value: '全新项目' } });

    await screen.findByText('不在项目表中，保存时将自动新建项目');
    // 新项目的编号要手动填写（新建时编号框可编辑）
    const codeInput = screen.getByPlaceholderText(CODE_PLACEHOLDER);
    expect(codeInput).not.toBeDisabled();
    fireEvent.change(codeInput, { target: { value: 'CODE-NEW' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(mockCreateProjectInfo).toHaveBeenCalled());
    expect(mockCreateProjectInfo.mock.calls[0][0]).toMatchObject({
      project_code: 'CODE-NEW', project_name: '全新项目',
    });
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
    expect(screen.queryByPlaceholderText(NAME_PLACEHOLDER)).not.toBeInTheDocument();
    expect(screen.queryByText('确认信息')).not.toBeInTheDocument();
    // scene 命中走 by-scene（登录即可接口），跳走前不再按 id 查
    expect(mockFetchQrcodeByScene).toHaveBeenCalledWith(SCENE);
    expect(mockFetchQrcode).not.toHaveBeenCalled();
  });

  it('/:id 无 scene（管理端「编辑信息」）且已 published：不跳转，仍打开那行编辑', async () => {
    mockFetchQrcode.mockResolvedValue({ ...infoRow, status: 'published' });
    renderInfoEntry('/admin/info-entry/9');

    await waitFor(() => expect(valueOf(screen.getByPlaceholderText(CODE_PLACEHOLDER))).toBe('CODE-9'));
    expect(screen.queryByTestId('call-page')).not.toBeInTheDocument();
  });
});
