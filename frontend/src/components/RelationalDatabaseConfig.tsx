import React from "react";
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Typography,
  Upload,
  message,
} from "antd";
import type { FormInstance, UploadProps } from "antd";
import { DeleteOutlined, UploadOutlined } from "@ant-design/icons";
import { projectService } from "../services/projectService";
import { DatabaseConfig, DatabaseType, RELATIONAL_DEFAULT_PORT } from "../types/project";
import { TableChoice, TablePicker } from "./mapping/TablePicker";
import { errorMessage } from "./mapping/reducer";

const { Option } = Select;
const { Text } = Typography;

//: Keep in step with MAX_MAPPING_BYTES in mapping/Toolbar.tsx and the proxy's own cap.
const MAX_MAPPING_BYTES = 1024 * 1024;

const ENGINE_LABEL: Record<string, string> = {
  mssql: "SQL Server",
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  snowflake: "Snowflake",
};

/**
 * The schema a new relational project starts on.
 *
 * MySQL is empty on purpose: there the schema *is* the database, so naming one on top
 * of it would only be a second word for the same thing.
 */
export const RELATIONAL_DEFAULT_SCHEMA: Record<string, string> = {
  mssql: "dbo",
  postgresql: "public",
  mysql: "",
  snowflake: "PUBLIC",
};

interface Props {
  databaseType: DatabaseType;
  form: FormInstance;
  /** Only a create takes a mapping file; an existing project's mapping is the editor's to change. */
  allowMappingUpload: boolean;
}

/**
 * Connection settings for the three engines that answer to a host and a port.
 *
 * It lives here rather than in `ProjectForm.tsx` because that file is already over the
 * source limit and this block is one sentence: how a table catalog is reached. The
 * fields are the ones `drivers/relational/engine.py` reads — `host`, `port`,
 * `database_id`, `schema_name`, `username`, `password`, `use_tls` — and nothing else,
 * because an engine knob this form has not been asked for belongs in `options`.
 */
const RelationalDatabaseConfig: React.FC<Props> = ({
  databaseType,
  form,
  allowMappingUpload,
}) => {
  const label = ENGINE_LABEL[databaseType] || databaseType;
  const snowflake = databaseType === "snowflake";
  const [mappingFileName, setMappingFileName] = React.useState<string | null>(null);
  // The connection the browse is reading, taken when the button was pressed, and the table
  // picked out of it. `null` means no browse is open, and nothing has been read.
  const [browseConnection, setBrowseConnection] = React.useState<DatabaseConfig | null>(null);
  const [browsePick, setBrowsePick] = React.useState<TableChoice | null>(null);

  /** A form value as `unknown`: `getFieldValue` answers `any`, and this is where it stops. */
  const raw = (name: string): unknown => form.getFieldValue(name);

  /** A text field, or `undefined` when it is blank — the shape `DatabaseConfig` wants. */
  const text = (name: string): string | undefined => {
    const value = raw(name);
    return typeof value === "string" && value ? value : undefined;
  };

  /**
   * The connection this section holds, as the `DatabaseConfig` the proxy reads.
   *
   * One builder, for both the test and the table browse: two hand-built copies is how a
   * picker ends up browsing a database on a port the user did not type. The values are read
   * as the form holds them, not as its rules require, so this works mid-edit.
   */
  const draftConfig = (): DatabaseConfig => ({
    type: databaseType,
    auth_type: "username_password",
    host: String(raw("host") ?? ""),
    port: Number(raw("port") ?? RELATIONAL_DEFAULT_PORT[databaseType] ?? 0),
    database_id: text("database_id"),
    schema_name: text("schema_name"),
    username: text("username"),
    password: text("password"),
    use_tls: snowflake || raw("use_tls") === true,
    options: (raw("options") as Record<string, unknown> | undefined) ?? {},
  });

  /**
   * Open the browse for the connection as it stands right now.
   *
   * Nothing is read until the dropdown inside it opens, so pressing this costs a snapshot of
   * the form and no catalog walk. The reason it refuses to open is said outright, because a
   * picker that answers with an empty list reads as an empty database.
   */
  const openBrowse = () => {
    const host = text("host");
    const database = text("database_id");
    if (!host || !database) {
      message.error("Enter the host and the database first — there is no connection to browse.");
      return;
    }
    setBrowsePick(null);
    setBrowseConnection(draftConfig());
  };

  const clearMappingFile = () => {
    setMappingFileName(null);
    form.setFieldsValue({ graph_mapping_yaml: undefined });
  };

  /**
   * Read the picked file as text and park it on the form.
   *
   * The browser never parses YAML: `createProject` posts this text to the proxy, which is
   * the only program that turns it into a mapping. A file that will not load therefore
   * costs a warning after the project exists, never the project itself.
   */
  const readFile: UploadProps["beforeUpload"] = (file) => {
    if (file.size > MAX_MAPPING_BYTES) {
      message.error(
        `That file is ${Math.round(file.size / 1024)} KB — the proxy reads at most 1 MiB`,
      );
      return Upload.LIST_IGNORE;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      if (!text.trim()) {
        message.error("That file is empty");
        return;
      }
      setMappingFileName(file.name);
      form.setFieldsValue({ graph_mapping_yaml: text });
    };
    reader.onerror = () => message.error("The file could not be read");
    reader.readAsText(file as File, "utf-8");
    // Returning false is what keeps antd from POSTing the file itself.
    return false;
  };

  const testConnection = async () => {
    try {
      await form.validateFields(snowflake ? ["host", "database_id", ["options", "warehouse"]] : ["host", "port"]);
      const result = await projectService.testConnectionWithConfig(databaseType, draftConfig());
      if (result.success) {
        message.success(result.message || "Connection successful");
      } else {
        message.error(`Connection failed: ${result.message || "Unknown error"}`);
      }
    } catch (caught: unknown) {
      if (caught && typeof caught === "object" && "errorFields" in caught) {
        message.error("Please fix the highlighted fields first.");
      } else {
        message.error(`Test failed: ${errorMessage(caught)}`);
      }
    }
  };

  return (
    <Card title={`${label} Configuration`} size="small" style={{ marginBottom: 16 }}>
      <Row gutter={16}>
        <Col span={snowflake ? 24 : 12}>
          <Form.Item
            label={snowflake ? "Account identifier" : "Host"}
            name="host"
            tooltip={snowflake ? "Your Snowflake organization-account identifier. HTTPS is always used." : undefined}
            rules={[{ required: true, message: snowflake ? "Please enter your Snowflake account" : "Please enter host" }]}
          >
            <Input placeholder={snowflake ? "e.g. myorg-myaccount" : "e.g. db.internal or 10.0.0.7"} />
          </Form.Item>
        </Col>
        <Col span={6} style={{ display: snowflake ? "none" : undefined }}>
          <Form.Item
            label="Port"
            name="port"
            rules={[{ required: true, message: "Please enter port" }]}
          >
            <InputNumber
              min={1}
              max={65535}
              style={{ width: "100%" }}
              placeholder={String(RELATIONAL_DEFAULT_PORT[databaseType] ?? "")}
            />
          </Form.Item>
        </Col>
        <Col span={6} style={{ display: snowflake ? "none" : undefined }}>
          <Form.Item
            label="Use TLS"
            name="use_tls"
            tooltip="Each engine spells TLS its own way; the proxy chooses the knob."
          >
            <Select>
              <Option value={false}>Off</Option>
              <Option value={true}>On</Option>
            </Select>
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Form.Item
            label="Database"
            name="database_id"
            tooltip={
              databaseType === "mysql"
                ? "On MySQL the database is also the schema the graph is read from"
                : "The database the graph is read from"
            }
            rules={[{ required: true, message: "Please enter the database name" }]}
          >
            <Input placeholder={databaseType === "mssql" ? "e.g. ERP" : "e.g. analytics"} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            label="Schema"
            name="schema_name"
            tooltip="Empty means the engine default. A mapping can still name a different schema for one record."
          >
            <Input placeholder={RELATIONAL_DEFAULT_SCHEMA[databaseType] || "the database"} />
          </Form.Item>
        </Col>
      </Row>

      {snowflake && <Row gutter={16}>
        <Col xs={24} sm={12}>
          <Form.Item label="Warehouse" name={["options", "warehouse"]}
            tooltip="The warehouse used to execute graph reads." rules={[{ required: true, message: "Please enter the warehouse" }]}>
            <Input placeholder="e.g. GRAPHXR_WH" />
          </Form.Item>
        </Col>
        <Col xs={24} sm={12}>
          <Form.Item label="Role" name={["options", "role"]} tooltip="Use a role with USAGE on the database, schema and warehouse, and SELECT on mapped tables. Empty uses the user's default role.">
            <Input placeholder="e.g. GRAPHXR_READER" />
          </Form.Item>
        </Col>
      </Row>}

      <Card
        title="Credentials"
        size="small"
        type="inner"
        extra={
          <span style={{ fontSize: 12, color: "#999" }}>
            A read-only login is enough — the proxy only ever reads
          </span>
        }
      >
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item label="Username" name="username">
              <Input placeholder="e.g. graphxr_reader" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item label="Password" name="password">
              <Input.Password placeholder="Password" />
            </Form.Item>
          </Col>
        </Row>
      </Card>

      {snowflake && <Text type="secondary" style={{ display: "block", marginTop: 12, fontSize: 12 }}>
        Table and column names are case-sensitive in mappings. Use the names from Browse tables.
        Standard Snowflake tables do not enforce primary-key uniqueness; choose a key whose values are unique and non-null.
      </Text>}

      {allowMappingUpload && (
        <div style={{ marginTop: 16 }}>
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            <Text strong style={{ fontSize: 13 }}>
              Mapping file (optional)
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              A reviewed <code>.yml</code>, <code>.yaml</code> or <code>.json</code> graph
              mapping to apply to the new project. Leave it empty and the proxy discovers
              one from the foreign keys.
            </Text>
            <Space size={8} wrap>
              <Upload
                beforeUpload={readFile}
                showUploadList={false}
                accept=".yml,.yaml,.json"
                maxCount={1}
              >
                <Button icon={<UploadOutlined />}>Choose mapping file</Button>
              </Upload>
              {mappingFileName ? (
                <>
                  <Text style={{ fontSize: 12 }}>{mappingFileName}</Text>
                  <Button
                    type="link"
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={clearMappingFile}
                  >
                    Remove
                  </Button>
                </>
              ) : null}
            </Space>
          </Space>
        </div>
      )}

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={24}>
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Space size={8} align="center" wrap>
              <Button onClick={testConnection}>Test Connection</Button>
              <Button onClick={openBrowse}>Browse tables</Button>
              {browseConnection ? (
                <Button type="link" size="small" onClick={() => setBrowseConnection(null)}>
                  Close
                </Button>
              ) : null}
            </Space>
            {browseConnection ? (
              <Space size={8} align="center" wrap>
                <TablePicker
                  project={text("name") || databaseType}
                  connection={browseConnection}
                  schema={browseConnection.schema_name ?? null}
                  value={browsePick?.table ?? null}
                  onChange={setBrowsePick}
                  placeholder={`tables of ${browseConnection.host}:${browseConnection.port}`}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Read from the connection as typed, and nothing is saved.
                </Text>
              </Space>
            ) : null}
            <Text type="secondary" style={{ fontSize: 12 }}>
              Anything this engine takes that is not modelled here goes in the config's
              <code> options</code> — the escape hatch the proxy passes straight to the
              driver. The Graph Mapping page is where the mapping itself is edited.
            </Text>
          </Space>
        </Col>
      </Row>
    </Card>
  );
};

export default RelationalDatabaseConfig;
