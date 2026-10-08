import React from 'react';
import { Button, Empty, Input, Segmented, Tooltip } from 'antd';
import { ArrowRightOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { MappingActions } from './actions';
import { DraftState, MappingAction } from './reducer';
import { included, MappingSelection, nodeColor, tableName } from './diagramModel';
import MappingCanvas from './MappingCanvas';
import MappingInspector from './MappingInspector';

interface Props {
  project: string;
  draft: DraftState;
  dispatch: React.Dispatch<MappingAction>;
  actions: MappingActions;
}

const VisualMapping: React.FC<Props> = ({ project, draft, dispatch, actions }) => {
  const [selection, select] = React.useState<MappingSelection>(null);
  const [search, setSearch] = React.useState('');
  const [filter, setFilter] = React.useState('all');
  const doc = draft.document;
  const nodes = doc.nodes ?? [];
  const edges = doc.edges ?? [];
  const matches = (name: string, table?: string) => `${name} ${table ?? ''}`.toLowerCase().includes(search.toLowerCase());
  const shownNodes = nodes.filter((node) => matches(node.label, node.table));
  const shownEdges = edges.filter((edge) => matches(edge.type, edge.table));

  const uniqueName = (prefix: string, names: string[]) => {
    let number = 1;
    while (names.includes(`${prefix}${number}`)) number += 1;
    return `${prefix}${number}`;
  };
  const addNode = () => {
    const label = uniqueName('Node', nodes.map((node) => node.label));
    dispatch({ kind: 'node-add', node: { label, table: '', include: true, properties: {} } });
    select({ kind: 'node', name: label });
  };
  const addEdge = (from?: string) => {
    const type = uniqueName('RELATIONSHIP', edges.map((edge) => edge.type));
    const source = from ?? nodes[0]?.label ?? '';
    const target = nodes.find((node) => node.label !== source)?.label ?? source;
    dispatch({ kind: 'edge-add', edge: { type, table: '', include: true, endpoints: [
      { src: { label: source, column: '' }, dst: { label: target, column: '' } },
    ] } });
    select({ kind: 'edge', name: type });
  };

  return <div className="mapping-visual-workspace">
    <aside className="mapping-library" aria-label="Mapping elements">
      <div className="mapping-library-heading"><span className="mapping-eyebrow">GRAPH ELEMENTS</span><span>{nodes.length + edges.length}</span></div>
      <Input prefix={<SearchOutlined />} placeholder="Find a node or relationship" aria-label="Find a mapping" allowClear value={search} onChange={(event) => setSearch(event.target.value)} />
      <Segmented block size="small" value={filter} options={[{ value: 'all', label: 'All' }, { value: 'nodes', label: 'Nodes' }, { value: 'edges', label: 'Relations' }]} onChange={(value) => setFilter(String(value))} />
      <div className="mapping-library-list">
        {filter !== 'edges' ? <>
          <div className="mapping-library-section"><span>Nodes</span><span>{shownNodes.length}</span></div>
          {shownNodes.map((node) => <button type="button" key={node.label} className={`mapping-library-item${selection?.kind === 'node' && selection.name === node.label ? ' is-selected' : ''}${included(node, doc) ? '' : ' is-excluded'}`}
            onClick={() => select({ kind: 'node', name: node.label })}>
            <span className="mapping-node-symbol" style={{ color: nodeColor(nodes.indexOf(node)), background: `${nodeColor(nodes.indexOf(node))}15` }}>○</span>
            <span><strong>{node.label}</strong><small title={tableName(node, doc)}>{tableName(node, doc)}</small></span>
            {!included(node, doc) ? <span className="mapping-excluded-mark" title="Excluded">—</span> : null}
          </button>)}
        </> : null}
        {filter !== 'nodes' ? <>
          <div className="mapping-library-section"><span>Relationships</span><span>{shownEdges.length}</span></div>
          {shownEdges.map((edge) => {
            const pair = edge.endpoints?.[0];
            const from = edge.flip ? pair?.dst.label : pair?.src.label;
            const to = edge.flip ? pair?.src.label : pair?.dst.label;
            return <button type="button" key={edge.type} className={`mapping-library-item${selection?.kind === 'edge' && selection.name === edge.type ? ' is-selected' : ''}${included(edge, doc) ? '' : ' is-excluded'}`}
              onClick={() => select({ kind: 'edge', name: edge.type })}>
              <span className="mapping-relation-symbol"><ArrowRightOutlined /></span>
              <span><strong>{edge.type}</strong><small>{from || '?'} → {to || '?'}</small></span>
            </button>;
          })}
        </> : null}
        {search && !shownNodes.length && !shownEdges.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No matching mappings" /> : null}
      </div>
      <div className="mapping-library-add"><Button icon={<PlusOutlined />} onClick={addNode}>Node</Button>
        <Tooltip title={!nodes.length ? 'Add a node first' : 'Connect two node types'}><Button icon={<PlusOutlined />} disabled={!nodes.length} onClick={() => addEdge()}>Relation</Button></Tooltip>
      </div>
    </aside>
    <MappingCanvas document={doc} selection={selection} onSelect={select} />
    <MappingInspector key={`${selection?.kind}:${selection?.name}`} project={project} draft={draft} dispatch={dispatch} actions={actions}
      selection={selection} onSelect={select} onAddEdge={addEdge} />
  </div>;
};

export default VisualMapping;
