# PyPI Publishing Guide

> Notice: When push a new version tag (e.g., v1.0.0) to the repository, GitHub Actions will automatically trigger the publishing workflow to build and publish the package to PyPI.

Complete steps for publishing Kineviz Database Proxy to PyPI (pip).

> **Language**: [English](PYPI_PUBLISHING.md) | [中文](PYPI_PUBLISHING.zh.md)

[Back to Development Guide](DEV_GUIDE.md)


## 📋 Pre-release Preparation

### 1. Environment Setup

```bash
# Install publishing tools
pip install --upgrade pip
pip install --upgrade build twine

# Or use pipx (recommended)
pipx install build
pipx install twine
```

### 2. Check Project Configuration

Ensure `pyproject.toml` is configured correctly:

- ✅ Version number updated
- ✅ Description and metadata complete
- ✅ Dependency list correct
- ✅ Classifiers accurate

### 3. Prepare Release Files

```bash
# Ensure these files exist and are complete
README.md        # Project description
LICENSE          # License file
pyproject.toml   # Project configuration
```

## 🔧 Publishing Steps

### 🚀 Quick Publish (Recommended)

> create new version with the following command:
> ***python scripts/update_version.py <new_version>***

> Notice: When push a new version tag (e.g., v1.0.0) to the repository, GitHub Actions will automatically trigger the publishing workflow to build and publish the package to PyPI.

Use our automation script to build and publish with one command:

```bash
# Test publish (includes frontend build)
python scripts/publish.py test

# Production publish (includes frontend build)
python scripts/publish.py prod

# Build only (no publish)
python scripts/publish.py build
```

The automation script handles:
- ✅ Frontend build and packaging
- ✅ Python package build
- ✅ Package validation and checks
- ✅ Upload to PyPI

### 🏗️ Frontend Integration

The package automatically includes Web UI frontend files:

```bash
# Build frontend separately
python scripts/build_frontend.py

# Verify static files
python scripts/test_package.py
```

### Step 1: Clean Build Files

```bash
# Remove old build files
rm -rf dist/
rm -rf build/
rm -rf *.egg-info/

# Windows PowerShell
Remove-Item -Recurse -Force dist, build, *.egg-info -ErrorAction SilentlyContinue
```

### Step 2: Build Distribution Package

```bash
# Build source and wheel packages
python -m build

# Or build separately
python -m build --sdist    # Source package
python -m build --wheel    # Wheel package
```

After successful build, the `dist/` directory will contain:
- `kineviz-database-proxy-1.0.1.tar.gz` (source package)
- `kineviz_database_proxy-1.0.1-py3-none-any.whl` (wheel package)

### Step 3: Validate Package Contents

```bash
# Check package contents
twine check dist/*

# View package file list
tar -tzf dist/kineviz-database-proxy-1.0.1.tar.gz
```

### Step 4: Test Publish (TestPyPI)

```bash
# Upload to TestPyPI for testing
twine upload --repository testpypi dist/*

# Requires TestPyPI username and password
# Or use API token (recommended)
```

### Step 5: Test Installation

```bash
# Install from TestPyPI for testing
pip install --index-url https://test.pypi.org/simple/ kineviz-database-proxy

# Test basic functionality
python -c "from graphxr_database_proxy import DatabaseProxy; print('✅ Import successful')"
```

### Step 6: Publish to PyPI

```bash
# Upload to production PyPI
twine upload dist/*

# Requires PyPI username and password
# Or use API token (recommended)
```

## 🔐 Authentication Configuration

### Method 1: Using API Token (Recommended)

1. Visit [PyPI Account Settings](https://pypi.org/manage/account/)
2. Create an API Token
3. Configure authentication:

```bash
# Create .pypirc file
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

### Method 2: Environment Variables

```bash
# Set environment variables
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-your-api-token-here

# Windows
set TWINE_USERNAME=__token__
set TWINE_PASSWORD=pypi-your-api-token-here
```

Create `.github/workflows/publish.yml`:

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - 'v*'  # Trigger on version tag push

jobs:
  publish:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install build twine
    
    - name: Build package
      run: python -m build
    
    - name: Check package
      run: twine check dist/*
    
    - name: Publish to PyPI
      env:
        TWINE_USERNAME: __token__
        TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
      run: twine upload dist/*
```

## 📝 Version Management

### Version Number Convention

Follow [Semantic Versioning](https://semver.org/):

- `MAJOR.MINOR.PATCH` (e.g., 1.0.0)
- `MAJOR`: Incompatible API changes
- `MINOR`: Backwards-compatible functionality additions
- `PATCH`: Backwards-compatible bug fixes

### Update Version Number

> You should modify the version in package.json , pyproject.toml , src/graphxr_database_proxy/__init__.py
> Use **python scripts/update_version.py <new_version>** to update all files at once.
```bash
# Update version number in pyproject.toml
version = "1.0.1"  # Patch version
version = "1.1.0"  # Minor version
version = "2.0.0"  # Major version
```

## 🔍 Publishing Checklist

Confirm before publishing:

- [ ] ✅ Version number updated
- [ ] ✅ CHANGELOG updated
- [ ] ✅ All tests passing
- [ ] ✅ Documentation updated
- [ ] ✅ Dependency versions correct
- [ ] ✅ README.md content accurate
- [ ] ✅ License file exists
- [ ] ✅ Tested successfully on TestPyPI

## 🚨 Common Issues

### 1. Version Conflict
```
ERROR: Version 1.0.0 already exists
```
**Solution**: Update version number in `pyproject.toml`

### 2. Authentication Failure
```
ERROR: Invalid credentials
```
**Solution**: Check API token or username/password

### 3. Package Validation Failed
```
ERROR: Check failed
```
**Solution**: Run `twine check dist/*` to see detailed errors

### 4. Missing Required Files
```
ERROR: Missing README.md
```
**Solution**: Ensure all required files exist and paths are correct

## 📚 Useful Links

- [PyPI Official Site](https://pypi.org/)
- [TestPyPI](https://test.pypi.org/)
- [Python Packaging User Guide](https://packaging.python.org/)
- [Twine Documentation](https://twine.readthedocs.io/)
- [Semantic Versioning](https://semver.org/)

---

Ready to publish? Run `python scripts/publish.py` to start the publishing process! 🚀