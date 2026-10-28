# Kineviz 数据库代理

[English](README.md) | [中文](README.zh.md)

基于 FastAPI 的中间件，通过 REST API 将 [GraphXR](https://www.kineviz.com/graphxr)
连接到数据库，并提供 React 管理界面来配置连接和设置。

支持 Spanner Graph、BigQuery、RocketGraph、Neo4j、Memgraph、Kuzu、Ladybug、
LatticeDB、SQL Server、PostgreSQL、MySQL 和 Snowflake。可选的管理员密码和 API 密钥
分别保护管理操作与数据库访问。

## 快速开始

```bash
pip install "kineviz-database-proxy[ui]"
kineviz-proxy --ui
```

Python 包名和源码目录均为 `kineviz_database_proxy`，已有 Python 导入需改用此名称。
继续兼容 `graphxr-proxy` 命令、`GRAPHXR_PROXY_*` 环境变量和现有存储路径。

打开 http://localhost:9080/admin，创建项目，将其 API URL 复制到 GraphXR 的
**Database Proxy** 连接中。例如：`http://localhost:9080/api/spanner/Test`。

使用 SQL 驱动时，安装对应扩展：`[mssql]`、`[postgresql]`、`[mysql]`、`[snowflake]`，
或用 `[relational]` 安装全部四个驱动。连接和认证配置见[使用指南](doc/USAGE.zh.md)。
Snowflake 的连接和映射说明见 [Snowflake 指南](doc/SNOWFLAKE.zh.md)。

## 源码开发

需要 Python >= 3.9、Node.js >= 22 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/Kineviz/kineviz-database-proxy.git
cd kineviz-database-proxy
uv sync --extra dev
npm install
npm run install:frontend
npm run dev
```

API 地址为 http://localhost:9080；前端请使用 webpack 输出的地址
（未设置 `PORT` 时自动选择端口）。API 文档位于 http://localhost:9080/docs。

Claude Code、Codex、Cursor、GitHub Copilot 和 Gemini CLI 共用
[AGENTS.md](AGENTS.md)。各工具入口与验证命令见[开发指南](doc/DEV_GUIDE.zh.md)。

## Docker

```bash
docker run -d -p 9080:9080 --name kineviz-database-proxy -v graphxr-proxy-config:/app/config kineviz/kineviz-database-proxy:latest
```

打开 http://localhost:9080/admin。命名卷会持久保存项目配置。

## 文档

- [使用与认证](doc/USAGE.zh.md) · [Python 示例](examples/README.md)
- [开发指南](doc/DEV_GUIDE.zh.md) · [架构说明](doc/ARCHITECTURE.zh.md)
- [API 参考](doc/API_Reference.md) · [关系型管理 API](doc/RELATIONAL_API.md)
- [关系型映射](doc/RELATIONAL_MAPPING.zh.md) · [嵌入式数据库](doc/EMBEDDED_STORES.zh.md)
- [Docker 发布](doc/DOCKER_PUBLISHING.zh.md) · [PyPI 发布](doc/PYPI_PUBLISHING.zh.md)

[MIT 许可证](LICENSE) · [问题反馈](https://github.com/Kineviz/kineviz-database-proxy/issues)
· support@kineviz.com
