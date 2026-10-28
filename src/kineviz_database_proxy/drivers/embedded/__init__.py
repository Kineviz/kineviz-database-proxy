# -*- coding: utf-8 -*-
"""
Machinery shared by the embedded stores (Kuzu and Ladybug).

An embedded store is a file rather than a server, so nothing here looks like the
other drivers' connection handling. What replaces it:

  - ``store_probe`` reads the file's own header to find out which engine family
    wrote it and at which storage version;
  - ``version_map`` turns that storage version into the engine release line that
    can open it, learning as it goes rather than trusting a frozen table;
  - ``runtime`` puts that release on disk, downloading it when it is missing;
  - ``worker`` / ``pool`` run it in its own process, because two engine builds
    cannot be imported into one.
"""
