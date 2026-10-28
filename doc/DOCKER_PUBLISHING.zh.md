

# Docker image 发布指南
本文档介绍了如何构建和发布 Docker 镜像到 Docker Hub。

> **语言**: [English](DOCKER_PUBLISHING.md) | [中文](DOCKER_PUBLISHING.zh.md)


[返回开发指南](DEV_GUIDE.zh.md)

## 镜像包含的数据库驱动

镜像安装本地 Python 包及其 `[relational]` 扩展，包含 PyMySQL、psycopg、pymssql
和 Snowflake SQLAlchemy 方言。Spanner、BigQuery、Neo4j、Memgraph、RocketGraph
使用基础依赖。Kuzu、Ladybug、LatticeDB 按需安装匹配版本的原生引擎，并运行于独立工作进程。

检查驱动导入和关系型数据库方言初始化，不连接数据库：

```bash
docker run --rm -i --entrypoint python kineviz/kineviz-database-proxy:latest - < scripts/check_docker_drivers.py
```


## 前提条件
- 已安装 Docker。
- 已创建 Docker Hub 账号。
- 已登录 Docker Hub：`docker login`

## 构建 Docker 镜像并发布

```bash
# 包含了源代码构建，docker 镜像构建和发布的自动化脚本
./docker/publish release
```
