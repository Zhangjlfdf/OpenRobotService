import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const mockNavigate = vi.fn();
const mockCreateRequest = vi.fn();
const mockToast = vi.fn();
const mockAiGet = vi.fn();

// 必须用 vi.hoisted：vi.mock 工厂会被提升到文件顶部，在普通顶层 class 之前执行，
// 直接引用会报 "Cannot access 'MockApiError' before initialization"
const MockApiError = vi.hoisted(() => class MockApiError extends Error {
  statusCode: number;
  constructor(message: string, statusCode: number) {
    super(message);
    this.name = 'ApiError';
    this.statusCode = statusCode;
  }
});

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ id: 'new' }),
    useNavigate: () => mockNavigate,
  };
});

vi.mock('@/api/client', () => ({
  createRequest: () => mockCreateRequest,
  ApiError: MockApiError,
  clearCache: vi.fn(),
}));

vi.mock('@/api/ai', () => ({
  aiGet: () => mockAiGet(),
}));

// 项目信息管理卡挂载后会异步拉取信息树：这里保持挂起（不 resolve），
// 既不触发 act 警告，也不占用下面 mockCreateRequest 的请求桩与调用次数断言
vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: () => new Promise(() => {}),
  createInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  importInfoTemplateApi: vi.fn(),
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { username: string }) => unknown) => selector({ username: 'admin' }),
}));

vi.mock('@/config/api', () => ({
  default: { ADMIN: { BASE_URL: '/api/admin' }, TASKS: { BASE_URL: '/api/tasks' } },
}));

vi.mock('tdesign-mobile-react', () => {
  interface FieldProps {
    value?: string;
    onChange?: (v: string) => void;
    onBlur?: () => void;
    placeholder?: string;
    autofocus?: boolean;
  }
  const Navbar = ({ title, right }: { title?: ReactNode; right?: ReactNode }) => (
    <div data-testid="navbar">
      <span>{title}</span>
      <div>{right}</div>
    </div>
  );
  const Loading = () => <div data-testid="loading" />;
  const Popup = ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null;
  const Upload = () => <div data-testid="upload" />;
  const Checkbox = () => <div data-testid="checkbox" />;
  const Input = (props: FieldProps) => (
    <input
      value={props.value ?? ''}
      onChange={(e) => props.onChange?.(e.target.value)}
      onBlur={props.onBlur}
      placeholder={props.placeholder}
      autoFocus={props.autofocus}
    />
  );
  const Textarea = (props: FieldProps) => (
    <textarea
      value={props.value ?? ''}
      onChange={(e) => props.onChange?.(e.target.value)}
      onBlur={props.onBlur}
      placeholder={props.placeholder}
    />
  );
  return {
    Navbar,
    Loading,
    Toast: (opts: { message?: string; theme?: string }) => {
      mockToast(opts);
      return null;
    },
    Popup,
    Upload,
    Checkbox,
    Input,
    Textarea,
  };
});

import ProjectDetail from '../admin/ProjectDetail';

const renderView = () =>
  render(
    <MemoryRouter>
      <ProjectDetail />
    </MemoryRouter>
  );

describe('ProjectDetail（USP 项目新建）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    mockToast.mockClear();
    mockCreateRequest.mockClear();
    mockAiGet.mockClear();
  });

  it('新建模式展示必填标记（项目名称/项目编号/项目状态）', () => {
    renderView();
    expect(screen.getAllByText('*')).toHaveLength(5);
  });

  it('未填必填字段点「创建」→ 提示先填写项目名称，且不发请求', () => {
    renderView();
    fireEvent.click(screen.getByText('创建'));
    expect(mockToast).toHaveBeenCalledWith({ message: '请填写项目名称', theme: 'warning' });
    expect(mockCreateRequest).not.toHaveBeenCalled();
  });

  it('项目编号重复（后端 409）→ 提示用户项目已存在请重新输入', async () => {
    mockCreateRequest.mockRejectedValueOnce(
      new MockApiError('项目编号「CODE-1」已存在，请重新输入', 409)
    );
    renderView();

    // 填写项目名称
    fireEvent.click(screen.getByText('未命名项目'));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '项目A' } });
    fireEvent.blur(screen.getByRole('textbox'));

    // 填写项目编号（项目概况卡「项目编号」行：点值进入编辑，标签自身不可点；
    // 项目基础画像里还有一个同名标签，所以按 meta 行定位）
    const codeRow = Array.from(document.querySelectorAll('.mac-meta-row'))
      .find((row) => row.textContent?.startsWith('项目编号'));
    expect(codeRow).toBeTruthy();
    fireEvent.click(within(codeRow as HTMLElement).getByText('未填写'));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'CODE-1' } });
    fireEvent.blur(screen.getByRole('textbox'));

    fireEvent.click(screen.getByText('创建'));
    // 409 由 createRequest 的 rejected promise 触发，catch 在微任务里跑，需等待
    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith({ message: '项目编号「CODE-1」已存在，请重新输入', theme: 'warning' });
    });
  });
});
