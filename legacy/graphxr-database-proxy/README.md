# Deprecated — use kineviz-database-proxy

**`graphxr-database-proxy` is deprecated and no longer maintained under this name.**
It has been replaced by **[kineviz-database-proxy](https://pypi.org/project/kineviz-database-proxy/)**.

The source code, documentation and issue tracker have moved to
**[Kineviz/kineviz-database-proxy](https://github.com/Kineviz/kineviz-database-proxy)**.

## Migrate

```bash
pip uninstall graphxr-database-proxy
pip install --upgrade "kineviz-database-proxy[ui]"
kineviz-proxy --ui
```

Update Python imports:

```python
from kineviz_database_proxy import DatabaseProxy
```

Replace the old source checkout with the new repository:

```bash
git clone https://github.com/Kineviz/kineviz-database-proxy.git
cd kineviz-database-proxy
```

## About this final release

Version 1.1.4 is a lightweight migration package. Installing or upgrading it
installs `kineviz-database-proxy>=1.1.4`. Existing optional extras are forwarded
to the replacement package. The `graphxr-proxy` command remains available through
the replacement package; `kineviz-proxy` is the recommended command.

The top-level `from graphxr_database_proxy import DatabaseProxy` import remains
available as a deprecated compatibility alias. Update imports of submodules to
use `kineviz_database_proxy`; the old submodule paths are not included.

Existing configuration files, storage paths and `GRAPHXR_PROXY_*` environment
variables remain supported by the replacement package.

All future development and releases will use the new package and repository.
