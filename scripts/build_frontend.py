#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build frontend and copy to Python package static files directory
"""

import shutil
import subprocess
import sys
from pathlib import Path

# Set console encoding
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

def run_command(command, description, cwd=None):
    """Run command and check result"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            cwd=cwd, 
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
    return True

def build_frontend():
    """Build frontend project"""
    frontend_dir = Path("frontend")
    
    if not frontend_dir.exists():
        print("❌ frontend directory does not exist")
        return False
    
    # Check if node_modules exists
    if not (frontend_dir / "node_modules").exists():
        print("📦 Installing frontend dependencies...")
        if not run_command("npm install", "Install frontend dependencies", cwd=frontend_dir):
            return False
    
    # Build frontend
    if not run_command("npm run build", "Build frontend", cwd=frontend_dir):
        return False
    
    return True

def copy_frontend_dist():
    """Copy frontend build files to Python package directory"""
    frontend_dist = Path("frontend/dist")
    static_dir = Path("src/kineviz_database_proxy/static")
    
    if not frontend_dist.exists():
        print("❌ frontend/dist directory does not exist, please build frontend first")
        return False
    
    # Clean and create static files directory
    if static_dir.exists():
        shutil.rmtree(static_dir)
        print("🗑️  Cleaned old static files")
    
    static_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy all frontend files
    try:
        for item in frontend_dist.iterdir():
            if item.is_file():
                shutil.copy2(item, static_dir)
                print(f"📄 Copied file: {item.name}")
            elif item.is_dir():
                shutil.copytree(item, static_dir / item.name)
                print(f"📁 Copied directory: {item.name}")
        
        print(f"✅ Frontend files copied to {static_dir}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to copy frontend files: {e}")
        return False

def list_static_files():
    """List static files"""
    static_dir = Path("src/kineviz_database_proxy/static")
    
    if not static_dir.exists():
        print("❌ Static files directory does not exist")
        return
    
    print("\n📁 Static files list:")
    for item in static_dir.rglob("*"):
        if item.is_file():
            size = item.stat().st_size / 1024  # KB
            relative_path = item.relative_to(static_dir)
            print(f"   📄 {relative_path} ({size:.1f} KB)")

def main():
    print("🏗️ Kineviz Database Proxy Frontend Build Tool")
    print("=" * 50)
    
    # Check if in project root directory
    if not Path("pyproject.toml").exists():
        print("❌ Please run this script from the project root directory")
        sys.exit(1)
    
    # Build frontend
    if not build_frontend():
        print("❌ Frontend build failed")
        sys.exit(1)
    
    # Copy frontend files
    if not copy_frontend_dist():
        print("❌ Failed to copy frontend files")
        sys.exit(1)
    
    # List static files
    list_static_files()
    
    print("\n✨ Frontend build and copy completed!")
    print("💡 Now you can run 'python scripts/publish.py build' to build the package")

if __name__ == "__main__":
    main()