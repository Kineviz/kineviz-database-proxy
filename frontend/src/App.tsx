import React, { useEffect, useState } from 'react';
import { Routes, Route, useLocation } from 'react-router-dom';
import { Layout, Spin } from 'antd';
import AppHeader from './components/AppHeader';
import ProjectManagement from './pages/ProjectManagement';
import StoreLibrary from './pages/StoreLibrary';
import GraphMapping from './pages/GraphMapping';
import Settings from './pages/Settings';
import Login from './pages/Login';
import { initializeApiKey, setAdminToken } from './services/projectService';
import './styles/App.css';

const { Content } = Layout;

interface AuthStatus {
  admin_auth_enabled: boolean;
  authenticated: boolean;
}

const App: React.FC = () => {
  const location = useLocation();
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [adminAuthEnabled, setAdminAuthEnabled] = useState(false);

  // Check auth status on startup
  useEffect(() => {
    checkAuthStatus();
  }, []);

  const checkAuthStatus = async () => {
    setLoading(true);
    try {
      // Get stored token
      const storedToken = localStorage.getItem('adminToken');
      if (storedToken) {
        setAdminToken(storedToken);
      }

      // Check auth status with the backend
      const response = await fetch('/api/admin/status', {
        headers: storedToken ? { 'X-Admin-Token': storedToken } : {},
      });
      
      if (response.ok) {
        const status: AuthStatus = await response.json();
        setAdminAuthEnabled(status.admin_auth_enabled);
        setAuthenticated(status.authenticated);
        
        // If authenticated, initialize API key
        if (status.authenticated) {
          await initializeApiKey();
        }
      } else {
        // If status check fails, clear token and show login
        localStorage.removeItem('adminToken');
        setAdminToken(null);
        setAuthenticated(false);
      }
    } catch (error) {
      console.error('Failed to check auth status:', error);
      // On error, assume auth is not required (for backward compatibility)
      setAuthenticated(true);
      setAdminAuthEnabled(false);
      await initializeApiKey();
    } finally {
      setLoading(false);
    }
  };

  const handleLoginSuccess = async (token: string) => {
    if (token) {
      setAdminToken(token);
    }
    setAuthenticated(true);
    await initializeApiKey();
  };

  const handleLogout = async () => {
    const token = localStorage.getItem('adminToken');
    try {
      await fetch('/api/admin/logout', {
        method: 'POST',
        headers: token ? { 'X-Admin-Token': token } : {},
      });
    } catch (error) {
      console.error('Logout error:', error);
    }
    
    localStorage.removeItem('adminToken');
    setAdminToken(null);
    setAuthenticated(false);
  };

  // Show loading spinner while checking auth
  if (loading) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
      }}>
        <Spin size="large" />
      </div>
    );
  }

  // Show login page if admin auth is enabled and not authenticated
  if (adminAuthEnabled && !authenticated) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <Layout className={`app-layout${location.pathname === '/mapping' ? ' mapping-layout' : ''}`}>
      <AppHeader onLogout={adminAuthEnabled ? handleLogout : undefined} />
      <Content className="app-content">
        <div className={`content-wrapper${location.pathname === '/mapping' ? ' mapping-content' : ''}`}>
          <Routes>
            <Route path="/" element={<ProjectManagement />} />
            <Route path="/listProjects" element={<ProjectManagement />} />
            <Route path="/addProject" element={<ProjectManagement />} />
            <Route path="/editProject/:projectId" element={<ProjectManagement />} />
            <Route path="/admin" element={<ProjectManagement />} />
            <Route path="/projects" element={<ProjectManagement />} />
            <Route path="/files" element={<StoreLibrary />} />
            <Route path="/mapping" element={<GraphMapping />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </div>
      </Content>
    </Layout>
  );
};

export default App;
