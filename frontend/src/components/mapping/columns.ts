import React from 'react';
import { projectService } from '../../services/projectService';
import { RelationalColumn, TablePage } from '../../types/project';

/**
 * A table's columns, read once per table rather than once per control.
 *
 * A mapping of any size shows the same table in a dozen places — the record's
 * properties, its key picker, its `show_as`, every link that points at it, the direction
 * check — so asking the catalog from each of those turns one review into hundreds of
 * round trips. This cache is keyed `(project, schema, table)`, shares one in-flight
 * request between callers, and holds exactly what the proxy answered: one page of
 * columns, with the table's full count alongside so a caller can say "3 of 41 columns
 * fetched" instead of pretending a page was the table.
 *
 * `?include=columns&table=` is paged like everything else here. `loadColumns` walks
 * pages only when a caller asks for more than the first page holds, one page per call.
 */

/** How many columns one read brings back. */
export const COLUMN_PAGE_SIZE = 200;

/** The columns of a `?include=columns&table=` reply, whichever key they arrived under. */
export const columnsOf = (page: TablePage): RelationalColumn[] => {
  if (Array.isArray(page.columns)) return page.columns;
  const row = (page.rows ?? [])[0];
  if (row && Array.isArray(row.columns)) return row.columns as RelationalColumn[];
  return [];
};

/**
 * The columns that could key this table, as the proxy reported them.
 *
 * Empty means the reply carried no candidates at all, which the key picker shows as "the
 * proxy reported none" instead of as a list that happens to be empty.
 */
export const keyCandidatesOf = (page: TablePage): string[] | null => {
  if (Array.isArray(page.key_candidates) && page.key_candidates.length) return page.key_candidates;
  const fromFlags = columnsOf(page)
    .filter((column) => column.is_primary_key === true || column.is_unique === true)
    .map((column) => columnName(column))
    .filter(Boolean);
  return fromFlags.length ? fromFlags : null;
};

export interface ColumnEntry {
  name: string;
  type: string;
  nullable?: boolean | null;
}

export interface ColumnsResult {
  columns: ColumnEntry[];
  /** The whole table's column count, as the proxy reported it. */
  total: number;
  /** How many of those are held here, which is not all of them when the table is wide. */
  loaded: number;
  /** The single-column key and unique candidates, when the proxy named them. */
  keyCandidates: string[] | null;
  /** The catalog's own estimate of how many rows this table holds, when it gave one. */
  rowEstimate: number | string | null;
}

export const columnName = (column: RelationalColumn | string): string =>
  typeof column === 'string' ? column : column.name ?? column.column ?? '';

export const columnType = (column: RelationalColumn | string): string =>
  typeof column === 'string' ? '' : column.type ?? column.data_type ?? '';

const normalize = (page: TablePage): ColumnsResult => {
  const columns = columnsOf(page).map((column) => ({
    name: columnName(column),
    type: columnType(column),
    nullable: column.nullable ?? null,
  }));
  const first = (page.rows ?? [])[0];
  return {
    columns,
    total: page.total ?? columns.length,
    loaded: columns.length,
    keyCandidates: keyCandidatesOf(page),
    // The row estimate rides on the table row rather than on the page, so either place is read.
    rowEstimate: page.row_estimate ?? first?.row_estimate ?? null,
  };
};

const EMPTY: ColumnsResult = { columns: [], total: 0, loaded: 0, keyCandidates: null, rowEstimate: null };

const cache = new Map<string, Promise<ColumnsResult>>();

const keyFor = (project: string, table: string, schema?: string | null): string =>
  `${project}|${schema || ''}|${table}`;

/**
 * One table's columns, from the cache when it has them.
 *
 * `refresh` is what a reload button means here: a schema that gained a column since the
 * page was opened should not keep showing the old one until a hard refresh.
 */
export const loadColumns = (
  project: string,
  table: string,
  schema?: string | null,
  refresh = false,
): Promise<ColumnsResult> => {
  if (!project || !table) return Promise.resolve(EMPTY);
  const key = keyFor(project, table, schema);
  if (!refresh) {
    const held = cache.get(key);
    if (held) return held;
  }
  const request = projectService
    .getTableColumns(project, table, { page: 1, pageSize: COLUMN_PAGE_SIZE, schema: schema || undefined })
    .then((page) => {
      const result = normalize(page);
      cache.set(key, Promise.resolve(result));
      return result;
    })
    .catch((caught: unknown) => {
      // A failed read must not sit in the cache, or every later control for that table
      // would show an empty column list it cannot retry.
      cache.delete(key);
      throw caught;
    });
  cache.set(key, request);
  return request;
};

/** Ask for the next page of the same table and add it to what is held. */
export const moreColumns = async (
  project: string,
  table: string,
  schema: string | null | undefined,
): Promise<ColumnsResult> => {
  const key = keyFor(project, table, schema);
  const held = await (cache.get(key) ?? Promise.resolve(EMPTY));
  const page = Math.floor(held.loaded / COLUMN_PAGE_SIZE) + 1;
  const next = normalize(
    await projectService.getTableColumns(project, table, {
      page,
      pageSize: COLUMN_PAGE_SIZE,
      schema: schema || undefined,
    }),
  );
  const seen = new Set(held.columns.map((column) => column.name));
  const merged = [...held.columns, ...next.columns.filter((column) => !seen.has(column.name))];
  const result: ColumnsResult = {
    columns: merged,
    total: next.total || held.total,
    loaded: merged.length,
    keyCandidates: held.keyCandidates ?? next.keyCandidates,
    rowEstimate: held.rowEstimate ?? next.rowEstimate,
  };
  cache.set(key, Promise.resolve(result));
  return result;
};

export const forgetColumns = (project?: string): void => {
  if (!project) {
    cache.clear();
    return;
  }
  Array.from(cache.keys())
    .filter((key) => key.startsWith(`${project}|`))
    .forEach((key) => cache.delete(key));
};

/**
 * The hook every control that names a column goes through.
 *
 * `loading` and `error` are returned rather than thrown away: a properties panel that
 * renders "no columns" while the read is still in flight is how a person concludes their
 * table is empty.
 */
export const useTableColumns = (
  project: string,
  table: string | null | undefined,
  schema?: string | null,
): {
  result: ColumnsResult;
  loading: boolean;
  error: string | null;
  reload: () => void;
  loadMore: () => void;
} => {
  const [result, setResult] = React.useState<ColumnsResult>(EMPTY);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const requestId = React.useRef(0);

  const run = React.useCallback(
    async (refresh: boolean) => {
      if (!project || !table) {
        setResult(EMPTY);
        setError(null);
        return;
      }
      const id = ++requestId.current;
      setLoading(true);
      try {
        const loaded = await loadColumns(project, table, schema, refresh);
        if (id !== requestId.current) return;
        setResult(loaded);
        setError(null);
      } catch (caught: unknown) {
        if (id !== requestId.current) return;
        setError(caught instanceof Error ? caught.message : String(caught));
        setResult(EMPTY);
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [project, table, schema],
  );

  React.useEffect(() => {
    run(false);
  }, [run]);

  const loadMore = React.useCallback(() => {
    if (!table) return;
    setLoading(true);
    moreColumns(project, table, schema)
      .then((merged) => {
        setResult(merged);
        setError(null);
      })
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setLoading(false));
  }, [project, table, schema]);

  return { result, loading, error, reload: () => run(true), loadMore };
};

/** A column's `name (type)` label, shared so every list spells it the same way. */
export const describeColumn = (column: ColumnEntry): string =>
  column.type ? `${column.name}  (${column.type})` : column.name;
