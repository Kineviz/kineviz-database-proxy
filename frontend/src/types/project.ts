export type DatabaseType =
  | 'spanner'
  | 'bigquery'
  | 'rocketgraph'
  | 'neo4j'
  | 'memgraph'
  | 'kuzu'
  | 'ladybug'
  | 'latticedb'
  | 'postgresql'
  | 'mysql'
  | 'mssql'
  | 'mongodb';

/**
 * The engines reached as a server over a host and port with a table-and-column
 * catalog behind them. Keep in step with `RELATIONAL_TYPES` in
 * `drivers/relational/engine.py`; a type here is a project type the mapping
 * editor can serve.
 */
export const RELATIONAL_TYPES: DatabaseType[] = ['mssql', 'postgresql', 'mysql'];

export const isRelationalType = (type?: DatabaseType | string | null): boolean =>
  !!type && (RELATIONAL_TYPES as string[]).includes(type);

/** Default port per relational engine, matching the spec's engine surface. */
export const RELATIONAL_DEFAULT_PORT: Record<string, number> = {
  mssql: 1433,
  postgresql: 5432,
  mysql: 3306,
};

/**
 * The engines whose store is a local file rather than a server.
 *
 * A store's first bytes name the engine that wrote it, so this is also the set of
 * answers `/api/embedded/stores` can give for `engine`. Keep it in step with
 * `ENGINES` in `drivers/embedded/store_probe.py`.
 */
export type EmbeddedEngine = 'kuzu' | 'ladybug' | 'latticedb';

export type AuthType = 'oauth2' | 'service_account' | 'google_ADC' | 'username_password' | 'bearer_token';

export interface OAuthConfig {
  client_id?: string;
  client_secret?: string;
  redirect_uri?: string;
  scopes?: string[];
  token?: string;
  refresh_token?: string;
  expires_in?: number;
  last_refreshed?: number;
}

export interface DatabaseConfig {
  type: DatabaseType;
  host?: string;
  port?: number;
  // BigQuery reads database_id as the dataset and graph_name as the property
  // graph; Neo4j reads database_id as the database to open.
  project_id?: string;
  instance_id?: string;
  database_id?: string;
  graph_name?: string;
  // BigQuery only: the dataset's processing location, e.g. "US" or "EU".
  location?: string;
  auth_type: AuthType;
  username?: string;
  password?: string;
  oauth_config?: OAuthConfig;
  service_account_path?: string;
  // Kuzu, Ladybug and LatticeDB (embedded): a store is a local path, not a server.
  // database_path is the store file, or the directory Kuzu 0.10 and older wrote;
  // engine_version pins a release ("0.19" means the newest 0.19.x) and is normally
  // left empty so the file's own header decides; read_only defaults true.
  database_path?: string;
  engine_version?: string;
  read_only?: boolean;
  // RocketGraph, Neo4j and Memgraph
  use_tls?: boolean;
  deployment_mode?: 'standalone' | 'plugin';
  api_base_path?: string;
  // MSSQL, PostgreSQL and MySQL: the schema the graph is read out of. Empty means
  // the engine's own default (dbo / public / the database itself).
  schema_name?: string;
  /** The override document. Absent or null means pure auto-discovery. */
  graph_mapping?: GraphMappingDocument | null;
  options: Record<string, any>;
}

export interface Project {
  id: string;
  name: string;
  database_type: DatabaseType;
  database_config: DatabaseConfig;
  create_time: string;
  update_time: string;
}

export interface ProjectCreate {
  name: string;
  database_type: DatabaseType;
  database_config: DatabaseConfig;
  /**
   * Mapping file text the person attached while creating a relational project.
   *
   * It is stripped from the body before `POST /api/project/create` and sent to
   * `PUT /api/relational/{project}/mapping` once the project exists, because the
   * only program that parses this YAML is the proxy: the browser reads the file as
   * text and never as structure.
   */
  graph_mapping_yaml?: string;
}

export interface ProjectUpdate {
  name?: string;
  database_config?: DatabaseConfig;
}

export interface APIInfo {
  type: DatabaseType;
  api_urls: Record<string, string>;
  version?: string;
}

export interface QueryRequest {
  query: string;
  parameters: Record<string, any>;
}

export interface QueryResponse {
  success: boolean;
  data?: any;
  error?: string;
  execution_time?: number;
}

export interface Category {
  name: string;
  props?: string[];
  keys?: string[];
  propsTypes?: Record<string, string>;
}
export interface Relationship {
  name: string;
  props?: string[];
  keys?: string[];
  propsTypes?: Record<string, string>;
  startCategory: string;
  endCategory: string;
}

export interface SchemaResponse {
  success: boolean;
  data?: Record<string, Record<string, string>>;
  error?: string;
}

export interface GraphSchemaResponse {
  success: boolean;
  data?: {
    categories: Category[];
    relationships: Relationship[];
  };
  error?: string;
}

export interface SampleDataResponse {
  success: boolean;
  data?: Record<string, any>;
  error?: string;
}
/** One engine build the proxy knows about, as reported by /api/embedded/engines. */
export interface EngineStatus {
  engine: string;
  version: string;
  status: 'absent' | 'installing' | 'ready' | 'failed';
  detail: string;
  error?: string | null;
  installed: boolean;
}

/**
 * What a path turned out to be.
 *
 * `success` is false for a path that is not a store — the user is probably still
 * typing — while a `success: true` body with no `resolved_version` means the file
 * is a store whose storage format no known release can read.
 */
export interface EmbeddedInspectResponse {
  success: boolean;
  path?: string | null;
  engine?: EmbeddedEngine | null;
  storage_version?: number | null;
  layout?: 'file' | 'directory' | null;
  description?: string | null;
  candidates: string[];
  resolved_version?: string | null;
  engine_status?: EngineStatus | null;
  error?: string | null;
}

/**
 * One file in the store library — the databases the proxy keeps on its own disk,
 * under `config/databases`.
 *
 * `kind` is what the first bytes say it is, which is not the same question as
 * whether the proxy can serve it: a SQLite file is listed as `sqlite` with
 * `servable: false` rather than hidden, because it really is sitting there.
 */
export interface StoreEntry {
  /** Library-relative, forward slashes. The id the delete route takes back. */
  relative_path: string;
  path: string;
  folder: string;
  name: string;
  size: number;
  /** Unix seconds. */
  modified: number;
  layout: 'file' | 'directory';
  kind: EmbeddedEngine | 'sqlite' | 'duckdb' | 'unknown';
  engine?: EmbeddedEngine | null;
  storage_version?: number | null;
  description: string;
  servable: boolean;
  /** Resolved from the map the proxy already holds, never over the network. */
  resolved_version?: string | null;
  engine_installed: boolean;
  /** Projects pointing at this file. Empty means deleting it breaks nothing. */
  used_by: string[];
}

/** A store a project names that lives outside the library. */
export interface ExternalStore {
  path: string;
  database_type: string;
  used_by: string[];
}

export interface StoreListResponse {
  root: string;
  items: StoreEntry[];
  total: number;
  offset: number;
  limit: number;
  folders: string[];
  external: ExternalStore[];
}

// -- relational graph mapping --------------------------------------------------
//
// These mirror `models/graph_mapping.py`, which is normative: the document is what
// the editor holds and what a save writes back, and `GraphMapping` refuses unknown
// keys, so nothing here may invent a field. `properties` is a **map** of column to
// type (a hand-written list is accepted on the way in and comes back as a map), and
// `tuning` is carried untouched because dropping it would be an edit nobody made.

/** One end of a link pair: a declared label and the link-table column holding its key. */
export interface MappingEndpoint {
  label: string;
  /** A column on the **link** table, never the node's own key column. */
  column: string;
  column_type?: string | null;
}

/** One `(src, dst)` a link type connects; several of these is a polymorphic link. */
export interface MappingPair {
  src: MappingEndpoint;
  dst: MappingEndpoint;
  /**
   * Rows discovery saw for this pair; it decides which pair a schema panel shows.
   * `null` means the engine's statistics never named this table, which is not the same
   * fact as `0` and must never be rendered as one.
   */
  row_estimate?: number | null;
}

export interface MappingNode {
  label: string;
  /** Physical table; defaults to the label when empty. */
  table?: string;
  /**
   * The table's own schema, which the wire spells `schema`. Lane A aliases it to
   * `schema_name` in Python because `schema` would shadow `BaseModel.schema`; both
   * spellings parse, so an edit that does not touch it passes it through unchanged.
   */
  schema?: string | null;
  schema_name?: string | null;
  /** The single column identity is minted from. A composite key is refused outright. */
  key?: string | null;
  /** Whitelist of columns: a column not here is invisible in GraphXR. */
  properties?: Record<string, string> | string[] | null;
  exclude_properties?: string[] | null;
  /** False keeps the entry as a decision without putting it in the graph. */
  include?: boolean;
  /** The property a row of this record is labelled with. */
  show_as?: string | null;
  notes?: string | null;
}

export interface MappingEdge {
  type: string;
  table?: string;
  schema?: string | null;
  schema_name?: string | null;
  /** The link table's own key, so parallel links stay distinct. Optional by design. */
  key?: string | null;
  /** True reads the link the other way round from what discovery proposed. */
  flip?: boolean;
  properties?: Record<string, string> | string[] | null;
  exclude_properties?: string[] | null;
  include?: boolean;
  show_as?: string | null;
  /** The reasoning behind the guess, so a person can disagree with a specific one. */
  notes?: string | null;
  endpoints?: MappingPair[] | null;
}

export interface GraphMappingDocument {
  version?: number;
  /** Engine default when empty: dbo, public, or the database itself. */
  schema?: string | null;
  schema_name?: string | null;
  /** When non-empty, only these records are in the graph. */
  include?: string[] | null;
  /** The short form of an entry with `include: false`. */
  exclude?: string[] | null;
  nodes?: MappingNode[] | null;
  edges?: MappingEdge[] | null;
  /** Query-layer overrides. Not the editor's to change, and not to drop. */
  tuning?: Record<string, unknown> | null;
}

/** How an entry came to be what it is, relative to what discovery proposed. */
export type MappingProvenanceTag = 'auto' | 'renamed' | 'flipped' | 'added' | 'excluded';

/**
 * A validator complaint, named by the document path it is about
 * (`edges[1].endpoints[0].dst.label`), which is what lets the editor put the message
 * on the control that is wrong instead of in a banner.
 */
export interface MappingProblem {
  path: string;
  message: string;
}

/**
 * How one problem may arrive: path-named, as a bare sentence (`warnings` are these), or as
 * the `(path, message)` pair `EffectiveMapping.problems` holds before a route reshapes it.
 */
export type MappingProblemLike = MappingProblem | string | (string | null)[];

/**
 * What `/mapping`, `/validate`, `/discover` and the save answer.
 *
 * `warnings` and `problems` may be plain strings or path-named objects, so the
 * editor normalises both rather than asserting one spelling. Every one of them answers the
 * document as `model`, which the service hands over as `mapping`.
 */
export interface MappingEnvelope {
  success?: boolean;
  mapping?: GraphMappingDocument | null;
  /** Per-entry tags, keyed either by document path or by label / type. */
  provenance?: unknown;
  problems?: MappingProblemLike | MappingProblemLike[] | null;
  warnings?: MappingProblemLike | MappingProblemLike[] | null;
  /** `true` when the draft *loads*; a save answers it with `saved` beside it. */
  valid?: boolean;
  /** Only the save answers it: the write happened, whatever `valid` says. */
  saved?: boolean;
  /** Only the save answers it, and only for the `null` body that drops the override. */
  removed?: boolean;
  error?: string | null;
}

/** `?format=yaml` and `/mapping/example` both answer text plus its format. */
export interface MappingTextResponse {
  text?: string | null;
  yaml?: string | null;
  format?: string | null;
  success?: boolean;
  error?: string | null;
}

/** One column as the catalog reports it. Spelling varies by engine. */
export interface RelationalColumn {
  name?: string;
  column?: string;
  type?: string;
  data_type?: string;
  nullable?: boolean | null;
  is_primary_key?: boolean | null;
  is_unique?: boolean | null;
}

/** One row of the paged table listing. */
export interface RelationalTableRow {
  table: string;
  schema?: string | null;
  /** A count while listing, the columns themselves when `include=columns`. */
  columns?: number | RelationalColumn[] | string[] | null;
  row_estimate?: number | null;
}

export interface TablePage {
  rows?: RelationalTableRow[] | null;
  total?: number | null;
  page?: number | null;
  page_size?: number | null;
  /** Present when one table's columns were asked for. */
  table?: string | null;
  row_estimate?: number | string | null;
  schema?: string | null;
  columns?: RelationalColumn[] | null;
  /** The single-column key/unique candidates, which is what the key picker offers. */
  key_candidates?: string[] | null;
  success?: boolean;
  error?: string | null;
}

/** The four answers a direction check can give. */
export type DirectionVerdict = 'settled' | 'backwards' | 'you decide' | 'neither';

/** One direction's rows and how many there were. */
export interface PreviewSide {
  rows?: Record<string, unknown>[] | null;
  count?: number | null;
  error?: string | null;
  /** `from` / `to` column names of the rows, so a label can be read off. */
  columns?: string[] | null;
}

export interface PreviewResponse {
  edge?: string | null;
  table?: string | null;
  pair?: number | null;
  /** True when the check ran the mapping's own flip, i.e. swapped ends. */
  flip?: boolean | null;
  /** The two directions the pair was run in, as the proxy names them. */
  src?: MappingEndpoint | null;
  dst?: MappingEndpoint | null;
  /** The proxy's own summary: a flag today, and a verdict word if it ever names one. */
  settled?: boolean | string | null;
  forwards?: PreviewSide | null;
  backwards?: PreviewSide | null;
  message?: string | null;
  verdict?: string | null;
  error?: string | null;
}

export interface PreviewRequest {
  edge: string;
  table: string;
  /** Which endpoint pair of that link to check. */
  pair: number;
  src: MappingEndpoint;
  dst: MappingEndpoint;
  limit?: number;
}
