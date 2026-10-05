import React, { useState, useEffect } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import {
  Card,
  Button,
  Table,
  Space,
  Typography,
  message,
  Modal,
  Popconfirm,
  Tag,
  Tooltip,
} from 'antd';
import {
  CopyOutlined,
  EditOutlined,
  DeleteOutlined,
  LinkOutlined,
  PartitionOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { Project, ProjectCreate, DatabaseType, isRelationalType } from '../types/project';
import { projectService } from '../services/projectService';
import ProjectForm, { StorePrefill } from '../components/ProjectForm';
import ApiUrlsModal from '../components/ApiUrlsModal';
import ProjectListHeader from '../components/ProjectListHeader';

const handleCopyApiUrl = async (project: Project) => {
  try {
    const apiUrl = `${window.location.origin}/api/${project?.database_type}/${project.name}`;
    await navigator.clipboard.writeText(apiUrl);
    message.success('API URL copied to clipboard');
  } catch (error) {
    message.error('Failed to copy API URL');
    console.error('Error copying API URL:', error);
  }
};

const ProjectManagement: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { projectId } = useParams<{ projectId: string }>();
  
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);

  // 根据路由确定当前状态
  const isCreateMode = location.pathname === '/addProject';
  // The Files page opens this form on a store it already identified, so the path
  // and the matching type arrive with the navigation rather than being retyped.
  const prefill = (location.state as { store?: StorePrefill } | null)?.store ?? undefined;
  const isEditMode = location.pathname.startsWith('/editProject/');
  const createModalVisible = isCreateMode;
  const editModalVisible = isEditMode;
  const [apiUrlsModalVisible, setApiUrlsModalVisible] = useState(false);

  useEffect(() => {
    let cancelled = false;
    
    const loadProjectsAsync = async () => {
      setLoading(true);
      try {
        const data = await projectService.getProjects();
        if (!cancelled) {
          setProjects(data);
        }
      } catch (error) {
        if (!cancelled) {
          message.error('Failed to load projects');
          console.error('Error loading projects:', error);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    loadProjectsAsync();
    
    return () => {
      cancelled = true;
    };
  }, []);

  // 当路由变为编辑模式时，查找对应的项目
  useEffect(() => {
    if (isEditMode && projectId && projects.length > 0) {
      const project = projects.find(p => p.id === projectId);
      if (project) {
        setSelectedProject(project);
      } else {
        message.error('Project not found');
        navigate('/listProjects');
      }
    }
  }, [isEditMode, projectId, projects, navigate]);

  const loadProjects = async () => {
    setLoading(true);
    try {
      const data = await projectService.getProjects();
      setProjects(data);
    } catch (error) {
      message.error('Failed to load projects');
      console.error('Error loading projects:', error);
    } finally {
      setLoading(false);
    }
  };


  const handleEditProject = (project: Project) => {
    setSelectedProject(project);
    navigate(`/editProject/${project.id}`);
  };

  const handleDeleteProject = async (projectId: string) => {
    try {
      await projectService.deleteProject(projectId);
      message.success('Project deleted successfully');
      loadProjects();
    } catch (error) {
      message.error('Failed to delete project');
      console.error('Error deleting project:', error);
    }
  };

  const handleShowApiUrls = (project: Project) => {
    setSelectedProject(project);
    setApiUrlsModalVisible(true);
  };

  const handleCreateSubmit = async (values: ProjectCreate) => {
    try {
      const created = await projectService.createProject(values);
      message.success('Project created successfully');
      navigate('/listProjects');
      loadProjects();
      // A mapping file rode with the create and the proxy — never the browser — decided
      // whether it loads. `createProject` warns in place about whatever will not load; the
      // project and the mapping are both real, so this is an offer to fix the file.
      if (values.graph_mapping_yaml?.trim() && created?.name) {
        Modal.confirm({
          title: `${created.name} was created with a mapping file`,
          content: `The text went to /api/relational/${created.name}/mapping. If the warning above reports problems, the file was stored and the graph will not load until they are fixed: open Graph Mapping, fix the file and apply it there.`,
          okText: 'Open Graph Mapping',
          cancelText: 'Stay on Projects',
          onOk: () => navigate(`/mapping?project=${encodeURIComponent(created.name)}`),
        });
      }
    } catch (error) {
      message.error('Failed to create project');
      console.error('Error creating project:', error);
    }
  };

  const handleEditSubmit = async (values: any) => {
    if (!selectedProject) return;
    
    try {
      await projectService.updateProject(selectedProject.id, values);
      message.success('Project updated successfully');
      navigate('/listProjects');
      loadProjects();
    } catch (error) {
      message.error('Failed to update project');
      console.error('Error updating project:', error);
    }
  };

  const handleModalCancel = () => {
    navigate('/listProjects');
    setSelectedProject(null);
  };

  const getDatabaseTypeColor = (type: DatabaseType): string => {
    switch (type) {
      case 'spanner':
        return 'blue';
      case 'bigquery':
        return 'geekblue';
      case 'rocketgraph':
        return 'volcano';
      case 'neo4j':
        return 'cyan';
      case 'memgraph':
        return 'magenta';
      case 'postgresql':
        return 'green';
      case 'mysql':
        return 'orange';
      case 'mssql':
        return 'gold';
      case 'mongodb':
        return 'purple';
      default:
        return 'default';
    }
  };

  const columns: ColumnsType<Project> = [
    {
      title: 'Project Name',
      dataIndex: 'name',
      key: 'name',
      render: (text: string) => <strong>{text}</strong>,
    },
    {
      title: 'Database Type',
      dataIndex: 'database_type',
      key: 'database_type',
      render: (type: DatabaseType) => (
        <Tag color={getDatabaseTypeColor(type)}>
          {type.toUpperCase()}
        </Tag>
      ),
    },
    {
      title: 'Created Time',
      dataIndex: 'create_time',
      key: 'create_time',
      render: (time: string) => new Date(time).toLocaleString(),
    },
    {
      title: 'Updated Time',
      dataIndex: 'update_time',
      key: 'update_time',
      render: (time: string) => new Date(time).toLocaleString(),
    },
    {
      title: 'Actions',
      key: 'actions',
      render: (_, record: Project) => (
        <Space size="small">
          <Tooltip title="Copy the API URL (GraphXR uses it)">
            <Button
              type="text"
              icon={<CopyOutlined />}
              onClick={() => handleCopyApiUrl(record)}
            />
          </Tooltip>
          <Tooltip title="Show API URLs">
            <Button
              type="text"
              icon={<LinkOutlined />}
              onClick={() => handleShowApiUrls(record)}
            />
          </Tooltip>
          {/* Only a table catalog has a mapping to review, so the action is not offered
              for an engine whose graph the database already names. */}
          {isRelationalType(record.database_type) && (
            <Tooltip title="Graph mapping">
              <Button
                type="text"
                icon={<PartitionOutlined />}
                onClick={() => navigate(`/mapping?project=${encodeURIComponent(record.name)}`)}
              />
            </Tooltip>
          )}
          <Tooltip title="Edit Project">
            <Button
              type="text"
              icon={<EditOutlined />}
              onClick={() => handleEditProject(record)}
            />
          </Tooltip>
          <Popconfirm
            title="Delete Project"
            description="Are you sure you want to delete this project?"
            icon={<ExclamationCircleOutlined style={{ color: 'red' }} />}
            onConfirm={() => handleDeleteProject(record.id)}
            okText="Yes"
            cancelText="No"
          >
            <Tooltip title="Delete Project">
              <Button
                type="text"
                danger
                icon={<DeleteOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div className="project-management">
      {/* 只在列表页面显示项目列表 */}
      {!isCreateMode && !isEditMode && (
        <>
          <ProjectListHeader />
          <Card style={{ marginTop: 16 }}>
            <Table
              columns={columns}
              dataSource={projects}
              rowKey="id"
              loading={loading}
              pagination={{
                pageSize: 10,
                showSizeChanger: true,
                showQuickJumper: true,
                showTotal: (total, range) =>
                  `${range[0]}-${range[1]} of ${total} items`,
              }}
            />
          </Card>
        </>
      )}

      {/* Create Project Modal */}
      <Modal
        title="Create New Project"
        open={createModalVisible}
        onCancel={handleModalCancel}
        footer={null}
        width={800}
      >
        <ProjectForm
          open={createModalVisible}
          prefill={prefill}
          onSubmit={handleCreateSubmit}
          onCancel={handleModalCancel}
        />
      </Modal>

      {/* Edit Project Modal */}
      <Modal
        title="Edit Project"
        open={editModalVisible}
        onCancel={handleModalCancel}
        footer={null}
        width={800}
      >
        <ProjectForm
          initialValues={selectedProject}
          open={editModalVisible}
          onSubmit={handleEditSubmit}
          onCancel={handleModalCancel}
        />
      </Modal>

      {/* API URLs Modal */}
      <ApiUrlsModal
        visible={apiUrlsModalVisible}
        project={selectedProject}
        onClose={() => setApiUrlsModalVisible(false)}
      />
    </div>
  );
};

export default ProjectManagement;