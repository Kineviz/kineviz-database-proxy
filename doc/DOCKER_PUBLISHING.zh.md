

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

[Docker 发布工作流](../.github/workflows/docker-publish.yml) 先运行 CI，再构建
Linux amd64 和 arm64 镜像。`vVERSION` 或 `docker-vVERSION` 标签必须与
`pyproject.toml` 版本一致。稳定版本同时发布 `VERSION` 和 `latest`；预发布版本
和 `develop` 分支不覆盖 `latest`。手动运行可选择 Docker Hub、阿里云或两者。

完成校验后，可从仓库根目录执行本地发布：

```bash
npm run install:frontend
npm run typecheck
npm run build
docker buildx build -f docker/Dockerfile --platform linux/amd64,linux/arm64 \
  -t kineviz/kineviz-database-proxy:1.1.4 \
  -t kineviz/kineviz-database-proxy:latest --push .
```

版本标签使用 `pyproject.toml` 中的版本号；发布预发布版本时省略 `latest`。
仅在本地构建而不上传时，将 `--push` 替换为
`--output type=oci,dest=dist/docker-image.tar`。

原 `kineviz/graphxr-database-proxy` 仓库已弃用。请将 Docker 和 Compose 的镜像引用
改为 `kineviz/kineviz-database-proxy`，保留现有配置和卷挂载。旧标签保留用于兼容，
后续版本只发布到新仓库。
