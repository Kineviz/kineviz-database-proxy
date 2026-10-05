import React from "react";
import { Button, Card, Col, Form, Input, InputNumber, Row, Select, message } from "antd";
import { projectService } from "../../services/projectService";
import type { ProjectFormState } from "./state";

const { Option } = Select;

type Props = Pick<ProjectFormState, "authType" | "form">;

/**
 * RocketGraph is an HTTP server rather than a bolt one, so it carries its own port
 * defaults per deployment mode and picks between a password and a bearer token.
 */
const RocketGraphFields: React.FC<Props> = ({ authType, form }) => (
    <Card
      title="RocketGraph Configuration"
      size="small"
      style={{ marginBottom: 16 }}
    >
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item
            label="Deployment Mode"
            name="deployment_mode"
            rules={[{ required: true, message: "Please select deployment mode" }]}
          >
            <Select
              onChange={(val) => {
                const tlsOn = form.getFieldValue("use_tls") === true;
                if (tlsOn) {
                  form.setFieldsValue({ port: 443 });
                } else if (val === "plugin") {
                  form.setFieldsValue({ port: 8080 });
                } else {
                  form.setFieldsValue({ port: 4368 });
                }
              }}
            >
              <Option value="standalone">Standalone (default port 4368)</Option>
              <Option value="plugin">Plugin in MC (default port 8080)</Option>
            </Select>
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item
            label="Use TLS"
            name="use_tls"
          >
            <Select
              onChange={(val) => {
                if (val === true) {
                  form.setFieldsValue({ port: 443 });
                } else {
                  const mode = form.getFieldValue("deployment_mode");
                  form.setFieldsValue({
                    port: mode === "plugin" ? 8080 : 4368,
                  });
                }
              }}
            >
              <Option value={false}>HTTP</Option>
              <Option value={true}>HTTPS</Option>
            </Select>
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={16}>
          <Form.Item
            label="Host"
            name="host"
            rules={[{ required: true, message: "Please enter host" }]}
          >
            <Input placeholder="e.g. kineviz.rocketgraph.com" />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item
            label="Port"
            name="port"
            rules={[{ required: true, message: "Please enter port" }]}
          >
            <InputNumber min={1} max={65535} style={{ width: "100%" }} />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={24}>
          <Form.Item
            label="Dataset"
            name="graph_name"
            rules={[{ required: true, message: "Please enter dataset name" }]}
          >
            <Input placeholder="e.g. FinTrans" />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={24}>
          <Form.Item
            label="API Base Path (optional)"
            name="api_base_path"
            tooltip="Leave empty to use default: /api/v1 (standalone) or /api/xgt/v1 (plugin)"
          >
            <Input placeholder="(default based on deployment mode)" />
          </Form.Item>
        </Col>
      </Row>

      {authType === "username_password" && (
        <Card title="Credentials" size="small" type="inner">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                label="Username"
                name="username"
                rules={[{ required: true, message: "Please enter username" }]}
              >
                <Input placeholder="e.g. MajorTom" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="Password"
                name="password"
                rules={[{ required: true, message: "Please enter password" }]}
              >
                <Input.Password placeholder="e.g. demo" />
              </Form.Item>
            </Col>
          </Row>
        </Card>
      )}

      {authType === "bearer_token" && (
        <Card
          title="Bearer Token (optional)"
          size="small"
          type="inner"
          extra={<span style={{ fontSize: 12, color: "#999" }}>Leave empty for anonymous access</span>}
        >
          <Row gutter={16}>
            <Col span={24}>
              <Form.Item label="Token" name="rg_token">
                <Input.Password placeholder="Pre-acquired bearer token (e.g. MC session) — leave empty for anonymous access" />
              </Form.Item>
            </Col>
          </Row>
        </Card>
      )}

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Button
            onClick={async () => {
              try {
                const values = await form.validateFields([
                  "host",
                  "port",
                  "graph_name",
                  "deployment_mode",
                ]);

                const testConfig: Record<string, any> = {
                  type: "rocketgraph",
                  auth_type: authType,
                  host: values.host,
                  port: values.port,
                  graph_name: values.graph_name,
                  use_tls:
                    form.getFieldValue("use_tls") === true ||
                    form.getFieldValue("use_tls") === "true",
                  deployment_mode: values.deployment_mode || "standalone",
                  api_base_path: form.getFieldValue("api_base_path") || undefined,
                  options: {},
                };

                if (authType === "username_password") {
                  testConfig.username = form.getFieldValue("username") || undefined;
                  testConfig.password = form.getFieldValue("password") || undefined;
                  testConfig.oauth_config = {};
                } else if (authType === "bearer_token") {
                  testConfig.oauth_config = {
                    token: form.getFieldValue("rg_token") || undefined,
                  };
                }

                const result = await projectService.testConnectionWithConfig(
                  "rocketgraph",
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
                  // Form validation error — Ant Design already highlights the fields.
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

export default RocketGraphFields;
