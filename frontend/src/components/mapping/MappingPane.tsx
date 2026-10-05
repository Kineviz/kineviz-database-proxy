import React from 'react';
import { Alert, Button, Card, Input, Modal, Select, Space, Spin, Tabs, Tag, Tooltip, Typography, message } from 'antd';
import { MappingProblem } from '../../types/project';
import { DraftState, emptyDraft, errorMessage, reduceDraft, toProblems } from './reducer';
import { MappingActions, createActions } from './actions';
import { projectService } from '../../services/projectService';
import ProblemsPanel from './ProblemsPanel';
import NodeList from './NodeList';
import LinkList from './LinkList';
import YamlPane from './YamlPane';
import Toolbar from './Toolbar';

const { Text, Paragraph } = Typography;

/**
 * The editor: three tabs over one draft.
 *
 * Records, Links and YAML are not three documents that need reconciling — they are views of
 * one object, and the YAML pane is that object as the proxy renders it. A save is one `PUT`
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
}

const MappingPane: React.FC<Props> = ({ project, databaseType }) => {
  const [draft, dispatch] = React.useReducer(reduceDraft, undefined, emptyDraft);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
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
  const [tab, setTab] = React.useState('records');
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

  return (
    <Card
      title={
        <Space>
          <Text strong>{project}</Text>
          <Tag>{databaseType.toUpperCase()}</Tag>
          {draft.dirty ? <Tag color="orange">unsaved</Tag> : <Tag color="green">saved</Tag>}
        </Space>
      }
      extra={
        <Toolbar
          draft={draft}
          dispatch={dispatch}
          actions={actions}
          saving={saving}
          onSave={save}
          onReload={reload}
          onReset={reset}
        />
      }
    >
      <Paragraph type="secondary" style={{ marginBottom: 12 }}>
        One document describes this schema as a graph: which tables are records, which are links, which
        column identifies a row, and which way round each link goes. Save is one call, and a mapping the
        proxy cannot load is stored and reported rather than refused — its problems name the control to fix.
      </Paragraph>

      {loading ? (
        <Spin style={{ display: 'block', margin: '40px auto' }} />
      ) : null}

      {failure ? (
        <Alert
          type="error"
          showIcon
          message="The mapping could not be read"
          description={
            <Space direction="vertical" size={6}>
              <Text style={{ fontSize: 12 }}>{failure}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                A relational project has to be able to open its database for this page to read a catalog.
                If the connection is wrong, fix it on the project before editing what it means.
              </Text>
              <Button size="small" onClick={() => load()}>
                Try again
              </Button>
            </Space>
          }
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {source === 'none' && !draft.dirty ? (
          <Alert
            type="info"
            showIcon
            message="Nothing is saved over auto-discovery yet"
            description={
              <Space direction="vertical" size={4}>
                <Text style={{ fontSize: 12 }}>
                  This page edits the override you have stored, and there is none. The graph you
                  see in GraphXR is whatever auto-discovery reads from the live schema each time it
                  is asked, so these empty tabs are not an empty graph.
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Re-discover above to see what this catalog would propose and edit that, or name
                  the records yourself — either way nothing is stored until you save.
                </Text>
              </Space>
            }
          />
        ) : null}

        <ProblemsPanel
          problems={draft.problems}
          warnings={draft.warnings}
          shownElsewhere={draft.problemIndex.rest.length === draft.problems.length ? 0 : countShown(draft)}
        />

        <Space wrap size={12} align="end">
          <Tooltip title="Every table below lives in this schema unless the row says otherwise. Empty means the engine's own default: dbo, public, or the database itself.">
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Schema
              </Text>
              <Input
                size="small"
                style={{ width: 180 }}
                defaultValue={draft.document.schema ?? ''}
                placeholder="engine default"
                onBlur={(event) =>
                  dispatch({ kind: 'level', patch: { schema: event.target.value.trim() || null } })
                }
              />
            </Space>
          </Tooltip>
          <Tooltip title="When this list is not empty, only the records named here are in the graph and everything else is out.">
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Include only
              </Text>
              <Select
                mode="multiple"
                size="small"
                style={{ minWidth: 260 }}
                value={draft.document.include ?? []}
                options={declared}
                placeholder="everything"
                onChange={(value: string[]) => dispatch({ kind: 'level', patch: { include: value } })}
              />
            </Space>
          </Tooltip>
          <Tooltip title="Records out of the graph by name — the short form of unticking one.">
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Exclude
              </Text>
              <Select
                mode="multiple"
                size="small"
                style={{ minWidth: 220 }}
                value={draft.document.exclude ?? []}
                options={declared}
                placeholder="nothing"
                onChange={(value: string[]) => dispatch({ kind: 'level', patch: { exclude: value } })}
              />
            </Space>
          </Tooltip>
        </Space>

        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            {
              key: 'records',
              label: `Records (${(draft.document.nodes ?? []).length})`,
              children: <NodeList project={project} draft={draft} dispatch={dispatch} actions={actions} />,
            },
            {
              key: 'links',
              label: `Links (${(draft.document.edges ?? []).length})`,
              children: <LinkList project={project} draft={draft} dispatch={dispatch} actions={actions} />,
            },
            {
              key: 'yaml',
              label: 'YAML',
              children: <YamlPane draft={draft} dispatch={dispatch} actions={actions} />,
            },
          ]}
        />
      </Space>
    </Card>
  );
};

/** How many of the current problems already sit under a control that shows them. */
const countShown = (draft: DraftState): number => draft.problems.length - draft.problemIndex.rest.length;

export default MappingPane;
