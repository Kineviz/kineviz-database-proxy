import React from 'react';
import { Alert, Button, Input, Space, Spin, Typography, message } from 'antd';
import { CheckCircleOutlined, CopyOutlined, EditOutlined, ReloadOutlined } from '@ant-design/icons';
import { MappingAction, DraftState, errorMessage, toProblems } from './reducer';
import { MappingActions, textOf } from './actions';

const { Text, Paragraph } = Typography;

/**
 * The draft as the proxy renders it, and the way back in.
 *
 * A person who has just clicked through forty checkboxes wants to see the thing they
 * described, and a person who has a reviewed file wants to paste it. Both go through the
 * server: the browser is given text and returns text, and the one program that turns
 * structure into YAML and YAML back into structure is the proxy, which is also the one that
 * reads a pasted file and names whatever will not load about it. So there is no second parser
 * here to keep in step with `pyyaml`, and no half-written file the editor believes is fine.
 *
 * Text is never applied on a keystroke. Pasting replaces the whole draft, so it is a
 * deliberate act — and the deliberate act is only offered once `POST …/validate` has said
 * what it thinks.
 */

interface Props {
  draft: DraftState;
  dispatch: React.Dispatch<MappingAction>;
  actions: MappingActions;
  /** A pasted or uploaded file lands here, so the page can say where it came from. */
  note?: string | null;
  format?: 'json' | 'yaml';
}

const YamlPane: React.FC<Props> = ({ draft, dispatch, actions, note, format = 'yaml' }) => {
  const [text, setText] = React.useState('');
  const [original, setOriginal] = React.useState('');
  const [editing, setEditing] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [problems, setProblems] = React.useState<string[]>([]);
  const [checked, setChecked] = React.useState(false);
  // Whether this proxy answers the parsed text alongside a validation. The spec asks the pane
  // to show the *draft* as text, and only the proxy can render it, so the pane asks for it and
  // falls back to the stored text when the answer carries none.
  const [live, setLive] = React.useState<boolean | null>(null);

  const fetchStored = React.useCallback(async () => {
    setLoading(true);
    try {
      const answer = textOf(await actions.text(format));
      setText(answer);
      setOriginal(answer);
      setProblems([]);
      setChecked(false);
    } catch (caught: unknown) {
      setProblems([errorMessage(caught)]);
    } finally {
      setLoading(false);
    }
  }, [actions, format]);

  // The draft's own text, asked of the proxy: what the clicks describe, before anything is saved.
  React.useEffect(() => {
    if (live === false || editing) return;
    let cancelled = false;
    const ask = async () => {
      try {
        const answer = await actions.validate(draft.document);
        if (cancelled) return;
        const rendered = format === 'json' ? answer.json : answer.text;
        if (typeof rendered === 'string') {
          setLive(true);
          setText(rendered);
          setOriginal(rendered);
          setProblems(toProblems(answer.problems).map((problem) => `${problem.path ? `${problem.path}: ` : ''}${problem.message}`));
          setChecked(true);
        } else {
          setLive(false);
        }
      } catch (caught: unknown) {
        if (cancelled) return;
        // A draft the proxy will not render is the draft's own news, not a pane failure.
        setLive(false);
        setProblems([errorMessage(caught)]);
        setChecked(true);
      }
    };
    ask();
    return () => {
      cancelled = true;
    };
  }, [actions, draft.document, live, editing, format]);

  React.useEffect(() => {
    if (live === false && !text) fetchStored();
  }, [live, text, fetchStored]);

  const check = async () => {
    setBusy(true);
    try {
      const answer = await actions.parseText(text);
      const found = toProblems(answer.problems);
      setProblems(found.map((problem) => `${problem.path ? `${problem.path}: ` : ''}${problem.message}`));
      setChecked(true);
      if (!found.length) message.success('The proxy reads this document as it stands');
    } catch (caught: unknown) {
      setProblems([errorMessage(caught)]);
      setChecked(true);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    setBusy(true);
    try {
      const answer = await actions.parseText(text);
      const found = toProblems(answer.problems);
      if (found.length) {
        setProblems(found.map((problem) => `${problem.path ? `${problem.path}: ` : ''}${problem.message}`));
        message.error('That text still has problems, so the tabs are unchanged');
        return;
      }
      // The tags travel with the document this answer installed, the same pair `/validate`
      // replies with: pasting someone else's file must not leave this screen's old badges
      // sitting on rows that came from it.
      dispatch({ kind: 'replace', document: answer.mapping ?? null, provenance: answer.provenance });
      setEditing(false);
      message.success('The structured tabs now show what you pasted');
    } catch (caught: unknown) {
      setProblems([errorMessage(caught)]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      {note ? (
        <Alert type="info" showIcon message={note} />
      ) : null}

      <Alert
        type="info"
        showIcon
        message={
          live === false
            ? 'This is the mapping that is stored. Your unsaved edits appear here once they are saved.'
            : 'This is how the proxy reads the draft on screen, right now.'
        }
        description={
          <Text type="secondary" style={{ fontSize: 12 }}>
            View or paste a JSON or YAML mapping. Validate the text before applying it to the draft.
            {draft.dirty ? ' The draft and the stored mapping are not the same thing yet.' : ''}
          </Text>
        }
      />

      <Space wrap size={6}>
        <Button size="small" icon={<CopyOutlined />} onClick={() => {
          navigator.clipboard
            .writeText(text)
            .then(() => message.success(`${format.toUpperCase()} copied`))
            .catch(() => message.error('Could not reach the clipboard'));
        }}>
          Copy
        </Button>
        <Button
          size="small"
          icon={<EditOutlined />}
          type={editing ? 'primary' : 'default'}
          onClick={() => setEditing((on) => !on)}
        >
          {editing ? 'Stop editing' : 'Edit as text'}
        </Button>
        <Button size="small" icon={<ReloadOutlined />} onClick={live === false ? fetchStored : () => setLive(null)}>
          {live === false ? 'Reload stored text' : 'Re-read the draft'}
        </Button>
        {text !== original ? (
          <Button size="small" onClick={() => { setText(original); setProblems([]); setChecked(false); }}>
            Undo text edits
          </Button>
        ) : null}
      </Space>

      <Spin spinning={loading}>
        <Input.TextArea
          value={text}
          onChange={(event) => { setText(event.target.value); setChecked(false); }}
          readOnly={!editing}
          spellCheck={false}
          autoSize={{ minRows: 14, maxRows: 30 }}
          style={{ fontFamily: 'Consolas, Menlo, monospace', fontSize: 12 }}
          placeholder="Paste a mapping here, or click Edit as text."
        />
      </Spin>

      {editing ? (
        <Space wrap size={6}>
          <Button icon={<CheckCircleOutlined />} loading={busy} onClick={check}>
            Validate this text
          </Button>
          <Button type="primary" loading={busy} onClick={apply}>
            Replace the draft with it
          </Button>
          <Paragraph type="secondary" style={{ fontSize: 12, margin: 0 }}>
            Replacing discards the structured edits that are not in this text.
          </Paragraph>
        </Space>
      ) : null}

      {checked && problems.length ? (
        <Alert
          type="error"
          showIcon
          message={`${problems.length} ${problems.length === 1 ? 'problem' : 'problems'} in this text`}
          description={
            <Space direction="vertical" size={4}>
              {problems.slice(0, 10).map((problem, index) => (
                <Text key={index} style={{ fontSize: 12, fontFamily: 'Consolas, Menlo, monospace' }}>
                  {problem}
                </Text>
              ))}
              {problems.length > 10 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  …and {problems.length - 10} more
                </Text>
              ) : null}
            </Space>
          }
        />
      ) : null}
      {checked && !problems.length ? (
        <Alert type="success" showIcon message="The proxy reads this document with nothing to report." />
      ) : null}
    </Space>
  );
};

export default YamlPane;
