"""
Relational engines (MSSQL, PostgreSQL, MySQL) served to GraphXR as graph sources.

A table is a node label, a link table or a foreign key is an edge, and the whole
picture is a document a person can rename, flip, exclude and extend. See
``doc/RELATIONAL_MAPPING.md`` and the spec at
``docs/superpowers/specs/2026-10-05-relational-graph-drivers.md``.

This file imports nothing on purpose: every submodule here is optional at import
time (the DBAPIs are extras) and several lanes import different ones, so the
package itself stays empty and contention-free.
"""
