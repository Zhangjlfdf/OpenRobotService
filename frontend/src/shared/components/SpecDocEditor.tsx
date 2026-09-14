/**
 * 问题文档编辑器（md 在线编辑）。
 *
 * - 全屏 Popup + `@uiw/react-md-editor`（成熟库）
 * - 微信窄屏：编辑 / 预览 Tab 切换（不分栏），规避横向滚动与失焦
 * - 受控：父级传 initialValue，点保存回调 onSave(content)
 */
import { useEffect, useState } from 'react';
import { Popup } from 'tdesign-mobile-react';
import MDEditor from '@uiw/react-md-editor';
import '@uiw/react-md-editor/markdown-editor.css';
import '@uiw/react-markdown-preview/markdown.css';
import '@/shared/styles/specDoc.css';

interface SpecDocEditorProps {
  visible: boolean;
  title?: string;
  initialValue: string;
  saving?: boolean;
  onClose: () => void;
  /** 返回 Promise 时，期间按钮进入 saving 态由父级控制 */
  onSave: (content: string) => void | Promise<void>;
}

export default function SpecDocEditor({
  visible,
  title = '问题文档',
  initialValue,
  saving = false,
  onClose,
  onSave,
}: SpecDocEditorProps) {
  const [content, setContent] = useState(initialValue);
  const [mode, setMode] = useState<'edit' | 'preview'>('edit');

  // 每次打开重置为传入内容与编辑态
  useEffect(() => {
    if (visible) {
      setContent(initialValue);
      setMode('edit');
    }
  }, [visible, initialValue]);

  return (
    <Popup
      visible={visible}
      onClose={onClose}
      placement="bottom"
      showOverlay
      closeOnOverlayClick={false}
      style={{ zIndex: 13000 }}
    >
      <div className="spec-editor" data-color-mode="light">
        <div className="spec-editor__head">
          <span className="spec-editor__title">{title}</span>
          <button type="button" className="spec-editor__close" onClick={onClose} aria-label="关闭">
            关闭
          </button>
        </div>

        <div className="spec-editor__tabs">
          <button
            type="button"
            className={`spec-editor__tab${mode === 'edit' ? ' is-active' : ''}`}
            onClick={() => setMode('edit')}
          >
            编辑
          </button>
          <button
            type="button"
            className={`spec-editor__tab${mode === 'preview' ? ' is-active' : ''}`}
            onClick={() => setMode('preview')}
          >
            预览
          </button>
        </div>

        <div className="spec-editor__body">
          <MDEditor
            value={content}
            onChange={(v) => setContent(v || '')}
            preview={mode}
            height="100%"
            visibleDragbar={false}
          />
        </div>

        <div className="spec-editor__btns">
          <button
            type="button"
            className="spec-editor__btn spec-editor__btn--cancel"
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="button"
            className="spec-editor__btn spec-editor__btn--confirm"
            onClick={() => onSave(content)}
            disabled={saving}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </Popup>
  );
}
