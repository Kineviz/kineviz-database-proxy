"""Deprecated top-level imports for the renamed Kineviz Database Proxy."""

import warnings

warnings.warn(
    "graphxr-database-proxy is deprecated; install kineviz-database-proxy and "
    "import kineviz_database_proxy instead. The repository is now "
    "https://github.com/Kineviz/kineviz-database-proxy.",
    DeprecationWarning,
    stacklevel=2,
)

from kineviz_database_proxy import (
    DatabaseConfig,
    DatabaseProxy,
    Project,
    ProjectService,
    __version__,
    app,
)

__all__ = ["app", "DatabaseProxy", "Project", "DatabaseConfig", "ProjectService"]
