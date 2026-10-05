import React from "react";
import { Button, Col, Form, Input, Row, Space } from "antd";
import { isRelationalType } from "../types/project";
import EmbeddedStoreConfig from "./EmbeddedStoreConfig";
import RelationalDatabaseConfig from "./RelationalDatabaseConfig";
import BigqueryFields from "./projectForm/BigqueryFields";
import BoltFields from "./projectForm/BoltFields";
import RocketGraphFields from "./projectForm/RocketGraphFields";
import SpannerFields from "./projectForm/SpannerFields";
import TypeAuthSelectors from "./projectForm/TypeAuthSelectors";
import { isEmbedded } from "./projectForm/types";
import type { ProjectFormProps } from "./projectForm/types";
import { useProjectFormState } from "./projectForm/state";

export type { StorePrefill } from "./projectForm/types";

/**
 * The project connection form.
 *
 * This is composition only: the state machine lives in `projectForm/state.ts`, the
 * Google credential and its listings in `projectForm/googleAuth.ts`, the payload in
 * `projectForm/submit.ts`, and each backend's fields in its own file next door.
 */
const ProjectForm: React.FC<ProjectFormProps> = ({
  initialValues,
  prefill,
  open,
  onSubmit,
  onCancel,
}) => {
  const s = useProjectFormState({ initialValues, prefill, open, onSubmit });

  return (
    <Form
      form={s.form}
      layout="vertical"
      onFinish={s.handleSubmit}
      initialValues={{
        redirect_uri: "http://localhost:9080/google/spanner/callback",
        max_sessions: 100,
        timeout: 30,
        read_only: true,
        auth_type:  s.authType,
        database_type: "spanner",
      }}
    >
      <Row gutter={16}>
        <Col span={24}>
          <Form.Item
            label="Project Name"
            name="name"
            rules={[{ required: true, message: "Please enter project name" }]}
          >
            <Input placeholder="Enter project name" />
          </Form.Item>
        </Col>
      </Row>

      <TypeAuthSelectors
        databaseType={s.databaseType}
        handleDatabaseTypeChange={s.handleDatabaseTypeChange}
        handleAuthTypeChange={s.handleAuthTypeChange}
      />

      {s.databaseType === "spanner" && (
        <SpannerFields authType={s.authType} form={s.form} google={s.google} />
      )}

      {s.databaseType === "bigquery" && (
        <BigqueryFields authType={s.authType} google={s.google} />
      )}

      {(s.databaseType === "neo4j" || s.databaseType === "memgraph") && (
        <BoltFields
          databaseType={s.databaseType}
          authType={s.authType}
          form={s.form}
        />
      )}

      {isRelationalType(s.databaseType) && (
        <RelationalDatabaseConfig
          databaseType={s.databaseType}
          form={s.form}
          // An existing project's mapping belongs to the Graph Mapping page, not to a
          // re-submitted connection form.
          allowMappingUpload={!initialValues}
        />
      )}

      {isEmbedded(s.databaseType) && (
        <EmbeddedStoreConfig
          databaseType={s.databaseType}
          form={s.form}
          projectName={s.watchedProjectName}
        />
      )}

      {s.databaseType === "rocketgraph" && (
        <RocketGraphFields authType={s.authType} form={s.form} />
      )}

      <Form.Item>
        <Space>
          <Button type="primary" htmlType="submit">
            {initialValues ? "Update Project" : "Create Project"}
          </Button>
          <Button onClick={onCancel}>Cancel</Button>
        </Space>
      </Form.Item>
    </Form>
  );
};

export default ProjectForm;
