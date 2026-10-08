import React from 'react';
import { Button, Empty, Space, Tooltip } from 'antd';
import { AimOutlined, ApartmentOutlined, MinusOutlined, PlusOutlined } from '@ant-design/icons';
import { GraphMappingDocument } from '../../types/project';
import { propertyNames } from './reducer';
import { arrangeNodes, diagramLinks, included, MappingSelection, NODE_HEIGHT, NODE_WIDTH, nodeColor, Point, tableName } from './diagramModel';

interface Props {
  document: GraphMappingDocument;
  selection: MappingSelection;
  onSelect: (selection: MappingSelection) => void;
}
interface View { x: number; y: number; zoom: number }
const short = (value: string, size = 24) => value.length > size ? `${value.slice(0, size - 1)}…` : value;

/** Interactive schema diagram. Coordinates are presentation state, never mapping data. */
const MappingCanvas: React.FC<Props> = ({ document, selection, onSelect }) => {
  const nodes = document.nodes ?? [];
  const links = diagramLinks(document);
  const [size, setSize] = React.useState({ width: 600, height: 540 });
  const topology = JSON.stringify([nodes.map((node) => node.label), links.map((link) => [link.from, link.to])]);
  const layout = React.useMemo(() => {
    const [labels, pairs] = JSON.parse(topology) as [string[], [string, string][]];
    return arrangeNodes(labels, pairs, size.width < 880 ? 2 : Infinity);
  }, [topology, size.width]);
  const [positions, setPositions] = React.useState(layout);
  const [view, setView] = React.useState<View>({ x: 0, y: 0, zoom: 1 });
  const host = React.useRef<HTMLDivElement>(null);
  const svg = React.useRef<SVGSVGElement>(null);
  const drag = React.useRef<{ label?: string; pointer: number; start: Point; origin: Point; moved: boolean } | null>(null);
  const marker = React.useId().replace(/:/g, '');

  React.useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);

  const fit = React.useCallback((points: Record<string, Point>) => {
    const values = Object.values(points);
    if (!values.length) return;
    const left = Math.min(...values.map((p) => p.x)) - 45;
    const top = Math.min(...values.map((p) => p.y)) - 100;
    const width = Math.max(...values.map((p) => p.x)) + NODE_WIDTH + 45 - left;
    const height = Math.max(...values.map((p) => p.y)) + NODE_HEIGHT + 60 - top;
    const zoom = Math.max(0.08, Math.min(1.1, (size.width - 24) / width, (size.height - 24) / height));
    setView({ x: (size.width - width * zoom) / 2 - left * zoom, y: (size.height - height * zoom) / 2 - top * zoom, zoom });
  }, [size]);

  React.useEffect(() => {
    setPositions(layout);
    fit(layout);
  }, [layout, fit]);

  const zoomAt = React.useCallback((factor: number, point: Point) => setView((previous) => {
    const zoom = Math.max(0.08, Math.min(2.5, previous.zoom * factor));
    const ratio = zoom / previous.zoom;
    return { zoom, x: point.x - (point.x - previous.x) * ratio, y: point.y - (point.y - previous.y) * ratio };
  }), []);

  React.useEffect(() => {
    const element = svg.current;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element!.getBoundingClientRect();
      zoomAt(event.deltaY > 0 ? 0.92 : 1.08, { x: event.clientX - rect.left, y: event.clientY - rect.top });
    };
    element?.addEventListener('wheel', wheel, { passive: false });
    return () => element?.removeEventListener('wheel', wheel);
  }, [zoomAt, nodes.length]);

  const start = (event: React.PointerEvent<SVGElement>, label?: string) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    svg.current?.setPointerCapture(event.pointerId);
    drag.current = { label, pointer: event.pointerId, start: { x: event.clientX, y: event.clientY },
      origin: label ? (positions[label] ?? layout[label]) : { x: view.x, y: view.y }, moved: false };
    if (label) onSelect({ kind: 'node', name: label });
  };
  const move = (event: React.PointerEvent<SVGSVGElement>) => {
    const active = drag.current;
    if (!active) return;
    const dx = event.clientX - active.start.x;
    const dy = event.clientY - active.start.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) active.moved = true;
    if (active.label) {
      const point = { x: active.origin.x + dx / view.zoom, y: active.origin.y + dy / view.zoom };
      setPositions((current) => ({ ...current, [active.label!]: point }));
    } else setView((current) => ({ ...current, x: active.origin.x + dx, y: active.origin.y + dy }));
  };
  const end = (event: React.PointerEvent<SVGSVGElement>) => {
    const active = drag.current;
    if (active && !active.label && !active.moved) onSelect(null);
    if (svg.current?.hasPointerCapture(event.pointerId)) svg.current.releasePointerCapture(event.pointerId);
    drag.current = null;
  };
  const known = new Map(nodes.map((node) => [node.label, node]));
  const missing = links.filter((link) => !known.has(link.from) || !known.has(link.to)).length;

  return (
    <div className="mapping-canvas" ref={host}>
      <div className="mapping-canvas-caption"><span className="mapping-live-dot" /> Schema preview <span>Draft</span></div>
      {nodes.length ? (
        <svg ref={svg} className="mapping-diagram" aria-label="Interactive graph mapping" width={size.width} height={size.height}
          onPointerDown={(event) => start(event)} onPointerMove={move} onPointerUp={end}
          onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
          <defs>
            <marker id={`${marker}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
            </marker>
          </defs>
          <g transform={`translate(${view.x},${view.y}) scale(${view.zoom})`}>
            {links.map((link, linkIndex) => {
              const from = positions[link.from] ?? layout[link.from];
              const to = positions[link.to] ?? layout[link.to];
              if (!from || !to) return null;
              const selected = selection?.kind === 'edge' && selection.name === link.edge.type;
              const connected = selection?.kind === 'node' && [link.from, link.to].includes(selection.name);
              const active = included(link.edge, document) && included(known.get(link.from)!, document) && included(known.get(link.to)!, document);
              const parallel = links.slice(0, linkIndex).filter((other) => other.from === link.from && other.to === link.to).length;
              const loop = link.from === link.to;
              const vertical = !loop && Math.abs(from.x - to.x) < 20;
              const returnDown = !loop && !vertical && to.x < from.x && to.y > from.y + NODE_HEIGHT / 2;
              const forward = to.x >= from.x;
              const x1 = from.x + (forward ? NODE_WIDTH : 0);
              const x2 = to.x + (forward ? 0 : NODE_WIDTH);
              const y1 = from.y + NODE_HEIGHT / 2;
              const y2 = to.y + NODE_HEIGHT / 2;
              const bend = (parallel ? parallel * 70 : 0) - (forward ? 0 : 80);
              const distance = Math.max(55, Math.abs(x2 - x1) / 2);
              const down = to.y > from.y;
              const verticalStart = from.y + (down ? NODE_HEIGHT : 0);
              const verticalEnd = to.y + (down ? 0 : NODE_HEIGHT);
              const verticalMiddle = (verticalStart + verticalEnd) / 2;
              const path = loop
                ? `M ${from.x + 38} ${from.y} C ${from.x - 30} ${from.y - 96 - parallel * 50}, ${from.x + NODE_WIDTH + 30} ${from.y - 96 - parallel * 50}, ${from.x + NODE_WIDTH - 38} ${from.y}`
                : returnDown ? `M ${from.x + NODE_WIDTH / 2} ${from.y + NODE_HEIGHT} C ${from.x + NODE_WIDTH / 2} ${from.y + NODE_HEIGHT + 80}, ${to.x + NODE_WIDTH + 80} ${y2}, ${to.x + NODE_WIDTH} ${y2}`
                  : vertical ? `M ${from.x + NODE_WIDTH / 2} ${verticalStart} C ${from.x + NODE_WIDTH / 2 + parallel * 90} ${verticalMiddle}, ${to.x + NODE_WIDTH / 2 + parallel * 90} ${verticalMiddle}, ${to.x + NODE_WIDTH / 2} ${verticalEnd}`
                  : `M ${x1} ${y1} C ${x1 + (forward ? distance : -distance)} ${y1 + bend}, ${x2 + (forward ? -distance : distance)} ${y2 + bend}, ${x2} ${y2}`;
              const labelX = loop ? from.x + NODE_WIDTH / 2 : returnDown ? (from.x + NODE_WIDTH / 2 + to.x + NODE_WIDTH) / 2 + 30 : vertical ? from.x + NODE_WIDTH / 2 + parallel * 67.5 : (x1 + x2) / 2;
              const labelY = loop ? from.y - 72 - parallel * 37.5 : returnDown ? (from.y + NODE_HEIGHT + y2) / 2 + 30 : vertical ? verticalMiddle : (y1 + y2) / 2 + bend * 0.75;
              const color = selected || connected ? '#5865df' : '#8c9bb2';
              const labelWidth = Math.max(84, Math.min(178, link.edge.type.length * 7 + 22));
              return <g key={`${link.edge.type}-${link.pairIndex}`} className={`mapping-connection${selected ? ' is-selected' : ''}`}
                opacity={active ? 1 : 0.4} role="button" tabIndex={0} aria-label={`Relationship ${link.edge.type}: ${link.from} to ${link.to}`}
                onPointerDown={(event) => event.stopPropagation()} onClick={() => onSelect({ kind: 'edge', name: link.edge.type })}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect({ kind: 'edge', name: link.edge.type }); } }}>
                <title>{`${link.from} → ${link.edge.type} → ${link.to}\n${link.fromColumn} → ${link.toColumn}`}</title>
                <path d={path} fill="none" stroke="transparent" strokeWidth="18" />
                <path d={path} fill="none" stroke={color} strokeWidth={selected ? 3 : 1.8} strokeDasharray={active ? undefined : '6 4'} markerEnd={`url(#${marker}-arrow)`} />
                <rect x={labelX - labelWidth / 2} y={labelY - 14} width={labelWidth} height="28" rx="7" fill={selected ? '#eef0ff' : '#fff'} stroke={selected ? '#5865df' : '#dce3ed'} />
                <text x={labelX} y={labelY + 4} textAnchor="middle" fill={selected ? '#4854cd' : '#54647c'} fontSize="11" fontWeight="600">{short(link.edge.type, 22)}</text>
              </g>;
            })}
            {nodes.map((node, index) => {
              const point = positions[node.label] ?? layout[node.label];
              if (!point) return null;
              const color = nodeColor(index);
              const selected = selection?.kind === 'node' && selection.name === node.label;
              const props = propertyNames(node.properties).filter((name) => name !== node.key && !(node.exclude_properties ?? []).includes(name));
              const active = included(node, document);
              return <g key={node.label} transform={`translate(${point.x},${point.y})`} className="mapping-diagram-node" role="button" tabIndex={0}
                aria-label={`Node ${node.label}`} onPointerDown={(event) => start(event, node.label)}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect({ kind: 'node', name: node.label }); } }} opacity={active ? 1 : 0.5}>
                <title>{`${node.label}\n${tableName(node, document)}\nKey: ${node.key || 'Not selected'}`}</title>
                <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx="12" fill="#fff" stroke={selected ? color : '#dce3ed'} strokeWidth={selected ? 2.5 : 1.2} strokeDasharray={active ? undefined : '5 3'} />
                <rect x="12" y="14" width="30" height="30" rx="9" fill={color} fillOpacity="0.12" />
                <circle cx="27" cy="29" r="6" fill="none" stroke={color} strokeWidth="2" />
                <text x="52" y="28" fill="#26344c" fontSize="14" fontWeight="650">{short(node.label, 18)}</text>
                <text x="52" y="44" fill="#8390a4" fontSize="10">NODE TYPE{active ? '' : ' · EXCLUDED'}</text>
                <text x="16" y="68" fill="#69788f" fontSize="11">{short(tableName(node, document), 29)}</text>
                <line x1="16" y1="80" x2={NODE_WIDTH - 16} y2="80" stroke="#edf0f5" />
                <text x="16" y="101" fill="#b68632" fontSize="11">KEY</text>
                <text x="52" y="101" fill={node.key ? '#46546a' : '#d94f4f'} fontSize="11">{short(node.key || 'Select a key', 23)}</text>
                <text x="16" y="123" fill="#78869b" fontSize="11">{short(props.slice(0, 2).join(', ') || 'All table properties', 26)}</text>
                <text x="16" y="141" fill="#9ca7b7" fontSize="10">{props.length ? `${props.length} mapped properties` : 'Properties from table'}</text>
                <circle cx="0" cy={NODE_HEIGHT / 2} r="4" fill="#fff" stroke={color} strokeWidth="1.5" />
                <circle cx={NODE_WIDTH} cy={NODE_HEIGHT / 2} r="4" fill="#fff" stroke={color} strokeWidth="1.5" />
              </g>;
            })}
          </g>
        </svg>
      ) : <div className="mapping-canvas-empty"><Empty description="Your graph starts with a node"><p>Import a mapping, discover tables, or add your first node.</p></Empty></div>}
      <div className="mapping-canvas-footer"><span>{missing ? `${missing} connection${missing === 1 ? '' : 's'} have missing endpoints` : 'Drag nodes · drag background to pan · scroll to zoom'}</span>
        <Space.Compact>
          <Tooltip title="Zoom out"><Button aria-label="Zoom out" icon={<MinusOutlined />} onClick={() => zoomAt(0.8, { x: size.width / 2, y: size.height / 2 })} /></Tooltip>
          <Button className="mapping-zoom-value" onClick={() => fit(positions)}>{Math.round(view.zoom * 100)}%</Button>
          <Tooltip title="Zoom in"><Button aria-label="Zoom in" icon={<PlusOutlined />} onClick={() => zoomAt(1.25, { x: size.width / 2, y: size.height / 2 })} /></Tooltip>
          <Tooltip title="Fit graph"><Button aria-label="Fit graph" icon={<AimOutlined />} onClick={() => fit(positions)} /></Tooltip>
          <Tooltip title="Arrange nodes"><Button aria-label="Arrange nodes" icon={<ApartmentOutlined />} onClick={() => { setPositions(layout); fit(layout); }} /></Tooltip>
        </Space.Compact>
      </div>
    </div>
  );
};

export default MappingCanvas;
