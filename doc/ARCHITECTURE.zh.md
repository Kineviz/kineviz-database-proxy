# 架构与驱动扩展

[English](ARCHITECTURE.md) | [中文](ARCHITECTURE.zh.md)

修改相关子系统时查阅本说明。日常命令与开发约定见 [AGENTS.md](../AGENTS.md)。

## 应用与契约

`src/graphxr_database_proxy/main.py` 组装 FastAPI。存在 `frontend/dist` 时使用其中的
前端资源，否则使用包内静态资源。以下顺序必须保留：

- 在导入 Google Cloud 库前设置 `OTEL_*` / `SPANNER_*` 环境变量。
- 在 `CORSMiddleware` 后添加 `PrivateNetworkAccessMiddleware`，使后者包裹 CORS。
- 在 `/api/{database_type}/{project_name}` 前挂载 `/api/relational`，避免通用路由
  把 `relational` 解释为数据库类型。

`contract/contract.schema.json` 镜像自 **graphxr-dev** 的 `shared/graphdb`，以该仓库的
TypeScript 契约为准。`tests/test_contract.py` 检查 Pydantic 模型是否一致。
契约字段变更需同步两个仓库。`/capabilities` 直接返回能力文档；其他读取路由通常
使用 `{success, data}` 包装。

`services/` 持久化 JSON 配置。驱动按请求创建，因此图模式 TTL 缓存放在
`services/graph_schema_cache.py`，不放在驱动实例上。`services/store_library.py`
分页遍历存储目录，仅探测当前返回页中的文件，修改时须保持此行为。

## 共享图行为与标识

驱动实现 `BaseDatabaseDriver`，由 `DriverFactory` 注册。图意图使用
`GraphIntentMixin`、`StatementDialect` 和 `graph_support.py`，遍历逻辑不放入单个驱动。
`dialect.py` 重导出 `dialect_core.py`（语句构建）和 `dialect_backends.py`
（后端符号与注册表）的名称。Bolt 系列共用 `bolt_mapping.py`、`bolt_schema.py`。

驱动返回的节点 ID 必须能被后续标识谓词匹配：

| 后端 | 标识规则 |
| --- | --- |
| Spanner | `ELEMENT_ID()` |
| BigQuery | 从 `TO_JSON(...)` 读取标识 |
| Neo4j / Memgraph | 共用兼容的 `ID()` |
| RocketGraph | `<Label>:<key>`，没有标识函数 |
| Kuzu / Ladybug | 从表主键构造 `<Label>:<key>`，内部 ID 无法作为字面量匹配 |
| 关系型数据库 | 从按名称引用并加引号的主键列构造 `<Label>:<key>` |
| LatticeDB | 整数 `id(n)` |

依赖主键构造标识的图结果需同时包含两端节点，方言返回 `n, r, m` 才能放置边。
LatticeDB 没有实体值类型，需显式投影 ID、标签、属性，并分别展开两个方向。

## 关系型数据库

`drivers/relational/driver.py` 通过 `driver_mapping.py`、`driver_query.py` 和
`driver_schema.py` 共享映射、查询、模式实现。内部协议是 Cypher：
`translate.py` -> `plan.py` -> `compile.py` 将图语句转为 SQLAlchemy Core。

后端差异集中在 `engines/*.py` 的 `EngineSpelling` 实现中，共享模块不按引擎名称分支。
类型转换及文档、拆分列类型分别通过 `cast_sqlalchemy_type`、`json_document_type`
和 `shredded_column_type` 处理。映射加载、分页反射和可复用连接池分属独立模块。
图标签和关系类型遵守 `SAFE_IDENT`；数据库表名、列名保留原始拼写并加引号。
语义以[关系型映射](RELATIONAL_MAPPING.zh.md)为准，管理路由见
[关系型 API](RELATIONAL_API.md)。

## 嵌入式数据库

`drivers/embedded/` 探测文件头、解析版本、安装运行时，并通过 `worker.py` / `pool.py`
启动独立进程。原生引擎必须隔离：Kuzu 与 Ladybug 同时导入会冲突，多个 LatticeDB
版本也不能共用解释器。工作进程回复使用字节长度分帧，不能用按行读取传输大型结果。

文件魔数仅在同一引擎家族内选择引擎。Kuzu 项目可打开 Ladybug 存储；LatticeDB
项目必须拒绝 Kuzu 家族存储。LatticeDB 是独立引擎，使用自己的映射、模式、会话和
查询适配器；`lattice_query.py` 改写不支持的投影与标识符，拒绝无法可靠解释的语句。
Kuzu/Ladybug 共用各自的映射与模式工具。用户自行构建的 wheel 可通过 wheelhouse
提供。版本、运行时路径和平台细节见[嵌入式数据库](EMBEDDED_STORES.zh.md)。

## 添加后端

1. 在 `models/project.py` 添加 `DatabaseType` 和所需 `DatabaseConfig` 字段。
2. 实现 `BaseDatabaseDriver` 并在 `drivers/factory.py` 注册。
3. 通过上述共享模块接入图意图、方言与能力声明。
4. 添加后端测试，检查 `tests/test_driver_intents.py` 的一致性覆盖。
5. 更新管理界面配置，以及下节所述 **graphxr-dev** 代理适配器。

新增关系型引擎则在 `relational/engines/` 实现所有 `EngineSpelling` 抽象方法并注册，
继承现有关系型驱动，设置 `database_type`、`engine_name`，检查
`tests/test_relational_engine_types.py`。通用数据库路由会使用已注册驱动，
客户端仍需显式接入。

## GraphXR 客户端接入

GraphXR 使用 **graphxr-dev** 中的
`web/react_views/configure/graphdb/databases/proxy/` 访问本服务。
原生数据库适配器属于独立直连方式。每个代理后端都需更新 `proxy/capabilities.ts`
和 `proxy/dialect.ts`；未知后端会回退到 Bolt 标识和方言，可能生成不适用的语句。

客户端镜像静态能力字段，但从实时 `/capabilities` 响应探测 `intents`。
意图模式向 `/expand`、`/pullCategory` 等发送 POST，由代理构建语句。
探测缺失或失败（包括旧代理的 404）时，回退到客户端依据旧版配置构建语句。
两种模式都须正确。移植后端支持时，以客户端原生能力与方言定义为参考；
若无法访问另一仓库，应单独说明仍需在其中完成的变更。
