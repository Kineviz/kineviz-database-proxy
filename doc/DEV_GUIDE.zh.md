# 开发指南

[English](DEV_GUIDE.md) | [中文](DEV_GUIDE.zh.md)

## 安装与运行

需要 Python >= 3.9、Node.js >= 22 和 [uv](https://docs.astral.sh/uv/)。
在项目根目录执行：

```bash
uv sync --extra dev
npm install
npm run install:frontend
npm run dev
```

依赖文件变更后，重新安装依赖。连接关系型数据库还需对应扩展，例如
`uv sync --extra dev --extra postgresql`；使用 `--extra relational` 安装全部四个 SQL 驱动。
单独安装 Snowflake 使用 `--extra snowflake`，见 [Snowflake 配置](SNOWFLAKE.zh.md)。

后端地址为 http://localhost:9080（API 文档位于 `/docs`）。
前端使用 `PORT` 指定的端口，未设置时由 webpack 自动选择，请以终端输出地址为准。
前端 API 请求会代理到 9080 端口。

`npm run dev:backend` 和 `npm run dev:frontend` 可分别启动后端和前端。
两者都支持热重载；在 nodemon 控制台输入 `rs` 可重启后端。
本地验证生产运行方式时，先执行 `npm run build`，再执行 `npm start`。

如果 Windows PowerShell 禁止运行 `npm.ps1`，将命令中的 `npm` 换成 `npm.cmd`，
无需修改系统执行策略。

## 使用 AI 开发工具

[AGENTS.md](../AGENTS.md) 是项目开发约定的统一来源。
工具专用文件只负责导入或指向它：

| 工具 | 项目入口 |
| --- | --- |
| Codex | 自动发现 `AGENTS.md` |
| Claude Code | `CLAUDE.md` 通过 `@AGENTS.md` 导入 |
| Cursor | 自动发现 `AGENTS.md` |
| GitHub Copilot | `.github/copilot-instructions.md` 指示读取 `AGENTS.md` |
| Gemini CLI | `GEMINI.md` 通过 `@./AGENTS.md` 导入 |
| 其他助手 | 在开始修改前，要求工具读取 `AGENTS.md` |

从项目根目录启动 Codex 或 Claude Code。个人凭据、模型选择和权限覆盖保留在
工具自身的本地配置中。Claude 的共享权限与提交信息检查钩子保留在
`.claude/settings.json`，仅对 Claude 生效。指令文件用于指导行为，不是安全边界。

官方加载说明：[Codex](https://developers.openai.com/codex/guides/agents-md/)、
[Claude Code](https://code.claude.com/docs/en/memory)、
[Cursor](https://cursor.com/docs/rules)、
[Copilot](https://docs.github.com/en/copilot/reference/custom-instructions-support)、
[Gemini CLI](https://geminicli.com/docs/cli/gemini-md/)。

## 验证修改

| 范围 | 命令 |
| --- | --- |
| 后端 | `npm test -- -q` |
| 指定后端测试 | `npm test -- tests/test_dialect.py -k expand -q` |
| 前端类型 | `npm run typecheck` |
| 前端构建 | `npm run build` |
| 全部本地检查 | `npm run verify` |

`npm test` 在已安装依赖的 `.venv` 中运行 pytest，不同步依赖。
默认测试离线运行。引擎集成测试需显式设置 `GRAPHXR_PROXY_ENGINE_TESTS=1`，
且可能下载运行时。webpack 不检查 TypeScript 类型，前端变更需同时执行类型检查
和构建。目前未安装前端单元测试运行器。

Claude 的 `/verify` 会根据变更文件选择检查并报告结果。
文档变更应检查本地链接，并同步已有的中英文版本。

### GitHub Actions

[CI](../.github/workflows/ci.yml) 检查工作流语法和 Bash 脚本，在 Python 3.11
和 3.14 上运行离线后端测试，检查前端类型，构建新旧 Python 包并校验元数据和
运行所需资源，同时检查 Dockerfile。分支推送和拉取请求都会触发 CI；三个发布
工作流也必须先通过 CI，才能执行发布。

构建前端后，可在本地复现打包检查：

```bash
uv run --with build --with twine python -m build --outdir dist/replacement
uv run --with build python -m build legacy/graphxr-database-proxy --outdir dist/legacy
uv run --with twine python -m twine check --strict dist/replacement/* dist/legacy/*
uv run --no-sync python scripts/check_distributions.py dist/replacement
docker build --check -f docker/Dockerfile .
```

安装 ShellCheck 后运行 [actionlint](https://github.com/rhysd/actionlint)，可在本地
检查工作流。发布条件测试需要 Bash；Windows 下运行 pytest 前，将
`BASH_EXECUTABLE` 设置为已安装的 Bash 可执行文件路径。这些检查不会上传包或镜像。

### PostgreSQL 18 示例集成测试

启动代理和 GraphXRSupportDB 的 pg18 示例后，在仓库根目录运行：

```sh
uv sync --extra dev --extra postgresql --inexact
uv run --no-sync python scripts/check_pg18_proxy.py --connection-file ../GraphXRSupportDB/connections/pg18.local.json
```

脚本通过管理 API 创建尚不存在的 `pg18-supportdb` 项目，已有项目则直接复用，
不覆盖配置。测试只读取示例数据库，不重置或修改数据。15 组检查将 HTTP 结果与
直接 SQL 查询对照，覆盖结构发现、计数、两跳查询、Expand、节点与关系拉取、
特殊字符以及写入拒绝。输出不包含凭据。可通过 `--proxy-url` 或 `--project`
指定其他代理地址或项目，通过 `--mapping-out` 导出不含凭据的映射，参见
[pg18 示例映射](../examples/pg18.graph-mapping.json)。

需要导入并验证指定 JSON/YAML 文件时，可增加 `--mapping-file` 参数并提供文件路径。
此选项会通过管理 API 明确替换项目映射，并检查 JSON/YAML 下载和重新导入。
原始格式示例参见 [pg18.graphxr-mapping.json](../examples/pg18.graphxr-mapping.json)。

## 延伸阅读

- [架构与驱动扩展](ARCHITECTURE.zh.md)
- [使用指南](USAGE.zh.md) · [API 参考](API_Reference.md)
- [关系型映射](RELATIONAL_MAPPING.zh.md) · [嵌入式数据库](EMBEDDED_STORES.zh.md)
- [Docker 发布](DOCKER_PUBLISHING.zh.md) · [PyPI 发布](PYPI_PUBLISHING.zh.md)

`docs/superpowers/` 保存历史设计与会话记录，按需查阅；
其中的任务、代理数量和流程不作为当前开发要求。
