# -*- coding: utf-8 -*-
"""
PostgreSQL driver — a Postgres schema served as a graph.

Thin on purpose, per ``CLAUDE.md``: one engine spelling, one capability record, one
dialect, and all of the traversal and SQL in ``drivers/relational``.

What is Postgres-specific lives in ``engines/postgresql.py`` and reaches this driver
through that spelling only: unquoted names fold to lower case *and* a quoted name keeps
its case, so ``Order`` and ``order`` are two tables and every identifier emitted here is
quoted; ``avg`` is already ``numeric``, so no cast is added; ``READ UNCOMMITTED`` is a
synonym for read committed rather than a level, so it is refused by name instead of
silently honoured.
"""

from __future__ import annotations

from .graph_support import PostgresqlGraphIntents
from .relational.driver import RelationalDriver


class PostgresqlDriver(PostgresqlGraphIntents, RelationalDriver):
    """PostgreSQL, over the relational pipeline."""

    #: The URL segment, the engine registry key and the dialect name: one word.
    database_type = "postgresql"
    engine_name = "postgresql"
