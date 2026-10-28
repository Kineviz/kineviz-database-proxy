# -*- coding: utf-8 -*-
"""
MySQL driver — a MySQL 8 database served as a graph.

Thin on purpose, per ``CLAUDE.md``: one engine spelling, one capability record, one
dialect, and all of the traversal and SQL in ``drivers/relational``.

There is no schema layer here: a MySQL *database* is the namespace, so the configured
database is what ``configured_schema`` falls back to and an empty engine default is the
honest answer. The rest of the engine's own — ``REGEXP``, ``JSON_TABLE`` from 8.0.4 with
placeholders below it, a ``MAX_EXECUTION_TIME`` hint instead of a session setting, a
``READ ONLY`` transaction — is ``engines/mysql.py``'s, reached through that spelling.
"""

from __future__ import annotations

from .graph_support import MysqlGraphIntents
from .relational.driver import RelationalDriver


class MysqlDriver(MysqlGraphIntents, RelationalDriver):
    """MySQL, over the relational pipeline."""

    #: The URL segment, the engine registry key and the dialect name: one word.
    database_type = "mysql"
    engine_name = "mysql"
