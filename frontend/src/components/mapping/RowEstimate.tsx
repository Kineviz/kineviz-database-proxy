import React from 'react';
import { Tooltip, Typography } from 'antd';
import { formatCount } from './reducer';
import { useTableColumns } from './columns';

const { Text } = Typography;

/**
 * How many rows a table is estimated to hold.
 *
 * The estimate is the engine's own — `sys.partitions`, `pg_class.reltuples`,
 * `information_schema.TABLES` — which is what makes it cheap enough to show on every row
 * and what makes it an estimate: those statistics name only the tables they have actually
 * counted, so an unanalysed table reports nothing at all. That arrives as no number, and is
 * drawn as `no statistics` rather than as `~0`, because a table that reads as empty is a
 * table a person stops looking at.
 *
 * It rides on the same paged read as the table's columns, so a list of rows does not ask
 * the catalog twice for the same table.
 */
const RowEstimate: React.FC<{ project: string; table: string | null; schema?: string | null }> = ({
  project,
  table,
  schema,
}) => {
  const { result, loading, error } = useTableColumns(project, table, schema);
  if (!table) return <Text type="secondary">—</Text>;
  if (loading && result.rowEstimate === null) return <Text type="secondary">…</Text>;
  if (error) {
    return (
      <Tooltip title={error}>
        <Text type="danger">unknown</Text>
      </Tooltip>
    );
  }
  if (result.rowEstimate === null) {
    // No number is not the same claim as zero: `~0` reads as "this table is empty", and a
    // person stops looking at an empty table — which is what an unanalysed MySQL, or a
    // PostgreSQL whose `pg_stats` the connection may not read, hands back for every table.
    return (
      <Tooltip title="The engine's statistics never named this table, so it is unmeasured rather than empty.">
        <Text type="secondary">no statistics</Text>
      </Tooltip>
    );
  }
  return (
    <Tooltip title="Estimated from the engine's own statistics, not counted">
      <Text>{formatCount(result.rowEstimate)}</Text>
    </Tooltip>
  );
};

export default RowEstimate;
