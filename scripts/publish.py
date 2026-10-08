#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kineviz Database Proxy Automated Publishing Script

Usage:
   python scripts/publish.py test    # Publish to TestPyPI
   python scripts/publish.py prod    # Publish to PyPI
   python scripts/publish.py build   # Build and validate only
   python scripts/publish.py         # Interactive selection
"""

import subprocess
import sys
import os
import argparse
import shutil
from pathlib import Path

# Windows encoding fix
if sys.platform == "win32":
    import codecs
    import locale
    
    # Set environment variables to fix rich/twine encoding issues
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    
    # Fix console output encoding
    try:
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())
    except:
        pass

def get_python_executable():
    """Get the path of the current Python interpreter"""
    return sys.executable

def twine_command():
    """Build the twine invocation.

    twine requires a newer "packaging" than uv.lock pins for this project, and
    any "uv run" resyncs .venv back to the lock, so "python -m twine" inside
    .venv dies with ImportError: cannot import name 'errors' from 'packaging'.
    Run twine from its own isolated environment whenever uv is available.
    """
    if shutil.which("uv"):
        return "uvx --from twine twine"
    return f'"{get_python_executable()}" -m twine'

def run_command(command, description):
    """Run command and check result"""
    print(f"🔄 {description}...")
    
    # If command contains python, replace with current Python interpreter
    if command.startswith("python "):
        command = command.replace("python ", f"{get_python_executable()} ", 1)
    elif command == "python -m build":
        command = f"{get_python_executable()} -m build"
    
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            capture_output=True, 
            text=True, 
            encoding='utf-8',
            errors='ignore'
        )
    except Exception as e:
        print(f"❌ {description} failed: {e}")
        return False
    
    if result.returncode != 0:
        print(f"❌ {description} failed:")
        if result.stderr:
            print(result.stderr)
        return False
    
    print(f"✅ {description} succeeded")
    if result.stdout and result.stdout.strip():
        print(f"   Output: {result.stdout.strip()}")
    return True

def check_and_install_dependencies():
    """Check and install publishing dependencies"""
    print("🔍 Checking publishing dependencies...")
    
    # Check and install required Python packages. twine is not one of them
    # when uv is available: twine_command() runs it out of an isolated
    # environment instead of this one.
    required_packages = ["build"] if shutil.which("uv") else ["build", "twine"]
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"✅ {package} installed")
        except ImportError:
            missing_packages.append(package)
            print(f"❌ Missing package: {package}")
    
    if missing_packages:
        print(f"\n📦 Installing missing packages: {', '.join(missing_packages)}")
        # Install into the interpreter that is running this script. A bare
        # "pip" is often a different environment than "python" on Windows, and
        # then "python -m build" still fails right after a successful install.
        python = get_python_executable()
        if shutil.which("uv"):
            install_command = f'uv pip install --python "{python}" {" ".join(missing_packages)}'
        else:
            install_command = f'"{python}" -m pip install {" ".join(missing_packages)}'
        
        # Ask for automatic installation
        auto_install = input("🤔 Install missing packages automatically? (y/n): ").lower().strip()
        if auto_install in ['y', 'yes']:
            if not run_command(install_command, f"Install {', '.join(missing_packages)}"):
                print("❌ Dependency installation failed, please install manually:")
                print(f"   {install_command}")
                return False
        else:
            print("❌ Please install missing packages manually:")
            print(f"   {install_command}")
            return False
    
    print("✅ All dependency checks passed")
    return True

def check_requirements():
    """Check publishing requirements"""
    print("🔍 Checking publishing requirements...")
    
    # Check if in correct directory
    if not Path("pyproject.toml").exists():
        print("❌ Please run this script from the project root directory")
        return False
    
    # Check required files
    required_files = ["README.md", "LICENSE", "pyproject.toml"]
    for file in required_files:
        if not Path(file).exists():
            print(f"❌ Missing file: {file}")
            return False
    
    print("✅ All requirement checks passed")
    return True

def get_version():
    """Get version number from pyproject.toml"""
    try:
        with open("pyproject.toml", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("version ="):
                    version = line.split("=")[1].strip().strip('"')
                    return version
    except Exception as e:
        print(f"❌ Unable to read version number: {e}")
        return None

def clean_build():
    """Clean build files"""
    dirs_to_clean = ["dist", "build"]
    for dir_name in dirs_to_clean:
        if Path(dir_name).exists():
            shutil.rmtree(dir_name)
            print(f"🗑️  Removed directory: {dir_name}")
    
    # Clean egg-info directories
    for egg_info in Path(".").glob("*.egg-info"):
        shutil.rmtree(egg_info)
        print(f"🗑️  Removed directory: {egg_info}")

def build_frontend():
    """Build frontend and copy static files"""
    print("🏗️  Building frontend...")
    
    # Run frontend build script
    build_script = Path("scripts/build_frontend.py")
    if build_script.exists():
        return run_command(f"{get_python_executable()} scripts/build_frontend.py", "Build frontend")
    else:
        print("⚠️  Frontend build script not found, skipping frontend build")
        return True

def build_package():
    """Build distribution package"""
    return run_command("python -m build", "Build distribution package")

def check_package():
    """Check package contents"""
    return run_command(f"{twine_command()} check dist/*", "Validate package contents")

def list_dist_files():
    """List built files"""
    print("\n📦 Built files:")
    dist_path = Path("dist")
    if dist_path.exists():
        for file in dist_path.iterdir():
            size = file.stat().st_size / 1024  # KB
            print(f"   📄 {file.name} ({size:.1f} KB)")

def upload_to_testpypi():
    """Upload to TestPyPI"""
    print("\n🧪 Uploading to TestPyPI...")
    
    # Set environment variables to avoid encoding issues
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    
    try:
        result = subprocess.run(
            f"{twine_command()} upload --repository testpypi dist/*",
            shell=True,
            env=env,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return False

def upload_to_pypi():
    """Upload to PyPI"""
    print("\n🚀 Uploading to PyPI...")
    
    # Set environment variables to avoid encoding issues
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    
    try:
        result = subprocess.run(
            f"{twine_command()} upload dist/*",
            shell=True,
            env=env,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Publish Kineviz Database Proxy to PyPI")
    parser.add_argument("target", nargs="?", choices=["test", "prod", "build"], 
                       help="Publish target: test (TestPyPI), prod (PyPI), or build (build and validate only)")
    args = parser.parse_args()
    
    print("🚀 Kineviz Database Proxy Publishing Tool")
    print("=" * 50)
    
    # Check and install dependencies
    if not check_and_install_dependencies():
        sys.exit(1)
    
    # Check requirements
    if not check_requirements():
        sys.exit(1)
    
    # Display current version
    version = get_version()
    if version:
        print(f"📋 Current version: {version}")
    else:
        print("❌ Unable to get version number")
        sys.exit(1)
    
    # Clean build files
    print("\n🧹 Cleaning build files...")
    clean_build()
    
    # Build frontend
    if not build_frontend():
        sys.exit(1)
    
    # Build package
    if not build_package():
        sys.exit(1)
    
    # Check package
    if not check_package():
        sys.exit(1)
    
    # List built files
    list_dist_files()
    
    # Determine publish target
    target = args.target
    if not target:
        print("\n📋 Select operation:")
        print("   1. build - Build and validate package only")
        print("   2. test  - TestPyPI (testing)")
        print("   3. prod  - PyPI (production)")
        choice = input("Please select (1/2/3): ").strip()
        if choice == "1":
            target = "build"
        elif choice == "2":
            target = "test"
        elif choice == "3":
            target = "prod"
        else:
            target = None
    
    if target == "build":
        print(f"\n✅ Package build and validation complete!")
        print(f"📦 Build files are in the dist/ directory")
        print(f"🔍 You can check the following files:")
        for file in Path("dist").iterdir():
            print(f"   📄 {file.name}")
        print(f"\n💡 Next steps:")
        print(f"   - Run 'python scripts/publish.py test' to publish to TestPyPI")
        print(f"   - Run 'python scripts/publish.py prod' to publish to PyPI")
        
    elif target == "test":
        print(f"\n🧪 Preparing to publish to TestPyPI...")
        if upload_to_testpypi():
            print(f"\n🎉 Successfully published to TestPyPI!")
            print(f"📦 Test installation:")
            print(f"   pip install --index-url https://test.pypi.org/simple/ kineviz-database-proxy=={version}")
            print(f"🔗 View: https://test.pypi.org/project/kineviz-database-proxy/{version}/")
        else:
            sys.exit(1)
            
    elif target == "prod":
        print(f"\n⚠️  Preparing to publish to production PyPI (version {version})")
        print("   This will make the package available to all users!")
        confirm = input("   Confirm publish? (yes/no): ").lower()
        
        if confirm == "yes":
            if upload_to_pypi():
                print(f"\n🎉 Successfully published to PyPI!")
                print(f"📦 Installation:")
                print(f"   pip install kineviz-database-proxy=={version}")
                print(f"🔗 View: https://pypi.org/project/kineviz-database-proxy/{version}/")
            else:
                sys.exit(1)
        else:
            print("❌ Publish cancelled")
            
    else:
        print("❌ Invalid selection")
        sys.exit(1)
    
    print(f"\n✨ Operation completed!")

if __name__ == "__main__":
    main()