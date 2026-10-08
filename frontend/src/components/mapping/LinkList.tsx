import React from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Input,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { DeleteOutlined, PlusOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { MappingEdge } from '../../types/project';
import {
  DraftState,
  MappingAction,
  PROVENANCE_COLOR,
  formatCount,
  problemField,
  proposalNote,
  provenanceTag,
} from './reducer';
import { MappingActions } from './actions';
import { ColumnPicker, TablePicker } from './TablePicker';
import { ProblemTags } from './ProblemsPanel';
import EndpointPairRow from './EndpointPair';
import DirectionCheck from './DirectionCheck';
import PropertyEditor from './PropertyEditor';
import RowEstimate from './RowEstimate';

const { Text } = Typography;

/**
 * The links this schema is read as — one row per relationship type.
 *
 * A link is a table plus the columns of that table that name its two ends, so the row that
 * matters most on this page is the one with no foreign key anywhere: an edge stated outright
 * is how a schema that joins on a code column, and never declared a constraint, still becomes
 * a graph.
 *
 * Each row carries the reasoning discovery had when it proposed the link, because the point of
 * showing a guess is to let a person disagree with *that* guess rather than with the graph.
 * Direction is not argued about here: `DirectionCheck` runs the pair against real rows and
 * answers with them.
 */

interface Props {
  project: string;
  draft: DraftState;
  dispatch: React.Dispatch<MappingAction>;
  actions: MappingActions;
}

/** The sentence this pair is claiming, in the mapping's own words when it has any. */
const describePair = (edge: MappingEdge, src: string, dst: string): string => {
  const flip = edge.flip === true;
  const from = flip ? dst : src;
  const to = flip ? src : dst;
  return edge.notes
    ? edge.notes
    : `${from} → ${to}${edge.type ? ` (${edge.type})` : ''}${flip ? ' · read reversed from discovery' : ''}`;
};

const LinkList: React.FC<Props> = ({ project, draft, dispatch, actions }) => {
  const edges = draft.document.edges ?? [];
  const labels = (draft.document.nodes ?? []).map((node) => node.label);

  const columns: ColumnsType<MappingEdge> = [
    {
      title: 'In',
      key: 'include',
      width: 46,
      render: (_: unknown, edge) => (
        <Tooltip title="An excluded link stays in the mapping as a decision, and is left out of the graph.">
          <Checkbox
            checked={edge.include !== false}
            onChange={(event) =>
              dispatch({ kind: 'edge-patch', type: edge.type, patch: { include: event.target.checked } })
            }
          />
        </Tooltip>
      ),
    },
    {
      title: 'Link type',
      key: 'type',
      width: 190,
      render: (_: unknown, edge) => {
        const problems = problemField(draft.problemIndex, 'edges', edge.type, 'type');
        return (
          <Space direction="vertical" size={0}>
            <Space size={6} wrap>
              <Input
                size="small"
                defaultValue={edge.type}
                status={problems.length ? 'error' : undefined}
                style={{ width: 160 }}
                onBlur={(event) => {
                  const next = event.target.value.trim();
                  if (next && next !== edge.type) {
                    dispatch({ kind: 'edge-patch', type: edge.type, patch: { type: next } });
                  } else if (!next) {
                    event.target.value = edge.type;
                  }
                }}
              />
              {provenanceTag(draft.provenance, 'edges', edge.type) ? (
                <Tag color={PROVENANCE_COLOR[provenanceTag(draft.provenance, 'edges', edge.type)!]}>
                  {provenanceTag(draft.provenance, 'edges', edge.type)}
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
      width: 230,
      render: (_: unknown, edge) => {
        const problems = problemField(draft.problemIndex, 'edges', edge.type, 'table');
        return (
          <Space direction="vertical" size={0}>
            <TablePicker
              project={project}
              value={edge.table ?? edge.type}
              schema={edge.schema ?? draft.document.schema ?? null}
              invalid={problems.length > 0}
              onChange={(choice) =>
                dispatch({
                  kind: 'edge-patch',
                  type: edge.type,
                  patch: { table: choice.table, schema: choice.schema },
                })
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
      width: 96,
      render: (_: unknown, edge) => <RowEstimate project={project} table={edge.table ?? null} schema={edge.schema ?? null} />,
    },
    {
      title: (
        <Space size={4}>
          Own key
          <Tooltip title="The link table's own key — its primary key, else its widest single-column unique — so two links between the same two records stay two links. With none, the id is minted from both ends and duplicate links collapse onto one.">
            <QuestionCircleOutlined style={{ color: '#999' }} />
          </Tooltip>
        </Space>
      ),
      key: 'key',
      width: 190,
      render: (_: unknown, edge) => {
        const problems = problemField(draft.problemIndex, 'edges', edge.type, 'key');
        return (
          <Space direction="vertical" size={0}>
            <ColumnPicker
              project={project}
              table={edge.table}
              schema={edge.schema ?? null}
              value={edge.key ?? null}
              keyOnly
              allowClear
              invalid={problems.length > 0}
              placeholder="no key…"
              onChange={(column) => dispatch({ kind: 'edge-patch', type: edge.type, patch: { key: column || null } })}
            />
            <ProblemTags problems={problems} />
          </Space>
        );
      },
    },
    {
      title: 'Flipped',
      key: 'flip',
      width: 86,
      render: (_: unknown, edge) => {
        const problems = problemField(draft.problemIndex, 'edges', edge.type, 'flip');
        return (
          <Space direction="vertical" size={0}>
            <Switch
              size="small"
              checked={edge.flip === true}
              onChange={() => dispatch({ kind: 'edge-flip', type: edge.type })}
            />
            <ProblemTags problems={problems} />
          </Space>
        );
      },
    },
    {
      title: 'Endpoints',
      key: 'endpoints',
      render: (_: unknown, edge) => {
        const pairs = edge.endpoints ?? [];
        const whole = problemField(draft.problemIndex, 'edges', edge.type, 'endpoints');
        return (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {pairs.map((pair, index) => (
              <EndpointPairRow
                key={`${edge.type}-${index}`}
                project={project}
                draft={draft}
                dispatch={dispatch}
                edge={edge}
                pair={pair}
                index={index}
                disabled={edge.include === false}
              />
            ))}
            {!pairs.length ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                No pair yet — a type with no endpoints can never be drawn.
              </Text>
            ) : null}
            <ProblemTags problems={whole} />
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={() => dispatch({ kind: 'pair-add', type: edge.type })}
            >
              Add a pair
            </Button>
          </Space>
        );
      },
    },
    {
      title: '',
      key: 'actions',
      width: 52,
      render: (_: unknown, edge) => (
        <Popconfirm
          title={`Delete ${edge.type}?`}
          description="The link leaves the mapping. Auto-discovery may propose it again the next time the override is dropped."
          okText="Delete"
          okButtonProps={{ danger: true }}
          cancelText="Cancel"
          onConfirm={() => dispatch({ kind: 'edge-remove', type: edge.type })}
        >
          <Tooltip title="Delete this link">
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Tooltip>
        </Popconfirm>
      ),
    },
  ];

  const noLabels = !labels.length;

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {noLabels ? (
        <Alert
          type="info"
          showIcon
          message="No records are declared yet"
          description={
            <Text style={{ fontSize: 12 }}>
              A link ends at a record. Add one on the Records tab, or let discovery propose them — a
              link typed against a label that is not declared is stored and then named as a problem:
              GraphXR will not read this schema until every end names a label that is.
            </Text>
          }
        />
      ) : null}

      <Table<MappingEdge>
        size="small"
        rowKey={(edge) => edge.type}
        columns={columns}
        dataSource={edges}
        scroll={{ x: 1100 }}
        pagination={{ pageSize: 10, showSizeChanger: true, size: 'small', hideOnSinglePage: true }}
        expandable={{
          columnWidth: 34,
          expandedRowRender: (edge) => {
            const notes = problemField(draft.problemIndex, 'edges', edge.type, 'notes');
            // The proposal's own reasoning for this link, not the entry's: reading the entry's
            // note here made the divergence below impossible to reach.
            const reasoning = proposalNote(draft.proposal, 'edges', edge.type);
            const structural = (edge.endpoints ?? []).reduce<string[]>(
              (found, pair) => [...found, pair.src.column, pair.dst.column],
              [],
            );
            return (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                {(edge.endpoints ?? []).map((_, index) => (
                  <DirectionCheck
                    key={`${edge.type}-check-${index}`}
                    actions={actions}
                    edge={edge}
                    pairIndex={index}
                    describe={(from, to) => describePair(edge, from, to)}
                    onFlip={() => dispatch({ kind: 'edge-flip', type: edge.type })}
                  />
                ))}
                <PropertyEditor
                  project={project}
                  list="edges"
                  name={edge.type}
                  entry={edge}
                  table={edge.table ?? null}
                  schema={edge.schema ?? draft.document.schema ?? null}
                  dispatch={dispatch}
                  structural={structural}
                />
                <Space align="start" size={8}>
                  <Text type="secondary" style={{ fontSize: 12, width: 110 }}>
                    Why this is a link
                  </Text>
                  <Input.TextArea
                    size="small"
                    rows={1}
                    autoSize={{ minRows: 1, maxRows: 4 }}
                    style={{ width: 520 }}
                    defaultValue={edge.notes ?? reasoning ?? ''}
                    placeholder="what this table means, and which way round the sentence goes"
                    onBlur={(event) =>
                      dispatch({ kind: 'edge-patch', type: edge.type, patch: { notes: event.target.value || null } })
                    }
                  />
                </Space>
                {reasoning && reasoning !== edge.notes ? (
                  <Alert
                    type="info"
                    showIcon
                    message="What discovery reasoned"
                    description={
                      <Text style={{ fontSize: 12 }}>
                        {reasoning}
                        {edge.notes ? ' — your note now says something else, and yours is what is saved.' : ''}
                      </Text>
                    }
                  />
                ) : null}
                <ProblemTags problems={notes} />
              </Space>
            );
          },
        }}
      />

      <Space wrap size={8}>
        <Button
          icon={<PlusOutlined />}
          onClick={() =>
            dispatch({
              kind: 'edge-add',
              edge: { type: `LINK${edges.length + 1}`, table: '', include: true, endpoints: [] },
            })
          }
        >
          Add a link
        </Button>
        <Button
          icon={<PlusOutlined />}
          onClick={() => {
            const src = labels[0] ?? '';
            const dst = labels[1] ?? labels[0] ?? '';
            dispatch({
              kind: 'edge-add',
              edge: {
                type: `LINK${edges.length + 1}`,
                table: '',
                include: true,
                endpoints: [{ src: { label: src, column: '' }, dst: { label: dst, column: '' } }],
              },
            });
          }}
        >
          Add a link with no foreign key anywhere
        </Button>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {formatCount(edges.length)} link {edges.length === 1 ? 'type' : 'types'} in this draft — a link
          with no table is read as the table named exactly like its type.
        </Text>
      </Space>
    </Space>
  );
};

export default LinkList;
