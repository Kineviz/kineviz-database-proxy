#!/usr/bin/env python3
"""
Version Update Script

Updates version numbers across all project files:
- package.json
- pyproject.toml
- src/kineviz_database_proxy/__init__.py

Usage:
    python scripts/update_version.py <new_version>
    
Example:
    python scripts/update_version.py 1.0.4
"""

import sys
import re
import json
from pathlib import Path


def update_package_json(version: str) -> bool:
    """Update version in package.json"""
    file_path = Path("package.json")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        old_version = data.get('version', 'unknown')
        data['version'] = version
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')  # Add newline at end of file
        
        print(f"✅ Updated package.json: {old_version} → {version}")
        return True
    except Exception as e:
        print(f"❌ Failed to update package.json: {e}")
        return False


def update_pyproject_toml(version: str) -> bool:
    """Update version in pyproject.toml"""
    file_path = Path("pyproject.toml")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find current version
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        old_version = match.group(1) if match else 'unknown'
        
        # Replace version
        new_content = re.sub(
            r'^version\s*=\s*"[^"]+"',
            f'version = "{version}"',
            content,
            count=1,
            flags=re.MULTILINE
        )
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"✅ Updated pyproject.toml: {old_version} → {version}")
        return True
    except Exception as e:
        print(f"❌ Failed to update pyproject.toml: {e}")
        return False


def update_init_py(version: str) -> bool:
    """Update version in src/kineviz_database_proxy/__init__.py"""
    file_path = Path("src/kineviz_database_proxy/__init__.py")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find current version
        match = re.search(r'^__version__\s*=\s*"([^"]+)"', content, re.MULTILINE)
        old_version = match.group(1) if match else 'unknown'
        
        # Replace version
        new_content = re.sub(
            r'^__version__\s*=\s*"[^"]+"',
            f'__version__ = "{version}"',
            content,
            count=1,
            flags=re.MULTILINE
        )
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"✅ Updated __init__.py: {old_version} → {version}")
        return True
    except Exception as e:
        print(f"❌ Failed to update __init__.py: {e}")
        return False


def get_current_version() -> str:
    """Get current version from package.json"""
    try:
        with open("package.json", 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('version', None)
    except Exception as e:
        print(f"❌ Failed to read current version: {e}")
        return None


def increment_version(version: str) -> str:
    """Increment the patch version (last number)"""
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$', version)
    if not match:
        return None
    
    major, minor, patch, prerelease = match.groups()
    new_patch = int(patch) + 1
    
    new_version = f"{major}.{minor}.{new_patch}"
    if prerelease:
        new_version += f"-{prerelease}"
    
    return new_version


def validate_version(version: str) -> bool:
    """Validate semantic version format"""
    pattern = r'^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$'
    return bool(re.match(pattern, version))


def main():
    """Main execution function"""
    # Check if we're in the project root
    if not Path("pyproject.toml").exists():
        print("❌ Error: Please run this script from the project root directory")
        sys.exit(1)
    
    # Determine new version
    if len(sys.argv) == 2:
        # Version provided as argument
        new_version = sys.argv[1]
    elif len(sys.argv) == 1:
        # No version provided, auto-increment
        current_version = get_current_version()
        if not current_version:
            print("❌ Error: Could not read current version from package.json")
            sys.exit(1)
        
        new_version = increment_version(current_version)
        if not new_version:
            print(f"❌ Error: Invalid version format in package.json: {current_version}")
            sys.exit(1)
        
        print(f"📦 Auto-incrementing version: {current_version} → {new_version}")
    else:
        print("❌ Error: Too many arguments")
        print(f"\nUsage: python {sys.argv[0]} [new_version]")
        print("\nExamples:")
        print("  python scripts/update_version.py        # Auto-increment patch version")
        print("  python scripts/update_version.py 1.0.4  # Set specific version")
        sys.exit(1)
    
    # Validate version format
    if not validate_version(new_version):
        print(f"❌ Error: Invalid version format: {new_version}")
        print("Version should follow semantic versioning (e.g., 1.0.4 or 1.0.4-beta)")
        sys.exit(1)
    
    print(f"\n🔄 Updating version to {new_version}...\n")
    
    # Update all files
    results = [
        update_package_json(new_version),
        update_pyproject_toml(new_version),
        update_init_py(new_version)
    ]
    
    # Check results
    if all(results):
        print(f"\n🎉 Successfully updated all files to version {new_version}!")
        print("\nNext steps:")
        print("  1. Review the changes: git diff")
        print("  2. Commit: git add . && git commit -m 'chore: bump version to {}'".format(new_version))
        print("  3. Tag: git tag v{}".format(new_version))
        print("  4. Push: git push && git push --tags")
    else:
        print("\n⚠️  Some files failed to update. Please check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
