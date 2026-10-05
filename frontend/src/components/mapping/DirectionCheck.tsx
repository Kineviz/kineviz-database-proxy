import React from 'react';
import { Alert, Button, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import { SwapOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { MappingEdge, PreviewResponse } from '../../types/project';
import { MappingActions, VERDICT_HELP, VERDICT_LABEL, countOf, sideOf, verdictOf } from './actions';
import { errorMessage } from './reducer';

const { Text } = Typography;

/** How many of the rows the proxy brought back are actually shown. */
const ROWS_SHOWN = 5;

/**
 * Which way round this link goes, decided against the customer's own rows.
 *
 * This is the page's reason to exist. A foreign key points one way; the sentence a person
 * means may point the other. Worse, a backwards edge answers *nothing* rather than an error,
 * so the graph just never shows the link and the mapping looks fine. Running the pair in
 * both directions against real data is the only check that turns that into a finding.
 *
 * Rows are printed as `show_as` values, because `1001 → 2001` cannot be seen to be wrong
 * while `Avery Chen → Northwind Logistics` can.
 */

interface Props {
  actions: MappingActions;
  edge: MappingEdge;
  pairIndex: number;
  /** What the mapping says the arrow means, in the person's own words if they wrote any. */
  describe: (from: string, to: string) => string;
  onFlip: () => void;
}

/** The two ends of a preview row, as labels when the proxy could name them, else as keys. */
const rowEnds = (row: Record<string, unknown>): { from: string; to: string; raw: string } => {
  const from = String(row.from_label ?? row.from ?? '');
  const to = String(row.to_label ?? row.to ?? '');
  const raw = `${String(row.from ?? '')} → ${String(row.to ?? '')}`;
  return { from, to, raw };
};

const SideRows: React.FC<{ title: string; response: PreviewResponse; side: 'forwards' | 'backwards' }> = ({
  title,
  response,
  side,
}) => {
  const given = sideOf(response, side);
  const rows = (given.rows ?? []).slice(0, ROWS_SHOWN);
  if (given.error) {
    return (
      <Alert
        type="error"
        showIcon
        message={`${title}: ${given.error}`}
      />
    );
  }
  if (!rows.length) {
    // The one line that must never read as "fine, no problems".
    return (
      <Text type="secondary" style={{ fontSize: 12 }}>
        {title}: no rows — this direction joins nothing.
      </Text>
    );
  }
  return (
    <Space direction="vertical" size={2} style={{ width: '100%' }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {title}: {countOf(given)} row{countOf(given) === 1 ? '' : 's'}
        {given.rows && given.rows.length > ROWS_SHOWN ? ` (showing ${ROWS_SHOWN})` : ''}
      </Text>
      <Table
        size="small"
        pagination={false}
        showHeader={false}
        rowKey={(_, index) => String(index)}
        dataSource={rows}
        columns={[
          {
            key: 'pair',
            render: (_: unknown, row: Record<string, unknown>) => {
              const ends = rowEnds(row);
              const value =
                ends.from || ends.to ? (
                  <Text style={{ fontSize: 12 }}>
                    {ends.from || '—'} <Text type="secondary">→</Text> {ends.to || '—'}
                  </Text>
                ) : (
                  <Text style={{ fontSize: 12 }}>{ends.raw}</Text>
                );
              // The keys are kept one hover away: a label that reads right can still be
              // joined on the wrong column.
              return <Tooltip title={`id ${ends.raw}`}>{value}</Tooltip>;
            },
          },
        ]}
      />
    </Space>
  );
};

const DirectionCheck: React.FC<Props> = ({ actions, edge, pairIndex, describe, onFlip }) => {
  const [response, setResponse] = React.useState<PreviewResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [failure, setFailure] = React.useState<string | null>(null);

  const pair = (edge.endpoints ?? [])[pairIndex];
  if (!pair) return null;

  // The route requires `table` and the join needs both columns; an empty one reaches the engine
  // as a statement about a table nobody named and comes back as a binder exception about the
  // person's own draft. So the missing names are said here instead of being posted as a guess.
  const table = (edge.table ?? '').trim();
  const missing = [
    !table ? 'the table whose rows this link joins' : '',
    !pair.src.column ? `the near end's column${pair.src.label ? ` (${pair.src.label})` : ''}` : '',
    !pair.dst.column ? `the far end's column${pair.dst.label ? ` (${pair.dst.label})` : ''}` : '',
  ].filter(Boolean);

  const run = async (): Promise<void> => {
    if (missing.length) return;
    setLoading(true);
    setFailure(null);
    try {
      const result = await actions.preview({
        edge: edge.type,
        table,
        pair: pairIndex,
        src: pair.src,
        dst: pair.dst,
        limit: ROWS_SHOWN,
      });
      setResponse(result);
      if (result.error) setFailure(result.error);
    } catch (caught: unknown) {
      setResponse(null);
      setFailure(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  };

  const verdict = response ? verdictOf(response) : null;
  const tone = verdict === 'settled' ? 'success' : verdict === 'you decide' ? 'info' : 'error';
  const flip = response?.flip === true;

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <Space size={6} wrap>
        <Button
          size="small"
          icon={<ThunderboltOutlined />}
          loading={loading}
          disabled={missing.length > 0}
          onClick={run}
        >
          Check direction
        </Button>
        {verdict ? (
          <Tag color={verdict === 'settled' ? 'green' : verdict === 'you decide' ? 'blue' : 'red'}>
            {VERDICT_LABEL[verdict]}
          </Tag>
        ) : null}
        <Text type="secondary" style={{ fontSize: 12 }}>
          {describe(pair.src.label || '?', pair.dst.label || '?')}
        </Text>
      </Space>

      {loading ? <Spin size="small" /> : null}

      {missing.length ? (
        <Alert
          type="info"
          showIcon
          message="This pair cannot be checked yet"
          description={
            <Text style={{ fontSize: 12 }}>
              It needs {missing.join(' and ')}. A direction is a fact about real rows, and with no
              join to run nothing has been ruled out either way.
            </Text>
          }
        />
      ) : null}

      {failure ? (
        <Alert
          type="error"
          showIcon
          message="The check did not run"
          description={
            <Text style={{ fontSize: 12 }}>
              {failure} — a check that could not run is not a verdict, and no direction has been ruled out.
            </Text>
          }
        />
      ) : null}

      {verdict && !loading ? (
        <Alert
          type={tone}
          showIcon
          message={
            <Space size={6}>
              <Text strong>{VERDICT_LABEL[verdict]}</Text>
              {flip ? <Tag>checked with the mapping&apos;s flip applied</Tag> : null}
            </Space>
          }
          description={
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {VERDICT_HELP[verdict]}
              </Text>
              <SideRows title="as mapped" response={response as PreviewResponse} side="forwards" />
              <SideRows title="reversed" response={response as PreviewResponse} side="backwards" />
              {verdict === 'backwards' || verdict === 'you decide' ? (
                <Button size="small" icon={<SwapOutlined />} onClick={onFlip}>
                  {verdict === 'backwards'
                    ? 'Flip it — this direction joins nothing'
                    : 'Flip the mapping'}
                </Button>
              ) : null}
              {verdict === 'neither' ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Check the two key columns of{' '}
                  <Text code>
                    {edge.table || 'the link table'}.{pair.src.column || '?'}
                  </Text>{' '}
                  and{' '}
                  <Text code>
                    {edge.table || 'the link table'}.{pair.dst.column || '?'}
                  </Text>{' '}
                  — a wrong one is what makes a table of links join to nothing at all.
                </Text>
              ) : null}
            </Space>
          }
        />
      ) : null}
    </Space>
  );
};

export default DirectionCheck;
