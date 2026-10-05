import React from "react";
import { Button, Card, Col, Form, Input, InputNumber, Row, Select, message } from "antd";
import { projectService } from "../../services/projectService";
import type { ProjectFormState } from "./state";

const { Option } = Select;

type Props = Pick<ProjectFormState, "authType" | "databaseType" | "form">;

/**
 * Connection settings for the two bolt servers, which differ only in whether the
 * server has a database to name.
 */
const BoltFields: React.FC<Props> = ({ databaseType, authType, form }) => (
    <Card
      title={databaseType === "neo4j" ? "Neo4j Configuration" : "Memgraph Configuration"}
      size="small"
      style={{ marginBottom: 16 }}
    >
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item
            label="Host"
            name="host"
            tooltip="A host that already names a scheme (neo4j+s://...) is used as given"
            rules={[{ required: true, message: "Please enter host" }]}
          >
            <Input placeholder="e.g. localhost or neo4j+s://abc.databases.neo4j.io" />
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item
            label="Port"
            name="port"
            rules={[{ required: true, message: "Please enter port" }]}
          >
            <InputNumber min={1} max={65535} style={{ width: "100%" }} />
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item label="Use TLS" name="use_tls">
            <Select>
              <Option value={false}>bolt://</Option>
              <Option value={true}>bolt+ssc://</Option>
            </Select>
          </Form.Item>
        </Col>
      </Row>

      {databaseType === "neo4j" && (
        <Row gutter={16}>
          <Col span={24}>
            <Form.Item
              label="Database"
              name="database_id"
              tooltip="Leave empty to use the server's default database"
            >
              <Input placeholder="e.g. neo4j" />
            </Form.Item>
          </Col>
        </Row>
      )}

      <Card
        title="Credentials"
        size="small"
        type="inner"
        extra={<span style={{ fontSize: 12, color: "#999" }}>Leave empty for a server with auth disabled</span>}
      >
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item label="Username" name="username">
              <Input placeholder="e.g. neo4j" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="Password" name="password">
              <Input.Password placeholder="Password" />
            </Form.Item>
          </Col>
        </Row>
      </Card>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Button
            onClick={async () => {
              try {
                const values = await form.validateFields(["host", "port"]);
                const testConfig: Record<string, any> = {
                  type: databaseType,
                  auth_type: authType,
                  host: values.host,
                  port: values.port,
                  use_tls:
                    form.getFieldValue("use_tls") === true ||
                    form.getFieldValue("use_tls") === "true",
                  username: form.getFieldValue("username") || undefined,
                  password: form.getFieldValue("password") || undefined,
                  options: {},
                };
                if (databaseType === "neo4j") {
                  testConfig.database_id = form.getFieldValue("database_id") || undefined;
                }

                const result = await projectService.testConnectionWithConfig(
                  databaseType,
                  testConfig,
                );
                if (result.success) {
                  message.success(result.message || "Connection successful");
                } else {
                  message.error(`Connection failed: ${result.message || "Unknown error"}`);
                }
              } catch (err: any) {
                if (err?.response?.status === 401) {
                  message.error("Not authenticated — please log in as admin first.");
                } else if (err?.errorFields) {
                  message.error("Please fix the highlighted fields first.");
                } else {
                  message.error(
                    `Test failed: ${err?.response?.data?.detail || err?.message || err}`,
                  );
                }
              }
            }}
          >
            Test Connection
          </Button>
        </Col>
      </Row>
    </Card>);

export default BoltFields;
