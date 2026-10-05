import React from 'react';
import { Alert, Space, Typography } from 'antd';
import { MappingProblem } from '../../types/project';

const { Text } = Typography;

/**
 * What the proxy said is wrong, in the two places it can be said.
 *
 * A problem that names a document path belongs on the control it names — that is what
 * `indexProblems` is for, and it is why a validator message like `edges[1].endpoints[0]
 * .dst.label: label "Ogranization" is not declared under nodes` is worth more than a red
 * banner. A problem that names nothing the editor can reach (a cap, a schema that will not
 * open, a document-level field) has nowhere to land except here, and dropping it would be
 * the worse half of the bargain: the save still failed, and nothing on screen says so.
 */

/** One problem, with the path it names kept visible. */
const ProblemLine: React.FC<{ problem: MappingProblem; tone?: 'danger' | 'warning' }> = ({ problem, tone }) => (
  <Space direction="vertical" size={0}>
    <Text type={tone === 'warning' ? 'warning' : 'danger'} style={{ fontSize: 12 }}>
      {problem.message}
    </Text>
    {problem.path ? (
      <Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>
        {problem.path}
      </Text>
    ) : null}
  </Space>
);

/** The problems attached to one control, under that control. */
export const ProblemTags: React.FC<{ problems: MappingProblem[] }> = ({ problems }) => (
  <Space direction="vertical" size={0} style={{ marginLeft: 42 }}>
    {problems.map((problem, index) => (
      <Text key={`${problem.path}-${index}`} type="danger" style={{ fontSize: 11 }}>
        {problem.message}
      </Text>
    ))}
  </Space>
);

interface PanelProps {
  problems: MappingProblem[];
  warnings: MappingProblem[];
  /**
   * Problems already shown on their own control. A validator answers one list for the
   * whole document, and repeating every item in two places is how a person starts
   * believing there are twice as many faults as there are.
   */
  shownElsewhere?: number;
  /** A refusal that came from somewhere other than the validator, e.g. a failed save. */
  note?: string | null;
}

const ProblemsPanel: React.FC<PanelProps> = ({ problems, warnings, shownElsewhere = 0, note }) => {
  if (!problems.length && !warnings.length && !note) return null;
  const remaining = problems.length - shownElsewhere;

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      {note ? <Alert type="error" showIcon message="The proxy refused this" description={note} /> : null}
      {problems.length ? (
        <Alert
          type="error"
          showIcon
          message={
            remaining > 0
              ? `${problems.length} ${problems.length === 1 ? 'problem' : 'problems'} with this mapping`
              : `${problems.length} ${problems.length === 1 ? 'problem is' : 'problems are'} on the controls they name`
          }
          description={
            <Space direction="vertical" size={6}>
              {problems.slice(0, 6).map((problem, index) => (
                <ProblemLine key={`${problem.path}-${index}`} problem={problem} />
              ))}
              {problems.length > 6 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  …and {problems.length - 6} more
                </Text>
              ) : null}
            </Space>
          }
        />
      ) : null}
      {warnings.length ? (
        <Alert
          type="warning"
          showIcon
          message={`${warnings.length} ${warnings.length === 1 ? 'warning' : 'warnings'} — a mapping can save with these`}
          description={
            <Space direction="vertical" size={6}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                A warning is the proxy noticing that something here does not match the database it can
                see. It does not block a save, because a mapping is routinely written for a database the
                author cannot reach yet.
              </Text>
              {warnings.slice(0, 8).map((warning, index) => (
                <ProblemLine key={`${warning.path}-${index}`} problem={warning} tone="warning" />
              ))}
              {warnings.length > 8 ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  …and {warnings.length - 8} more
                </Text>
              ) : null}
            </Space>
          }
        />
      ) : null}
    </Space>
  );
};

export default ProblemsPanel;
