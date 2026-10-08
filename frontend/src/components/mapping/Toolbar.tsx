import React from 'react';
import {
  Alert,
  Button,
  Divider,
  Drawer,
  Dropdown,
  Modal,
  Space,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import {
  CloudUploadOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  ReloadOutlined,
  SaveOutlined,
  ScanOutlined,
  MoreOutlined,
  CheckCircleOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { MappingAction, DraftState, errorMessage, toProblems } from './reducer';
import { MappingActions, textOf } from './actions';

const { Text, Paragraph } = Typography;

/** A mapping file is read as UTF-8 text and capped at the size the proxy itself refuses. */
const MAX_MAPPING_BYTES = 1024 * 1024;

/**
 * The file actions beside the structured and text tabs.
 *
 * Upload and paste both send text to the proxy and take a parsed document back; Download and
 * Example both carry served text. Nothing here turns a document into YAML or YAML into a
 * document, so there is no second parser to drift from `pyyaml`, and no file that will not
 * load is ever quietly applied: the proxy's own problems are shown, and the draft is left
 * alone.
 */

interface Props {
  draft: DraftState;
  dispatch: React.Dispatch<MappingAction>;
  actions: MappingActions;
  saving: boolean;
  checking: boolean;
  onValidate: () => Promise<void>;
  onSave: () => Promise<boolean>;
  onReload: () => void;
  onReset: () => void;
}

const showProblems = (problems: string[], onApply: (() => void) | null): void => {
  Modal[problems.length ? 'warning' : 'confirm']({
    title: problems.length ? 'That file will not load' : 'That file reads cleanly',
    content: (
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        {problems.slice(0, 12).map((problem, index) => (
          <Text key={index} style={{ fontSize: 12, fontFamily: 'Consolas, Menlo, monospace' }}>
            {problem}
          </Text>
        ))}
        {problems.length > 12 ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            …and {problems.length - 12} more
          </Text>
        ) : null}
        {problems.length ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            Nothing was applied. A file that will not load is reported rather than saved, so the
            mapping you have is still the one that loads.
          </Text>
        ) : null}
      </Space>
    ),
    okText: problems.length ? 'See the problems' : 'Apply to the draft',
    cancelText: 'Cancel',
    onOk: problems.length ? undefined : onApply ?? undefined,
  });
};

const Toolbar: React.FC<Props> = ({ draft, dispatch, actions, saving, checking, onValidate, onSave, onReload, onReset }) => {
  const [busy, setBusy] = React.useState<string | null>(null);
  const [example, setExample] = React.useState<string | null>(null);
  const [exampleOpen, setExampleOpen] = React.useState(false);
  // The sentences `/discover` answered beside the proposal now on the screen; they live in the
  // draft, not here, so they cannot outlive it.
  const leftOut = draft.proposal?.leftOut ?? [];

  /** Read the dropped file as text, then let the proxy say what it is. */
  const readFile: UploadProps['beforeUpload'] = (file) => {
    if (file.size > MAX_MAPPING_BYTES) {
      message.error(`That file is ${Math.round(file.size / 1024)} KB — the proxy reads at most 1 MiB`);
      return Upload.LIST_IGNORE;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      const text = String(reader.result ?? '');
      if (!text.trim()) {
        message.error('That file is empty');
        return;
      }
      setBusy('upload');
      try {
        const answer = await actions.parseText(text);
        const problems = toProblems(answer.problems).map(
          (problem) => `${problem.path ? `${problem.path}: ` : ''}${problem.message}`,
        );
        showProblems(problems, () => {
          dispatch({ kind: 'replace', document: answer.mapping ?? null, provenance: answer.provenance });
          message.success('Applied to the draft — it is not saved yet');
        });
      } catch (caught: unknown) {
        showProblems([errorMessage(caught)], null);
      } finally {
        setBusy(null);
      }
    };
    reader.onerror = () => message.error('The file could not be read');
    reader.readAsText(file as File, 'utf-8');
    // The file never reaches the proxy as a body: the text it carries goes to validate.
    return false;
  };

  const download = async (format: 'json' | 'yaml'): Promise<void> => {
    setBusy('download');
    try {
      const write = async (): Promise<void> => {
        const answer = textOf(await actions.text(format));
        if (!answer) {
          message.info('There is no override stored yet — this project is on pure auto-discovery');
          return;
        }
        const blob = new Blob([answer], {
          type: format === 'json' ? 'application/json;charset=utf-8' : 'application/x-yaml;charset=utf-8',
        });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `graph-mapping.${format === 'json' ? 'json' : 'yml'}`;
        link.click();
        URL.revokeObjectURL(url);
      };
      if (draft.dirty) {
        // Downloading is the proxy's `to_yaml`, and the proxy only renders what it holds.
        Modal.confirm({
          title: 'Your draft has unsaved changes',
          content: (
            <Text style={{ fontSize: 12 }}>
              The file is written by the proxy from the mapping it stores, so saving first is what makes
              the download match this page.
            </Text>
          ),
          okText: 'Save, then download',
          cancelText: 'Download what is stored',
          onOk: async () => {
            if (await onSave()) await write();
          },
          onCancel: () => {
            write();
          },
        });
        return;
      }
      await write();
    } catch (caught: unknown) {
      message.error(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  };

  const openExample = async (): Promise<void> => {
    setBusy('example');
    try {
      setExample(textOf(await actions.example()));
      setExampleOpen(true);
    } catch (caught: unknown) {
      message.error(`The example could not be read: ${errorMessage(caught)}`);
    } finally {
      setBusy(null);
    }
  };

  const discover = async (): Promise<void> => {
    setBusy('discover');
    try {
      const answer = await actions.discover();
      // One action carries the whole proposal: the document, and the reasoning each of its
      // entries arrived with, which is what the note fields are compared against from now on.
      dispatch({
        kind: 'proposal',
        document: answer.mapping ?? null,
        provenance: answer.provenance,
        leftOut: toProblems(answer.warnings),
      });
      // The proposal is a draft to review, not a save: the person has to agree to it.
      message.success('Discovery proposed a mapping. Nothing is stored until you save it.');
    } catch (caught: unknown) {
      message.error(`Discovery failed: ${errorMessage(caught)}`);
    } finally {
      setBusy(null);
    }
  };

  const rediscover = () => {
    if (!draft.dirty) { discover(); return; }
    Modal.confirm({ title: 'Replace the unsaved draft?', content: 'Discovery will replace the edits on this page.',
      okText: 'Discover and replace', onOk: discover });
  };

  return (
    <>
      <div className="mapping-toolbar">
        <div className="mapping-toolbar-files">
          <Upload beforeUpload={readFile} showUploadList={false} accept=".yml,.yaml,.json" maxCount={1}>
            <Button icon={<CloudUploadOutlined />} loading={busy === 'upload'}>Import mapping</Button>
          </Upload>
          <Dropdown menu={{ items: [
            { key: 'json', label: 'Download JSON' }, { key: 'yaml', label: 'Download YAML' },
          ], onClick: ({ key }) => { download(key as 'json' | 'yaml'); } }}>
            <Button icon={<DownloadOutlined />} loading={busy === 'download'}>Export</Button>
          </Dropdown>
          <Button icon={<ScanOutlined />} loading={busy === 'discover'} onClick={rediscover}>Discover tables</Button>
          <Dropdown trigger={['click']} menu={{ items: [
            { key: 'example', label: 'View YAML example', icon: <ExperimentOutlined /> },
            { key: 'reload', label: 'Reload saved mapping', icon: <ReloadOutlined /> },
            { type: 'divider' },
            { key: 'reset', label: 'Reset to auto-discovery', icon: <UndoOutlined />, danger: true },
          ], onClick: ({ key }) => { if (key === 'example') openExample(); else if (key === 'reload') onReload(); else if (key === 'reset') onReset(); } }}>
            <Button icon={<MoreOutlined />} aria-label="More mapping actions" />
          </Dropdown>
        </div>
        <div className="mapping-toolbar-save">
          {draft.dirty ? <Button type="text" onClick={() => dispatch({ kind: 'discard' })}>Discard</Button> : null}
          <Button icon={<CheckCircleOutlined />} loading={checking} onClick={onValidate}>Validate</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!draft.dirty} onClick={onSave}>Save mapping</Button>
        </div>
      </div>

      <Drawer
        title="The example the proxy ships"
        width={720}
        open={exampleOpen}
        onClose={() => setExampleOpen(false)}
        extra={
          <Space>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                navigator.clipboard?.writeText(example ?? '').then(
                  () => message.success('Copied'),
                  () => message.error('Could not reach the clipboard'),
                );
              }}
            >
              Copy
            </Button>
          </Space>
        }
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          Served from inside the installed package, not copied into this page, so what you read here is the
          file the proxy will accept. Its comments are the ones a person needs — what `key` is for, why a
          link wants its own key, how `flip` reads — and comments do not survive a save, because the stored
          form is the document.
        </Paragraph>
        <pre
          style={{
            background: '#141414',
            color: '#e6e6e6',
            padding: 12,
            borderRadius: 4,
            fontSize: 12,
            overflow: 'auto',
            maxHeight: '70vh',
          }}
        >
          {example ?? '—'}
        </pre>
      </Drawer>

      {leftOut.length ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 8 }}
          message="What discovery left out of this proposal"
          description={
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              {leftOut.slice(0, 6).map((warning, at) => (
                <Text key={at} style={{ fontSize: 12 }}>
                  {warning.message}
                </Text>
              ))}
              {leftOut.length > 6 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  …and {leftOut.length - 6} more
                </Text>
              ) : null}
              <Text type="secondary" style={{ fontSize: 12 }}>
                So this is not the whole schema. Name what you want with include, or exclude what
                you do not.
              </Text>
            </Space>
          }
        />
      ) : null}
    </>
  );
};

export default Toolbar;
