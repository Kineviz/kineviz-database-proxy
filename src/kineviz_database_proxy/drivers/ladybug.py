# -*- coding: utf-8 -*-
"""
Ladybug driver.

Ladybug is Kuzu's continuation after the fork -- same Cypher, same catalog
procedures, same single-file store -- so this is a true subclass rather than a
second implementation. Three things differ, and each one is data the shared code
already reads from the right place:

  - **The magic bytes.** ``LBUG`` rather than ``KUZU``, so a store of one family is
    refused by the other's driver with a message naming the right project type.
  - **The storage-version series.** Ladybug continued Kuzu's counter at 40 instead
    of restarting it, which is why the format number alone is unambiguous across
    both families. It also carries ``canReadStorageVersion``, so a newer Ladybug
    build may open an older store -- something Kuzu never does.
  - **The key casing in results.** ``_ID`` / ``_LABEL`` / ``_SRC`` / ``_DST`` rather
    than the lowercase spellings. ``kuzu_mapping`` accepts both, so nothing here has
    to know about it.

Its own capability record and dialect exist so ``capabilities.type`` says
``ladybug``: the client is entitled to know which engine it actually reached.
"""

from __future__ import annotations

from .graph_support import LadybugGraphIntents
from .kuzu import KuzuDriver


class LadybugDriver(LadybugGraphIntents, KuzuDriver):
    """Ladybug driver -- Kuzu's, pointed at the other package and the other magic."""

    database_type = "ladybug"
    engine = "ladybug"
    expected_engine = "ladybug"
