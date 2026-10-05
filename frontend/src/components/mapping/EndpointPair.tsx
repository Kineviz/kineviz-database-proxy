import React from 'react';
import { Button, Select, Space, Tag, Tooltip, Typography } from 'antd';
import { DeleteOutlined } from '@ant-design/icons';
import { MappingEdge, MappingPair } from '../../types/project';
import { DraftState, MappingAction, problemField } from './reducer';
import { ColumnPicker } from './TablePicker';
import { ProblemTags } from './ProblemsPanel';

const { Text } = Typography;

/**
 * One `(src, dst)` an edge type connects.
 *
 * Each end is two picks, because the wire carries two facts and they are not the same
 * question: *which record* this end points at (a label the document declares), and *which
 * column of the link table* holds it. Several pairs on one type is a polymorphic link —
 * the same relationship reaching several kinds of record — which is why a pair is added
 * rather than a second edge wearing the same name.
 *
 * A column here belongs to the **link** table, never to the record: the join is
 * `link.column = record.key`. That mix-up is the commonest wrong answer in a mapping, so
 * the picker says which table it is reading, and the column is kept when the label changes
 * because retargeting an end corrects the record, not the join.
 */

interface Props {
  project: string;
  draft: DraftState;
  dispatch: React.Dispatch<MappingAction>;
  edge: MappingEdge;
  pair: MappingPair;
  index: number;
  /** Why the pair is disabled, when the edge is excluded and cannot be drawn anyway. */
  disabled?: boolean;
}

const ENDPOINT_LABEL: Record<'src' | 'dst', string> = { src: 'from', dst: 'to' };

/** Every label the draft declares, and whether it is still in the graph. */
const labelOptions = (draft: DraftState) =>
  (draft.document.nodes ?? []).map((node) => {
    const table = node.schema ? `${node.schema}.${node.table ?? node.label}` : node.table ?? node.label;
    if (node.include === false) {
      return {
        value: node.label,
        label: (
          <Tooltip title="This record is excluded, so a link pointing at it joins nothing.">
            <Text delete>{node.label}</Text>
          </Tooltip>
        ),
      };
    }
    return {
      value: node.label,
      label: (
        <Space size={4}>
          <Text>{node.label}</Text>
          {table !== node.label ? <Text type="secondary">{table}</Text> : null}
        </Space>
      ),
    };
  });

interface EndProps extends Props {
  side: 'src' | 'dst';
}

const EndpointEnd: React.FC<EndProps> = ({ project, draft, dispatch, edge, pair, index, side, disabled }) => {
  const end = pair[side] ?? { label: '', column: '' };
  const field = `endpoints.${index}.${side}`;
  const labelProblems = problemField(draft.problemIndex, 'edges', edge.type, `${field}.label`);
  const columnProblems = problemField(draft.problemIndex, 'edges', edge.type, `${field}.column`);

  return (
    <Space wrap align="start" size={6}>
      <Text type="secondary" style={{ width: 36, display: 'inline-block' }}>
        {ENDPOINT_LABEL[side]}
      </Text>
      <Select
        size="small"
        style={{ minWidth: 190 }}
        placeholder="record label…"
        disabled={disabled}
        status={labelProblems.length ? 'error' : undefined}
        value={end.label || undefined}
        notFoundContent={<Text type="secondary">No records declared yet</Text>}
        options={labelOptions(draft)}
        onChange={(label: string) => dispatch({ kind: 'pair-patch', type: edge.type, index, side, patch: { label } })}
      />
      <ColumnPicker
        project={project}
        table={edge.table}
        schema={edge.schema ?? null}
        value={end.column || null}
        invalid={columnProblems.length > 0}
        disabled={disabled}
        placeholder={edge.table ? `column of ${edge.table}` : 'pick the link table first'}
        onChange={(column) => dispatch({ kind: 'pair-patch', type: edge.type, index, side, patch: { column } })}
      />
      {end.column_type ? (
        <Tooltip title="Declared type of that column, as discovery read it">
          <Tag>{end.column_type}</Tag>
        </Tooltip>
      ) : null}
      {labelProblems.length ? <ProblemTags problems={labelProblems} /> : null}
      {columnProblems.length ? <ProblemTags problems={columnProblems} /> : null}
    </Space>
  );
};

const EndpointPairRow: React.FC<Props> = ({ project, draft, dispatch, edge, pair, index, disabled }) => {
  // A problem that names the pair itself rather than one end of it — no pair at all,
  // or a pair whose two ends are the same record.
  const wholePair = problemField(draft.problemIndex, 'edges', edge.type, `endpoints.${index}`);
  return (
    <Space direction="vertical" size={4} style={{ width: '100%' }}>
      <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          pair {index + 1}
          {pair.row_estimate !== undefined && pair.row_estimate !== null
            ? ` · ~${pair.row_estimate.toLocaleString('en-US')} links`
            : ''}
        </Text>
        <Tooltip title="Remove this pair. A type left with no pair can never be drawn, and is refused when the mapping is saved.">
          <Button
            size="small"
            type="text"
            danger
            disabled={disabled}
            icon={<DeleteOutlined />}
            onClick={() => dispatch({ kind: 'pair-remove', type: edge.type, index })}
          />
        </Tooltip>
      </Space>
      <EndpointEnd {...{ project, draft, dispatch, edge, pair, index, disabled }} side="src" />
      <EndpointEnd {...{ project, draft, dispatch, edge, pair, index, disabled }} side="dst" />
      {wholePair.length ? <ProblemTags problems={wholePair} /> : null}
    </Space>
  );
};

export default EndpointPairRow;
