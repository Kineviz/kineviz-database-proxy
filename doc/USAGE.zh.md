# Kineviz Database Proxy - 使用指南

> **语言**: [English](https://github.com/Kineviz/kineviz-database-proxy/blob/main/doc/USAGE.md) | [中文](https://github.com/Kineviz/kineviz-database-proxy/blob/main/doc/USAGE.zh.md)

## 🚀 快速启动

### 1. 启动完整开发环境

#### 后端 API 服务器
```bash
# 在项目根目录
python -m uvicorn src.kineviz_database_proxy.main:app --reload --port 9080
```
服务器运行在: http://localhost:9080

#### 前端开发服务器 (可选，用于开发)
```bash
# 在 frontend 目录
cd frontend
npm install
npm run dev
```
开发服务器运行在: http://localhost:3002 (带热重载)

### 2. 访问 Web 界面

- **生产界面**: http://localhost:9080 (后端直接服务前端)
- **开发界面**: http://localhost:3002 (前端开发服务器，带热重载)

## 💼 项目管理功能

### 创建新项目
1. 点击 "Add Project" 按钮
2. 填写项目基本信息：
   - **Project Name**：项目的显示名
   - **Database Type**：项目指向的引擎——图数据库、嵌入式库文件，或者
     `SQL Server` / `PostgreSQL` / `MySQL`
   - **Database Configuration**：连接参数（见下面各引擎的字段说明）

   关系型、嵌入式、Kuzu / Ladybug / LatticeDB 的连接卡只问主机/端口或库文件路径 + 用户名密码；
   下面的「Google Cloud Spanner」一节才需要选 **OAuth2** 或 **Service Account**，其它类型都没有这一步。

### Google Cloud Spanner

**基本配置**：
- **Project ID**：Google Cloud 项目 ID
- **Instance ID**：Spanner 实例名
- **Database ID**：目标数据库名

**认证方式**（仅 Spanner）：
- **OAuth2**: 用户级认证，适合开发与测试
- **Service Account**: 机机认证，适合生产

OAuth2 配置：
```json
{
  "client_id": "your-client-id.apps.googleusercontent.com",
  "client_secret": "your-client-secret",
  "redirect_uri": "http://localhost:9080/google/spanner/callback"
}
```

Service Account 配置：
- 上传服务账号 JSON 文件路径

### Kuzu / Ladybug / LatticeDB（嵌入式）
这几种是文件而不是服务器，因此没有主机、也没有密码。

LatticeDB 没有发布 Windows 轮子。引擎编译出来之后在 Windows 上是能跑的，所以那里需要先做
一次编译，见 [EMBEDDED_STORES.zh.md](EMBEDDED_STORES.zh.md#自己编译引擎)。
Kuzu 和 Ladybug 在哪都能装。

- **Database path**：库文件的路径；Kuzu 0.10 及更早写出的是目录。这个字段会列出代理已有的库
  和其它项目正在使用的路径，也可以把文件直接拖到表单上，路径会自动填好。
- **Engine version**：一般留空。文件头会说明该由哪个引擎版本打开，缺失时代理会自动下载。
- **Access**：默认只读，只读打开还允许其他进程同时打开同一个库。

**Files** 页面会列出代理磁盘上所有的库：每个文件到底是什么、哪些项目正指向它，以及上传、删除、
直接用它建项目的按钮。

引擎如何选择、装在哪里、打不开时怎么排查，见 [EMBEDDED_STORES.zh.md](EMBEDDED_STORES.zh.md)。

### 关系型（SQL Server / PostgreSQL / MySQL）
把客户自己的数据库当成图来服务。这张卡片问的是 **Host**、**Port**、**Use TLS**、**Database**、
**Schema**，以及 Credentials 里的 **Username** / **Password**：选好类型之后，端口和 schema 会直接填上
那个引擎自己的默认值，登录方式只有用户名 / 密码一种。

字段下面有两个按钮。**Test Connection** 按表单上当前的内容去试连，不用先保存项目。
**Browse tables** 对着同一个还没保存的连接列出表名，一次读一页。

新建项目还可以带上 **Mapping file (optional)**——一份你已经确认过的 `.yml`、`.yaml` 或 `.json`。
浏览器只把它当文本读，解析发生在项目建好之后的代理里，所以文件被拒只会留下一句提醒，项目本身不会丢。

这些字段各自意味着什么、mapping 怎么描述一张表，见
[RELATIONAL_MAPPING.zh.md](RELATIONAL_MAPPING.zh.md#建项目的时候要填什么)；mapping 本身怎么改，见下面的
「Graph Mapping 页」。

### 连接测试
- **Test Connection** 在关系型、Neo4j / Memgraph、RocketGraph 项目的连接卡片里。
- 它用的就是表单当前的值：不用先保存项目，试连本身也不保存任何东西。
- 答案是一句 `Connection successful`，或者 `Connection failed: …`。

### API URL 生成
- 项目列表每一行有两个图标：复制图标把 GraphXR 要用的连接地址放进剪贴板；链接图标打开 **API URLs**，
  里面除了这个地址，还列着 query、schema、graphSchema、sampleData，每个都能单独复制。
- 关系型项目走的还是那套通用路由，所以地址是 `http://<host>:9080/api/mssql/<project>`
  （`postgresql`、`mysql` 换在同一个位置）。`/api/relational/…` 是 mapping 页用的管理路由，不是填给
  GraphXR 的；路由清单见 [RELATIONAL_API.md](RELATIONAL_API.md)（这份没有中文对照）。

### Graph Mapping 页
顶部菜单的 **Graph Mapping**（或者关系型那一行的 mapping 图标）打开的是把表目录读成图的那一页。它只对
已经存在的项目起作用，选中的项目会写进网址（`?project=`），所以一个链接就能回到同一份复核；非关系型的
项目会灰着列在那里并写明原因——它们的数据库自己就叫得出节点的名字。

- **Records**、**Links**、**YAML** 是同一份草稿的三个视图，不是三份文档。**Add a record** /
  **Add a link** 加行，取消勾选就是把这条从图里拿掉；链接上的 **Check direction** 会用真实数据两头各跑
  一次，答案是 `Settled`、`Backwards`、`You decide` 或 `Neither`。
- 工具条上有 **Upload mapping**、**Download**、**Re-discover**、**Example**、**Reset to
  auto-discovery**、**Reload** 和 **Save mapping**。Re-discover 只是提一个方案，什么都不存；它在大
  schema 里漏掉了什么，就写在这个方案旁边。**Example** 显示代理自带的那份带注释的示例文件——要看就看它，
  别抄到别处去。
- 标签页是空的，不等于图是空的：只是还没人覆盖自动发现。
- 读不进来的文件只报问题、不动草稿，每一条都带着「该改哪个控件」的路径；能解析但仍有问题的 mapping
  **会**被存下来，页面也会明说——自动发现会把剩下的补齐。

文档自己的规则在 [RELATIONAL_MAPPING.zh.md](RELATIONAL_MAPPING.zh.md)，这些按钮背后的路由在
[RELATIONAL_API.md](RELATIONAL_API.md)。

### 编辑项目
1. 点击项目名旁边的编辑图标
2. 修改配置参数
3. 保存修改——系统会校验连接

### 删除项目
1. 选中要删除的项目
2. 点击删除按钮
3. 确认删除


## 🎯 GraphXR 集成

### 配置 GraphXR 连接
1. 在 GraphXR 中添加新的数据源
2. 使用生成的 API URL: `http://localhost:9080/api/spanner/{project_id}`
3. 配置认证头（如果需要）

### 数据模式映射
代理服务把数据库模式自动转成 GraphXR 能识别的格式：

**Nodes**：表示数据库表
**Edges**：表示外键关系
**Properties**：表示表的列

### 样本数据导出
系统按 GraphXR 兼容的格式提供样本数据：
- **JSON 格式**：直接导入 GraphXR
- **关系映射**：根据外键自动创建边
- **类型转换**：数据库类型映射到 GraphXR 的属性类型

### 示例查询
```sql
-- Property Graph 查询
GRAPH example_graph
MATCH (n)-[r]->(m)
RETURN n, r, m
LIMIT 100

-- SQL 查询
SELECT * FROM users LIMIT 10
```

## 🔐 安全配置

### API 密钥认证（可选）

代理支持可选的 API 密钥认证来保护所有 API 端点。开启后，客户端必须在请求里带上有效的 API 密钥。

#### 启用 API 密钥认证

设置 `API_KEY` 环境变量即可开启：

```bash
# 通过环境变量设置 API 密钥
export API_KEY=your-secure-api-key-here

# 启动服务器
python -m uvicorn src.kineviz_database_proxy.main:app --port 9080
```

或者写在 `.env` 文件里：
```
API_KEY=your-secure-api-key-here
```

#### 发起带认证的请求

开启 API 密钥认证后，所有 API 请求都要带上 `X-API-Key` 请求头：

```bash
# 示例：列出项目
curl -H "X-API-Key: your-secure-api-key-here" \
  http://localhost:9080/api/project/list

# 示例：执行查询
curl -X POST \
  -H "X-API-Key: your-secure-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT * FROM table"}' \
  http://localhost:9080/api/spanner/my-project/query
```

#### 受保护与公开端点

`src/kineviz_database_proxy/api/auth.py` 里挂了两套独立的认证：

- **管理员会话** —— `X-Admin-Token` 请求头，由 `POST /api/admin/login` 颁发，触发条件是
  `ADMIN_PASSWORD` 已设置。`/api/admin`、`/api/project`、`/api/embedded`、
  `/api/relational`、`/api/settings` 和 `/api/google` 下的所有路由都要求它。
- **API 密钥** —— `X-API-Key` 请求头，来源是 `API_KEY` 环境变量或 settings 文件
  （`src/kineviz_database_proxy/api/auth.py:192`）。通用数据路由
  `/api/{db_type}/{project}/*` 需要它；那条路由也接受管理员 token
  （`verify_api_key_or_admin`，见 `src/kineviz_database_proxy/api/auth.py:249`）。

`ADMIN_PASSWORD` / `API_KEY` 没设时，两层都关掉，路由不要凭据就答。下表反映了
`verify_admin_token` 和 `verify_api_key_or_admin` 实际强制的内容。

| 端点 | 是否受保护 | 说明 |
|----------|-----------|-------------|
| `/api/project/*` | 是（管理员） | 项目管理 |
| `/api/admin/*` | 是（管理员，登录除外） | 管理员认证（`/login` 公开） |
| `/api/embedded/*` | 是（管理员） | 嵌入式库管理 |
| `/api/relational/*` | 是（管理员） | 关系型 mapping 管理（见 [RELATIONAL_API.md](RELATIONAL_API.md)） |
| `/api/settings/*` | 是（管理员） | 服务器设置 |
| `/api/{db_type}/{project}/*` | 是（API 密钥或管理员） | 数据库操作 |
| `/api/google/*` | 是（管理员） | Google Cloud 操作 |
| `/google/spanner/login` | 否 | OAuth 登录流 |
| `/google/spanner/callback` | 否 | OAuth 回调 |
| `/health` | 否 | 健康检查 |
| `/docs`、`/redoc` | 否 | API 文档 |

#### 关闭认证

要把 API 密钥认证关掉，只要把 `API_KEY` 环境变量留空或 unset。所有端点就都能免认证访问。

### 使用 Google OAuth 凭据文件（推荐）

1. **在 Google Cloud Console 创建 OAuth2 客户端**：
   - 访问 [Google Cloud Console](https://console.cloud.google.com)
   - 进入 "APIs & Services" > "Credentials"
   - 点击 "Create Credentials" > "OAuth 2.0 Client IDs"
   - 选择 "Desktop application"
   - 下载凭据文件

2. **使用凭据文件创建项目**：
   - 在 Web 界面点击 "From Google Credentials" 按钮
   - 上传下载的 `credentials.json` 文件
   - 系统会自动填充 `client_id`, `client_secret`, `project_id` 等信息
   - 填写 Spanner 实例和数据库信息

3. **示例凭据文件格式**：
   ```json
   {
     "installed": {
       "client_id": "your-client-id.apps.googleusercontent.com",
       "project_id": "your-gcp-project",
       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
       "token_uri": "https://oauth2.googleapis.com/token",
       "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
       "client_secret": "your-client-secret",
       "redirect_uris": ["http://localhost"]
     }
   }
   ```

### OAuth2 手动设置
1. 在 Google Cloud Console 创建 OAuth2 客户端
2. 添加重定向 URI: `http://localhost:9080/google/spanner/callback`
3. 配置作用域: `https://www.googleapis.com/auth/spanner.data`

### Service Account 设置
1. 创建服务账号
2. 授予 Spanner 访问权限
3. 下载 JSON 密钥文件
4. 在项目配置中指定文件路径

## 🛠️ 开发说明

### 项目结构
```
kineviz-database-proxy/
├── src/
│   └── kineviz_database_proxy/
│       ├── main.py          # FastAPI 应用
│       ├── api/             # API 路由
│       ├── drivers/         # 数据库驱动
│       └── models/          # 数据模型
├── frontend/
│   ├── src/
│   │   ├── components/      # React 组件
│   │   ├── services/        # API 服务
│   │   └── types/           # TypeScript 类型
│   ├── webpack.config.js    # Webpack 配置
│   └── package.json         # 前端依赖
└── config
   └── projects.json         #  项目配置存储
```

### 添加新数据库类型
1. 在 `src/drivers/` 创建新驱动
2. 继承 `BaseDatabaseDriver` 类
3. 实现必要的方法
4. 在 `api/database.py` 注册新驱动

### 前端开发
- 使用 TypeScript 进行类型安全开发
- Ant Design 提供一致的 UI 体验
- Webpack 热重载加速开发过程
- API 服务层统一管理后端交互

## 📊 监控和日志

### 健康检查
- `GET /health` - 服务器健康状态

### API 文档
- `GET /docs` - Swagger UI 文档
- `GET /redoc` - ReDoc 文档

## 🔄 部署说明

### 生产部署
1. 构建前端: `cd frontend && npm run build`
2. 启动服务器: `python -m uvicorn src.kineviz_database_proxy.main:app --host 0.0.0.0 --port 9080`
3. 配置反向代理（如 Nginx）

### Docker 部署
```bash
# 构建镜像
docker build -t kineviz/kineviz-database-proxy .

# 运行容器
docker run -p 9080:9080 \
  -v $(pwd)/config:/app/config \
  kineviz/kineviz-database-proxy:latest
```

或者脚本方式:
```bash
./docker/publish.sh release
```

## 🆘 故障排除

### 常见问题
1. **连接失败**: 检查数据库配置和网络连接
2. **认证错误**: 验证 OAuth2 或服务账号配置
3. **前端访问问题**: 确认后端服务器正在运行

### 日志查看
```bash
# 查看服务器日志
python -m uvicorn src.kineviz_database_proxy.main:app --log-level debug
```