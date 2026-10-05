import React from 'react';
import { Button, Select, Space, Spin, Typography } from 'antd';
import { LeftOutlined, RightOutlined } from '@ant-design/icons';
import { projectService } from '../../services/projectService';
import { TablePage } from '../../types/project';
import { columnName, columnType, columnsOf, loadColumns } from './columns';

const { Text } = Typography;

/**
 * Pickers that read the catalog one page at a time.
 *
 * A relational schema can hold three thousand tables, so a dropdown that fetched the
 * whole catalog to render twenty rows of a mapping editor would be the most expensive
 * thing on the page — and every column picker in it would do it again. Both pickers
 * therefore:
 *
 * - fetch nothing until the dropdown is opened;
 * - ask the proxy for one page (`?page=&page_size=`) and offer prev / next inside the
 *   dropdown, rather than fetching everything and slicing it here;
 * - keep showing the value that is already set even when it is on a page that has not
 *   been fetched, because a picker that blanks the stored value the moment it renders
 *   is how an editor quietly rewrites a mapping.
 */

/** How many entries one page of a picker holds. */
const PICKER_PAGE_SIZE = 50;

export interface PickerOption {
  value: string;
  /** Absent on the cleared pick, which is the one option no caller renders. */
  label?: React.ReactNode;
  /** Shown in the option's tooltip, which is where a type or a row estimate goes. */
  title?: string;
  /** Carried so the caller can take a second field with the pick, e.g. a schema. */
  extra?: Record<string, unknown>;
}

interface LoadResult {
  options: PickerOption[];
  total: number;
}

interface PagedSelectProps {
  load: (page: number) => Promise<LoadResult>;
  value: string | null | undefined;
  /** What the stored value reads as when its page has not been fetched. */
  fallbackLabel?: React.ReactNode;
  onChange: (option: PickerOption) => void;
  placeholder?: string;
  disabled?: boolean;
  allowClear?: boolean;
  invalid?: boolean;
  size?: 'small' | 'middle' | 'large';
}

/** One page of options, with the page controls in the dropdown's own footer. */
export const PagedSelect: React.FC<PagedSelectProps> = ({
  load,
  value,
  fallbackLabel,
  onChange,
  placeholder,
  disabled,
  allowClear,
  invalid,
  size = 'small',
}) => {
  const [page, setPage] = React.useState(1);
  const [options, setOptions] = React.useState<PickerOption[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(false);
  const [fetched, setFetched] = React.useState(false);
  const [failure, setFailure] = React.useState<string | null>(null);
  // The latest request wins: a fast next-next-next must not let an older page land last.
  const requestId = React.useRef(0);

  const fetchPage = React.useCallback(
    async (target: number) => {
      const id = ++requestId.current;
      setLoading(true);
      try {
        const result = await load(target);
        if (id !== requestId.current) return;
        setOptions(result.options);
        setTotal(result.total);
        setPage(target);
        setFetched(true);
        setFailure(null);
      } catch (caught: unknown) {
        if (id !== requestId.current) return;
        setFailure(caught instanceof Error ? caught.message : String(caught));
        setOptions([]);
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [load],
  );

  const pages = Math.max(1, Math.ceil((total || 1) / PICKER_PAGE_SIZE));

  const selectValue = (() => {
    if (value === null || value === undefined || value === '') return undefined;
    // antd shows the raw value when no option matches it, which reads as a bare column
    // name in a list of typed ones; the stored label is handed over for that case.
    return options.some((option) => option.value === value) ? value : (fallbackLabel ?? value);
  })();

  return (
    <Select
      size={size}
      showSearch={false}
      disabled={disabled}
      allowClear={allowClear}
      status={invalid ? 'error' : undefined}
      placeholder={placeholder ?? 'Choose…'}
      value={selectValue as string | undefined}
      loading={loading}
      notFoundContent={
        loading ? (
          <Spin size="small" />
        ) : failure ? (
          <Text type="danger" style={{ fontSize: 12 }}>
            {failure}
          </Text>
        ) : fetched ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            Nothing on this page
          </Text>
        ) : null
      }
      options={options}
      onChange={(picked: string) => {
        const option = options.find((item) => item.value === picked);
        if (option) onChange(option);
      }}
      onClear={() => onChange({ value: '' })}
      onOpenChange={(open) => {
        if (open && !fetched && !loading) fetchPage(1);
      }}
      popupRender={(menu) => (
        <Space direction="vertical" size={4} style={{ padding: 4 }}>
          {menu}
          <Space size={4} align="center" style={{ justifyContent: 'space-between', width: '100%' }}>
            <Button
              size="small"
              type="text"
              icon={<LeftOutlined />}
              disabled={page <= 1 || loading}
              onClick={() => fetchPage(page - 1)}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              page {page} of {pages} · {total} total
            </Text>
            <Button
              size="small"
              type="text"
              icon={<RightOutlined />}
              disabled={page >= pages || loading}
              onClick={() => fetchPage(page + 1)}
            />
          </Space>
        </Space>
      )}
    />
  );
};

/** A table as the catalog reports it: a bare name, and the schema it lives in. */
export interface TableChoice {
  table: string;
  schema: string | null;
}

interface TablePickerProps {
  project: string;
  value: string | null | undefined;
  /** The schema the stored table belongs to, so the label reads qualified. */
  schema?: string | null;
  onChange: (choice: TableChoice) => void;
  disabled?: boolean;
  invalid?: boolean;
  placeholder?: string;
}

/**
 * Pick a table.
 *
 * The value a mapping carries is the **bare** table name, with its schema in the
 * entry's own `schema` field; the person sees `schema.table`, because a name that is
 * unique inside one schema is not unique across three of them.
 */
export const TablePicker: React.FC<TablePickerProps> = ({
  project,
  value,
  schema,
  onChange,
  disabled,
  invalid,
  placeholder,
}) => {
  const load = React.useCallback(
    async (page: number): Promise<LoadResult> => {
      const listed = await projectService.listRelationalTables(project, { page, pageSize: PICKER_PAGE_SIZE });
      const rows = listed.rows ?? [];
      return {
        total: listed.total ?? rows.length,
        options: rows.map((row) => {
          const qualified = row.schema ? `${row.schema}.${row.table}` : row.table;
          return {
            value: row.table,
            label: qualified,
            title: `${qualified} · ${row.row_estimate ?? '?'} rows`,
            extra: { schema: row.schema ?? null },
          };
        }),
      };
    },
    [project],
  );

  return (
    <PagedSelect
      load={load}
      value={value}
      fallbackLabel={schema ? `${schema}.${value ?? ''}` : value ?? undefined}
      invalid={invalid}
      disabled={disabled}
      placeholder={placeholder ?? 'Table…'}
      onChange={(option) =>
        onChange({ table: option.value, schema: (option.extra?.schema as string | null) ?? null })
      }
    />
  );
};

interface ColumnPickerProps {
  project: string;
  /** The table whose columns are offered; nothing loads until it is named. */
  table: string | null | undefined;
  schema?: string | null;
  value: string | null | undefined;
  onChange: (column: string, type?: string) => void;
  /**
   * Offer only the columns the proxy reported as single-column key or unique candidates.
   *
   * Which columns qualify is the discovery rule — a composite key is refused outright, not
   * quietly shortened — so the list is read from `key_candidates` rather than derived here.
   * When the reply carries no candidates the picker says so and offers the table's columns:
   * an unusable one is still refused by the save, so the authority never moves.
   */
  keyOnly?: boolean;
  disabled?: boolean;
  invalid?: boolean;
  allowClear?: boolean;
  placeholder?: string;
}

/**
 * Pick a column of one table, typed.
 *
 * The columns arrive one page at a time and only once the dropdown is opened: a record
 * list with a key picker and a `show_as` picker on every row would otherwise ask the
 * catalog for two pages per row before anyone has read a single one of them.
 */
export const ColumnPicker: React.FC<ColumnPickerProps> = ({
  project,
  table,
  schema,
  value,
  onChange,
  keyOnly,
  disabled,
  invalid,
  allowClear,
  placeholder,
}) => {
  const load = React.useCallback(
    async (page: number): Promise<LoadResult> => {
      if (!table) return { options: [], total: 0 };
      if (keyOnly) {
        // Cached per table, so a whole list of key pickers is one read, not two per row.
        const held = await loadColumns(project, table, schema);
        const names = held.keyCandidates ?? held.columns.map((column) => column.name);
        const typed = new Map(held.columns.map((column) => [column.name, column.type]));
        return {
          total: names.length,
          options: names.map((name) => ({
            value: name,
            label: typed.get(name) ? `${name}  (${typed.get(name)})` : name,
            title: held.keyCandidates ? 'reported as a key or unique candidate' : 'no candidates reported',
            extra: { type: typed.get(name) ?? '' },
          })),
        };
      }
      const result: TablePage = await projectService.getTableColumns(project, table, {
        page,
        pageSize: PICKER_PAGE_SIZE,
        schema: schema || undefined,
      });
      const named = columnsOf(result).filter((column) => columnName(column));
      return {
        total: result.total ?? named.length,
        options: named.map((column) => {
          const name = columnName(column);
          const type = columnType(column);
          return { value: name, label: type ? `${name}  (${type})` : name, title: type || undefined, extra: { type } };
        }),
      };
    },
    [project, table, schema, keyOnly],
  );

  return (
    <PagedSelect
      load={load}
      value={value}
      invalid={invalid}
      disabled={disabled || !table}
      allowClear={allowClear}
      placeholder={placeholder ?? (table ? 'column…' : 'pick a table first')}
      onChange={(option) => onChange(option.value, (option.extra?.type as string) ?? undefined)}
    />
  );
};
