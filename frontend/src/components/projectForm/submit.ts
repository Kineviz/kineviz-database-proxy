import { message } from "antd";
import type { FormInstance } from "antd";
import type { AuthType, Project, ProjectCreate } from "../../types/project";
import { isRelationalType } from "../../types/project";
import { isEmbedded } from "./types";
import type { CredentialState } from "./googleAuth";

interface SubmitContext {
  form: FormInstance;
  authType: AuthType;
  serviceAccountKey: CredentialState;
  initialValues?: Project | null;
  onSubmit: (values: ProjectCreate) => void;
}

/**
 * Turns the form's flat values into the `ProjectCreate` the proxy expects.
 *
 * One branch per backend, because each engine wants a different slice of the form
 * under a different name — and the two fields the form never registers (the stored
 * `options` and the Graph Mapping page's `graph_mapping`) have to be carried over
 * from the project being edited rather than rebuilt from controls that do not exist.
 */
export const makeSubmitHandler =
  ({ form, authType, serviceAccountKey, initialValues, onSubmit }: SubmitContext) =>
  (values: any) => {
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
