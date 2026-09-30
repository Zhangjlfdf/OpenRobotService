// 录入信息 —— 「项目管理 → 新建项目 → 录入信息」进入的项目信息登记页。
//
// 五个字段（2026-09-29 用户口径）：项目编号 / 项目名 / 项目地点 / 客户名 / 车型。
// 一条录入 = wechat_qrcodes 里的一行（五个字段和行 id 同行存）：
//   创建 POST /qrcodes/project-info；点条目回到本页（/admin/info-entry/:id）修改走
//   PUT /qrcodes/{id}/project-info。保存后该行在二维码管理里可见、可继续生成 ticket。
//
// 项目id 就是行 id（2026-09-30 用户口径）：不再单独占列、不随表单提交——
//   新建时保存后自动生成（表单里只读展示占位）；编辑时显示 str(id)。
// 扫码跳转链接（…/info-entry/{id}?scene=xxx&openid=…）里的 scene 即 str(id)：
//   - 场景值能查到行 → 就是编辑那一行；
//   - 场景值查不到行（码还没录入过）→ 按新录入处理（项目id 保存后自动生成，
//     不需要、也无法预填）；
//   - 链接没带 scene（管理端手动新建）→ 同样新录入。
//   - 该行已是 published（录入+确认都完成）→ 不停留本页，直接跳「我要摇人」（2026-09-30
//     用户口径）：scene/openid 原样带过去，CallView 按 scene 弹车体信息确认；
//     管理端「编辑信息」链接不带 scene，不受影响（那是修改数据的入口）。
//   注：录入信息相关的四个接口（by-scene / 按 :id 查 / 保存 / 确认）都是「登录即可」——
//   所有人扫码都能录入信息并确认（2026-09-30 用户口径）；管理端其余接口仍是 admin 权限。
//
// 扫码确认流程：该行 ticket 生成后状态是 entering，此时扫这张码会跳转到本页
//   （后端 _send_scan_redirect_card 按状态分流），页面底部出现「确认信息」按钮，
//   点击直接 entering → published（录入信息行「确认即发布」，2026-09-30 用户口径；
//   不经 confirming 中间态，后端 confirm 接口按行类型分流）。
//   已 published 后再扫同一张码：后端对新卡片本就分流到 /app/call；若点的是生成于
//   entering 时期的旧卡片、链接落回本页，则由上面的 published 判断兜底重定向。
//
// 规则（界面不写注解，由交互体现）：
// - 项目id：不可编辑，= str(行 id)，保存后自动生成
// - 项目编号：唯一，可改；与其他录入行重复时后端 400，detail 直接 Toast 出来
// - 项目名：必填（列表/预览里码记录名跟随它）
// - 项目地点 / 客户名 / 车型：自由填写，可留空
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Form, FormItem, Loading, Toast } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import {
  createProjectInfo, fetchQrcode, fetchQrcodeByScene, qrcodeTransition, updateProjectInfo,
  type QrcodeItem,
} from '@/api/qrcode';

const errMsg = (err: unknown, fallback: string) =>
  err instanceof Error && err.message ? err.message : fallback;

/** 场景值 = str(id)（2026-09-30 口径），只认纯数字（容错路径手输的链接） */
const SCENE_PATTERN = /^\d{1,10}$/;

const emptyForm = {
  project_id: '',
  project_code: '',
  project_name: '',
  project_location: '',
  customer_name: '',
  vehicle_model: '',
};

export default function InfoEntry() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const numId = id ? Number(id) : NaN;
  const pathId = Number.isInteger(numId) && numId > 0 ? numId : null;

  // 扫码进入：链接里的 scene 即 str(id)（不合规按没带处理，容错路径手输的链接）
  const sceneCode = useMemo(() => {
    const raw = (searchParams.get('scene') ?? '').trim();
    return SCENE_PATTERN.test(raw) ? raw : null;
  }, [searchParams]);

  // 正在编辑的行 id（来自 /:id 或 scene 查到的那行）；null = 新录入
  const [rowId, setRowId] = useState<number | null>(pathId);
  const [loading, setLoading] = useState(!!pathId || !!sceneCode);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState(emptyForm);
  // 行状态：entering 时页面底部出「确认信息」按钮（扫码确认流程，见后端 _send_scan_redirect_card）
  const [status, setStatus] = useState('');
  const [confirming, setConfirming] = useState(false);

  const fillFromRow = useCallback((row: QrcodeItem) => {
    setForm({
      // 项目id 就是行 id（后端下发的 scene_str = str(id)），只读展示
      project_id: row.scene_str || '',
      project_code: row.project_code || '',
      project_name: row.project_name || '',
      project_location: row.project_location || '',
      customer_name: row.customer_name || '',
      vehicle_model: row.vehicle_model || '',
    });
    setRowId(row.id);
    setStatus(row.status || '');
  }, []);

  // 扫码进入（链接带 scene）且该行已 published：录入+确认都完成，不停留本页，
  // 直接去「我要摇人」——scene/openid 原样带过去，CallView 会按 scene 弹车体信息确认
  // （2026-09-30 用户口径）。管理端「编辑信息」链接不带 scene，走不到这里。
  const toCallIfPublished = useCallback((row: QrcodeItem): boolean => {
    if (!sceneCode || row.status !== 'published') return false;
    const qs = searchParams.toString();
    navigate(qs ? `/call?${qs}` : '/call', { replace: true });
    return true;
  }, [sceneCode, navigate, searchParams]);

  useEffect(() => {
    // 没带 scene 也没带 :id：管理端手动新建，空表单直接可填
    if (!sceneCode && !pathId) return;
    setLoading(true);

    (async () => {
      try {
        // 扫码进入优先按场景值查行（登录即可接口）。scene 即 str(id)：
        // 查到 → 编辑那一行；查不到且链接还带 :id → 退回按 id 查（管理端场景）；
        // 都没有 → 新录入（项目id 保存后自动生成，无需预填）
        if (sceneCode) {
          const row = await fetchQrcodeByScene(sceneCode);
          if (row) {
            if (toCallIfPublished(row)) return;
            fillFromRow(row);
            return;
          }
          if (!pathId) {
            setForm(emptyForm);
            setRowId(null);
            setStatus('');
            return;
          }
        }
        if (pathId) {
          const row = await fetchQrcode(pathId);
          if (toCallIfPublished(row)) return;
          fillFromRow(row);
        }
      } catch (err) {
        Toast({ message: `加载失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
      } finally {
        setLoading(false);
      }
    })();
  }, [sceneCode, pathId, fillFromRow, toCallIfPublished]);

  const setField = (key: keyof typeof emptyForm) => (value: unknown) =>
    setForm((prev) => ({ ...prev, [key]: String(value ?? '') }));

  const handleSubmit = async () => {
    const code = form.project_code.trim();
    const name = form.project_name.trim();
    if (!code) {
      Toast({ message: '请填写项目编号', theme: 'warning' });
      return;
    }
    if (!name) {
      Toast({ message: '请填写项目名', theme: 'warning' });
      return;
    }

    const fields = {
      project_code: code,
      project_name: name,
      project_location: form.project_location.trim(),
      customer_name: form.customer_name.trim(),
      vehicle_model: form.vehicle_model.trim(),
    };

    setSubmitting(true);
    try {
      // 项目id 不入参：编辑时 = 行 id 本身；新建时保存后由后端自动生成（str(id)）
      if (rowId) await updateProjectInfo(rowId, fields);
      else await createProjectInfo(fields);
      Toast({ message: '保存成功', theme: 'success' });
      navigate(-1);
    } catch (err) {
      Toast({ message: `保存失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  // 确认信息：扫码核对无误后确认即发布（录入信息行 entering → published，
  // 2026-09-30 用户口径；后端 confirm 接口直接给 published）。按钮随状态变化消失。
  const handleConfirm = async () => {
    if (!rowId) return;
    setConfirming(true);
    try {
      const updated = await qrcodeTransition(rowId, 'confirm');
      setStatus(updated.status || 'published');
      Toast({ message: '信息已确认，已发布', theme: 'success' });
    } catch (err) {
      Toast({ message: `确认失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setConfirming(false);
    }
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 16 }}>{rowId ? '编辑信息' : '录入信息'}</h4>
      <Form onSubmit={handleSubmit}>
        {/* 项目id = str(行 id)：恒只读——新建时保存后自动生成，编辑时显示行 id */}
        <FormItem label="项目id" name="project_id">
          <ClearableInput
            value={form.project_id}
            onChange={setField('project_id')}
            placeholder={rowId ? '' : '保存后自动生成'}
            maxlength={64}
            disabled
            showClear={false}
          />
        </FormItem>
        <FormItem label="项目编号" name="project_code" requiredMark>
          <ClearableInput
            value={form.project_code}
            onChange={setField('project_code')}
            placeholder="请输入项目编号"
            maxlength={64}
          />
        </FormItem>
        <FormItem label="项目名" name="project_name" requiredMark>
          <ClearableInput
            value={form.project_name}
            onChange={setField('project_name')}
            placeholder="请输入项目名"
            maxlength={128}
          />
        </FormItem>
        <FormItem label="项目地点" name="project_location">
          <ClearableInput
            value={form.project_location}
            onChange={setField('project_location')}
            placeholder="请输入项目地点"
            maxlength={128}
          />
        </FormItem>
        <FormItem label="客户名" name="customer_name">
          <ClearableInput
            value={form.customer_name}
            onChange={setField('customer_name')}
            placeholder="请输入客户名"
            maxlength={128}
          />
        </FormItem>
        <FormItem label="车型" name="vehicle_model">
          <ClearableInput
            value={form.vehicle_model}
            onChange={setField('vehicle_model')}
            placeholder="请输入车型"
            maxlength={128}
          />
        </FormItem>
        <FormItem>
          <Button theme="primary" block type="submit" loading={submitting}>
            保存
          </Button>
        </FormItem>
      </Form>

      {/* 扫码确认流程：状态机 entering 时（已生成 ticket、未确认）页面底部出「确认信息」，
          点击 → published（录入信息行确认即发布，不经 confirming） */}
      {rowId !== null && status === 'entering' && (
        <Button
          theme="primary"
          block
          loading={confirming}
          onClick={handleConfirm}
          style={{ marginTop: 12 }}
        >
          确认信息
        </Button>
      )}
    </div>
  );
}
