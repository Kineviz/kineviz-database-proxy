import React from 'react';
import { Alert, Button, Checkbox, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import { DownOutlined, ReloadOutlined, RightOutlined } from '@ant-design/icons';
import { MappingEdge, MappingNode } from '../../types/project';
import { MappingAction, isMappedProperty, propertyNames } from './reducer';
import { ColumnEntry, describeColumn, useTableColumns } from './columns';

const { Text } = Typography;

/**
 * The columns of one table, and which of them reach GraphXR.
 *
 * A relational record shows what the mapping names and nothing else, so a column that is
 * not here has not been forgotten by the database — it has been left out of the graph,
 * which a person normally finds out a week later, in a graph, going looking for it. So the
 * ones that are *out* are counted on the face of the row and expandable, not hidden behind
 * the ones that are in.
 *
 * Which of `properties` and `exclude_properties` a toggle writes is the reducer's business
 * (`property-toggle`), because an entry may use either list and writing the wrong one makes
 * a checkbox that looks like it does nothing.
 */

interface Props {
  project: string;
  list: 'nodes' | 'edges';
  name: string;
  entry: MappingNode | MappingEdge;
  table: string | null | undefined;
  schema?: string | null;
  dispatch: React.Dispatch<MappingAction>;
  /**
   * Columns that are not properties by nature: for a link these are its endpoint columns,
   * which discovery leaves out of the property list because they are the join, not data.
   */
  structural?: string[];
}

const PropertyEditor: React.FC<Props> = ({ project, list, name, entry, table, schema, dispatch, structural }) => {
  const { result, loading, error, reload, loadMore } = useTableColumns(project, table, schema);
  const [showUnmapped, setShowUnmapped] = React.useState(false);
  const structure = structural ?? [];

  const columns = result.columns.filter((column) => column.name && !structure.includes(column.name));
  const graphName = (column: string) => propertyNames(entry.properties)
    .find((name) => entry.property_columns?.[name] === column) ?? column;
  const mapped = columns.filter((column) => isMappedProperty(entry, graphName(column.name)));
  const unmapped = columns.filter((column) => !isMappedProperty(entry, graphName(column.name)));
  const unread = Math.max(0, result.total - result.loaded);

  if (!table) {
    return (
      <Text type="secondary">
        No table named, so there is nothing to list. Pick one above and its columns appear here.
      </Text>
    );
  }
  if (loading && !columns.length) return <Spin size="small" />;
  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message={`Could not read ${table}`}
        description={
          <Space>
            <Text style={{ fontSize: 12 }}>{error}</Text>
            <Button size="small" icon={<ReloadOutlined />} onClick={reload}>
              Try again
            </Button>
          </Space>
        }
      />
    );
  }

  const toggle = (column: string, on: boolean, type?: string): void => {
    dispatch({ kind: 'property-toggle', list, name, column, on, type });
  };

  const renderRow = (column: ColumnEntry, key: 'in' | 'out') => (
    <div key={`${key}-${column.name}`} style={{ lineHeight: '22px' }}>
      <Checkbox
        checked={key === 'in'}
        onChange={(event) => toggle(graphName(column.name), event.target.checked, column.type)}
      >
        <Text style={{ fontSize: 12 }}>{graphName(column.name) !== column.name
          ? `${graphName(column.name)} ? ${describeColumn(column)}` : describeColumn(column)}</Text>
      </Checkbox>
      {column.name === entry.show_as ? (
        <Tooltip title="The property a row of this record is labelled with">
          <Tag color="blue" style={{ marginLeft: 6 }}>
            show_as
          </Tag>
        </Tooltip>
      ) : null}
    </div>
  );

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Space size={8} wrap>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {mapped.length} of {columns.length} columns are properties
          {structure.length ? ` · ${structure.length} join columns are not` : ''}
        </Text>
        {propertyNames(entry.properties).length ? (
          <Tooltip title="This entry lists its columns explicitly, so a column added to the table later is out until it is named here.">
            <Tag>whitelist</Tag>
          </Tooltip>
        ) : (
          <Tooltip title="No whitelist, so every column of the table is a property except the ones excluded below.">
            <Tag color="default">all columns</Tag>
          </Tooltip>
        )}
        {result.keyCandidates?.length ? (
          <Tooltip title={`Key candidates the proxy reported: ${result.keyCandidates.join(', ')}`}>
            <Tag color="green">{result.keyCandidates.length} key candidate{result.keyCandidates.length === 1 ? '' : 's'}</Tag>
          </Tooltip>
        ) : null}
      </Space>

      <div>{mapped.map((column) => renderRow(column, 'in'))}</div>

      {unmapped.length ? (
        <div>
          <Button
            size="small"
            type="text"
            icon={showUnmapped ? <DownOutlined /> : <RightOutlined />}
            onClick={() => setShowUnmapped((open) => !open)}
          >
            <Text type="warning" style={{ fontSize: 12 }}>
              {unmapped.length} column{unmapped.length === 1 ? '' : 's'} not in the graph
            </Text>
          </Button>
          {showUnmapped ? (
            <div style={{ paddingLeft: 8 }}>
              {unmapped.map((column) => renderRow(column, 'out'))}
            </div>
          ) : null}
        </div>
      ) : null}

      {unread > 0 ? (
        <Button size="small" type="link" onClick={loadMore} loading={loading} style={{ paddingLeft: 0 }}>
          Load {Math.min(unread, 200)} more of {result.total} columns
        </Button>
      ) : null}
    </Space>
  );
};

export default PropertyEditor;
