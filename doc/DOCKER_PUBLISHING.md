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

```bash
# Automated script that includes source code build, docker image build and publish
./docker/publish release
```
