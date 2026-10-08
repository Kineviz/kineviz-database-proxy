import React, { useEffect } from "react";
import { Form } from "antd";
import type { AuthType, DatabaseType } from "../../types/project";
import { RELATIONAL_DEFAULT_PORT, isRelationalType } from "../../types/project";
import { RELATIONAL_DEFAULT_SCHEMA } from "../RelationalDatabaseConfig";
import { isEmbedded } from "./types";
import type { ProjectFormProps, StorePrefill } from "./types";
import { useGoogleAuth } from "./googleAuth";
import { makeSubmitHandler } from "./submit";

//window is localhost:9080 use the oauth2 auth by default, otherwise use service account by default
//@todo maybe we can support other auth types in the future
const DefaultAuthType = window.location.origin === "http://localhost:9080" ? "oauth2" : "service_account";

/**
 * Which engine the form is pointed at, which credential it is opened with, and what
 * changing either one does to the fields and the listings below it.
 *
 * The Google credential and its listings live in `useGoogleAuth`, called here rather
 * than at the top: its effects have to run after the two below, exactly as they did
 * when all of this sat in one component body.
 */
export const useProjectFormState = ({
  initialValues,
  prefill,
  open,
  onSubmit,
}: Pick<ProjectFormProps, "initialValues" | "prefill" | "open" | "onSubmit">) => {
  const [form] = Form.useForm();
  // Hoisted rather than read where it is used: Form.useWatch is a hook, and the
  // embedded section it feeds is rendered conditionally.
  const watchedProjectName = (Form.useWatch("name", form) as string | undefined) || "";
  const [databaseType, setDatabaseType] =
    React.useState<DatabaseType>("spanner");
  const [authType, setAuthType] = React.useState<AuthType>(DefaultAuthType);

  const cleanupForm = () => {
     form && form.resetFields();
       setServiceAccountKey({});
       setDatabaseType("spanner");
       setAuthType(DefaultAuthType);
       setLoading({ listProjects: false, listDatabases: false });
       resetListings();
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

  const google = useGoogleAuth({ form, databaseType, authType });
  const {
    resetListings,
    serviceAccountKey,
    setServiceAccountKey,
    setLoading,
    updateServiceAccountKey,
  } = google;

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
      "options",
      // Unregistered by any control, so it only goes away when asked for by name.
      "graph_mapping_yaml",
    ]);
    resetListings();

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
        use_tls: value === "snowflake",
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

  const handleSubmit = makeSubmitHandler({
    form,
    authType,
    serviceAccountKey,
    initialValues,
    onSubmit,
  });

  return {
    form,
    watchedProjectName,
    databaseType,
    authType,
    google,
    handleSubmit,
    handleDatabaseTypeChange,
    handleAuthTypeChange,
  };
};

export type ProjectFormState = ReturnType<typeof useProjectFormState>;
