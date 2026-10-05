import React from "react";
import { Card, Col, Form, Input, Row, Select, Spin } from "antd";
import type { ProjectFormState } from "./state";
import GoogleAuthFields from "./GoogleAuthFields";

const { Option } = Select;

type Props = Pick<ProjectFormState, "authType" | "form" | "google">;

/**
 * The Spanner cascade: project -> instance -> database -> property graph, every list
 * read with the credential the auth block above holds.
 */
const SpannerFields: React.FC<Props> = ({ authType, form, google }) => {
  const {
    databases,
    isADC,
    loading,
    projectsList,
    serviceAccountKey,
    updateServiceAccountKey,
  } = google;

  return (
    <Card
      title="Spanner Configuration"
      size="small"
      style={{ marginBottom: 16 }}
    >
      <GoogleAuthFields authType={authType} google={google} />

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Form.Item
            label={ <> <span>Project ID </span>  {loading.listProjects &&  <Spin style={{marginLeft:5}} size="small" />} </> }
            name="project_id"
            rules={[{ required: true, message: "Please select a project" }]}
          >
            {authType === "service_account" && (
              <input
                style={{ width: "100%", height: 32 }}
                disabled={authType === "service_account"}
                value={serviceAccountKey?.project_id}
              />
            )}
            {authType !== "service_account" && (
                <Select style={{ width: "100%", height: 32 }} showSearch
                loading={loading.listProjects}
                placeholder={loading.listProjects ? "Loading projects..." : "Select a project"}
                notFoundContent={loading.listProjects ? "Loading projects..." : "No projects found"}
                value={serviceAccountKey?.project_id}
                onChange={(value) => {
                   updateServiceAccountKey({
                    ...serviceAccountKey,
                    project_id: value,
                    instance_id: null,
                    database_id: null,
                    graph_name: null,
                   })
                }}
                filterOption={(input, option) =>
                String(option?.children)?.toLowerCase().includes(input.toLowerCase())
                }>
                {projectsList.map((proj) => (
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
            label={ <> <span>Instance ID </span>  {loading.listProjects &&   <Spin style={{marginLeft:5}} size="small" />} </> }
            name="instance_id"
            rules={[
              { required: true, message: isADC ? "Please enter or select an instance ID" : "Please select an instance" },
            ]}
          >
            {(() => {
              const instances = projectsList.find((p) => p.id === serviceAccountKey?.project_id)?.instances || [];
              const hasInstances = instances.length > 0;
                  
              if (isADC && !hasInstances) {
                return (
                  <Input 
                    placeholder="Enter instance ID manually"
                    onChange={(e) => {
                      updateServiceAccountKey({
                        ...serviceAccountKey,
                        instance_id: e.target.value,
                        database_id: null,
                        graph_name: null,
                      });
                    }}
                  />
                );
              }
                  
              return (
                <Select 
                  style={{ width: "100%" }} 
                  showSearch
                  filterOption={(input, option) =>
                    String(option?.children)?.toLowerCase().includes(input.toLowerCase())
                  }
                  onChange={(value) => {
                    updateServiceAccountKey({
                      ...serviceAccountKey,
                      instance_id: value,
                      database_id: null,
                      graph_name: null,
                    });
                  }}
                >
                  {instances.map((inst) => (
                    <Option key={inst.id} value={inst.id}>
                      {inst.name || inst.id}
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
            label= { <> <span>Database ID </span>  {loading.listDatabases &&  <Spin style={{marginLeft:5}} size="small" />} </> }
            name="database_id"
            rules={[
              { required: true, message: isADC ? "Please enter or select a database ID" : "Please select a database" },
            ]}
          >
            {(() => {
              const hasDatabases = databases.length > 0;
                  
              if (isADC && !hasDatabases && !loading.listDatabases) {
                return (
                  <Input 
                    placeholder="Enter database ID manually"
                    onChange={(e) => {
                      updateServiceAccountKey({
                        ...serviceAccountKey,
                        database_id: e.target.value,
                        graph_name: null,
                      });
                    }}
                  />
                );
              }
                  
              return (
                <Select 
                  style={{ width: "100%" }} 
                  showSearch
                  onChange={(value) => {
                    updateServiceAccountKey({
                      ...serviceAccountKey,
                      database_id: value,
                      graph_name: null,
                    });
                  }}
                  filterOption={(input, option) =>
                    String(option?.children)?.toLowerCase().includes(input.toLowerCase())
                  }
                  loading={loading.listDatabases}
                  placeholder={loading.listDatabases ? "Loading databases..." : "Select a database"}
                  notFoundContent={loading.listDatabases ? "Loading databases..." : "No databases found"}
                >
                  {databases.map((db) => (
                    <Option key={db.id} value={db.id}>
                      {db.name || db.id}
                    </Option>
                  ))}
                </Select>
              );
            })()}
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            label={ <> <span>Property Graph </span>  {loading.listDatabases &&  <Spin style={{marginLeft:5}} size="small" />} </> }
            name="graph_name"
            rules={[{ required: false }]}
          >
            {(() => {
              const graphDBs = databases.find((db) => db.id === form.getFieldValue("database_id"))?.graphDBs || [];
              const hasGraphDBs = graphDBs.length > 0;
                  
              if (isADC && !hasGraphDBs && !loading.listDatabases) {
                return (
                  <Input 
                    placeholder="Enter property graph name (optional)"
                    onChange={(e) => {
                      updateServiceAccountKey({
                        ...serviceAccountKey,
                        graph_name: e.target.value,
                      });
                    }}
                  />
                );
              }
                  
              return (
                <Select 
                  style={{ width: "100%" }} 
                  showSearch
                  onChange={(value) => {
                    updateServiceAccountKey({
                      ...serviceAccountKey,
                      graph_name: value,
                    });
                  }}
                  filterOption={(input, option) =>
                    String(option?.children)?.toLowerCase().includes(input.toLowerCase())
                  }
                >
                  {graphDBs.map((graphDB) => (
                    <Option key={graphDB.id} value={graphDB.id}>
                      {graphDB.name || graphDB.id}
                    </Option>
                  ))}
                  <Option value={""}>No Property Graph</Option>
                </Select>
              );
            })()}
          </Form.Item>
        </Col>
      </Row>

      {/* <Card title="Connection Options" size="small" type="inner">
        <Row gutter={16}>
          <Col span={8}>
            <Form.Item label="Max Sessions" name="max_sessions">
              <InputNumber min={1} max={1000} style={{ width: "100%" }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item label="Timeout (seconds)" name="timeout">
              <InputNumber min={1} max={300} style={{ width: "100%" }} />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item
              label="Read Only"
              name="read_only"
              valuePropName="checked"
            >
              <Select style={{ width: "100%" }}>
                <Option value={false}>No</Option>
                <Option value={true}>Yes</Option>
              </Select>
            </Form.Item>
          </Col>
        </Row>
      </Card> */}
    </Card>
  );
};

export default SpannerFields;
