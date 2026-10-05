import React from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Card, Empty, Select, Space, Spin, Tag, Typography } from 'antd';
import { PartitionOutlined } from '@ant-design/icons';
import { Project, RELATIONAL_TYPES, isRelationalType } from '../types/project';
import { projectService } from '../services/projectService';
import MappingPane from '../components/mapping/MappingPane';
import { errorMessage } from '../components/mapping/reducer';

const { Paragraph, Text, Title } = Typography;

/**
 * Reviewing what a relational schema means as a graph.
 *
 * This page is only about the three relational project types, because a graph mapping is
 * only a question you can ask of a table-and-column catalog. A Spanner, a Neo4j or a Kuzu
 * project is listed here greyed out with the reason rather than left out — reaching this
 * page from a project and finding an empty table is how a person concludes the tool is
 * broken when the honest answer is that their database already names its own nodes.
 *
 * Choosing a project puts it in the URL, so a link to `?project=` lands on the same review
 * and a reload does not lose it.
 */

const GraphMapping: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [failure, setFailure] = React.useState<string | null>(null);
  const selected = params.get('project') || '';

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    projectService
      .getProjects()
      .then((found) => {
        if (cancelled) return;
        const list = Array.isArray(found) ? found : [];
        setProjects(list);
        setFailure(null);
        // A link from elsewhere may name a project that no longer exists; staying on the
        // first relational one is friendlier than an empty page with a name no longer there.
        if (!selected) {
          const first = list.find((project) => isRelationalType(project.database_type));
          if (first) {
            const next = new URLSearchParams(params);
            next.set('project', first.name);
            setParams(next, { replace: true });
          }
        }
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setFailure(errorMessage(caught));
        setProjects([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectService, params, selected, setParams]);

  const chosen = projects.find((project) => project.name === selected) ?? null;
  const relationalCount = projects.filter((project) => isRelationalType(project.database_type)).length;

  const options = projects.map((project) => ({
    value: project.name,
    disabled: !isRelationalType(project.database_type),
    label: (
      <Space size={6}>
        <Text>{project.name}</Text>
        <Tag color={isRelationalType(project.database_type) ? 'green' : 'default'}>
          {project.database_type}
        </Tag>
        {!isRelationalType(project.database_type) ? (
          <Text type="secondary" style={{ fontSize: 11 }}>
            not a table catalog
          </Text>
        ) : null}
      </Space>
    ),
  }));

  const choose = (name: string): void => {
    const next = new URLSearchParams(params);
    next.set('project', name);
    setParams(next);
  };

  const body = (): React.ReactNode => {
    if (loading) return <Spin style={{ display: 'block', margin: '48px auto' }} />;

    if (failure) {
      return (
        <Alert
          type="error"
          showIcon
          message="The project list could not be read"
          description={failure}
        />
      );
    }

    if (!projects.length) {
      return (
        <Empty
          description={
            <Space direction="vertical" size={4}>
              <Text>There are no projects yet.</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Add a MySQL, PostgreSQL or SQL Server project first; a mapping is a reading of a catalog
                that has to be reachable.
              </Text>
            </Space>
          }
        />
      );
    }

    if (!relationalCount) {
      return (
        <Alert
          type="info"
          showIcon
          message="No relational project to map yet"
          description={
            <Space direction="vertical" size={4}>
              <Text style={{ fontSize: 13 }}>
                None of the {projects.length} projects is a MySQL, PostgreSQL or SQL Server database, and a
                graph mapping is only a question you can ask of a table-and-column catalog.
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Other engines name their own nodes and relationships — a graph database does not need this
                page, and a Spanner or BigQuery graph already has one.
              </Text>
            </Space>
          }
        />
      );
    }

    if (!chosen) {
      return (
        <Alert type="warning" showIcon message="No project selected" description="Pick one above." />
      );
    }

    if (!isRelationalType(chosen.database_type)) {
      return (
        <Alert
          type="warning"
          showIcon
          message={`${chosen.name} is a ${chosen.database_type} project`}
          description={
            <Space direction="vertical" size={4}>
              <Text style={{ fontSize: 13 }}>
                This page reads a relational catalog — tables, columns, keys and foreign keys — and turns it
                into records and links. That is the only mapping there is to review here.
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {chosen.database_type === 'spanner' || chosen.database_type === 'bigquery'
                  ? 'Its graph is declared inside the database and is served straight from it.'
                  : chosen.database_type === 'neo4j' ||
                    chosen.database_type === 'memgraph' ||
                    chosen.database_type === 'rocketgraph'
                    ? 'Its nodes and relationships are already named by the database itself.'
                    : 'Its engine stores a graph, so there is nothing to infer from tables.'}
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Relational projects: {RELATIONAL_TYPES.join(', ')}.
              </Text>
            </Space>
          }
        />
      );
    }

    return <MappingPane key={chosen.name} project={chosen.name} databaseType={chosen.database_type} />;
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title={
          <Space>
            <PartitionOutlined />
            <span>Graph Mapping</span>
          </Space>
        }
        extra={
          <Space size={8}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Project
            </Text>
            <Select
              size="small"
              style={{ minWidth: 320 }}
              value={selected || undefined}
              placeholder="Choose a relational project"
              options={options}
              onChange={choose}
            />
          </Space>
        }
      >
        <Title level={5} style={{ marginTop: 0 }}>
          What this schema means as a graph
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          Auto-discovery reads foreign keys as links, and a foreign key is a real direction — but not always
          the one the sentence in the room uses. This page is where the proposal is renamed, flipped,
          excluded, extended, and checked against real rows before it is saved.
        </Paragraph>
      </Card>

      {body()}
    </Space>
  );
};

export default GraphMapping;
