# Snowflake

[English](SNOWFLAKE.md) | [中文](SNOWFLAKE.zh.md)

Snowflake 复用关系型数据库驱动，支持表发现、图查询、Expand、节点/关系拉取、
方向预览，以及可视化 Graph Mapping 编辑器。支持 JSON v2 和 YAML 映射；
映射语义见 [RELATIONAL_MAPPING](RELATIONAL_MAPPING.zh.md)。

## 安装与连接

```sh
uv sync --extra dev --extra snowflake
```

安装发行包时使用 `pip install "graphxr-database-proxy[snowflake]"`。
`relational` 扩展包含 Snowflake、SQL Server、PostgreSQL 和 MySQL。
Snowflake 的 SQLAlchemy 方言目前依赖 2.0 的编译接口，因此此扩展将版本限制在 2.1 以下。

创建项目时选择 **Snowflake**，填写：

| 字段 | 示例 | 含义 |
| --- | --- | --- |
| Account identifier | `myorg-myaccount` | 账号标识；也支持完整的 `*.snowflakecomputing.com` 主机名，不包含协议或路径 |
| Database | `ANALYTICS` | 初始数据库 |
| Schema | `PUBLIC` | 默认 schema；映射条目可以覆盖 |
| Warehouse | `GRAPHXR_WH` | 执行图读取使用的计算仓库 |
| Role | `GRAPHXR_READER` | 可选；留空使用账号默认角色 |
| Username / Password | 你的登录信息 | 密码认证 |

连接始终使用 HTTPS。账号存放于 `host`，数据库存放于 `database_id`，schema
存放于 `schema_name`，仓库和角色存放于 `options.warehouse` / `options.role`。
**Test Connection**、**Browse tables** 和保存项目均使用这些配置。
API 可通过 `options.authenticator` 选择连接器的其他认证方式；界面提供用户名/密码认证。
本次集成未提供密钥对或 OAuth token 的配置入口。

角色需要对仓库、数据库和 schema 的 `USAGE` 权限，以及对映射表的 `SELECT` 权限。
代理的图查询翻译器会拒绝写操作。Snowflake 没有只读事务模式，因此数据库角色也需要限制为
只读权限。`options.connect_timeout` 默认 15 秒，`options.query_timeout` 默认 120 秒，
后者同时设置服务端的 `STATEMENT_TIMEOUT_IN_SECONDS`。

GraphXR 的代理 API 地址为 `/api/snowflake/<项目名>`。映射管理沿用
`/api/relational/<项目名>/...`，见 [RELATIONAL_API](RELATIONAL_API.md)。

## 映射名称与节点身份

请使用 **Browse tables** 返回的真实表名和列名。Snowflake 将普通未加引号的标识符保存为
大写；使用引号创建的小写或混合大小写名称是不同的名称。代理保留目录返回的大小写，生成 SQL
时对 schema、表和列显式加引号。例如，图标签 `Person` 可以映射到物理表 `PERSON`，
主键为 `ID`，图属性 `name` 映射到列 `NAME`。

[映射示例](../examples/snowflake.graphxr-mapping.json) 使用 `PUBLIC` 中的 `PERSON`、
`COMPANY` 和 `WORKS_AT`。按实际表结构调整后导入 JSON，也可以通过 YAML 标签页编辑和导出。

Snowflake 普通表不会强制执行 `PRIMARY KEY`、`UNIQUE` 或 `FOREIGN KEY` 约束。
自动发现会给出提示：需要确认主键唯一且非空、关联列能正确连接节点。
没有声明主键或外键的表需要显式映射。复合主键仍需选择一个适合图身份的单列唯一键。

`NUMBER(p,0)` / `DECIMAL(p,0)` 主键保留全部精度，包括 38 位标识符；带小数的数值保持
Decimal 精度。不同 Timestamp 类型均显示为时间戳。`VARIANT`、`OBJECT` 和 `ARRAY` 属性
可读取，但这些列的标量过滤需要本查询管线尚未实现的路径语义；过滤和主键应使用标量列。

表选择器直接在 Snowflake 的 `INFORMATION_SCHEMA.TABLES` 上过滤、分页，列读取限定于
请求的表。行数估计使用目录的 `ROW_COUNT`，不会扫描源表；大批主键查询会拆为有界的查询块。

## 验证

```sh
npm test -- tests/test_snowflake.py tests/test_relational_capability_record.py -q
npm run typecheck
npm run build
```

测试离线运行，并使用已安装的 Snowflake 方言编译 SQL；未安装可选扩展时，方言相关测试
会跳过。真实连接验证需要已授权的 Snowflake 账号和 Warehouse，PostgreSQL 示例测试
不能证明 Snowflake 已连通。

GraphXR 客户端需要将 `snowflake` 识别为关系型代理后端，使用标签/主键身份并协商代理公布的
图操作。使用静态后端名单的旧客户端需要在代理能力表和方言表中增加 Snowflake，见
[集成说明](ARCHITECTURE.zh.md#graphxr-客户端接入)。

官方参考：[Snowflake SQLAlchemy](https://docs.snowflake.com/en/developer-guide/python-connector/sqlalchemy)、
[Python 连接器参数](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-api)、
[约束行为](https://docs.snowflake.com/en/sql-reference/constraints-overview)。
