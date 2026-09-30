// 录入信息 —— 「项目管理 → 新建项目 → 录入信息」进入的项目信息登记页。
//
// 六个字段（2026-09-29 用户口径）：项目id / 项目编号 / 项目名 / 项目地点 / 客户名 / 车型。
// 一条录入 = wechat_qrcodes 里的一行（六个字段和行 id 同行存）：
//   创建 POST /qrcodes/project-info；点条目回到本页（/admin/info-entry/:id）修改走
//   PUT /qrcodes/{id}/project-info。保存后该行在二维码管理里可见、可继续生成 ticket。
//
// 扫码确认流程：该行 ticket 生成后状态是 entering，此时扫这张码会跳转到本页
//   （后端 _send_scan_redirect_card 按状态分流），页面底部出现「确认信息」按钮，
//   点击走 entering → confirming（同二维码管理的「确认」）。
//
// 规则（界面不写注解，由交互体现）：
// - 项目id：唯一；已有值时输入框锁定（不可改），企微表格同步来之前可以先留空、之后补填一次
// - 项目编号：唯一，可改；与其他录入行重复时后端 400，detail 直接 Toast 出来
// - 项目名：必填（列表/预览里码记录名跟随它）
// - 项目地点 / 客户名 / 车型：自由填写，可留空
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Form, FormItem, Loading, Toast } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createProjectInfo, fetchQrcode, qrcodeTransition, updateProjectInfo } from '@/api/qrcode';

const errMsg = (err: unknown, fallback: string) =>
  err instanceof Error && err.message ? err.message : fallback;

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
  const numId = id ? Number(id) : NaN;
  const qid = Number.isInteger(numId) && numId > 0 ? numId : null;

  const [loading, setLoading] = useState(!!qid);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState(emptyForm);
  // 已保存过的项目id：锁输入框（唯一不可改）；空值可补填一次
  const [lockedProjectId, setLockedProjectId] = useState(false);
  // 行状态：entering 时页面底部出「确认信息」按钮（扫码确认流程，见后端 _send_scan_redirect_card）
  const [status, setStatus] = useState('');
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!qid) return;
    setLoading(true);
    fetchQrcode(qid)
      .then((data) => {
        setForm({
          project_id: data.project_id || '',
          project_code: data.project_code || '',
          project_name: data.project_name || '',
          project_location: data.project_location || '',
          customer_name: data.customer_name || '',
          vehicle_model: data.vehicle_model || '',
        });
        setLockedProjectId(!!data.project_id);
        setStatus(data.status || '');
      })
      .catch((err) => Toast({ message: `加载失败：${errMsg(err, '请稍后重试')}`, theme: 'error' }))
      .finally(() => setLoading(false));
  }, [qid]);

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

    // 项目id：锁定（已有值）或留空时不提交——后端只接受「空 → 有」的补填
    const pid = form.project_id.trim();
    const payload = {
      project_code: code,
      project_name: name,
      project_location: form.project_location.trim(),
      customer_name: form.customer_name.trim(),
      vehicle_model: form.vehicle_model.trim(),
      ...(pid && !(qid && lockedProjectId) ? { project_id: pid } : {}),
    };

    setSubmitting(true);
    try {
      if (qid) await updateProjectInfo(qid, payload);
      else await createProjectInfo(payload);
      Toast({ message: '保存成功', theme: 'success' });
      navigate(-1);
    } catch (err) {
      Toast({ message: `保存失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  // 确认信息：扫码核对无误后进入 confirming（确认后按钮消失；要回退可在二维码管理里操作）
  const handleConfirm = async () => {
    if (!qid) return;
    setConfirming(true);
    try {
      await qrcodeTransition(qid, 'confirm');
      setStatus('confirming');
      Toast({ message: '信息已确认', theme: 'success' });
    } catch (err) {
      Toast({ message: `确认失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setConfirming(false);
    }
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 16 }}>{qid ? '编辑信息' : '录入信息'}</h4>
      <Form onSubmit={handleSubmit}>
        <FormItem label="项目id" name="project_id">
          <ClearableInput
            value={form.project_id}
            onChange={setField('project_id')}
            placeholder="请输入项目id"
            maxlength={64}
            disabled={lockedProjectId}
            showClear={!lockedProjectId}
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
          点击 → confirming（同二维码管理的「确认」动作） */}
      {qid !== null && status === 'entering' && (
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
