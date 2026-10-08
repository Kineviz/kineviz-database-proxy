import { GraphMappingDocument, MappingEdge, MappingNode } from '../../types/project';

export type MappingSelection = { kind: 'node' | 'edge'; name: string } | null;
export interface Point { x: number; y: number }
export const NODE_WIDTH = 208;
export const NODE_HEIGHT = 154;
const COLORS = ['#5865df', '#0d9488', '#d18a22', '#be5aa0', '#3783ba', '#82912d'];

export const nodeColor = (index: number): string => COLORS[index % COLORS.length];
export const tableName = (entry: MappingNode | MappingEdge, document: GraphMappingDocument): string => {
  const name = entry.table || ('label' in entry ? entry.label : entry.type);
  const schema = entry.schema ?? document.schema;
  return schema ? `${schema}.${name}` : name;
};

export const included = (entry: MappingNode | MappingEdge, doc: GraphMappingDocument): boolean => {
  const name = 'label' in entry ? entry.label : entry.type;
  const names = [name, entry.table || name].map((value) => value.toLowerCase());
  const matches = (values: string[]) => values.some((value) => names.includes(value.toLowerCase()));
  return entry.include !== false && !matches(doc.exclude ?? [])
    && (!(doc.include ?? []).length || matches(doc.include ?? []));
};

/** Layer the declared topology, including disconnected nodes and cycles. */
export const arrangeNodes = (labels: string[], pairs: [string, string][], maxColumns = Infinity): Record<string, Point> => {
  const incoming = new Set(pairs.filter(([a, b]) => a !== b).map(([, b]) => b));
  const levels = new Map<string, number>();
  const known = new Set(labels);
  const walk = (root: string) => {
    if (levels.has(root)) return;
    const queue = [root];
    levels.set(root, 0);
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const current = queue[cursor];
      pairs.forEach(([from, to]) => {
        if (from === current && known.has(to) && !levels.has(to)) {
          levels.set(to, levels.get(current)! + 1);
          queue.push(to);
        }
      });
    }
  };
  labels.filter((label) => !incoming.has(label)).forEach(walk);
  labels.forEach(walk);
  const columns = new Map<number, string[]>();
  labels.forEach((label) => {
    const level = (levels.get(label) ?? 0) % maxColumns;
    columns.set(level, [...(columns.get(level) ?? []), label]);
  });
  const tallest = Math.max(1, ...Array.from(columns.values(), (group) => group.length));
  const positions: Record<string, Point> = {};
  columns.forEach((group, level) => group.forEach((label, row) => {
    positions[label] = { x: 64 + level * 340, y: 112 + (row + (tallest - group.length) / 2) * 236 };
  }));
  return positions;
};

export interface DiagramLink {
  edge: MappingEdge;
  pairIndex: number;
  from: string;
  to: string;
  fromColumn: string;
  toColumn: string;
}

export const diagramLinks = (doc: GraphMappingDocument): DiagramLink[] =>
  (doc.edges ?? []).flatMap((edge) => (edge.endpoints ?? []).map((pair, pairIndex) => {
    const from = edge.flip ? pair.dst : pair.src;
    const to = edge.flip ? pair.src : pair.dst;
    return { edge, pairIndex, from: from.label, to: to.label, fromColumn: from.column, toColumn: to.column };
  }));
