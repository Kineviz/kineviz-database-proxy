import axios, { InternalAxiosRequestConfig } from 'axios';
import { message } from 'antd';
import { Project, ProjectCreate, ProjectUpdate, APIInfo, QueryRequest, QueryResponse, SchemaResponse, GraphSchemaResponse, SampleDataResponse, EmbeddedInspectResponse, EngineStatus, StoreListResponse, DatabaseConfig, GraphMappingDocument, MappingEnvelope, MappingProblemLike, MappingTextResponse, TablePage, PreviewRequest, PreviewResponse } from '../types/project';

const API_BASE_URL = process.env.NODE_ENV === 'production' ? '' : 'http://localhost:9080';

// Cached tokens for authenticated requests
let cachedApiKey: string | null = null;
// Initialize admin token from localStorage (handles page refresh and HMR)
let cachedAdminToken: string | null = localStorage.getItem('adminToken');

// Promise that resolves when API key initialization is complete
let initializationPromise: Promise<void> | null = null;
let isInitialized = false;

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor - waits for initialization and adds auth headers
api.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    // Don't wait for initialization when fetching settings (avoid deadlock)
    const isSettingsRequest = config.url?.includes('/api/settings');
    const isAdminRequest = config.url?.includes('/api/admin');
    
    // Wait for initialization to complete before making other requests
    if (!isSettingsRequest && !isAdminRequest && initializationPromise && !isInitialized) {
      await initializationPromise;
    }
    
    // Add admin token for management endpoints
    // Always check localStorage as fallback (handles HMR reloads in dev)
    const adminToken = cachedAdminToken || localStorage.getItem('adminToken');
    if (adminToken) {
      config.headers['X-Admin-Token'] = adminToken;
      // Update cache if it was stale
      if (!cachedAdminToken) {
        cachedAdminToken = adminToken;
      }
    }
    
    // Add API key for database endpoints
    if (cachedApiKey) {
      config.headers['X-API-Key'] = cachedApiKey;
    }
    
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor
api.interceptors.response.use(
  (response) => {
    return response;
  },
  (error) => {
    console.error('API Error:', error);
    return Promise.reject(error);
  }
);

/**
 * Initialize the API key from settings.
 * Call this on app startup to enable authenticated requests.
 * All other requests will wait for this to complete.
 */
export function initializeApiKey(): Promise<void> {
  if (initializationPromise) {
    return initializationPromise;
  }
  
  initializationPromise = (async () => {
    try {
      const response = await api.get('/api/settings');
      const settings = response.data;
      if (settings.api_key_enabled && settings.api_key) {
        cachedApiKey = settings.api_key;
      } else {
        cachedApiKey = null;
      }
    } catch (error) {
      console.warn('Failed to load API key from settings:', error);
      cachedApiKey = null;
    } finally {
      isInitialized = true;
    }
  })();
  
  return initializationPromise;
}

/**
 * Update the cached API key (called after settings changes)
 */
export function setApiKey(apiKey: string | null): void {
  cachedApiKey = apiKey;
}

/**
 * Set the admin token for management API requests
 */
export function setAdminToken(token: string | null): void {
  cachedAdminToken = token;
  if (token) {
    localStorage.setItem('adminToken', token);
  } else {
    localStorage.removeItem('adminToken');
  }
}

/** One problem as the proxy sent it: an object, a sentence, or a `(path, message)` pair. */
const problemText = (problem: MappingProblemLike): string => {
  if (typeof problem === 'string') return problem;
  if (Array.isArray(problem)) return [problem[0], problem[1]].filter(Boolean).join(': ');
  return problem.message ?? '';
};

/**
 * What the four mapping routes answer. The wire names the document `model`
 * (`_mapping_answer` in `api/relational.py`) and the editor holds it as `mapping`, so the two
 * spellings sit on one type and the join happens once. `source: "draft"` from `/discover` is a
 * proposal, not a failure, and travels as itself.
 */
export interface RelationalAnswer extends MappingEnvelope {
  model?: GraphMappingDocument | null;
  source?: 'override' | 'none' | 'draft';
  valid?: boolean;
  text?: string | null;
  project?: string;
  id?: string;
  backend?: string;
  saved?: boolean;
  removed?: boolean;
  nodes?: number;
  edges?: number;
}

/** The one place `model` becomes `mapping`; a route that answers neither leaves it null. */
const asEnvelope = (answer: RelationalAnswer): RelationalAnswer => ({
  ...answer,
  mapping: answer.mapping ?? answer.model ?? null,
});

/**
 * Whether a 404 is `?format=yaml`'s documented "no saved mapping" rather than a missing
 * project: the first is empty text to the panes, the second has to stay an error.
 */
const isMissingOverride = (caught: unknown): boolean => {
  const response = (caught as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response;
  if (response?.status !== 404) return false;
  const detail = response.data?.detail;
  return typeof detail === 'string' && detail.includes('has no saved mapping');
};

export class ProjectService {
  async getProjects(): Promise<Project[]> {
    const response = await api.get('/api/project/list');
    return response.data;
  }

  async getProject(id: string): Promise<Project> {
    const response = await api.get(`/api/project/${id}`);
    return response.data;
  }

  async createProject(project: ProjectCreate): Promise<Project> {
    // A mapping a person brought with the project is text the browser only read, never
    // parsed. `POST /api/project/create` takes a config, not a file, so the text follows
    // the project onto the mapping route the moment it exists — one parser, on the server.
    const { graph_mapping_yaml: mappingText, ...body } = project;
    const response = await api.post('/api/project/create', body);
    const name = body.name;
    if (mappingText && mappingText.trim() && name) {
      try {
        const saved = await this.saveMapping(name, { yaml: mappingText });
        const problems =
          saved.problems === null || saved.problems === undefined
            ? []
            : Array.isArray(saved.problems)
              ? (saved.problems as MappingProblemLike[])
              : [saved.problems as MappingProblemLike];
        if (problems.length) {
          // The project is real and so is this mapping: the save reports what will not load rather
          // than refusing it, so what is left to do is named, not the file.
          message.warning(
            `Project created, and the mapping saved with ${problems.length} ${
              problems.length === 1 ? 'problem' : 'problems'
            }: ${problems.map(problemText).filter(Boolean).join('; ') || 'see Graph Mapping'}. ` +
              'GraphXR will not read this schema until they are fixed.',
          );
        } else {
          message.success('Mapping file applied to the new project');
        }
      } catch (caught) {
        message.warning('Project created, but the mapping file could not be applied. Open Graph Mapping to upload it again.');
        console.error('mapping import failed:', caught);
      }
    }
    return response.data;
  }

  async updateProject(id: string, project: ProjectUpdate): Promise<Project> {
    const response = await api.put(`/api/project/update?project_id=${id}`, project);
    return response.data;
  }

  async deleteProject(id: string): Promise<void> {
    await api.delete(`/api/project/delete?project_id=${id}`);
  }

  async getDatabaseInfo(databaseType: string, projectName: string): Promise<APIInfo> {
    const response = await api.get(`/api/${databaseType}/${projectName}`);
    return response.data;
  }

  async executeQuery(
    databaseType: string,
    projectName: string,
    queryRequest: QueryRequest
  ): Promise<QueryResponse> {
    const response = await api.post(`/api/${databaseType}/${projectName}/query`, queryRequest);
    return response.data;
  }

  async getSchema(databaseType: string, projectName: string): Promise<SchemaResponse> {
    const response = await api.get(`/api/${databaseType}/${projectName}/schema`);
    return response.data;
  }

  async getGraphSchema(databaseType: string, projectName: string): Promise<GraphSchemaResponse> {
    const response = await api.get(`/api/${databaseType}/${projectName}/graphSchema`);
    return response.data;
  }

  async getSampleData(databaseType: string, projectName: string): Promise<SampleDataResponse> {
    const response = await api.get(`/api/${databaseType}/${projectName}/sampleData`);
    return response.data;
  }

  async testConnection(databaseType: string, projectName: string): Promise<{ success: boolean; message: string }> {
    const response = await api.post(`/api/${databaseType}/${projectName}/test`);
    return response.data;
  }

  async testConnectionWithConfig(
    databaseType: string,
    config: Record<string, any>,
  ): Promise<{ success: boolean; message: string }> {
    const response = await api.post(`/api/${databaseType}/test_config`, config);
    return response.data;
  }

  // Settings API methods
  async getSettings(): Promise<{ api_key: string | null; api_key_enabled: boolean; api_key_env_configured: boolean }> {
    const response = await api.get('/api/settings');
    return response.data;
  }

  async updateSettings(settings: { api_key_enabled: boolean }): Promise<{ api_key: string | null; api_key_enabled: boolean; api_key_env_configured: boolean }> {
    const response = await api.put('/api/settings', settings);
    return response.data;
  }

  async generateAndSaveApiKey(): Promise<{ api_key: string | null; api_key_enabled: boolean; api_key_env_configured: boolean }> {
    const response = await api.post('/api/settings/generate-key');
    return response.data;
  }

  // Google Cloud API methods
  async listGoogleProjects(auth: any, authType: string): Promise<any[]> {
    const response = await api.post('/api/google/spanner/list_projects', {
      auth,
      auth_type: authType,
    });
    return response.data;
  }

  async listGoogleDatabases(auth: any, authType: string): Promise<any[]> {
    const response = await api.post('/api/google/spanner/list_databases', {
      auth,
      auth_type: authType,
    });
    return response.data;
  }

  /**
   * Google Cloud projects that hold BigQuery datasets, each with its datasets.
   * The property graphs inside a dataset cost a query each, so they are a
   * separate call rather than part of this listing.
   */
  async listBigQueryProjects(auth: any, authType: string): Promise<any[]> {
    const response = await api.post('/api/google/bigquery/list_projects', {
      auth,
      auth_type: authType,
    });
    return response.data;
  }

  async listBigQueryGraphs(auth: any, authType: string): Promise<any[]> {
    const response = await api.post('/api/google/bigquery/list_graphs', {
      auth,
      auth_type: authType,
    });
    return response.data;
  }

  async refreshGoogleToken(refreshToken: string): Promise<any> {
    const response = await api.post('/api/google/refresh-token', {
      refresh_token: refreshToken,
    });
    return response.data;
  }

  // -- embedded stores (Kuzu / Ladybug / LatticeDB) --------------------------

  /** Identify the store at a path the user typed, and resolve the engine for it. */
  async inspectEmbeddedPath(path: string, engineVersion?: string): Promise<EmbeddedInspectResponse> {
    const response = await api.post('/api/embedded/inspect', {
      path,
      engine_version: engineVersion || undefined,
    });
    return response.data;
  }

  /**
   * Upload a dropped store.
   *
   * `overwrite` is false by default and the proxy answers 409 when something is
   * already there, so replacing a database is always a deliberate second call.
   */
  async uploadEmbeddedStore(
    file: File,
    projectName: string,
    overwrite = false,
    folder?: string,
  ): Promise<EmbeddedInspectResponse> {
    const body = new FormData();
    body.append('file', file);
    body.append('project', projectName || '');
    // The store library files uploads under a folder the user picked; the project
    // form files them under the project. Same directory on disk either way.
    body.append('folder', folder || '');
    body.append('overwrite', overwrite ? 'true' : 'false');
    const response = await api.post('/api/embedded/upload', body, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // A store is a database file; the default 30s is for JSON.
      timeout: 0,
    });
    return response.data;
  }

  async listEngines(): Promise<EngineStatus[]> {
    const response = await api.get('/api/embedded/engines');
    return response.data;
  }

  /** Start a download and return at once; poll `listEngines` for progress. */
  async installEngine(engine: string, version: string): Promise<EngineStatus> {
    const response = await api.post(`/api/embedded/engines/${engine}/${version}/install`);
    return response.data;
  }

  /**
   * One page of the store library.
   *
   * Paged at the proxy rather than fetched whole: the listing reads a header per
   * row it returns, so asking for everything would ask it to open every file on
   * the disk.
   */
  async listStores(params: {
    offset?: number;
    limit?: number;
    search?: string;
    includeExternal?: boolean;
  } = {}): Promise<StoreListResponse> {
    const response = await api.get('/api/embedded/stores', {
      params: {
        offset: params.offset ?? 0,
        limit: params.limit ?? 25,
        search: params.search || '',
        include_external: params.includeExternal ? true : false,
      },
    });
    return response.data;
  }

  /**
   * Delete one store from the library.
   *
   * Answers 409 with the project names while a project still points at it, so
   * `force` is always a deliberate second call.
   */
  async deleteStore(relativePath: string, force = false): Promise<{ removed: string; used_by: string[] }> {
    const response = await api.delete('/api/embedded/stores', {
      params: { path: relativePath, force },
    });
    return response.data;
  }

  // -- relational graph mapping ------------------------------------------------
  //
  // The routes live under `/api/relational`, which is a literal prefix and so must be
  // registered before `/api/{database_type}/…` — otherwise `mapping` reads as a database
  // type. `{project}` is the project's **name**, the same identifier the generic
  // `/api/{database_type}/{project_name}/…` surface resolves with `get_project_by_name`.
  //
  // Text and structure are separate calls on purpose. The browser never parses or emits
  // YAML: it sends text and takes text back, and the only program that turns one into the
  // other is the proxy.

  /** The annotated example the proxy ships. Needs no project and reads no database. */
  async getMappingExample(): Promise<MappingTextResponse> {
    const response = await api.get('/api/relational/mapping/example');
    return response.data;
  }

  /**
   * What this project has saved — the override, not the effective mapping: `source` answers
   * `"override"` or `"none"`, and nothing here reaches the customer's database.
   */
  async getMapping(project: string): Promise<RelationalAnswer> {
    const response = await api.get<RelationalAnswer>(
      `/api/relational/${encodeURIComponent(project)}/mapping`,
    );
    return asEnvelope(response.data);
  }

  /**
   * The stored override as text, which is what Download writes and the YAML pane shows.
   * `?format=yaml` answers the file itself (`text/yaml`), not the `{name, text}` the example
   * route answers, so it is wrapped here; its documented 404 for "never saved" is empty text.
   */
  async getMappingText(project: string): Promise<MappingTextResponse> {
    try {
      const response = await api.get<string>(
        `/api/relational/${encodeURIComponent(project)}/mapping`,
        { params: { format: 'yaml' }, responseType: 'text' },
      );
      return { text: response.data ?? '', format: 'yaml' };
    } catch (caught: unknown) {
      if (!isMissingOverride(caught)) throw caught;
      return { text: null, format: 'yaml' };
    }
  }

  /**
   * Ask whether a draft would load, without loading it.
   *
   * Answers every problem named by path, so a mistake sits under the control that caused it.
   * The route reads `model` or `yaml` and refuses a body with neither, so the editor's older
   * `{ mapping: … }` spelling is renamed here: as it stood every form-path check came back
   * refused. `/validate` answers `model` and `text` the way the save does, so the structured
  * tabs and the YAML tab are both fed from one check, with no second parser in the browser.
   */
  async validateMapping(
    project: string,
    body: { mapping: GraphMappingDocument } | { model: GraphMappingDocument } | { yaml: string },
  ): Promise<RelationalAnswer> {
    const wire =
      'yaml' in body
        ? { yaml: body.yaml }
        : { model: 'mapping' in body ? body.mapping : body.model };
    const response = await api.post<RelationalAnswer>(
      `/api/relational/${encodeURIComponent(project)}/validate`,
      wire,
    );
    return asEnvelope(response.data);
  }

  /**
   * Save an override, or take it away.
   *
   * Removing is a **null body**: the route tests `if body is None` and answers `removed: true`.
   * `{ model: null }` is a body with neither half and is refused like an empty save, so the
   * alias the toolbar sends is read as the removal it means.
   */
  async saveMapping(
    project: string,
    body: { model: GraphMappingDocument } | { yaml: string } | { model: null } | null,
  ): Promise<RelationalAnswer> {
    const removing = body === null || ('model' in body && body.model === null);
    const response = await api.put<RelationalAnswer>(
      `/api/relational/${encodeURIComponent(project)}/mapping`,
      removing ? null : body,
    );
    return asEnvelope(response.data);
  }

  /**
   * What discovery would propose, with the reasoning behind each guess.
   *
   * Nothing is stored: the route says so with `source: "draft"`, a state of the proposal and
   * not a failure. An optional `{ schema, connection }` body reads another schema or a draft.
   */
  async discoverMapping(project: string): Promise<RelationalAnswer> {
    const response = await api.post<RelationalAnswer>(
      `/api/relational/${encodeURIComponent(project)}/discover`,
    );
    return asEnvelope(response.data);
  }

  /**
   * One page of the catalog.
   *
   * Paged at the proxy: an ERP schema holds thousands of tables and a picker that asked
   * for all of them to draw one dropdown is the mistake the paging rule exists to stop.
   */
  async listRelationalTables(
    project: string,
    params: { page?: number; pageSize?: number; search?: string; schema?: string } = {},
  ): Promise<TablePage> {
    const response = await api.get(`/api/relational/${encodeURIComponent(project)}/tables`, {
      params: {
        page: params.page ?? 1,
        page_size: params.pageSize ?? 50,
        search: params.search || undefined,
        schema: params.schema || undefined,
      },
    });
    return response.data;
  }

  /**
   * The same page of names against a connection that has **not** been saved.
   *
   * `POST /{project}/tables` is the identical walk, so a draft cannot drift from the project it
   * may become and picking a graph does not force a save. Credentials ride in the body and
   * never the query string — a URL ends up in an access log. Nothing has to exist yet.
   */
  async listDraftTables(
    project: string,
    connection: DatabaseConfig,
    params: {
      page?: number;
      pageSize?: number;
      search?: string;
      schema?: string;
      includeColumns?: boolean;
      table?: string;
    } = {},
  ): Promise<TablePage> {
    const response = await api.post(`/api/relational/${encodeURIComponent(project)}/tables`, {
      project,
      connection,
      schema: params.schema || undefined,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
      search: params.search || undefined,
      include: params.includeColumns ? 'columns' : undefined,
      table: params.table || undefined,
    });
    return response.data;
  }

  /** One table's columns — what the column pickers and the property list read. */
  async getTableColumns(
    project: string,
    table: string,
    params: { page?: number; pageSize?: number; schema?: string } = {},
  ): Promise<TablePage> {
    const response = await api.get(`/api/relational/${encodeURIComponent(project)}/tables`, {
      params: {
        include: 'columns',
        table,
        schema: params.schema || undefined,
        page: params.page ?? 1,
        page_size: params.pageSize ?? 200,
      },
    });
    return response.data;
  }

  /**
   * Run one edge pair both ways against real rows.
   *
   * One call, because the question is which of the two directions is the sentence the
   * person means, and answering it from two calls would let a half-failure look like a
   * verdict.
   */
  async previewEdge(project: string, body: PreviewRequest): Promise<PreviewResponse> {
    const response = await api.post(`/api/relational/${encodeURIComponent(project)}/preview`, body, {
      // Two real joins against a customer's database; the JSON default is for local reads.
      timeout: 60000,
    });
    return response.data;
  }
}

export const projectService = new ProjectService();