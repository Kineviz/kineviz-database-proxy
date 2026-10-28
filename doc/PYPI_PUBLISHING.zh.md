# PyPI 发布指南

Kineviz Database Proxy 发布到 PyPI (pip) 的完整步骤。

> **语言**: [English](PYPI_PUBLISHING.md) | [中文](PYPI_PUBLISHING.zh.md)

[返回英文版开发指南](DEV_GUIDE.md)


## 📋 发布前准备

### 1. 环境准备

```bash
# 安装发布工具
pip install --upgrade pip
pip install --upgrade build twine

# 或者使用 pipx (推荐)
pipx install build
pipx install twine
```

### 2. 检查项目配置

确保 `pyproject.toml` 配置正确：

- ✅ 版本号已更新
- ✅ 描述和元数据完整
- ✅ 依赖项列表正确
- ✅ 分类器 (classifiers) 准确

### 3. 准备发布文件

```bash
# 确保这些文件存在且内容完整
README.md        # 项目说明
LICENSE          # 许可证文件
pyproject.toml   # 项目配置
```

## 🔧 发布步骤

### 🚀 快速发布 (推荐)


> 创建新版本，使用以下命令：
> ***python scripts/update_version.py <new_version>***


使用我们的自动化脚本，一键完成构建和发布：

```bash
# 测试发布 (包含前端构建)
python scripts/publish.py test

# 正式发布 (包含前端构建)  
python scripts/publish.py prod

# 仅构建验证 (不发布)
python scripts/publish.py build
```

自动化脚本会处理：
- ✅ 前端构建和打包
- ✅ Python 包构建
- ✅ 包验证和检查
- ✅ 上传到 PyPI

### 🏗️ 前端集成

包会自动包含 Web UI 前端文件：

```bash
# 单独构建前端
python scripts/build_frontend.py

# 验证静态文件
python scripts/test_package.py
```

### 步骤 1: 清理构建文件

```bash
# 删除旧的构建文件
rm -rf dist/
rm -rf build/
rm -rf *.egg-info/

# Windows PowerShell
Remove-Item -Recurse -Force dist, build, *.egg-info -ErrorAction SilentlyContinue
```

### 步骤 2: 构建发布包

```bash
# 构建源代码包和 wheel 包
python -m build

# 或者分别构建
python -m build --sdist    # 源码包
python -m build --wheel    # wheel 包
```

构建成功后，`dist/` 目录将包含：
- `kineviz-database-proxy-1.0.1.tar.gz` (源码包)
- `kineviz_database_proxy-1.0.1-py3-none-any.whl` (wheel 包)

### 步骤 3: 验证包内容

```bash
# 检查包内容
twine check dist/*

# 查看包文件列表
tar -tzf dist/kineviz-database-proxy-1.0.1.tar.gz
```

### 步骤 4: 测试发布 (TestPyPI)

```bash
# 上传到 TestPyPI 进行测试
twine upload --repository testpypi dist/*

# 需要输入 TestPyPI 的用户名和密码
# 或者使用 API token (推荐)
```

### 步骤 5: 测试安装

```bash
# 从 TestPyPI 安装测试
pip install --index-url https://test.pypi.org/simple/ kineviz-database-proxy

# 测试基本功能
python -c "from kineviz_database_proxy import DatabaseProxy; print('✅ 导入成功')"
```

### 步骤 6: 正式发布到 PyPI

```bash
# 上传到正式 PyPI
twine upload dist/*

# 需要输入 PyPI 的用户名和密码
# 或者使用 API token (推荐)
```

## 🔐 认证配置

### 方法 1: 使用 API Token (推荐)

1. 访问 [PyPI Account Settings](https://pypi.org/manage/account/)
2. 创建 API Token
3. 配置认证：

```bash
# 创建 .pypirc 文件
cat > ~/.pypirc << EOF
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
repository = https://upload.pypi.org/legacy/
username = __token__
password = pypi-your-api-token-here

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-your-test-api-token-here
EOF
```

### 方法 2: 环境变量

```bash
# 设置环境变量
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-your-api-token-here

# Windows
set TWINE_USERNAME=__token__
set TWINE_PASSWORD=pypi-your-api-token-here
```


## 📝 版本管理

### 版本号规范

> You should modify the version in package.json , pyproject.toml , src/kineviz_database_proxy/__init__.py
> Use **python scripts/update_version.py <new_version>** to update all files at once.

遵循 [语义化版本](https://semver.org/lang/zh-CN/):

- `MAJOR.MINOR.PATCH` (例如: 1.0.0)
- `MAJOR`: 不兼容的 API 变更
- `MINOR`: 向后兼容的功能增加
- `PATCH`: 向后兼容的问题修复

### 更新版本号

```bash
# 在 pyproject.toml 中更新版本号
version = "1.0.1"  # 修复版本
version = "1.1.0"  # 新功能版本
version = "2.0.0"  # 重大变更版本
```

## 🔍 发布检查清单

发布前确认：

- [ ] ✅ 版本号已更新
- [ ] ✅ CHANGELOG 已更新
- [ ] ✅ 所有测试通过
- [ ] ✅ 文档已更新
- [ ] ✅ 依赖项版本正确
- [ ] ✅ README.md 内容准确
- [ ] ✅ 许可证文件存在
- [ ] ✅ 在 TestPyPI 测试成功

## 🚨 常见问题

### 1. 版本冲突
```
ERROR: Version 1.0.0 already exists
```
**解决**: 更新 `pyproject.toml` 中的版本号

### 2. 认证失败
```
ERROR: Invalid credentials
```
**解决**: 检查 API token 或用户名密码

### 3. 包验证失败
```
ERROR: Check failed
```
**解决**: 运行 `twine check dist/*` 查看详细错误

### 4. 缺少必要文件
```
ERROR: Missing README.md
```
**解决**: 确保所有必要文件存在且路径正确

## 📚 有用的链接

- [PyPI 官网](https://pypi.org/)
- [TestPyPI](https://test.pypi.org/)
- [Python 打包用户指南](https://packaging.python.org/)
- [Twine 文档](https://twine.readthedocs.io/)
- [语义化版本](https://semver.org/lang/zh-CN/)

---

准备好发布了吗？运行 `python scripts/publish.py` 开始发布流程！ 🚀