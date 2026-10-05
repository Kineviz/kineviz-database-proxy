import React from "react";
import { Card, Col, Form, Input, Row, Select, Spin } from "antd";
import BigqueryRegionSelect from "../BigqueryRegionSelect";
import type { ProjectFormState } from "./state";
import GoogleAuthFields from "./GoogleAuthFields";

const { Option } = Select;

type Props = Pick<ProjectFormState, "authType" | "google">;

/**
 * The BigQuery cascade: project -> dataset -> property graph, with the dataset's own
 * location carried along because a query has to be sent where the dataset lives.
 */
const BigqueryFields: React.FC<Props> = ({ authType, google }) => {
  const {
    bigqueryGraphs,
    bigqueryProjects,
    loading,
    serviceAccountKey,
    setBigqueryGraphs,
    updateServiceAccountKey,
  } = google;

  return (
    <Card
      title="BigQuery Configuration"
      size="small"
      style={{ marginBottom: 16 }}
    >
      <GoogleAuthFields authType={authType} google={google} />

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Form.Item
            label={ <> <span>Project ID </span> {loading.listProjects && <Spin style={{marginLeft:5}} size="small" />} </> }
            name="project_id"
            rules={[{ required: true, message: "Please select a project" }]}
          >
            {authType === "service_account" ? (
              <Input disabled value={serviceAccountKey?.project_id} />
            ) : (
              <Select
                showSearch
                style={{ width: "100%" }}
                loading={loading.listProjects}
                placeholder={loading.listProjects ? "Loading projects..." : "Select a project"}
                notFoundContent={loading.listProjects ? "Loading projects..." : "No projects found"}
                filterOption={(input, option) =>
                  String(option?.children)?.toLowerCase().includes(input.toLowerCase())
                }
                onChange={(value) => {
                  updateServiceAccountKey({
                    ...serviceAccountKey,
                    project_id: value,
                    database_id: null,
                    graph_name: null,
                  });
                  setBigqueryGraphs([]);
                }}
              >
                {bigqueryProjects.map((proj) => (
                  <Option key={proj.id} value={proj.id}>
                    {proj.name || proj.id}
                  </Option>
                ))}
              </Select>
            )}
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            label={ <> <span>Dataset </span> {loading.listProjects && <Spin style={{marginLeft:5}} size="small" />} </> }
            name="database_id"
            tooltip="The BigQuery dataset that holds the property graph"
            rules={[{ required: true, message: "Please select a dataset" }]}
          >
            {(() => {
              const datasets =
                bigqueryProjects.find((proj) => proj.id === serviceAccountKey?.project_id)?.datasets || [];

              const pick = (value: string, location?: string) => {
                // The dataset's location decides where the query runs, so it is
                // carried along with the choice rather than asked for twice.
                updateServiceAccountKey({
                  ...serviceAccountKey,
                  database_id: value,
                  graph_name: null,
                  ...(location ? { location } : {}),
                });
                setBigqueryGraphs([]);
              };

              if (datasets.length === 0 && !loading.listProjects) {
                return <Input placeholder="Enter dataset id" onChange={(e) => pick(e.target.value)} />;
              }

              return (
                <Select
                  showSearch
                  style={{ width: "100%" }}
                  loading={loading.listProjects}
                  placeholder={loading.listProjects ? "Loading datasets..." : "Select a dataset"}
                  filterOption={(input, option) =>
                    String(option?.children)?.toLowerCase().includes(input.toLowerCase())
                  }
                  onChange={(value) =>
                    pick(value, datasets.find((dataset) => dataset.id === value)?.location)
                  }
                >
                  {datasets.map((dataset) => (
                    <Option key={dataset.id} value={dataset.id}>
                      {dataset.name || dataset.id}
                    </Option>
                  ))}
                </Select>
              );
            })()}
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Form.Item
            label={ <> <span>Property Graph </span> {loading.listDatabases && <Spin style={{marginLeft:5}} size="small" />} </> }
            name="graph_name"
            tooltip="Leave empty for a SQL-only project; graph queries need a property graph"
          >
            {bigqueryGraphs.length === 0 && !loading.listDatabases ? (
              <Input
                placeholder="Enter property graph name (optional)"
                onChange={(e) =>
                  updateServiceAccountKey({ ...serviceAccountKey, graph_name: e.target.value })
                }
              />
            ) : (
              <Select
                showSearch
                style={{ width: "100%" }}
                loading={loading.listDatabases}
                filterOption={(input, option) =>
                  String(option?.children)?.toLowerCase().includes(input.toLowerCase())
                }
                onChange={(value) =>
                  updateServiceAccountKey({ ...serviceAccountKey, graph_name: value })
                }
              >
                {bigqueryGraphs.map((graph) => (
                  <Option key={graph.id} value={graph.id}>
                    {graph.name || graph.id}
                  </Option>
                ))}
                <Option value={""}>No Property Graph</Option>
              </Select>
            )}
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            label="Location"
            name="location"
            tooltip="Filled in from the dataset when the listing supplies one. A query has to be sent to the dataset's own location, so a wrong value fails as if the dataset did not exist."
            rules={[{ required: true, message: "Please select a location" }]}
          >
            <BigqueryRegionSelect />
          </Form.Item>
        </Col>
      </Row>
    </Card>
  );
};

export default BigqueryFields;
