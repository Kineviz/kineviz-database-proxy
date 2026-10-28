# Docker Image Publishing Guide
This document describes how to build and publish Docker images to Docker Hub.

> **Language**: [English](DOCKER_PUBLISHING.md) | [中文](DOCKER_PUBLISHING.zh.md)

[Back to Development Guide](DEV_GUIDE.md)

## Included database drivers

The image installs the local Python package with its `[relational]` extra, including
PyMySQL, psycopg, pymssql and the Snowflake SQLAlchemy dialect. Spanner, BigQuery,
Neo4j, Memgraph and RocketGraph use the base package dependencies. Kuzu, Ladybug and
LatticeDB install version-matched native engines on demand in isolated workers.

Check imports and relational dialect initialization without connecting to databases:

```bash
docker run --rm -i --entrypoint python kineviz/kineviz-database-proxy:latest - < scripts/check_docker_drivers.py
```

## Prerequisites
- Docker is installed.
- Docker Hub account has been created.
- Logged in to Docker Hub: `docker login`

## Build and Publish Docker Image

The [Docker publishing workflow](../.github/workflows/docker-publish.yml) runs CI
before building Linux amd64 and arm64 images. A `vVERSION` or `docker-vVERSION`
tag must match `pyproject.toml`. Stable versions publish both `VERSION` and `latest`;
prereleases and the `develop` branch do not replace `latest`. Manual runs select
Docker Hub, Aliyun, or both registries.

To publish locally after verification, run from the repository root:

```bash
npm run install:frontend
npm run typecheck
npm run build
docker buildx build -f docker/Dockerfile --platform linux/amd64,linux/arm64 \
  -t kineviz/kineviz-database-proxy:1.1.4 \
  -t kineviz/kineviz-database-proxy:latest --push .
```

Use the version from `pyproject.toml` for the version tag. Omit `latest` when
publishing a prerelease. For a local build without uploading, replace `--push`
with `--output type=oci,dest=dist/docker-image.tar`.

The former `kineviz/graphxr-database-proxy` repository is deprecated. Update
Docker and Compose image references to `kineviz/kineviz-database-proxy` and retain
existing configuration and volume mounts. Existing old tags remain available
for compatibility; future releases use the new repository.
