import React from 'react';
import { Alert, Button, Collapse, Empty, Form, Input, Popconfirm, Select, Space, Switch, Tag, Typography } from 'antd';
import { ArrowRightOutlined, DeleteOutlined, KeyOutlined, PlusOutlined, SwapOutlined } from '@ant-design/icons';
import { MappingEdge, MappingNode } from '../../types/project';
import { MappingActions } from './actions';
import { MappingSelection, nodeColor, tableName } from './diagramModel';
import { DraftState, MappingAction, problemField, propertyNames } from './reducer';
import { TablePicker, ColumnPicker } from './TablePicker';
import PropertyEditor from './PropertyEditor';
import { ProblemTags } from './ProblemsPanel';
import DirectionCheck from './DirectionCheck';

const { Text } = Typography;
interface Props {
  project: string;
  draft: DraftState;
  dispatch: React.Dispatch<MappingAction>;
  actions: MappingActions;
  selection: MappingSelection;
  onSelect: (selection: MappingSelection) => void;
  onAddEdge: (from?: string) => void;
}

/** Edit one mapping beside its graph; only the selected table's columns are read. */
const MappingInspector: React.FC<Props> = ({ project, draft, dispatch, actions, selection, onSelect, onAddEdge }) => {
  const document = draft.document;
  const node = selection?.kind === 'node' ? document.nodes?.find((item) => item.label === selection.name) : undefined;
  const edge = selection?.kind === 'edge' ? document.edges?.find((item) => item.type === selection.name) : undefined;
  const entry = node ?? edge;
  if (!selection || !entry) return <aside className="mapping-inspector mapping-inspector-empty">
    <span className="mapping-eyebrow">DETAILS</span>
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Select a node or relationship" />
    <p>Click a card or connection to edit its source table, properties and direction.</p>
    <div className="mapping-inspector-tip"><KeyOutlined /> A table becomes a node when you choose the column that identifies each row.</div>
  </aside>;

  const list = node ? 'nodes' : 'edges';
  const name = node ? node.label : edge!.type;
  const schema = entry.schema ?? document.schema ?? null;
  const physicalTable = entry.table || name;
  const patch = (change: Partial<MappingNode> & Partial<MappingEdge>) => {
    if (node) dispatch({ kind: 'node-patch', label: name, patch: change });
    else dispatch({ kind: 'edge-patch', type: name, patch: change });
  };
  const fieldProblems = (field: string) => problemField(draft.problemIndex, list, name, field);
  const entryProblems = draft.problems.filter((problem) => {
    const index = node ? (document.nodes ?? []).indexOf(node) : (document.edges ?? []).indexOf(edge!);
    return problem.path === `${list}[${index}]` || problem.path.startsWith(`${list}[${index}].`);
  });
  const index = node ? (document.nodes ?? []).indexOf(node) : 0;
  const color = node ? nodeColor(index) : '#64748b';

  return <aside className="mapping-inspector" aria-label="Mapping details">
    <div className="mapping-inspector-heading">
      <span className="mapping-eyebrow">{node ? 'NODE MAPPING' : 'RELATIONSHIP MAPPING'}</span>
      <Tag color={entry.include === false ? 'default' : 'green'}>{entry.include === false ? 'Excluded' : 'Included'}</Tag>
    </div>
    <h3><span className="mapping-type-dot" style={{ background: color }} />{name}</h3>
    <div className="mapping-source-caption">{tableName(entry, document)}</div>
    {entryProblems.length ? <Alert type="error" showIcon message={`${entryProblems.length} field${entryProblems.length === 1 ? '' : 's'} need attention`} /> : null}
    <Form layout="vertical" size="small" key={`${list}:${name}`}>
      <section className="mapping-inspector-section">
        <h4>Source & identity</h4>
        <Form.Item label={node ? 'Node label' : 'Relationship type'}>
          <Input aria-label={node ? 'Node label' : 'Relationship type'} defaultValue={name} status={fieldProblems(node ? 'label' : 'type').length ? 'error' : undefined}
            onBlur={(event) => {
              const next = event.target.value.trim();
              if (!next || next === name) { event.target.value = name; return; }
              const collection = node ? document.nodes?.map((item) => item.label) : document.edges?.map((item) => item.type);
              if (collection?.includes(next)) { event.target.value = name; return; }
              if (node) dispatch({ kind: 'node-rename', from: name, to: next });
              else dispatch({ kind: 'edge-patch', type: name, patch: { type: next } });
              onSelect({ kind: node ? 'node' : 'edge', name: next });
            }} />
          <ProblemTags problems={fieldProblems(node ? 'label' : 'type')} />
        </Form.Item>
        <Form.Item label="Source table">
          <TablePicker project={project} value={physicalTable} schema={schema} invalid={fieldProblems('table').length > 0}
            onChange={(choice) => patch({ table: choice.table, schema: choice.schema })} />
          <ProblemTags problems={fieldProblems('table')} />
        </Form.Item>
        <Form.Item label={node ? 'Primary key' : 'Relationship key'} extra={node ? 'One unique column identifies each node.' : 'Keep parallel relationships distinct with a unique key.'}>
          <ColumnPicker project={project} table={physicalTable} schema={schema} value={entry.key} keyOnly allowClear={!node}
            invalid={fieldProblems('key').length > 0} placeholder="Choose a key column" onChange={(column) => patch({ key: column || null })} />
          <ProblemTags problems={fieldProblems('key')} />
        </Form.Item>
        {node ? <Form.Item label="Display name" extra="The property used to label a node.">
          <Select allowClear value={node.show_as ?? undefined} placeholder="Use primary key" aria-label="Display name"
            options={Array.from(new Set([node.key, ...propertyNames(node.properties)].filter(Boolean))).map((value) => ({ value, label: value }))}
            onChange={(value) => patch({ show_as: value || null })} />
        </Form.Item> : null}
        <div className="mapping-include-control"><div><Text strong>Include in graph</Text><small>Keep this mapping visible in GraphXR.</small></div>
          <Switch size="small" aria-label="Include in graph" checked={entry.include !== false}
            onChange={(include) => node ? dispatch({ kind: 'node-include', label: name, include }) : patch({ include })} />
        </div>
      </section>
      {edge ? <section className="mapping-inspector-section">
        <div className="mapping-section-title"><h4>Connections</h4><Button size="small" icon={<SwapOutlined />} onClick={() => dispatch({ kind: 'edge-flip', type: name })}>Reverse</Button></div>
        {(edge.endpoints ?? []).map((pair, pairIndex) => <div className="mapping-pair-card" key={pairIndex}>
          <div className="mapping-pair-summary"><span>{(edge.flip ? pair.dst : pair.src).label || '?'}</span><ArrowRightOutlined /><span>{(edge.flip ? pair.src : pair.dst).label || '?'}</span>
            <Popconfirm title="Remove this connection?" onConfirm={() => dispatch({ kind: 'pair-remove', type: name, index: pairIndex })}>
              <Button type="text" size="small" danger aria-label="Remove connection" icon={<DeleteOutlined />} />
            </Popconfirm>
          </div>
          {(['src', 'dst'] as const).map((direction, endIndex) => {
            const side = edge.flip ? (direction === 'src' ? 'dst' : 'src') : direction;
            return <div className="mapping-endpoint" key={side}>
              <span className="mapping-field-label">{endIndex === 0 ? 'From node' : 'To node'}</span>
              <Select size="small" aria-label={endIndex === 0 ? 'From node' : 'To node'} value={pair[side].label || undefined} placeholder="Choose a node"
                options={(document.nodes ?? []).map((item) => ({ value: item.label, label: item.label }))}
                onChange={(label) => dispatch({ kind: 'pair-patch', type: name, index: pairIndex, side, patch: { label } })} />
              <span className="mapping-field-label">Column in {physicalTable}</span>
              <ColumnPicker project={project} table={physicalTable} schema={schema} value={pair[side].column} placeholder="Join column"
                onChange={(column) => dispatch({ kind: 'pair-patch', type: name, index: pairIndex, side, patch: { column } })} />
              <ProblemTags problems={fieldProblems(`endpoints.${pairIndex}.${side}.label`)} />
              <ProblemTags problems={fieldProblems(`endpoints.${pairIndex}.${side}.column`)} />
            </div>;
          })}
          <Collapse ghost size="small" items={[{ key: 'test', label: 'Test direction with database', children:
            <DirectionCheck actions={actions} edge={edge} pairIndex={pairIndex}
              describe={(from, to) => `${from} → ${edge.type} → ${to}`}
              onFlip={() => dispatch({ kind: 'edge-flip', type: name })} /> }]} />
        </div>)}
        <Button block type="dashed" icon={<PlusOutlined />} onClick={() => dispatch({ kind: 'pair-add', type: name })}>Add connection</Button>
        <ProblemTags problems={fieldProblems('endpoints')} />
      </section> : null}
      <section className="mapping-inspector-section">
        <h4>Properties</h4>
        <PropertyEditor project={project} list={list} name={name} entry={entry} table={physicalTable} schema={schema} dispatch={dispatch}
          structural={edge ? (edge.endpoints ?? []).flatMap((pair) => [pair.src.column, pair.dst.column]) : [node!.key ?? '']} />
        <ProblemTags problems={fieldProblems('properties')} />
      </section>
      <Collapse ghost size="small" items={[{ key: 'notes', label: 'Notes', children: <Input.TextArea defaultValue={entry.notes ?? ''} autoSize={{ minRows: 2, maxRows: 5 }} placeholder="Describe what this mapping means" onBlur={(event) => patch({ notes: event.target.value || null })} /> }]} />
    </Form>
    <div className="mapping-inspector-actions">
      {node ? <Button icon={<PlusOutlined />} onClick={() => onAddEdge(node.label)}>Add relationship</Button> : null}
      <Popconfirm title={`Remove ${name} from the mapping?`} description={node ? 'Relationships pointing to this node will need a new endpoint.' : 'This removes the relationship definition.'}
        onConfirm={() => { if (node) dispatch({ kind: 'node-remove', label: name }); else dispatch({ kind: 'edge-remove', type: name }); onSelect(null); }}>
        <Button danger type="text" icon={<DeleteOutlined />}>Remove</Button>
      </Popconfirm>
    </div>
  </aside>;
};

export default MappingInspector;
