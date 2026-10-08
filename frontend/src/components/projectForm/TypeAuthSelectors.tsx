import React from "react";
import { Col, Form, Input, Row, Select } from "antd";
import { isRelationalType } from "../../types/project";
import { isEmbedded } from "./types";
import type { ProjectFormState } from "./state";

const { Option } = Select;

type Props = Pick<
  ProjectFormState,
  "databaseType" | "handleDatabaseTypeChange" | "handleAuthTypeChange"
>;

/**
 * Which engine the project points at, and for the engines that offer a choice, how
 * it is authenticated. The option list is conditional because not every backend has
 * more than one way in, and an embedded store has none at all.
 */
const TypeAuthSelectors: React.FC<Props> = ({
  databaseType,
  handleDatabaseTypeChange,
  handleAuthTypeChange,
}) => (
    <Row gutter={16}>
      <Col span={12}>
        <Form.Item
          label="Database Type"
          name="database_type"
          rules={[{ required: true, message: "Please select database type" }]}
        >
          <Select
            placeholder="Select database type"
            onChange={handleDatabaseTypeChange}
          >
            <Option value="spanner">Google Cloud Spanner</Option>
            <Option value="bigquery">Google BigQuery</Option>
            <Option value="rocketgraph">RocketGraph</Option>
            <Option value="neo4j">Neo4j</Option>
            <Option value="memgraph">Memgraph</Option>
            <Option value="kuzu">Kuzu (embedded)</Option>
            <Option value="ladybug">Ladybug (embedded)</Option>
            <Option value="latticedb">LatticeDB (embedded)</Option>
            <Option value="mssql">SQL Server</Option>
            <Option value="postgresql">PostgreSQL</Option>
            <Option value="mysql">MySQL</Option>
            <Option value="snowflake">Snowflake</Option>
            {/* <Option value="mongodb">MongoDB</Option> */}
          </Select>
        </Form.Item>
      </Col>
      <Col span={12} style={{ display: isEmbedded(databaseType) ? "none" : undefined }}>
        <Form.Item
          label="Authentication Type"
          name="auth_type"
          rules={[
            { required: true, message: "Please select authentication type" },
          ]}
        >
          <Select
            placeholder="Select authentication type"
            onChange={handleAuthTypeChange}
          >
            {(databaseType === "spanner" || databaseType === "bigquery") && (
              <>
                <Option value="service_account">Service Account</Option>
                <Option value="google_ADC">Application Default Credentials (ADC)</Option>
                <Option value="oauth2">OAuth2</Option>
              </>
            )}
            {databaseType === "rocketgraph" && (
              <>
                <Option value="username_password">Username / Password</Option>
                <Option value="bearer_token">Bearer Token</Option>
              </>
            )}
            {(databaseType === "neo4j" || databaseType === "memgraph") && (
              <Option value="username_password">Username / Password</Option>
            )}
            {isRelationalType(databaseType) && (
              <Option value="username_password">Username / Password</Option>
            )}
          </Select>
        </Form.Item>
      </Col>
    </Row>);

export default TypeAuthSelectors;
