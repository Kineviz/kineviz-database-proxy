import React from "react";
import { Alert, Button, Card, Col, Form, Row, Spin, Upload } from "antd";
import { InfoCircleOutlined, UploadOutlined } from "@ant-design/icons";
import type { ProjectFormState } from "./state";

type Props = Pick<ProjectFormState, "authType" | "google">;

  // The Google auth block is shared by Spanner and BigQuery: both pick between
  // OAuth2, a service account key and ADC, and both drive the same
  // serviceAccountKey state that the project/dataset lookups read.
const GoogleAuthFields: React.FC<Props> = ({
  authType,
  google: {
    handleGoogleLogin,
    handleRemoveServiceAccount,
    handleServiceAccountUpload,
    isGoogleOauth2,
    isServiceAccount,
    loading,
    projectsList,
    serviceAccountKey,
  },
}) => (
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
    </>);

export default GoogleAuthFields;
