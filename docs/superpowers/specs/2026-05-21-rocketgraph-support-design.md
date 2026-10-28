# RocketGraph 图数据库支持 - 设计文档

**日期**: 2026-05-21
**状态**: 已批准，待实施
**作者**: Claude + Sean Li

## 1. 背景与目标

为 graphxr-database-proxy 添加 RocketGraph 图数据库支持。RocketGraph 是基于 XGT 图数据库引擎的 REST API 服务，支持 Cypher / GQL 查询。

**必须实现的三个 API**（对应 `doc/API_Reference.md`）：

1. **API Info** — `GET /api/rocketgraph/{project_id}`
2. **Query** — `POST /api/rocketgraph/{project_id}/query`
3. **Graph Schema** — `GET /api/rocketgraph/{project_id}/graphSchema`

**参考资源**：
- API 文档：`doc/dbs/RocketGraph-REST-API-User-Guide.docx`
- 示例数据库：https://kineviz.rocketgraph.com/
- 目标 API 规范：`doc/API_Reference.md`

## 2. 设计决策摘要

| 决策点 | 选择 |
|--------|------|
| 部署模式支持 | Standalone + Plugin 两种均支持 |
| Query 结果处理 | 智能识别 TABLE / GRAPH（基于值结构检测） |
| 配置方式 | 复用现有 `DatabaseConfig` + 扩展字段 |
| Token 生命周期 | 持久化到项目配置，过期前缓冲刷新 |
| 前端 UI | 同时实现前后端 |
| Plugin 认证 | 支持 `username_password` 和 `bearer_token` 两种 |
| graph_name 位置 | 项目级别（一项目=一graph） |

## 3. 架构总览

**核心策略**：复用现有 `BaseDatabaseDriver` 抽象，添加 `RocketGraphDriver`，对应新枚举值 `DatabaseType.ROCKETGRAPH`。所有功能通过现有 `/api/{database_type}/{project_name}/...` 路由自动暴露，无需新增 API 路由文件。

```
现有架构：
  api/database.py (通用路由)
       ↓
  drivers/factory.py → DriverFactory
       ↓
  drivers/base.py (BaseDatabaseDriver)
       ↓
  drivers/spanner.py (现有)
  drivers/rocketgraph.py (新增) ← 本次工作焦点

  models/project.py
    └─ DatabaseType, AuthType, DatabaseConfig (扩展)
    └─ OAuthConfig (复用 token 字段)
```

Driver 内部子组件：
- `AuthClient` — 登录、token 缓存/刷新
- `QueryParser` — 智能 TABLE/GRAPH 识别
- `SchemaMapper` — RocketGraph schema → 项目格式

## 4. 数据模型扩展

### 4.1 枚举扩展

```python
# models/project.py

class DatabaseType(str, Enum):
    SPANNER = "spanner"
    ROCKETGRAPH = "rocketgraph"   # 新增
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MONGODB = "mongodb"

class AuthType(str, Enum):
    OAUTH2 = "oauth2"
    SERVICE_ACCOUNT = "service_account"
    USERNAME_PASSWORD = "username_password"
    GOOGLE_ADC = "google_ADC"
    BEARER_TOKEN = "bearer_token"   # 新增（用于 Plugin 模式静态 token）
```

### 4.2 DatabaseConfig 扩展

新增字段，全部可选，仅 RocketGraph 使用：

```python
class DatabaseConfig(BaseModel):
    # ... 现有字段 ...

    # RocketGraph specific (新增)
    use_tls: Optional[bool] = False
    deployment_mode: Optional[str] = None   # "standalone" | "plugin"
    api_base_path: Optional[str] = None     # 可选自定义路径
```

复用字段：
- `host`, `port` — 已存在
- `graph_name` — 已存在（Spanner 沿用）
- `username`, `password` — 已存在（USERNAME_PASSWORD 模式）
- `oauth_config.token`, `oauth_config.expires_in`, `oauth_config.last_refreshed` — 复用作 JWT 缓存

### 4.3 Base URL 构造规则

```python
scheme = "https" if config.use_tls else "http"
default_path = "/api/xgt/v1" if config.deployment_mode == "plugin" else "/api/v1"
base_path = config.api_base_path or default_path
base_url = f"{scheme}://{config.host}:{config.port}{base_path}"
```

默认端口建议：
- `standalone`: 4368
- `plugin`: 8080

## 5. RocketGraphDriver 实现

文件路径：`src/kineviz_database_proxy/drivers/rocketgraph.py`

### 5.1 HTTP 客户端

使用 `httpx.AsyncClient`（FastAPI 生态原生选择）。如未在 `requirements.txt` 引入则添加。

### 5.2 接口实现

| 方法 | 行为 |
|------|------|
| `connect()` | 验证配置；按需登录并缓存 token |
| `disconnect()` | 关闭 httpx 客户端 |
| `test_connection()` | GET `/health` 或 `/version`，返回布尔 |
| `execute_query(query, parameters)` | POST `/graphs/{graph_name}/query`，调用 QueryParser |
| `get_graph_schema()` | GET `/graphs/{graph_name}/schema`，调用 SchemaMapper |
| `get_schema()` | 抛 `NotImplementedError`（本次不实现） |
| `get_sample_data()` | 抛 `NotImplementedError`（本次不实现） |
| `get_api_info(project_name)` | 返回已实现的 endpoint URL 映射 |

### 5.3 AuthClient 子组件

**USERNAME_PASSWORD 模式**：
1. 检查 `oauth_config.token` + `expires_in` + `last_refreshed`
2. 若 `(now - last_refreshed) >= (expires_in - 300)` 或 token 缺失 → 重新登录
3. POST `/auth/xgt/basic` body `{username, password}`
4. 解析响应 `{access_token, expires_in}`，通过 `ProjectService.update_project_token()` 持久化
5. 后续请求 header 加 `Authorization: Bearer {token}`

**BEARER_TOKEN 模式**：
- 直接使用配置中的 `oauth_config.token`，不做刷新（用户自行管理生命周期）

**401 处理**：执行查询时收到 401 → 触发一次重新登录后重试（仅 USERNAME_PASSWORD 模式）

### 5.4 QueryParser 智能 TABLE/GRAPH 识别

**输入格式**（RocketGraph 响应）：
```json
{
  "columns": ["p", "r", "q"],
  "data": [[{...node...}, {...edge...}, {...node...}], ...]
}
```

**识别规则**：
1. 对每行的每个值检测是否为图对象：
   - 节点判定：dict 且包含 `id`/`identifier` 且包含 `labels`（数组）或 `label`，且包含 `properties` 字段（dict）
   - 边判定：dict 且包含 `source`/`source_node_identifier` 和 `target`/`destination_node_identifier`，且包含 `type`/`label`
2. 若**任何值**符合节点或边的判定 → 输出 `QueryData(type="GRAPH", ...)`
   - 收集所有节点（按 id 去重）和所有边（按 id 去重）
   - 标量值在 GRAPH 模式下被忽略（GraphXR 不需要）
3. 若全部为标量 → 输出 `QueryData(type="TABLE", data=[{col_name: value, ...}, ...])`

**已知不确定性**：RocketGraph 文档未明确定义 `RETURN p` 时节点的 JSON 结构。实施阶段将：
- 通过 `kineviz.rocketgraph.com` 实测节点和边的实际结构
- 在 QueryParser 中支持多种已观察到的字段命名（`id` vs `identifier`，`label` vs `labels`，`source` vs `source_node_identifier` 等）

### 5.5 SchemaMapper

**RocketGraph schema 输入**：
```json
{
  "graph_name": "social",
  "graph_schema": {
    "node_types": [
      {
        "type": "Person",
        "key": "id",
        "properties": [{"name": "id", "type": "TEXT"}, {"name": "age", "type": "INT"}]
      }
    ],
    "edge_types": [
      {
        "type": "KNOWS",
        "source": "Person", "target": "Person",
        "source_key": "id", "target_key": "id",
        "properties": [{"name": "since", "type": "DATE"}]
      }
    ]
  }
}
```

**映射规则**：

节点：
```python
Category(
    name=node_type["type"],
    keys=[node_type["key"]],
    props=[p["name"] for p in node_type["properties"]],
    keysTypes={node_type["key"]: <从 properties 查找>},
    propsTypes={p["name"]: p["type"] for p in node_type["properties"]}
)
```

边：
```python
Relationship(
    name=edge_type["type"],
    startCategory=edge_type["source"],
    endCategory=edge_type["target"],
    keys=list({edge_type["source_key"], edge_type["target_key"]}),  # 去重
    props=[p["name"] for p in edge_type.get("properties", [])],
    keysTypes={...},
    propsTypes={p["name"]: p["type"] for p in edge_type.get("properties", [])}
)
```

类型保留原值字符串（`TEXT`, `INT`, `FLOAT`, `BOOL`, `DATE`, `DATETIME`, 等）。

### 5.6 get_api_info 实现

```python
def get_api_info(self, project_name: str) -> Dict[str, Any]:
    base_url = f"/api/rocketgraph/{project_name}"
    return {
        "type": "rocketgraph",
        "api_urls": {
            "info": base_url,
            "query": f"{base_url}/query",
            "graphSchema": f"{base_url}/graphSchema",
            "test": f"{base_url}/test"
        },
        "version": "1.0",
        "features": {
            "property_graph": True,
            "cypher": True,
            "gql": True,
            "graph_schema": True
        }
    }
```

### 5.7 错误处理

| RocketGraph 响应 | 行为 |
|-----------------|------|
| 401 | (USERNAME_PASSWORD) 重新登录后重试一次；仍失败则透传错误 |
| 400 / 422 | 错误信息包含 `error.code` + `error.message` 传递给 QueryResponse.error |
| 404 | 透传"Graph not found"等明确错误 |
| 5xx / 网络异常 | 包装为 `ConnectionError`，QueryResponse 标记失败 |

## 6. Factory 注册

```python
# drivers/factory.py
from .rocketgraph import RocketGraphDriver

_drivers: Dict[DatabaseType, Type[BaseDatabaseDriver]] = {
    DatabaseType.SPANNER: SpannerDriver,
    DatabaseType.ROCKETGRAPH: RocketGraphDriver,   # 新增
}
```

## 7. 前端 UI 扩展

### 7.1 类型扩展（`frontend/src/types/project.ts`）

```typescript
export type DatabaseType = 'spanner' | 'rocketgraph' | 'postgresql' | 'mysql' | 'mongodb';

export type AuthType = 'oauth2' | 'service_account' | 'google_ADC'
                     | 'username_password' | 'bearer_token';

export interface DatabaseConfig {
  // ... 现有字段 ...
  use_tls?: boolean;
  deployment_mode?: 'standalone' | 'plugin';
  api_base_path?: string;
}
```

### 7.2 ProjectForm.tsx 扩展

- 数据库类型下拉添加：
  ```tsx
  <Option value="rocketgraph">RocketGraph</Option>
  ```

- 添加 `{databaseType === "rocketgraph" && (...)}` 配置卡片：

| 字段 | 控件 | 默认值 |
|------|------|--------|
| Deployment Mode | Radio (standalone / plugin) | standalone |
| Host | Input | (空) |
| Port | InputNumber | 4368 / 8080（按 mode） |
| Use TLS | Switch | false |
| Graph Name | Input | (空) |
| Auth Type | Radio (Username/Password / Bearer Token) | username_password |
| Username (条件显示) | Input | (空) |
| Password (条件显示) | Password Input | (空) |
| Token (条件显示) | Password Input | (空) |
| Test Connection | Button | — |

`api_base_path` 作为高级选项，默认隐藏。

提交时设置 `database_type: "rocketgraph"`，构造 `database_config` 包含上述字段及 `auth_type`、`oauth_config: { token }` 或 `username/password`。

## 8. 测试策略

### 8.1 单元测试

- **QueryParser**：
  - 纯标量 → TABLE
  - 含节点 dict → GRAPH（验证节点提取、去重）
  - 含边 dict → GRAPH（验证边的 source/target 映射）
  - 混合标量与图对象 → GRAPH（标量被忽略）
  - 空 data → 返回空 GRAPH / TABLE

- **SchemaMapper**：
  - 完整 schema 转换正确
  - 缺少 `properties` 字段时的鲁棒处理
  - 类型字符串保留

- **AuthClient**：
  - token 在缓冲期内不刷新
  - token 过期触发刷新并持久化
  - BEARER_TOKEN 模式不调用 /auth/xgt/basic

### 8.2 集成测试

使用公开示例数据库 `kineviz.rocketgraph.com` 端到端验证：
1. 创建 RocketGraph 项目 → 测试连接成功
2. GET `/api/rocketgraph/{project}` → 返回正确 api_urls
3. GET `/api/rocketgraph/{project}/graphSchema` → 返回非空 categories + relationships
4. POST `/api/rocketgraph/{project}/query` 简单 Cypher → 返回 TABLE 或 GRAPH

### 8.3 错误路径手动验证

- 错误的 host/port → 连接失败错误
- 错误的 username/password → 401，错误信息友好
- 不存在的 graph_name → 404，错误信息明确

## 9. 不实现的功能（YAGNI 范围）

| 功能 | 原因 |
|------|------|
| `/schema` (表 schema) endpoint | API_Reference.md 标注为 Optional，本次需求未明确 |
| `/sampleData` endpoint | 同上 |
| Ingest / Tables / Namespaces 等 XGT 独有 endpoint | GraphXR 不需要 |
| PKI、Proxy-PKI、OAuth2 认证 | 仅 username/password + bearer token 足够 |
| 异步 query (`/query/submit` + polling) | 同步 query 满足 GraphXR 用例；可作为后续增强 |
| TLS 证书自定义验证 | 依赖 httpx 默认 |
| Token refresh 端点 | RocketGraph 无 refresh endpoint，到期重新登录 |

## 10. 实施顺序建议

1. 后端模型扩展（`models/project.py`）
2. 新增 `drivers/rocketgraph.py`，按 AuthClient → SchemaMapper → QueryParser → Driver 主类顺序
3. Factory 注册 + 依赖安装（httpx，如缺失）
4. 单元测试（QueryParser、SchemaMapper）
5. 前端类型扩展 + ProjectForm UI
6. 集成测试（against kineviz.rocketgraph.com）
7. 文档更新（README 中提及 RocketGraph 支持，`doc/API_Reference.md` 添加 rocketgraph 类型示例）
