import React from 'react';
import { Alert, Button, Drawer, Input, Modal, Select, Space, Spin, Tabs, Tag, Typography, message } from 'antd';
import { MappingProblem } from '../../types/project';
import { DraftState, emptyDraft, errorMessage, reduceDraft, toProblems } from './reducer';
import { MappingActions, createActions } from './actions';
import { projectService } from '../../services/projectService';
import ProblemsPanel from './ProblemsPanel';
import NodeList from './NodeList';
import LinkList from './LinkList';
import YamlPane from './YamlPane';
import Toolbar from './Toolbar';
import VisualMapping from './VisualMapping';
import { SettingOutlined, ApartmentOutlined, TableOutlined, CodeOutlined } from '@ant-design/icons';

const { Text, Paragraph } = Typography;

/**
 * The editor: structured and text views over one draft.
 *
 * Records, Links, JSON and YAML are views of one object. Text panes show that
 * object as the proxy renders it. A save is one `PUT`
 * of the whole draft, so there is no order of operations to get wrong and no half-applied
 * edit. The proxy answers a save with two facts and they are not the same one: whether it
 * stored the document, and whether that document loads.
 *
 * What comes back from `GET /mapping` is the **override** this project has saved — not the
 * effective mapping, and it reaches no database to find out what discovery would say. So an
 * empty editor is not an empty graph: it says nobody has overridden anything yet, and the page
 * says so rather than leaving that to be inferred from zero rows.
 */

interface Props {
  project: string;
  /** The database type, so the header can say what kind of schema it is reading. */
  databaseType: string;
  onDirtyChange?: (dirty: boolean) => void;
}

const MappingPane: React.FC<Props> = ({ project, databaseType, onDirtyChange }) => {
  const [draft, dispatch] = React.useReducer(reduceDraft, undefined, emptyDraft);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [checking, setChecking] = React.useState(false);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  React.useEffect(() => { onDirtyChange?.(draft.dirty); }, [draft.dirty, onDirtyChange]);
  const [failure, setFailure] = React.useState<string | null>(null);
  /**
   * Whether an override is stored, as `GET /mapping` says it: `override` or `none`.
   *
   * `none` is said out loud rather than answered by loading discovery, because opening this
   * page would then depend on the customer's server — the one thing that route is built not to
   * do — and a machine's proposal would sit on the screen as if a person had accepted it. The
   * proposal stays behind the button that makes it: Re-discover.
   */
  const [source, setSource] = React.useState<string | null>(null);
  const [tab, setTab] = React.useState('visual');
  const [actions, setActions] = React.useState<MappingActions>(() => createActions(project));

  React.useEffect(() => {
    setActions(createActions(project));
  }, [project]);

  const load = React.useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const answer = await projectService.getMapping(project);
        setSource(answer.source ?? null);
        dispatch({
          kind: 'load',
          document: answer.mapping ?? null,
          provenance: answer.provenance,
          problems: toProblems(answer.problems),
          warnings: toProblems(answer.warnings),
        });
        setFailure(null);
      } catch (caught: unknown) {
        setFailure(errorMessage(caught));
      } finally {
        setLoading(false);
      }
    },
    [project],
  );

  React.useEffect(() => {
    load();
  }, [load]);

  // An unsaved draft is work. A reload or a closed tab must not be what teaches that.
  React.useEffect(() => {
    if (!draft.dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = true;
      return true;
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [draft.dirty]);

  const save = async (): Promise<boolean> => {
    setSaving(true);
    try {
      const answer = await actions.save(draft.document);
      const problems = toProblems(answer.problems);
      dispatch({
        kind: 'saved',
        document: answer.mapping ?? draft.document,
        provenance: answer.provenance,
        problems,
        warnings: toProblems(answer.warnings),
      });
      // `saved` and `valid` are two facts and the route answers both: a mapping that will not
      // load is *still stored*, because a record with no key is a legal thing to keep while
      // discovery fills it in. A PUT that reached this line stored — a write that fails
      // raises and is answered in the catch below — so the only save-time news is the
      // warning about a document that will not load.
      setSource('override');
      if (problems.length) {
        const count = `${problems.length} ${problems.length === 1 ? 'problem' : 'problems'}`;
        message.warning(
          `Saved — but this mapping will not load (${count}). Fix them and save again; GraphXR refuses the schema until it does.`,
        );
        setTab('records');
        return false;
      }
      message.success('Mapping saved. GraphXR will read this schema the way you described it.');
      return true;
    } catch (caught: unknown) {
      // A refusal is answered by the proxy with paths, so the controls light up rather than
      // leaving a person to find which of forty boxes the server meant.
      const detail = (caught as { response?: { data?: unknown } })?.response?.data;
      const shaped = detail && typeof detail === 'object' ? (detail as { problems?: unknown }) : null;
      const problems = shaped ? toProblems(shaped.problems as (MappingProblem | string)[] | undefined) : [];
      dispatch({ kind: 'problems', problems });
      message.error(problems.length ? `Not saved — ${problems.length} problems` : errorMessage(caught));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const reload = (): void => {
    if (!draft.dirty) {
      load();
      return;
    }
    Modal.confirm({
      title: 'Reload discards your draft',
      content: <Text style={{ fontSize: 12 }}>The stored mapping will replace everything on this page.</Text>,
      okText: 'Discard and reload',
      okButtonProps: { danger: true },
      cancelText: 'Keep editing',
      onOk: () => load(),
    });
  };

  const reset = (): void => {
    Modal.confirm({
      title: 'Reset to auto-discovery?',
      content: (
        <Space direction="vertical" size={6}>
          <Text style={{ fontSize: 12 }}>
            The override is dropped and the graph is whatever the foreign keys say it is. Every rename,
            flip, exclusion and hand-stated link in this draft goes with it.
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Download first if you want to keep this version.
          </Text>
        </Space>
      ),
      okText: 'Drop the override',
      okButtonProps: { danger: true },
      cancelText: 'Cancel',
      onOk: async () => {
        try {
          await actions.reset();
          message.success('Override dropped — the project is on pure auto-discovery');
          load(true);
        } catch (caught: unknown) {
          message.error(errorMessage(caught));
        }
      },
    });
  };

  const declared = (draft.document.nodes ?? []).map((node) => ({ value: node.label }));
  const validate = async () => {
    setChecking(true);
    try {
      const answer = await actions.validate(draft.document);
      const problems = toProblems(answer.problems);
      dispatch({ kind: 'problems', problems, warnings: toProblems(answer.warnings) });
      if (problems.length) message.warning(`${problems.length} mapping problems to review`);
      else message.success('Mapping validated. No structural problems found.');
    } catch (caught: unknown) { message.error(errorMessage(caught)); }
    finally { setChecking(false); }
  };

  return (
    <section className="mapping-workbench">
      <div className="mapping-workbench-heading">
        <div className="mapping-project-identity">
          <h2>{project}</h2>
          <Tag>{databaseType.toUpperCase()}</Tag>
          <Tag color={draft.dirty ? 'orange' : 'green'}>{draft.dirty ? 'Unsaved changes' : 'Saved'}</Tag>
        </div>
        <Button icon={<SettingOutlined />} onClick={() => setSettingsOpen(true)}>Mapping settings</Button>
      </div>
      <Toolbar draft={draft} dispatch={dispatch} actions={actions} saving={saving} checking={checking}
        onValidate={validate} onSave={save} onReload={reload} onReset={reset} />
      <div className="mapping-summary-strip">
        <span><strong>{draft.document.nodes?.length ?? 0}</strong> node types</span>
        <span><strong>{draft.document.edges?.length ?? 0}</strong> relationship types</span>
        <span className="mapping-summary-schema">Schema: {draft.document.schema || 'connection default'}</span>
        <span className="mapping-summary-hint">Select an element to configure its mapping</span>
      </div>
      {failure ? <div className="mapping-notice"><Alert type="error" showIcon message="Could not read the mapping" description={failure}
        action={<Button size="small" onClick={() => load()}>Try again</Button>} /></div> : null}
      {source === 'none' && !draft.dirty && !loading ? <div className="mapping-notice"><Alert type="info" showIcon
        message="Start with your database schema" description="Discover tables to create a draft, upload a JSON/YAML mapping, or add nodes below. Save when you are ready." /></div> : null}
      {draft.problems.length || draft.warnings.length ? <div className="mapping-notice">
        <ProblemsPanel problems={draft.problems} warnings={draft.warnings} />
      </div> : null}
      <Spin spinning={loading}>
        <Tabs className="mapping-workbench-tabs" activeKey={tab} onChange={setTab} items={[
          { key: 'visual', label: <span><ApartmentOutlined /> Visual mapping</span>, children:
            <VisualMapping project={project} draft={draft} dispatch={dispatch} actions={actions} /> },
          { key: 'records', label: <span><TableOutlined /> Nodes</span>, children: <div className="mapping-table-view">
            <NodeList project={project} draft={draft} dispatch={dispatch} actions={actions} /></div> },
          { key: 'links', label: <span><TableOutlined /> Relationships</span>, children: <div className="mapping-table-view">
            <LinkList project={project} draft={draft} dispatch={dispatch} actions={actions} /></div> },
          { key: 'json', label: <span><CodeOutlined /> JSON</span>, children: <div className="mapping-text-view">
            <YamlPane draft={draft} dispatch={dispatch} actions={actions} format="json" /></div> },
          { key: 'yaml', label: <span><CodeOutlined /> YAML</span>, children: <div className="mapping-text-view">
            <YamlPane draft={draft} dispatch={dispatch} actions={actions} /></div> },
        ]} />
      </Spin>
      <Drawer title="Mapping settings" open={settingsOpen} onClose={() => setSettingsOpen(false)} width={360}>
        <div className="mapping-settings-form">
          <label>Default schema</label>
          <Input key={draft.document.schema ?? ''} defaultValue={draft.document.schema ?? ''} placeholder="Use connection default"
            onBlur={(event) => dispatch({ kind: 'level', patch: { schema: event.target.value.trim() || null } })} />
          <Paragraph type="secondary">Used for every table that does not specify its own schema.</Paragraph>
          <label>Include only</label>
          <Select mode="multiple" value={draft.document.include ?? []} options={declared} placeholder="Include all mappings"
            onChange={(value: string[]) => dispatch({ kind: 'level', patch: { include: value } })} />
          <label>Exclude</label>
          <Select mode="multiple" value={draft.document.exclude ?? []} options={declared} placeholder="No exclusions"
            onChange={(value: string[]) => dispatch({ kind: 'level', patch: { exclude: value } })} />
          <Paragraph type="secondary">Excluded mappings stay in the document and are left out of the graph.</Paragraph>
        </div>
      </Drawer>
    </section>
  );
};

export default MappingPane;
