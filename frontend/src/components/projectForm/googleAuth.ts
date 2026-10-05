import React, { useEffect } from "react";
import { message } from "antd";
import type { FormInstance, UploadProps } from "antd";
import { projectService } from "../../services/projectService";
import type { AuthType, DatabaseType } from "../../types/project";

interface GoogleAuthDeps {
  form: FormInstance;
  databaseType: DatabaseType;
  authType: AuthType;
}

/**
 * The credential in force, whichever of the two Google shapes it happens to be.
 *
 * One slot holds an uploaded service account key, an OAuth2 token grant, and the saved
 * project's `oauth_config` when a form is reopened. The fields a document carries that this UI
 * never reads stay `unknown` under the index signature rather than `any`, so a caller that
 * wants one has to say what it is before it can use it. Where the same field arrives as two
 * types, the union says which: `expires_in` is a number off a saved config and a string off
 * `localStorage`, and a cascade pick clears the level below it with `null`.
 */
export interface GoogleCredential {
  /** `service_account` on an uploaded key — what tells a key from a token grant. */
  type?: string;
  private_key?: string;
  private_key_id?: string;
  client_email?: string;
  /** The OAuth2 grant, the account it was given for, and how it was obtained. */
  token?: string;
  refresh_token?: string | null;
  expires_in?: number | string | null;
  last_refreshed?: number;
  state?: string | null;
  email?: string | null;
  // The cascade: each listing writes its answer back, and the next listing is asked with it.
  project_id?: string | null;
  instance_id?: string | null;
  database_id?: string | null;
  graph_name?: string | null;
  location?: string | null;
  [key: string]: unknown;
}

/**
 * Everything a Google-shaped backend needs: the credential in force (an uploaded
 * service account key, an OAuth2 token or ADC) and the cloud resources that
 * credential lists — projects, instances, databases, datasets and property graphs.
 *
 * It owns both because one cannot be read without the other: every listing writes
 * its answer back into the credential the next listing is asked with.
 */
export const useGoogleAuth = ({ form, databaseType, authType }: GoogleAuthDeps) => {
  const [serviceAccountKey, setServiceAccountKey] = React.useState<GoogleCredential>({});
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

  // A different engine, or a different credential, means these answers are stale.
  const resetListings = () => {
    setProjectsList([]);
    setDatabases([]);
    setBigqueryProjects([]);
    setBigqueryGraphs([]);
  };

  const updateServiceAccountKey = (newData: GoogleCredential) => {
    const newService = {
      ...serviceAccountKey,
      ...newData,
    };
    setServiceAccountKey((prevKey) => ({
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

  const handleServiceAccountUpload: UploadProps["customRequest"] = (
    options
  ) => {
    const { file, onSuccess, onError } = options;

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const content = e.target?.result as string;
        const jsonContent = JSON.parse(content);

        // A credential file only counts when it is a service account key, in full.
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

  const isServiceAccount = serviceAccountKey && serviceAccountKey?.type === "service_account" && serviceAccountKey.private_key_id;
  const isGoogleOauth2 = serviceAccountKey && serviceAccountKey?.token && authType == "oauth2";
  const isADC = authType === "google_ADC";

  return {
    serviceAccountKey,
    setServiceAccountKey,
    loading,
    setLoading,
    projectsList,
    databases,
    bigqueryProjects,
    bigqueryGraphs,
    setBigqueryGraphs,
    resetListings,
    updateServiceAccountKey,
    handleServiceAccountUpload,
    handleRemoveServiceAccount,
    handleGoogleLogin,
    isServiceAccount,
    isGoogleOauth2,
    isADC,
  };
};

export type GoogleAuth = ReturnType<typeof useGoogleAuth>;

/**
 * The credential blob one block at a time.
 *
 * Derived from the state the hook holds rather than written out here, so `GoogleCredential`
 * stays the one place that decides what a credential is.
 */
export type CredentialState = GoogleAuth["serviceAccountKey"];
