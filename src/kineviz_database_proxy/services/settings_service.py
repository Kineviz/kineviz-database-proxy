"""
Settings service for managing application settings
"""

import json
import os
import secrets
from pathlib import Path
from typing import Dict, Optional


class SettingsService:
    """Service for managing application settings"""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.settings_file = self.config_dir / "settings.json"
        self._ensure_config_dir()
    
    def _ensure_config_dir(self) -> None:
        """Ensure config directory exists"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if not self.settings_file.exists():
            self._save_settings(self._get_default_settings())
    
    def _get_default_settings(self) -> Dict:
        """Get default settings"""
        return {
            "api_key": None,
            "api_key_enabled": False
        }
    
    def _load_settings(self) -> Dict:
        """Load settings from config file"""
        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return self._get_default_settings()
    
    def _save_settings(self, settings: Dict) -> None:
        """Save settings to config file"""
        with open(self.settings_file, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    
    def get_settings(self) -> Dict:
        """Get all settings with additional metadata"""
        settings = self._load_settings()
        # Add flag indicating if API_KEY env var is set
        settings["api_key_env_configured"] = bool(os.environ.get("API_KEY"))
        return settings
    
    def get_api_key(self) -> Optional[str]:
        """Get configured API key (if enabled)"""
        settings = self._load_settings()
        if settings.get("api_key_enabled", False):
            return settings.get("api_key")
        return None
    
    def is_api_key_env_configured(self) -> bool:
        """Check if API_KEY environment variable is set"""
        return bool(os.environ.get("API_KEY"))
    
    def update_enabled(self, api_key_enabled: bool) -> Dict:
        """Update only the enabled state (preserves existing API key)"""
        settings = self._load_settings()
        settings["api_key_enabled"] = api_key_enabled
        self._save_settings(settings)
        # Return with metadata
        settings["api_key_env_configured"] = self.is_api_key_env_configured()
        return settings
    
    def generate_and_save_api_key(self) -> Dict:
        """Generate a new API key and save it immediately"""
        settings = self._load_settings()
        settings["api_key"] = self.generate_api_key()
        self._save_settings(settings)
        # Return with metadata
        settings["api_key_env_configured"] = self.is_api_key_env_configured()
        return settings
    
    @staticmethod
    def generate_api_key() -> str:
        """Generate a secure random API key with gxr_ prefix"""
        return f"gxr_{secrets.token_urlsafe(32)}"
