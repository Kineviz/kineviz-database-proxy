import {
  GraphMappingDocument,
  MappingEdge,
  MappingEndpoint,
  MappingNode,
  MappingPair,
  MappingProblem,
  MappingProblemLike,
  MappingProvenanceTag,
} from '../../types/project';

/**
 * The draft document, and the one place its edits are described.
 *
 * Every click becomes a `MappingAction` and every action becomes a new document, so the
 * Records tab, the Links tab and the YAML pane are three views of one object instead of
 * three copies that can disagree, and "dirty" is one comparison rather than a flag each
 * control has to remember to set.
 *
 * Nothing in here decides whether a mapping is valid. The proxy answers path-named
 * problems for that, and `indexProblems` puts each one back on the control it names. The
 * one rule kept close is which property list an entry uses (whitelist or exclusion),
 * because the two lists mean the same thing to a person and opposite things to a save —
 * so that choice is made once, here, rather than at each checkbox.
 */

/** What an empty override is: not "no nodes", but "nothing overridden yet". */
export const emptyDocument = (): GraphMappingDocument => ({
  version: 1,
  schema: null,
  include: [],
  exclude: [],
  nodes: [],
  edges: [],
});

/**
 * Fill in what an absent or partial document leaves out.
 *
 * A document the proxy answered carries its defaults; a hand-written or pasted one may
 * carry none of them. `null` — the override a project with no mapping has — is drawn as
 * this empty document, because every edit still has to end up as `PUT` with `{model: …}`
 * and a save of an empty document means the same as no override at all.
 */
export const normalizeDocument = (
  document: GraphMappingDocument | null | undefined,
): GraphMappingDocument => ({
  version: document?.version ?? 1,
  schema: document?.schema ?? document?.schema_name ?? null,
  include: document?.include ?? [],
  exclude: document?.exclude ?? [],
  tuning: document?.tuning ?? undefined,
  nodes: (document?.nodes ?? []).map((node) => ({
    ...node,
    table: node.table ?? node.label,
    schema: node.schema ?? node.schema_name ?? null,
    include: node.include ?? true,
    exclude_properties: node.exclude_properties ?? [],
  })),
  edges: (document?.edges ?? []).map((edge) => ({
    ...edge,
    table: edge.table ?? edge.type,
    schema: edge.schema ?? edge.schema_name ?? null,
    include: edge.include ?? true,
    flip: edge.flip ?? false,
    exclude_properties: edge.exclude_properties ?? [],
    endpoints: edge.endpoints ?? [],
  })),
});

/** The columns a whitelist names, whichever spelling the document used. */
export const propertyNames = (
  properties: Record<string, string> | string[] | null | undefined,
): string[] => (Array.isArray(properties) ? properties.map(String) : Object.keys(properties ?? {}));

/** True when this column currently reaches GraphXR as a property of this entry. */
export const isMappedProperty = (entry: MappingNode | MappingEdge, column: string): boolean => {
  const whitelist = propertyNames(entry.properties);
  // An empty whitelist means "every column but these", so a column is in unless excluded.
  if (whitelist.length) return whitelist.includes(column);
  return !(entry.exclude_properties ?? []).includes(column);
};

const toggleProperty = (
  entry: MappingNode | MappingEdge,
  column: string,
  type: string | undefined,
  on: boolean,
): MappingNode | MappingEdge => {
  const whitelist = propertyNames(entry.properties);
  const excluded = entry.exclude_properties ?? [];
  if (!whitelist.length) {
    return {
      ...entry,
      exclude_properties: on
        ? excluded.filter((name) => name !== column)
        : Array.from(new Set([...excluded, column])),
    };
  }
  const typed = entry.properties && !Array.isArray(entry.properties) ? entry.properties : {};
  const written: Record<string, string> = {};
  (on ? [...whitelist, column] : whitelist.filter((name) => name !== column))
    .filter((name, index, all) => all.indexOf(name) === index)
    .forEach((name) => {
      written[name] = (name === column && type) || typed[name] || '';
    });
  return { ...entry, properties: written, exclude_properties: excluded.filter((name) => name !== column) };
};

export interface DraftState {
  /** What the editor is showing. */
  document: GraphMappingDocument;
  /** What the proxy has, so `dirty` is a comparison and not a flag every control sets. */
  stored: GraphMappingDocument;
  provenance: Record<string, MappingProvenanceTag>;
  /** What discovery last proposed, which an entry a person has since edited is read against. */
  proposal: Proposal | null;
  problems: MappingProblem[];
  warnings: MappingProblem[];
  /**
   * `problems` keyed by the entry and field each one names, kept with the document it was
   * indexed against so a row never has to re-resolve `edges[3]` itself.
   */
  problemIndex: ProblemIndex;
  dirty: boolean;
}

export type MappingAction =
  /** A document from the proxy, or from a server-parsed file: a new start, not an edit. */
  | { kind: 'load'; document: GraphMappingDocument | null; provenance?: unknown;
      problems?: MappingProblem[]; warnings?: MappingProblem[] }
  /** Take the entries of a document and keep this one's document-level fields. */
  | { kind: 'merge'; document: GraphMappingDocument }
  /** Replace the whole draft, document level included — pasted YAML lands this way. */
  | { kind: 'replace'; document: GraphMappingDocument | null; provenance?: unknown }
  /**
   * What `/discover` proposed: the same whole-document install, plus the reasoning the draft
   * is now a diff against. Only this route gets the second half, which is why it is its own
   * kind — an uploaded file carries its author's `notes` and calling those "discovery said"
   * would put words in a machine's mouth.
   */
  | { kind: 'proposal'; document: GraphMappingDocument | null; provenance?: unknown; leftOut?: MappingProblem[] }
  | { kind: 'discard' }
  | { kind: 'saved'; document?: GraphMappingDocument | null; problems?: MappingProblem[]; warnings?: MappingProblem[] }
  | { kind: 'problems'; problems?: MappingProblem[]; warnings?: MappingProblem[] }
  | { kind: 'level'; patch: Partial<Pick<GraphMappingDocument, 'version' | 'schema' | 'include' | 'exclude'>> }
  | { kind: 'node-add'; node: MappingNode }
  | { kind: 'node-remove'; label: string }
  | { kind: 'node-rename'; from: string; to: string }
  | { kind: 'node-patch'; label: string; patch: Partial<MappingNode> }
  /** `dropped` is the list the proxy's own problems named, never one derived here. */
  | { kind: 'node-include'; label: string; include: boolean; dropped?: string[] }
  | { kind: 'edge-add'; edge: MappingEdge }
  | { kind: 'edge-remove'; type: string }
  | { kind: 'edge-patch'; type: string; patch: Partial<MappingEdge> }
  | { kind: 'edge-flip'; type: string }
  | { kind: 'pair-add'; type: string; pair?: MappingPair }
  | { kind: 'pair-remove'; type: string; index: number }
  | { kind: 'pair-patch'; type: string; index: number; side: 'src' | 'dst'; patch: Partial<MappingEndpoint> }
  | { kind: 'property-toggle'; list: 'nodes' | 'edges'; name: string; column: string; type?: string; on: boolean };

const nameOf = (entry: MappingNode | MappingEdge): string => (entry as MappingNode).label ?? (entry as MappingEdge).type;

/**
 * The last thing auto-discovery proposed, kept because a draft is read against it.
 *
 * No route answers this as a separate object: `/discover` answers one document whose entries
 * each carry their own `notes`, plus the caps and skips as sentences in `warnings`. Both
 * halves describe the proposal, so both are held together and both go the moment a document
 * that did not come from discovery is installed — a sentence about a proposal no longer on
 * the screen is a lie on it.
 */
export interface Proposal {
  /** What discovery said for each entry, keyed `nodes:Person` / `edges:WORKS_FOR`. */
  notes: Record<string, string>;
  leftOut: MappingProblem[];
}

/** The reasoning off the entries as they arrived, rather than handed in beside them. */
const proposalNotes = (document: GraphMappingDocument): Record<string, string> => {
  const notes: Record<string, string> = {};
  const keyed = (list: 'nodes' | 'edges'): void => {
    // Keyed by list and name, the way provenance and problems are: a record and a link can
    // carry the same name, and one sentence must not be quoted at both of them.
    (document[list] ?? []).forEach((entry) => {
      if (entry.notes) notes[`${list}:${nameOf(entry)}`] = entry.notes;
    });
  };
  keyed('nodes');
  keyed('edges');
  return notes;
};

/** What the proposal on the screen said about this entry, or null when none is. */
export const proposalNote = (
  proposal: Proposal | null,
  list: 'nodes' | 'edges',
  name: string,
): string | null => proposal?.notes[`${list}:${name}`] ?? null;

/** Find an entry by name, or add one, and run `edit` on that one entry. */
const upsert = <T extends MappingNode | MappingEdge>(
  list: T[],
  name: string,
  make: () => T,
  edit: (entry: T) => T,
): T[] => {
  const index = list.findIndex((entry) => nameOf(entry) === name);
  if (index === -1) return [...list, edit(make())];
  return list.map((entry, at) => (at === index ? edit(entry) : entry));
};

/**
 * Rename a label, and every endpoint that points at it.
 *
 * The proxy applies the same rewrite when a mapping is loaded, so a draft that did not do
 * it would still save — but it would spend the whole time between the rename and the save
 * claiming the link is broken, which is exactly the doubt this page is meant to remove.
 */
const renameLabel = (document: GraphMappingDocument, from: string, to: string): GraphMappingDocument => ({
  ...document,
  include: (document.include ?? []).map((entry) => (entry === from ? to : entry)),
  exclude: (document.exclude ?? []).map((entry) => (entry === from ? to : entry)),
  nodes: (document.nodes ?? []).map((node) => (node.label === from ? { ...node, label: to } : node)),
  edges: (document.edges ?? []).map((edge) => ({
    ...edge,
    endpoints: (edge.endpoints ?? []).map((pair) => ({
      ...pair,
      src: pair.src.label === from ? { ...pair.src, label: to } : pair.src,
      dst: pair.dst.label === from ? { ...pair.dst, label: to } : pair.dst,
    })),
  })),
});

/** Which links still name this label, so an untick can be said out loud before it lands. */
export const linksDependingOn = (document: GraphMappingDocument, label: string): string[] =>
  (document.edges ?? [])
    .filter((edge) => (edge.endpoints ?? []).some((pair) => pair.src.label === label || pair.dst.label === label))
    .map((edge) => edge.type);

export const emptyDraft = (): DraftState => {
  const document = emptyDocument();
  return {
    document,
    stored: document,
    provenance: {},
    proposal: null,
    problems: [],
    warnings: [],
    problemIndex: indexProblems([], document),
    dirty: false,
  };
};

const entries = (document: GraphMappingDocument, list: 'nodes' | 'edges'): (MappingNode | MappingEdge)[] =>
  (list === 'nodes' ? document.nodes : document.edges) ?? [];

/** Write one entry back into its list, leaving the other list untouched. */
const withEntry = <T extends MappingNode | MappingEdge>(
  document: GraphMappingDocument,
  list: 'nodes' | 'edges',
  next: T[],
): GraphMappingDocument => (list === 'nodes' ? { ...document, nodes: next as MappingNode[] } : { ...document, edges: next as MappingEdge[] });

/**
 * An entry that is not in the document yet, from the fields being written to it.
 *
 * Only the name key of its own list goes in: `GraphMapping` refuses an unknown key rather
 * than dropping it, so a `type` left on a node by a sloppy default is a refused save with a
 * path nobody would guess to look at.
 */
const blankEntry = (list: 'nodes' | 'edges', name: string): MappingNode | MappingEdge =>
  list === 'nodes' ? { label: name } : { type: name };

const patchEntry = <T extends MappingNode | MappingEdge>(
  list: T[],
  name: string,
  make: () => T,
  patch: Partial<T>,
): T[] => upsert(list, name, make, (entry) => ({ ...entry, ...patch }));

const patchPair = (
  edges: MappingEdge[],
  type: string,
  index: number,
  edit: (pair: MappingPair) => MappingPair,
): MappingEdge[] =>
  upsert(edges, type, () => ({ type, endpoints: [] }), (edge) => ({
    ...edge,
    // A pair that is not there yet is grown, not skipped: picking an end of a pair the
    // document never had is how "add a link with no FK anywhere" starts.
    endpoints: (() => {
      const pairs = edge.endpoints ?? [];
      if (index >= pairs.length) {
        const blank = (): MappingPair => ({ src: { label: '', column: '' }, dst: { label: '', column: '' } });
        const grown = [...pairs];
        while (grown.length <= index) grown.push(blank());
        return grown.map((pair, at) => (at === index ? edit(pair) : pair));
      }
      return pairs.map((pair, at) => (at === index ? edit(pair) : pair));
    })(),
  }));

/**
 * Run one action.
 *
 * `problemIndex` is recomputed for every result rather than maintained by each branch:
 * an edit can move an entry's position, and an index that still points at the old
 * position is how a problem lands on the wrong row.
 */
export const reduceDraft = (state: DraftState, action: MappingAction): DraftState =>
  withIndex(applyAction(state, action));

const blankIndex = (): ProblemIndex => ({ byEntry: {}, byField: {}, rest: [] });

const withIndex = (next: DraftState): DraftState => ({
  ...next,
  problemIndex: indexProblems(next.problems, next.document),
});

const applyAction = (state: DraftState, action: MappingAction): DraftState => {
  const { document } = state;
  const nodes = document.nodes ?? [];
  const edges = document.edges ?? [];
  const edited = (next: GraphMappingDocument): DraftState => ({ ...state, document: next, dirty: true });
  /** A whole document that did not arrive as a proposal has no reasoning to diff against. */
  const installed = (next: GraphMappingDocument, provenance?: unknown): DraftState => ({
    ...edited(next),
    provenance: provenance ? indexProvenance(provenance) : state.provenance,
    proposal: null,
  });

  switch (action.kind) {
    case 'load': {
      const stored = normalizeDocument(action.document);
      return {
        document: stored,
        stored,
        provenance: indexProvenance(action.provenance),
        proposal: null,
        problems: action.problems ?? [],
        warnings: action.warnings ?? [],
        problemIndex: blankIndex(),
        dirty: false,
      };
    }

    case 'merge': {
      const next = normalizeDocument(action.document);
      return installed({
        ...next,
        schema: document.schema ?? next.schema,
        include: document.include?.length ? document.include : next.include,
        exclude: document.exclude?.length ? document.exclude : next.exclude,
        tuning: document.tuning ?? next.tuning,
      });
    }

    case 'replace':
      return installed(normalizeDocument(action.document), action.provenance);

    case 'proposal': {
      const next = normalizeDocument(action.document);
      return {
        ...edited(next),
        provenance: action.provenance ? indexProvenance(action.provenance) : state.provenance,
        proposal: { notes: proposalNotes(next), leftOut: action.leftOut ?? [] },
      };
    }

    case 'discard':
      return {
        ...state,
        document: state.stored,
        proposal: null,
        problems: [],
        warnings: [],
        problemIndex: blankIndex(),
        dirty: false,
      };

    case 'saved': {
      // The proxy answers the document it stored, so that — not the draft — becomes both
      // the draft and the baseline. A rename or a dropped link it made for us shows up
      // without a reload, and a refused save leaves the draft exactly as it was.
      const problems = action.problems ?? [];
      const next = normalizeDocument(action.document ?? undefined);
      const stored = action.document ? next : state.stored;
      return {
        ...state,
        document: action.document ? next : state.document,
        stored,
        problems,
        warnings: action.warnings ?? state.warnings,
        problemIndex: blankIndex(),
        dirty: problems.length > 0 || Boolean(action.document && !isSame(stored, next)),
      };
    }

    case 'problems':
      return { ...state, problems: action.problems ?? [], warnings: action.warnings ?? state.warnings };

    case 'level':
      return edited({ ...document, ...action.patch });

    case 'node-add':
      return edited(withEntry(document, 'nodes', upsert(nodes, action.node.label, () => action.node, (node) => ({ ...node, ...action.node }))));

    case 'node-remove':
      return edited(withEntry(document, 'nodes', nodes.filter((node) => node.label !== action.label)));

    case 'node-patch':
      return edited(
        withEntry(
          document,
          'nodes',
          patchEntry(nodes, action.label, () => blankEntry('nodes', action.label) as MappingNode, action.patch),
        ),
      );

    case 'node-rename':
      return edited(renameLabel(document, action.from, action.to));

    case 'node-include': {
      const next = withEntry(
        document,
        'nodes',
        upsert(nodes, action.label, () => ({ label: action.label }), (node) => ({ ...node, include: action.include })),
      );
      if (action.include || !action.dropped?.length) return edited(next);
      return edited({ ...next, edges: (next.edges ?? []).filter((edge) => !action.dropped?.includes(edge.type)) });
    }

    case 'edge-add':
      return edited(withEntry(document, 'edges', upsert(edges, action.edge.type, () => action.edge, (edge) => ({ ...edge, ...action.edge }))));

    case 'edge-remove':
      return edited(withEntry(document, 'edges', edges.filter((edge) => edge.type !== action.type)));

    case 'edge-patch':
      return edited(
        withEntry(
          document,
          'edges',
          patchEntry(edges, action.type, () => blankEntry('edges', action.type) as MappingEdge, action.patch),
        ),
      );

    case 'edge-flip':
      return edited(
        withEntry(
          document,
          'edges',
          edges.map((edge) =>
            edge.type === action.type
              ? {
                  ...edge,
                  flip: !edge.flip,
                  endpoints: (edge.endpoints ?? []).map((pair) => ({ ...pair, src: pair.dst, dst: pair.src })),
                }
              : edge,
          ),
        ),
      );

    case 'pair-add':
      return edited(
        withEntry(
          document,
          'edges',
          upsert(edges, action.type, () => ({ type: action.type, endpoints: [] }), (edge) => ({
            ...edge,
            endpoints: [...(edge.endpoints ?? []), action.pair ?? { src: { label: '', column: '' }, dst: { label: '', column: '' } }],
          })),
        ),
      );

    case 'pair-remove':
      return edited(
        withEntry(
          document,
          'edges',
          edges.map((edge) =>
            edge.type === action.type
              ? { ...edge, endpoints: (edge.endpoints ?? []).filter((_, at) => at !== action.index) }
              : edge,
          ),
        ),
      );

    case 'pair-patch':
      return edited(
        withEntry(
          document,
          'edges',
          patchPair(edges, action.type, action.index, (pair) => ({
            ...pair,
            [action.side]: { ...(pair[action.side] ?? { label: '', column: '' }), ...action.patch },
          })),
        ),
      );

    case 'property-toggle': {
      const list = entries(document, action.list);
      const next = upsert(
        list,
        action.name,
        () => blankEntry(action.list, action.name),
        (entry) => toggleProperty(entry, action.column, action.type, action.on),
      );
      return edited(withEntry(document, action.list, next));
    }

    default:
      return state;
  }
};

/** Two documents that mean the same thing, compared as text rather than by identity. */
export const isSame = (left: unknown, right: unknown): boolean => JSON.stringify(left) === JSON.stringify(right);

// -- reading the proxy back ----------------------------------------------------

const pathWords = (path: string): string[] => path.replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean);

/** Which entry a problem path names: `edges[1].endpoints[0].dst.label` -> `edges.1`. */
export const problemEntryKey = (path: string): { list: 'nodes' | 'edges' | ''; name: string } => {
  const words = pathWords(path);
  const first = words[0];
  if (first !== 'nodes' && first !== 'edges') return { list: '', name: '' };
  return { list: first, name: words[1] ?? '' };
};

export interface ProblemIndex {
  byEntry: Record<string, MappingProblem[]>;
  byField: Record<string, MappingProblem[]>;
  /** Problems that name no entry the editor can highlight — a banner, never dropped. */
  rest: MappingProblem[];
}

/**
 * Problems keyed so a control can find its own.
 *
 * The proxy names a problem by document path, and that path is the only thing that tells
 * a person which of two dozen boxes is wrong. A path arrives either by position
 * (`nodes[3].key`) or by name (`edges[WORKS_FOR].src.label`), so both are resolved against
 * the draft on screen; a position that points past the end of its list stays in `rest`
 * rather than being guessed at.
 */
export const indexProblems = (problems: MappingProblem[], document: GraphMappingDocument): ProblemIndex => {
  const namesAt = (list: 'nodes' | 'edges'): string[] =>
    (list === 'nodes' ? document.nodes : document.edges)!.map((entry) => (entry as MappingNode).label ?? (entry as MappingEdge).type);
  const byEntry: Record<string, MappingProblem[]> = {};
  const byField: Record<string, MappingProblem[]> = {};
  const rest: MappingProblem[] = [];

  problems.forEach((problem) => {
    const words = pathWords(problem.path);
    const entry = problemEntryKey(problem.path);
    if (!entry.list) {
      rest.push(problem);
      return;
    }
    const resolved = /^\d+$/.test(entry.name)
      ? words.length > 1
        ? namesAt(entry.list)[Number(entry.name)] ?? ''
        : entry.name
      : entry.name;
    if (!resolved) {
      rest.push(problem);
      return;
    }
    const key = `${entry.list}:${resolved}`;
    const field = words.slice(2).join('.');
    byEntry[key] = [...(byEntry[key] ?? []), problem];
    byField[`${key}|${field}`] = [...(byField[`${key}|${field}`] ?? []), problem];
  });
  return { byEntry, byField, rest };
};

/** The problems attached to one control of one entry. */
export const problemField = (
  index: ProblemIndex,
  list: 'nodes' | 'edges',
  name: string,
  field = '',
): MappingProblem[] => index.byField[`${list}:${name}|${field}`] ?? [];

export const entryProblems = (index: ProblemIndex, list: 'nodes' | 'edges', name: string): MappingProblem[] =>
  index.byEntry[`${list}:${name}`] ?? [];

export const warningFor = (warnings: MappingProblem[], name: string): MappingProblem[] =>
  warnings.filter((warning) => warning.path.includes(name) || warning.message.includes(name));

const TAGS: MappingProvenanceTag[] = ['auto', 'renamed', 'flipped', 'added', 'excluded'];

const asTag = (value: unknown): MappingProvenanceTag | null => {
  if (typeof value === 'string') {
    const lowered = value.toLowerCase().replace(/[_\s-]/g, '');
    return TAGS.find((tag) => lowered.includes(tag)) ?? null;
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return asTag(record.source) ?? asTag(record.tag) ?? asTag(record.provenance) ?? asTag(record.kind);
  }
  return null;
};

/**
 * Provenance, wherever this build put it.
 *
 * Specced as a tag per entry but not as a fixed shape: it arrives keyed by name
 * (`{Person: "renamed"}`), by path (`{'nodes[0]': 'renamed'}`), or as a list of
 * `{path, source}`. All three are read, because a row that says "auto" when a person
 * renamed it is worse than a row with no tag at all.
 */
export const indexProvenance = (provenance: unknown): Record<string, MappingProvenanceTag> => {
  const out: Record<string, MappingProvenanceTag> = {};
  if (!provenance || typeof provenance !== 'object') return out;

  if (Array.isArray(provenance)) {
    provenance.forEach((item, at) => {
      if (!item || typeof item !== 'object') return;
      const record = item as Record<string, unknown>;
      const path = String(record.path ?? `nodes[${at}]`);
      const entry = problemEntryKey(path);
      const tag = asTag(record);
      if (tag && entry.list) out[`${entry.list}:${entry.name}`] = tag;
    });
    return out;
  }

  Object.entries(provenance as Record<string, unknown>).forEach(([key, value]) => {
    const tag = asTag(value);
    if (!tag) return;
    const words = pathWords(key);
    if (words.length >= 2 && (words[0] === 'nodes' || words[0] === 'edges')) {
      out[`${words[0]}:${words[1]}`] = tag;
      return;
    }
    // A bare name does not say which list it belongs to; tag both, and let the row that
    // actually has that name be the one to show it.
    out[`nodes:${key}`] = tag;
    out[`edges:${key}`] = tag;
  });
  return out;
};

export const provenanceTag = (
  provenance: Record<string, MappingProvenanceTag>,
  list: 'nodes' | 'edges',
  name: string,
): MappingProvenanceTag | null => provenance[`${list}:${name}`] ?? null;

export const PROVENANCE_COLOR: Record<MappingProvenanceTag, string> = {
  auto: 'default',
  renamed: 'blue',
  flipped: 'orange',
  added: 'green',
  excluded: 'red',
};

export const formatCount = (value: number | string | null | undefined): string => {
  if (value === null || value === undefined || value === '') return 'unknown';
  if (typeof value === 'string') return value;
  if (!Number.isFinite(value)) return 'unknown';
  // An estimate comes from the engine's own statistics, which is an approximation of a
  // row count. Rendering `1000000` as if it were counted would be a false promise.
  return `~${Math.round(value).toLocaleString('en-US')}`;
};

export const formatList = (items: (string | null | undefined)[] | null | undefined): string =>
  (items ?? []).filter(Boolean).join(', ') || '—';

/**
 * Every spelling of a problem list.
 *
 * `mapping.EffectiveMapping.problems` is a list of ``(path, message)`` tuples and the route
 * may serialise it as objects or leave the pairs as arrays; `warnings` are plain strings.
 * The path is what puts a message under the control that caused it, so a pair is read as a
 * pair rather than stringified into one loses it.
 */
export const toProblems = (value: MappingProblemLike | MappingProblemLike[] | null | undefined): MappingProblem[] => {
  if (value === null || value === undefined) return [];
  const list: unknown[] = Array.isArray(value) ? (value as unknown[]) : [value];
  return list.map((item) => {
    if (typeof item === 'string') return { path: '', message: item };
    if (Array.isArray(item)) return { path: String(item[0] ?? ''), message: String(item[1] ?? '') };
    const problem = item as MappingProblem;
    return { path: problem.path ?? '', message: problem.message ?? String(item) };
  });
};

/** The message an axios failure carries, in the few places a toast needs one. */
export const errorMessage = (caught: unknown): string => {
  const error = caught as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((item) => String(item)).join('; ');
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  return error?.message || String(caught);
};
