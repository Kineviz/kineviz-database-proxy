"""Pytest configuration for kineviz-database-proxy tests."""
import sys
from pathlib import Path

# Add src to path so tests can import the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
