import React from 'react';
import {
  Button,
  Checkbox,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { DeleteOutlined, PlusOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { GraphMappingDocument, MappingNode, MappingProblem } from '../../types/project';
import {
  DraftState,
  MappingAction,
  PROVENANCE_COLOR,
  linksDependingOn,
  problemField,
  proposalNote,
  provenanceTag,
  toProblems,
} from './reducer';
import { MappingActions } from './actions';
import { ColumnPicker, TablePicker } from './TablePicker';
import { ProblemTags } from './ProblemsPanel';
import PropertyEditor from './PropertyEditor';
import RowEstimate from './RowEstimate';

const { Text } = Typography;

/**
 * The records this schema is read as — one row per node label.
 *
 * Each row is the four decisions that make a table a record: what GraphXR calls it, which
 * table it comes from, which single column identifies a row, and which columns are visible
 * at all. The key is chosen from the candidates the proxy reported rather than from every
 * column, because a composite key is refused by the graph contract itself, not merely
 * discouraged; the reason is on the picker's question-mark.
 *
 * A rename is not a text edit. Every link that points at the old name points at it *by
 * name*, so the row says which links it is re-pointing before it lets the name change.
 * Unticking a record is the same conversation in the other direction, and the list of links
 * that would be dropped is asked of the proxy rather than worked out here: one rule, one
 * authority, and a file a person uploads obeys the same cascade as a box they tick.
 */

interface Props {
  project: string;
  draft: DraftState;
  dispatch: React.Dispatch<MappingAction>;
  actions: MappingActions;
}

/** Which links a server answer named, as the types the draft shows. */
const namedLinks = (problems: MappingProblem[], document: GraphMappingDocument): string[] => {
  const types = (document.edges ?? []).map((edge) => edge.type);
  const found: string[] = [];
  problems.forEach((problem) => {
    const match = /^edges(?:\[(\d+|[^\]]+)\])/.exec(problem.path ?? '');
    if (!match) return;
    const token = match[1];
    const type = /^\d+$/.test(token) ? types[Number(token)] : token.replace(/^'|'$/g, '');
    if (type && !found.includes(type)) found.push(type);
  });
  return found;
};

const NodeList: React.FC<Props> = ({ project, draft, dispatch, actions }) => {
  const [unticking, setUnticking] = React.useState<string | null>(null);
  const nodes = draft.document.nodes ?? [];

  const untick = async (label: string): Promise<void> => {
    // Ask the proxy what an exclusion would cost *before* applying it: the prospective
    // document is validated without being saved, and the problems it answers name the
    // links whose endpoint would no longer be declared.
    const prospective: GraphMappingDocument = {
      ...draft.document,
      nodes: nodes.map((node) => (node.label === label ? { ...node, include: false } : node)),
    };
    setUnticking(label);
    let dropped: string[] = [];
    let note = '';
    try {
      const answer = await actions.validate(prospective);
      dropped = namedLinks([...toProblems(answer.problems), ...toProblems(answer.warnings)], draft.document);
      note = dropped.length
        ? 'The proxy says these links would no longer load, because this is the record at one of their ends.'
        : '';
    } catch (caught) {
      // A failed check is not a clean one. The untick still happens — it is the person's
      // own decision — but the links are not silently removed, and the save will answer.
      note = `Could not ask the proxy which links depend on it (${
        caught instanceof Error ? caught.message : String(caught)
      }). Nothing has been removed; a save will report what will not load.`;
    } finally {
      setUnticking(null);
    }

    Modal.confirm({
      title: `Take ${label} out of the graph?`,
      content: (
        <Space direction="vertical" size={6}>
          {dropped.length ? (
            <>
              <Text>These links go with it:</Text>
              <Space size={4} wrap>
                {dropped.map((type) => (
                  <Tag key={type} color="red">
                    {type}
                  </Tag>
                ))}
              </Space>
            </>
          ) : (
            <Text>No link names this record, so nothing else goes with it.</Text>
          )}
          {note ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {note}
            </Text>
          ) : null}
        </Space>
      ),
      okText: dropped.length ? 'Exclude and drop them' : 'Exclude it',
      okButtonProps: { danger: true },
      cancelText: 'Keep it in',
      onOk: () => dispatch({ kind: 'node-include', label, include: false, dropped }),
    });
  };

  const columns: ColumnsType<MappingNode> = [
    {
      title: 'In',
      key: 'include',
      width: 46,
      render: (_: unknown, node) => (
        <Tooltip title="An excluded record stays in the mapping as a decision, and is left out of the graph.">
          <Checkbox
            checked={node.include !== false}
            disabled={unticking === node.label}
            onChange={(event) =>
              event.target.checked
                ? dispatch({ kind: 'node-include', label: node.label, include: true })
                : untick(node.label)
            }
          />
        </Tooltip>
      ),
    },
    {
      title: 'Record label',
      key: 'label',
      render: (_: unknown, node) => {
        const problems = problemField(draft.problemIndex, 'nodes', node.label, 'label');
        return (
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            <Space size={6} wrap>
              <Input
                size="small"
                defaultValue={node.label}
                status={problems.length ? 'error' : undefined}
                style={{ width: 180 }}
                onBlur={(event) => {
                  const next = event.target.value.trim();
                  if (!next || next === node.label) {
                    event.target.value = node.label;
                    return;
                  }
                  const depends = linksDependingOn(draft.document, node.label);
                  if (!depends.length) {
                    dispatch({ kind: 'node-rename', from: node.label, to: next });
                    return;
                  }
                  Modal.confirm({
                    title: `Rename ${node.label} to ${next}?`,
                    content: (
                      <Space direction="vertical" size={4}>
                        <Text>These links point at it and will be re-pointed:</Text>
                        <Space size={4} wrap>
                          {depends.map((type) => (
                            <Tag key={type}>{type}</Tag>
                          ))}
                        </Space>
                      </Space>
                    ),
                    okText: 'Rename them too',
                    cancelText: 'Keep the name',
                    onCancel: () => undefined,
                    onOk: () => dispatch({ kind: 'node-rename', from: node.label, to: next }),
                  });
                }}
              />
              {provenanceTag(draft.provenance, 'nodes', node.label) ? (
                <Tag color={PROVENANCE_COLOR[provenanceTag(draft.provenance, 'nodes', node.label)!]}>
                  {provenanceTag(draft.provenance, 'nodes', node.label)}
                </Tag>
              ) : null}
            </Space>
            <ProblemTags problems={problems} />
          </Space>
        );
      },
    },
    {
      title: 'Table',
      key: 'table',
      render: (_: unknown, node) => {
        const problems = problemField(draft.problemIndex, 'nodes', node.label, 'table');
        return (
          <Space direction="vertical" size={0}>
            <TablePicker
              project={project}
              value={node.table ?? node.label}
              schema={node.schema ?? draft.document.schema ?? null}
              invalid={problems.length > 0}
              onChange={(choice) =>
                dispatch({ kind: 'node-patch', label: node.label, patch: { table: choice.table, schema: choice.schema } })
              }
            />
            <ProblemTags problems={problems} />
          </Space>
        );
      },
    },
    {
      title: 'Rows',
      key: 'rows',
      width: 110,
      render: (_: unknown, node) => (
        <RowEstimate project={project} table={node.table ?? node.label} schema={node.schema ?? null} />
      ),
    },
    {
      title: (
        <Space size={4}>
          Key
          <Tooltip title="Only a single column can identify a row: the graph contract carries one key pair per seed node, so a composite primary key is refused rather than approximated.">
            <QuestionCircleOutlined style={{ color: '#999' }} />
          </Tooltip>
        </Space>
      ),
      key: 'key',
      width: 210,
      render: (_: unknown, node) => {
        const problems = problemField(draft.problemIndex, 'nodes', node.label, 'key');
        return (
          <Space direction="vertical" size={0}>
            <ColumnPicker
              project={project}
              table={node.table ?? node.label}
              schema={node.schema ?? null}
              value={node.key ?? null}
              keyOnly
              invalid={problems.length > 0}
              placeholder="key column…"
              onChange={(column) => dispatch({ kind: 'node-patch', label: node.label, patch: { key: column } })}
            />
            <ProblemTags problems={problems} />
          </Space>
        );
      },
    },
    {
      title: 'Labelled by',
      key: 'show_as',
      width: 190,
      render: (_: unknown, node) => {
        const problems = problemField(draft.problemIndex, 'nodes', node.label, 'show_as');
        return (
          <Space direction="vertical" size={0}>
            <ColumnPicker
              project={project}
              table={node.table ?? node.label}
              schema={node.schema ?? null}
              value={node.show_as ?? null}
              allowClear
              invalid={problems.length > 0}
              placeholder="a property…"
              onChange={(column) => dispatch({ kind: 'node-patch', label: node.label, patch: { show_as: column || null } })}
            />
            <ProblemTags problems={problems} />
          </Space>
        );
      },
    },
    {
      title: 'Properties',
      key: 'properties',
      width: 130,
      render: (_: unknown, node) => {
        const problems = [
          ...problemField(draft.problemIndex, 'nodes', node.label, 'properties'),
          ...problemField(draft.problemIndex, 'nodes', node.label, 'exclude_properties'),
        ];
        return problems.length ? <ProblemTags problems={problems} /> : null;
      },
    },
    {
      title: '',
      key: 'actions',
      width: 56,
      render: (_: unknown, node) => (
        <Popconfirm
          title={`Delete ${node.label} from the mapping?`}
          description={
            linksDependingOn(draft.document, node.label).length
              ? 'Links pointing at it stay in the document: the save stores them, and GraphXR will not read this schema until every end names a declared label again.'
              : 'It goes back to being whatever auto-discovery makes of that table.'
          }
          okText="Delete"
          okButtonProps={{ danger: true }}
          cancelText="Cancel"
          onConfirm={() => dispatch({ kind: 'node-remove', label: node.label })}
        >
          <Tooltip title="Delete this record">
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Tooltip>
        </Popconfirm>
      ),
    },
  ];

  const excluded = nodes.filter((node) => node.include === false).length;

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {excluded ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {excluded} of {nodes.length} records are excluded and stay here as decisions the graph does not
          carry.
        </Text>
      ) : null}

      <Table<MappingNode>
        size="small"
        rowKey={(node) => node.label}
        columns={columns}
        dataSource={nodes}
        scroll={{ x: 1300 }}
        pagination={{ pageSize: 20, showSizeChanger: true, size: 'small', hideOnSinglePage: false }}
        expandable={{
          columnWidth: 34,
          expandedRowRender: (node) => {
            const said = proposalNote(draft.proposal, 'nodes', node.label);
            return (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <PropertyEditor
                  project={project}
                  list="nodes"
                  name={node.label}
                  entry={node}
                  table={node.table ?? node.label}
                  schema={node.schema ?? draft.document.schema ?? null}
                  dispatch={dispatch}
                />
                <Space align="start" size={8}>
                  <Text type="secondary" style={{ fontSize: 12, width: 110 }}>
                    Why this is a record
                  </Text>
                  <Input.TextArea
                    size="small"
                    rows={1}
                    autoSize={{ minRows: 1, maxRows: 4 }}
                    style={{ width: 520 }}
                    defaultValue={node.notes ?? said ?? ''}
                    placeholder={said || 'what this table means, and why its key is the one it is'}
                    onBlur={(event) =>
                      dispatch({ kind: 'node-patch', label: node.label, patch: { notes: event.target.value || null } })
                    }
                  />
                </Space>
                {said && said !== node.notes ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    discovery said: {said}
                  </Text>
                ) : null}
              </Space>
            );
          },
        }}
      />

      <Button
        icon={<PlusOutlined />}
        onClick={() => {
          const label = `Record${nodes.length + 1}`;
          dispatch({ kind: 'node-add', node: { label, table: '', include: true } });
        }}
      >
        Add a record
      </Button>
      <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
        A record with no table is read as the table named exactly like its label — name one when the table
        is spelled differently, or the record finds no key and stays out of the graph.
      </Text>
    </Space>
  );
};

export default NodeList;
