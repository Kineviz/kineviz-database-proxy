import React, { useEffect } from "react";
import {
  Form,
  Input,
  Select,
  Button,
  Card,
  Row,
  Col,
  Space,
  InputNumber,
  Upload,
  message,
  Alert,
  Spin
} from "antd";
import { UploadOutlined, ClockCircleOutlined, InfoCircleOutlined } from "@ant-design/icons";
import type { UploadProps } from "antd";
import {
  DatabaseType,
  AuthType,
  Project,
  ProjectCreate,
  RELATIONAL_DEFAULT_PORT,
  isRelationalType,
} from "../types/project";
import { projectService } from "../services/projectService";
import BigqueryRegionSelect from "./BigqueryRegionSelect";
import EmbeddedStoreConfig from "./EmbeddedStoreConfig";
import RelationalDatabaseConfig, {
  RELATIONAL_DEFAULT_SCHEMA,
} from "./RelationalDatabaseConfig";

// The embedded engines are files rather than servers: no host, no port, no
// credentials. Keep this in step with EMBEDDED_ENGINES in types/project.ts.
const EMBEDDED_TYPES: DatabaseType[] = ["kuzu", "ladybug", "latticedb"];
const isEmbedded = (type: DatabaseType) => EMBEDDED_TYPES.includes(type);

const { Option } = Select;

/**
 * A store the user picked somewhere else, opened here.
 *
 * The Files page has already read the file's header, so it knows both the path and
 * which engine wrote it — carrying both across means the form opens on the right
 * type instead of asking a question that has already been answered.
 */
export interface StorePrefill {
  path: string;
  database_type?: DatabaseType | null;
}

interface ProjectFormProps {
  initialValues?: Project | null;
  /** Only read when creating: an existing project's own config always wins. */
  prefill?: StorePrefill;
  open: boolean;
  onSubmit: (values: ProjectCreate) => void;
  onCancel: () => void;
}


//window is localhost:9080 use the oauth2 auth by default, otherwise use service account by default
//@todo maybe we can support other auth types in the future
const DefaultAuthType = window.location.origin === "http://localhost:9080" ? "oauth2" : "service_account";


const ProjectForm: React.FC<ProjectFormProps> = ({
  initialValues,
  prefill,
  open,
  onSubmit,
  onCancel,
}) => {
  const [form] = Form.useForm();
  // Hoisted rather than read where it is used: Form.useWatch is a hook, and the
  // embedded section it feeds is rendered conditionally.
  const watchedProjectName = (Form.useWatch("name", form) as string | undefined) || "";
  const [databaseType, setDatabaseType] =
    React.useState<DatabaseType>("spanner");
  const [authType, setAuthType] = React.useState<AuthType>(DefaultAuthType);
  const [serviceAccountKey, setServiceAccountKey] = React.useState<any>({});
  // const [showOauthAdvanced, setShowOauthAdvanced] = React.useState(false);
  const [loading, setLoading] = React.useState({
    listProjects: false,
    listDatabases: false,
  });
  const [projectsList, setProjectsList] = React.useState<
    Array<{ id: string; name: string; instances: Array<{ id: string; name: string }> }>
  >([]);
  const [databases, setDatabases] = React.useState<
    Array<{ id: string; name: string; graphDBs?: Array<{ id: string; name: string }> }>
  >([]);
  // BigQuery has no instance layer, so its cascade is project -> dataset -> graph.
  // Datasets arrive with the project listing; graphs cost a query per dataset and
  // are fetched only once a dataset is picked.
  const [bigqueryProjects, setBigqueryProjects] = React.useState<
    Array<{ id: string; name: string; datasets: Array<{ id: string; name: string; location?: string }> }>
  >([]);
  const [bigqueryGraphs, setBigqueryGraphs] = React.useState<Array<{ id: string; name: string }>>([]);

  const cleanupForm = () => {
     form && form.resetFields();
       setServiceAccountKey({});
       setDatabaseType("spanner");
       setAuthType(DefaultAuthType);
       setLoading({ listProjects: false, listDatabases: false });
       setProjectsList([]);
       setDatabases([]);
       setBigqueryProjects([]);
       setBigqueryGraphs([]);
  }
  const applyStorePrefill = (store?: StorePrefill) => {
    if (!store?.path) return;
    // The file decides the engine either way, so this only picks which of the two
    // labels the project wears — and the one that wrote the file is the honest one.
    const type = (store.database_type || "kuzu") as DatabaseType;
    const defaultAuth: AuthType = "username_password";
    setDatabaseType(type);
    setAuthType(defaultAuth);
    form.setFieldsValue({
      database_type: type,
      auth_type: defaultAuth,
      database_path: store.path,
      engine_version: "",
      read_only: true,
    });
  };

  useEffect(() => {
    if(!open) {
      cleanupForm();
    }
   }, [open]);
  useEffect(() => {
    if (initialValues) {
      form.setFieldsValue({
        name: initialValues.name,
        database_type: initialValues.database_type,
        ...initialValues.database_config,
      });
      setDatabaseType(initialValues.database_type);
      setAuthType(initialValues.database_config.auth_type);
      if (initialValues.database_type === "spanner" || initialValues.database_type === "bigquery") {
        updateServiceAccountKey({
          ...initialValues.database_config.oauth_config,
        });
      } else if (initialValues.database_type === "rocketgraph") {
        const rgToken = (initialValues.database_config as any)?.oauth_config?.token;
        if (rgToken) {
          form.setFieldsValue({ rg_token: rgToken });
        }
      }
    } else {
      cleanupForm();
      applyStorePrefill(prefill);
    }
  }, [initialValues, prefill]);

  const handleSubmit = (values: any) => {
    const { name, database_type, ...dbConfigFields } = values;

    let databaseConfig: any = {
      type: database_type,
      auth_type: authType,
      options: {},
    };

    if (database_type === "spanner") {
      databaseConfig = {
        ...databaseConfig,
        project_id: dbConfigFields.project_id,
        instance_id: dbConfigFields.instance_id,
        database_id: dbConfigFields.database_id,
        graph_name: dbConfigFields.graph_name || "",
      };

      if (authType === "oauth2") {
        databaseConfig.oauth_config = {
           ...serviceAccountKey,
        };
      } else if (authType === "service_account") {
        if (!serviceAccountKey) {
          message.error("Please upload a service account key file");
          return;
        }
        databaseConfig.project_id = serviceAccountKey?.project_id;
        databaseConfig.oauth_config = {
          ...serviceAccountKey,
        };
      } else if (authType === "google_ADC") {
        // ADC 模式不需要额外的配置
        databaseConfig.oauth_config = {
          ...serviceAccountKey,
          google_ADC: true,
        };
      }

      // databaseConfig.options = {
      //   max_sessions: dbConfigFields.max_sessions || 100,
      //   timeout: dbConfigFields.timeout || 30,
      //   read_only: dbConfigFields.read_only || false,
      // };
    }

    if (database_type === "bigquery") {
      databaseConfig = {
        ...databaseConfig,
        project_id: dbConfigFields.project_id,
        // The dataset, under the field name the proxy already uses for it.
        database_id: dbConfigFields.database_id,
        graph_name: dbConfigFields.graph_name || "",
        location: dbConfigFields.location || "US",
      };

      if (authType === "service_account") {
        if (!serviceAccountKey?.private_key) {
          message.error("Please upload a service account key file");
          return;
        }
        databaseConfig.project_id = serviceAccountKey?.project_id;
      }
      databaseConfig.oauth_config = {
        ...serviceAccountKey,
        ...(authType === "google_ADC" ? { google_ADC: true } : {}),
      };
    }

    if (database_type === "neo4j" || database_type === "memgraph") {
      databaseConfig = {
        ...databaseConfig,
        host: dbConfigFields.host,
        port: dbConfigFields.port,
        use_tls: dbConfigFields.use_tls === true || dbConfigFields.use_tls === "true",
        username: dbConfigFields.username || "",
        password: dbConfigFields.password || "",
        oauth_config: {},
      };
      if (database_type === "neo4j") {
        // Memgraph has no multi-database concept, so it never carries one.
        databaseConfig.database_id = dbConfigFields.database_id || "";
      }
    }

    if (isRelationalType(database_type)) {
      // `graph_mapping` and `options` come from the stored config rather than the form:
      // editing a host must not drop a mapping the Graph Mapping page owns, and both are
      // fields this card deliberately does not have.
      databaseConfig = {
        ...databaseConfig,
        options: initialValues?.database_config?.options ?? {},
        graph_mapping: initialValues?.database_config?.graph_mapping ?? null,
        host: dbConfigFields.host,
        port: dbConfigFields.port,
        database_id: dbConfigFields.database_id || "",
        schema_name: dbConfigFields.schema_name || "",
        username: dbConfigFields.username || "",
        password: dbConfigFields.password || "",
        use_tls: dbConfigFields.use_tls === true || dbConfigFields.use_tls === "true",
        oauth_config: {},
      };
    }

    if (isEmbedded(database_type)) {
      databaseConfig = {
        ...databaseConfig,
        database_path: dbConfigFields.database_path,
        engine_version: dbConfigFields.engine_version || undefined,
        // The Select carries real booleans; anything unset means read-only.
        read_only: dbConfigFields.read_only !== false,
        oauth_config: {},
      };
    }

    if (database_type === "rocketgraph") {
      databaseConfig = {
        ...databaseConfig,
        host: dbConfigFields.host,
        port: dbConfigFields.port,
        graph_name: dbConfigFields.graph_name,
        use_tls: dbConfigFields.use_tls === true || dbConfigFields.use_tls === "true",
        deployment_mode: dbConfigFields.deployment_mode || "standalone",
        api_base_path: dbConfigFields.api_base_path || undefined,
      };

      if (authType === "username_password") {
        databaseConfig.username = dbConfigFields.username;
        databaseConfig.password = dbConfigFields.password;
        databaseConfig.oauth_config = {};
      } else if (authType === "bearer_token") {
        databaseConfig.oauth_config = {
          token: dbConfigFields.rg_token,
        };
      }
    }

    // The mapping text is not a form control the layout registers; the relational card
    // parks it on the store, and it is the one thing here that must not be parsed twice.
    const mappingText = form.getFieldValue("graph_mapping_yaml");

    const projectData: ProjectCreate = {
      name,
      database_type,
      database_config: databaseConfig,
      ...(typeof mappingText === "string" && mappingText.trim()
        ? { graph_mapping_yaml: mappingText }
        : {}),
    };

    onSubmit(projectData);
  };

  const handleDatabaseTypeChange = (value: DatabaseType) => {
    setDatabaseType(value);
    form.resetFields([
      "database_path",
      "engine_version",
      "read_only",
      "project_id",
      "instance_id",
      "database_id",
      "host",
      "port",
      "graph_name",
      "rg_token",
      "use_tls",
      "deployment_mode",
      "api_base_path",
      "location",
      "username",
      "password",
      "schema_name",
      // Unregistered by any control, so it only goes away when asked for by name.
      "graph_mapping_yaml",
    ]);
    setProjectsList([]);
    setDatabases([]);
    setBigqueryProjects([]);
    setBigqueryGraphs([]);

    if (value === "neo4j" || value === "memgraph") {
      const defaultAuth: AuthType = "username_password";
      setAuthType(defaultAuth);
      form.setFieldsValue({
        auth_type: defaultAuth,
        port: 7687,
        use_tls: false,
        ...(value === "neo4j" ? { database_id: "neo4j" } : {}),
      });
    } else if (isRelationalType(value)) {
      // One auth type, the engine's own port, and the schema that engine calls home.
      const defaultAuth: AuthType = "username_password";
      setAuthType(defaultAuth);
      form.setFieldsValue({
        auth_type: defaultAuth,
        port: RELATIONAL_DEFAULT_PORT[value],
        use_tls: false,
        schema_name: RELATIONAL_DEFAULT_SCHEMA[value] ?? "",
      });
    } else if (value === "bigquery") {
      setAuthType(DefaultAuthType);
      form.setFieldsValue({ auth_type: DefaultAuthType, location: "US" });
    } else if (value === "rocketgraph") {
      const defaultAuth: AuthType = "username_password";
      setAuthType(defaultAuth);
      form.setFieldsValue({
        auth_type: defaultAuth,
        deployment_mode: "plugin",
        port: 8080,
        use_tls: false,
        username: "MajorTom",
      });
    } else if (value === "spanner") {
      setAuthType(DefaultAuthType);
      form.setFieldsValue({ auth_type: DefaultAuthType });
    } else if (isEmbedded(value)) {
      // The filesystem is the access control, so auth_type is only carried to keep
      // the shared config shape whole. Read-only is the default because the proxy
      // is a read path and a read-only open can be shared between processes.
      const defaultAuth: AuthType = "username_password";
      setAuthType(defaultAuth);
      form.setFieldsValue({
        auth_type: defaultAuth,
        read_only: true,
        engine_version: "",
      });
    }
  };

  const handleAuthTypeChange = (value: AuthType) => {
    setAuthType(value);
    form.resetFields(["client_id", "client_secret", "username", "password"]);
    updateServiceAccountKey({});
  };

  const handleServiceAccountUpload: UploadProps["customRequest"] = (
    options
  ) => {
    const { file, onSuccess, onError } = options;

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const content = e.target?.result as string;
        const jsonContent = JSON.parse(content);

        // 验证是否为有效的服务账号文件
        if (
          jsonContent.type === "service_account" &&
          jsonContent.private_key &&
          jsonContent.client_email
        ) {
          updateServiceAccountKey(jsonContent);
          message.success(`${(file as File).name} uploaded successfully`);
          onSuccess?.(jsonContent);
        } else {
          message.error("Invalid service account key file format");
          onError?.(new Error("Invalid file format"));
        }
      } catch (error) {
        message.error("Failed to parse JSON file");
        onError?.(error as Error);
      }
    };

    reader.onerror = () => {
      message.error("Failed to read file");
      onError?.(new Error("Failed to read file"));
    };

    reader.readAsText(file as File);
  };

  const handleRemoveServiceAccount = () => {
    updateServiceAccountKey({});
    message.success("Service account key removed");
  };

  const getProjectsList = async () => {
    try {
      setLoading({ listDatabases: false, listProjects: true });
      const isBigQuery = databaseType === "bigquery";
      const projects = isBigQuery
        ? await projectService.listBigQueryProjects(serviceAccountKey, authType)
        : await projectService.listGoogleProjects(serviceAccountKey, authType);
      const found = Array.isArray(projects) ? projects : [];
      if (isBigQuery) {
        setBigqueryProjects(found);
      } else {
        setProjectsList(found);
      }
      updateServiceAccountKey({
        ...serviceAccountKey,
        // A listing that comes back empty must not erase the project id the
        // uploaded key already names: a service account is bound to one project,
        // and blanking it leaves the form with nothing to look datasets up by.
        project_id: found.length > 0 ? found[0].id : serviceAccountKey?.project_id || "",
      });
    } catch (error: any) {
      if (authType === "google_ADC") {
        message.error("Failed to fetch projects. Please ensure that Application Default Credentials are properly configured in your environment.");
      } else if (error.response?.status === 401) {
        message.error("Authentication required. Please login first.");
      } else {
        message.error("Failed to fetch projects");
      }
    } finally {
      setLoading({ listDatabases: false, listProjects: false });
    }
  };

  const getBigQueryGraphs = async () => {
    try {
      setLoading({ listDatabases: true, listProjects: false });
      const graphs = await projectService.listBigQueryGraphs(serviceAccountKey, authType);
      setBigqueryGraphs(Array.isArray(graphs) ? graphs : []);
    } catch (error: any) {
      // A dataset with no property graph answers with an empty list, so reaching
      // here means the call itself failed — most often the wrong location. The
      // field still falls back to free text, but silently doing so is what left
      // the cascade looking broken with nothing to explain it.
      setBigqueryGraphs([]);
      const detail = error?.response?.data?.detail;
      message.warning(
        detail
          ? `Could not list property graphs: ${detail}`
          : "Could not list property graphs. Check the dataset and location, or enter the graph name manually."
      );
    } finally {
      setLoading({ listDatabases: false, listProjects: false });
    }
  };

  const getDatabaseList = async () => {
    try {
      setLoading({ listDatabases: true, listProjects: false });
      const databases = await projectService.listGoogleDatabases(serviceAccountKey, authType);
      setDatabases(databases);
    } catch (error: any) {
      if (error.response?.status === 401) {
        message.error("Authentication required. Please login first.");
      } else {
        message.error("Failed to fetch databases");
      }
    } finally {
      setLoading({ listDatabases: false, listProjects: false });
    }
  };



  useEffect(() => {

    const isNewOauth2 = serviceAccountKey && serviceAccountKey?.token && authType == "oauth2";
    const isNewServiceAccount = serviceAccountKey && serviceAccountKey?.type === "service_account"   && serviceAccountKey.private_key_id;
    const isADC = authType === "google_ADC";
    
    if (
      isNewOauth2 ||
      isNewServiceAccount ||
      isADC
    ) {
      getProjectsList();
    }
  }, [serviceAccountKey?.token, authType, databaseType, serviceAccountKey?.type , serviceAccountKey.private_key_id]);

  useEffect(() => {
    if (databaseType === "spanner" && serviceAccountKey?.project_id && serviceAccountKey?.instance_id) {
      getDatabaseList();
    }
  }, [databaseType, serviceAccountKey?.project_id, serviceAccountKey?.instance_id]);

  useEffect(() => {
    if (databaseType === "bigquery" && serviceAccountKey?.project_id && serviceAccountKey?.database_id) {
      getBigQueryGraphs();
    }
  }, [databaseType, serviceAccountKey?.project_id, serviceAccountKey?.database_id, serviceAccountKey?.location]);


  const handleGoogleLogin = () => {
    if (window.location.hostname !== "localhost") {
      return message.error(
        "Google OAuth2 login is only supported on localhost for development purposes."
      );
    }
    // The callback path is shared because that is the URI registered with Google;
    // `service` only decides which scopes the consent screen asks for.
    const authUrl = `/google/spanner/login?service=${databaseType === "bigquery" ? "bigquery" : "spanner"}`;

    // Open popup window instead of iframe
    let popup = window.open(
      authUrl,
      "googleAuth",
      "width=600,height=700,scrollbars=yes,resizable=yes,status=yes,location=yes,toolbar=no,menubar=no,left=" +
        (screen.width / 2 - 300) +
        ",top=" +
        (screen.height / 2 - 350)
    );

    //read the g_auth_token , g_auth_state, g_auth_email from localStorage, then clean up the localStorage

    const handleGoogleLoginData = () => {
      const token = localStorage.getItem("g_auth_token");
      const state = localStorage.getItem("g_auth_state");
      const email = localStorage.getItem("g_auth_email");
      const refresh_token = localStorage.getItem("g_auth_refresh_token"); 
      const expires_in = localStorage.getItem("g_auth_expires_in");

      localStorage.removeItem("g_auth_token");
      localStorage.removeItem("g_auth_state");
      localStorage.removeItem("g_auth_email");
      localStorage.removeItem("g_auth_refresh_token");
      localStorage.removeItem("g_auth_expires_in");

      if (token && email && state) {
        setServiceAccountKey({
          token,
          refresh_token,
          expires_in,
          last_refreshed: Date.now()/1000,
          state,
          email,
        });
        console.log("Google OAuth2 login successful:", email);
      }
    };

    const checkGoogleLoginDataWrite = () => {
      // Check if the data has been written to localStorage
      const token = localStorage.getItem("g_auth_token");
      const state = localStorage.getItem("g_auth_state");
      const email = localStorage.getItem("g_auth_email");

      if (token && email && state) {
        return true;
      }
      return false;
    };

    // Listen for popup to close
    const timer = setInterval(() => {
      if (checkGoogleLoginDataWrite()) {
        try {
          clearInterval(timer);
          popup?.close();
          popup = null;
          handleGoogleLoginData();
        } catch (e) {
          // Ignore if popup is already closed
        }
      }
    }, 500);
  };

 const updateServiceAccountKey = (newData: any) => {
  const newService = {
      ...serviceAccountKey,
      ...newData,
  };
    setServiceAccountKey((prevKey: any) => ({
      ...prevKey,
      ...newData,
    }));
    form.setFieldsValue({
      project_id: newService.project_id,
      instance_id: newService.instance_id,
      database_id: newService.database_id,
      graph_name: newService.graph_name,
      ...(newService.location ? { location: newService.location } : {}),
    });
  };

  const isServiceAccount = serviceAccountKey && serviceAccountKey?.type === "service_account" && serviceAccountKey.private_key_id;
  const isGoogleOauth2 = serviceAccountKey && serviceAccountKey?.token && authType == "oauth2";
  const isADC = authType === "google_ADC";

  // The Google auth block is shared by Spanner and BigQuery: both pick between
  // OAuth2, a service account key and ADC, and both drive the same
  // serviceAccountKey state that the project/dataset lookups read.
  const googleAuthSection = (
    <>
          {authType === "oauth2" && (
            <Card title="OAuth2 Configuration" size="small" type="inner">
              <Row gutter={16}>
                {isGoogleOauth2 && (
                  <Col span={12}>
                    <Button type="link">
                      <span>
                        Logged in as:{" "}
                        <strong>{serviceAccountKey?.email}</strong>
                      </span>
                    </Button>
                  </Col>
                )}
                <Col span={serviceAccountKey?.email ? 12 : 24}>
                  <Button
                    type="primary"
                    icon={<span>🔐</span>}
                    size="large"
                    style={{ width: "100%", marginBottom: 16 }}
                    onClick={handleGoogleLogin}
                  >
                    {isGoogleOauth2
                      ? "Re-login with Google"
                      : "Login with Google"}
                  </Button>
                </Col>
              </Row>
              {/* <Row justify="end">
                <Button
                  type="link"
                  onClick={() => setShowOauthAdvanced(!showOauthAdvanced)}
                  style={{ paddingRight: 0 }}
                >
                  {showOauthAdvanced
                    ? "Hide Advanced Settings"
                    : "Advanced Settings"}
                </Button>
              </Row> */}
              {/* {showOauthAdvanced && (
                <>
                  <Row gutter={16}>
                    <Col span={12}>
                      <Form.Item
                        label="Client ID"
                        name="client_id"
                        rules={[
                          { required: true, message: "Please enter client ID" },
                        ]}
                      >
                        <Input placeholder="your-oauth-client-id" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item
                        label="Client Secret"
                        name="client_secret"
                        rules={[
                          {
                            required: true,
                            message: "Please enter client secret",
                          },
                        ]}
                      >
                        <Input.Password placeholder="your-oauth-client-secret" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Row gutter={16}>
                    <Col span={24}>
                      <Form.Item label="Redirect URI" name="redirect_uri">
                        <Input placeholder="http://localhost:9080/google/auth/callback" />
                      </Form.Item>
                    </Col>
                  </Row>
                </>
              )} */}
            </Card>
          )}

          {authType === "service_account" && (
            <Card
              title="Service Account Configuration"
              size="small"
              type="inner"
            >
              <Row gutter={16}>
                <Col span={24}>
                  <Form.Item
                    label="Service Account Key File"
                    rules={[
                      {
                        required: true,
                        message: "Please upload service account key file",
                      },
                    ]}
                  >
                    <Upload
                      customRequest={handleServiceAccountUpload}
                      onRemove={handleRemoveServiceAccount}
                      maxCount={1}
                      accept=".json"
                      showUploadList={{
                        showPreviewIcon: false,
                        showDownloadIcon: false,
                      }}
                    >
                    <Button icon={<UploadOutlined />}>
                     {isServiceAccount && "Re-upload Service Account Key (.json)"}
                      {!isServiceAccount && "Upload Service Account Key (.json)"}
                    </Button>
                    </Upload>
                    {isServiceAccount && (
                      <div style={{ marginTop: 8, color: "#52c41a" }}>
                        ✅ Service account key uploaded successfully
                      </div>
                    )}
                  </Form.Item>
                </Col>
              </Row>
            </Card>
          )}

          {authType === "google_ADC" && (
            <Card
              title="Application Default Credentials (ADC)"
              size="small"
              type="inner"
            >
              {!loading.listProjects && !loading.listDatabases && projectsList.length === 0 ? (
                <Alert
                  message="ADC Mode Not Supported"
                  description={
                    <div>
                      <p>Unable to use Application Default Credentials.</p>
                      <p style={{ marginTop: 8, marginBottom: 8 }}>
                        This could be because:
                      </p>
                      <ul style={{ marginBottom: 8, paddingLeft: 20 }}>
                        <li>ADC is not properly configured in your environment</li>
                        <li>The service account lacks necessary permissions</li>
                        <li>The backend server doesn't support ADC mode</li>
                      </ul>
                      <p style={{ marginTop: 8, marginBottom: 0 }}>
                        Please use <strong>Service Account</strong> authentication instead.
                      </p>
                    </div>
                  }
                  type="error"
                  showIcon
                />
              ) : (
                <Alert
                  message="Using Application Default Credentials"
                  icon={ (loading.listProjects || loading.listDatabases) ? <Spin /> : <InfoCircleOutlined /> }
                  description={
                    <div>
                      <p> ADC will automatically use credentials from:</p>
                      <ul style={{ marginBottom: 0, paddingLeft: 20 }}>
                        <li>GOOGLE_APPLICATION_CREDENTIALS environment variable</li>
                        <li>gcloud CLI default credentials</li>
                        <li>Compute Engine/GKE service account (if running on GCP)</li>
                      </ul>
                      <p style={{ marginTop: 8, marginBottom: 0 }}>
                        Make sure your environment is properly configured with GCP credentials.
                      </p>
                    </div>
                  }
                  type="info"
                  showIcon
                />
              )}
            </Card>
          )}
    </>
  );

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={handleSubmit}
      initialValues={{
        redirect_uri: "http://localhost:9080/google/spanner/callback",
        max_sessions: 100,
        timeout: 30,
        read_only: true,
        auth_type:  authType,
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
      </Row>

      {databaseType === "spanner" && (
        <Card
          title="Spanner Configuration"
          size="small"
          style={{ marginBottom: 16 }}
        >
          {googleAuthSection}

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
      )}

      {databaseType === "bigquery" && (
        <Card
          title="BigQuery Configuration"
          size="small"
          style={{ marginBottom: 16 }}
        >
          {googleAuthSection}

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
      )}

      {(databaseType === "neo4j" || databaseType === "memgraph") && (
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
        </Card>
      )}

      {isRelationalType(databaseType) && (
        <RelationalDatabaseConfig
          databaseType={databaseType}
          form={form}
          // An existing project's mapping belongs to the Graph Mapping page, not to a
          // re-submitted connection form.
          allowMappingUpload={!initialValues}
        />
      )}

      {isEmbedded(databaseType) && (
        <EmbeddedStoreConfig
          databaseType={databaseType}
          form={form}
          projectName={watchedProjectName}
        />
      )}

      {databaseType === "rocketgraph" && (
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
        </Card>
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
