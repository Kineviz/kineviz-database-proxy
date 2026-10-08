import React, { useState } from 'react';
import { Card, Form, Input, Button, Typography, message, Alert } from 'antd';
import { LockOutlined } from '@ant-design/icons';

const { Title, Text } = Typography;

interface LoginProps {
  onLoginSuccess: (token: string) => void;
}

const Login: React.FC<LoginProps> = ({ onLoginSuccess }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (values: { password: string }) => {
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch('/api/admin/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ password: values.password }),
      });
      
      const data = await response.json();
      
      if (!response.ok) {
        throw new Error(data.detail || 'Login failed');
      }
      
      if (data.success && data.token) {
        // Store the token
        localStorage.setItem('adminToken', data.token);
        message.success('Login successful');
        onLoginSuccess(data.token);
      } else if (data.success && !data.token) {
        // Auth is disabled, proceed without token
        message.info('Admin authentication is disabled');
        onLoginSuccess('');
      } else {
        throw new Error('Unexpected response');
      }
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      background: '#f0f2f5',
    }}>
      <Card
        style={{
          width: 400,
          boxShadow: '0 4px 12px rgba(0, 0, 0, 0.1)',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <LockOutlined style={{ fontSize: 48, color: '#1890ff', marginBottom: 16 }} />
          <Title level={3} style={{ marginBottom: 8 }}>
            Kineviz Database Proxy
          </Title>
          <Text type="secondary">
            Enter admin password to continue
          </Text>
        </div>

        {error && (
          <Alert
            message={error}
            type="error"
            showIcon
            style={{ marginBottom: 24 }}
          />
        )}

        <Form
          name="login"
          onFinish={handleSubmit}
          layout="vertical"
        >
          <Form.Item
            name="password"
            rules={[{ required: true, message: 'Please enter the admin password' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="Admin Password"
              size="large"
              autoFocus
            />
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              block
              size="large"
            >
              Login
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default Login;
