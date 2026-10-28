# -*- coding: utf-8 -*-
"""
The engine's own process. Standard library only.

This file is executed by an interpreter that has exactly one engine build
installed, never imported into the proxy. That separation is forced rather than
chosen: importing ``kuzu`` into a process that has already imported ``ladybug``
raises ``ImportError: generic_type: type "Database" is already registered!``,
because both are pybind11 extensions claiming the same type names in one global
registry. Two versions of the *same* engine collide harder still. A proxy that
serves one project on Kuzu 0.11 and another on Ladybug 0.19 therefore cannot hold
either of them itself.

LatticeDB reaches the same conclusion by a different road. It is a ctypes binding
rather than a pybind11 extension, so it has no type registry to collide over --
but the shared library it loads is found *inside its own package*, so two releases
means two ``latticedb`` packages, which one interpreter cannot have. The
per-release install and the process around it are what every engine here needs,
for whichever reason applies to it.

Engines do not share a session class. The Kuzu family opens a ``Database`` and
runs statements through a ``Connection`` that yields rows positionally; LatticeDB
opens its ``Database`` explicitly and answers with a ``QueryResult`` whose rows are
dicts. Both are normalised to the same framed reply, so nothing above this file has
to know which one answered.

It reads newline-delimited JSON on stdin and writes **length-prefixed** frames on
stdout: a short header line naming the payload's size in bytes, then the payload,
then a newline::

    GXRPROXY 137
    {"ok": true, ...}

The prefix is not decoration. A reply is read on the other side with
``StreamReader.readline()``, which gives up at 64 KiB with "Separator is not found,
and chunk exceed the limit" -- and any real query result is larger than that. The
length line is always short, so it reads as a line; the payload is then read by
byte count, which has no such ceiling.

Nothing else may be written to stdout -- diagnostics go to stderr -- and the reader
skips anything that is neither a frame nor a JSON object, so an engine that prints
a banner cannot desync the protocol.

Runs on Python 3.8 and up, because the interpreter is chosen for the engine's
wheels rather than for this file.
"""

from __future__ import annotations

import base64
import json
import math
import os
import sys
import traceback

#: Rows one response may carry. The driver caps its statements as well; this is the
#: backstop for a statement that arrived uncapped.
DEFAULT_MAX_ROWS = 20000


def _prepare_native_library_path():
    """
    Make the engine's own shared libraries loadable before it is imported.

    On Windows the Ladybug wheel is packaged with a hole in it: ``_lbug.pyd``
    imports ``libssl-3-x64.dll`` and ``libcrypto-3-x64.dll``, and ``ladybug.libs``
    vendors only ``msvcp140``. The import then fails with a bare "DLL load failed
    while importing _lbug", naming nothing. CPython ships both OpenSSL libraries in
    ``<base_prefix>/DLLs`` for its own ``ssl`` module, so putting that directory on
    the DLL search path is enough to make the extension load.

    Harmless everywhere else, which is why it is not conditional on the engine.
    """
    if os.name != "nt":
        return
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return
    for candidate in (
        os.path.join(sys.base_prefix, "DLLs"),
        os.path.join(sys.prefix, "DLLs"),
    ):
        if os.path.isdir(candidate):
            try:
                add_dll_directory(candidate)
            except OSError:
                pass


def _to_json_value(value):
    """
    One cell, as something a JSON response can carry.

    ``bytes`` becomes base64 to match what the bolt drivers already send for a
    ``BYTEA``. Non-finite floats become null: ``json.dumps`` would happily emit
    ``NaN``, which is not JSON and which the client's parser rejects.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_json_value(item) for item in value]

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):  # date, time, datetime
        try:
            return isoformat()
        except Exception:
            pass

    # Decimal, UUID, timedelta, and anything else the engine invents. A JSON
    # response has no decimal type and the client holds properties as JS values,
    # so a numeric-looking value goes across as a number and the rest as text.
    try:
        import decimal

        if isinstance(value, decimal.Decimal):
            return float(value) if value.is_finite() else None
    except Exception:
        pass
    return str(value)


class EngineSession(object):
    """One engine module, one open database, one connection."""

    def __init__(self, engine):
        self.engine = engine
        self.module = None
        self.database = None
        self.connection = None
        self.path = None

    # -- lifecycle ------------------------------------------------------

    def load(self):
        if self.module is None:
            _prepare_native_library_path()
            self.module = __import__(self.engine)
        return self.module

    def info(self):
        module = self.load()
        return {
            "version": str(getattr(module, "__version__", "") or ""),
            "storage_version": self._storage_version(module),
        }

    @staticmethod
    def _storage_version(module):
        """
        The format this build writes.

        Exposed as a module attribute from Kuzu 0.8 and by every Ladybug release;
        older Kuzu builds have only the class method behind it. Returns None rather
        than guessing when neither is there -- the caller falls back to the seeded
        map.
        """
        value = getattr(module, "storage_version", None)
        if value is None:
            database = getattr(module, "Database", None)
            getter = getattr(database, "get_storage_version", None) if database else None
            if callable(getter):
                try:
                    value = getter()
                except Exception:
                    value = None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def open(self, path, read_only=True):
        module = self.load()
        if self.connection is not None and self.path == path:
            return {"path": path}
        self.close()
        try:
            self.database = module.Database(path, read_only=read_only)
        except TypeError:
            # A build old enough not to take the keyword. Opening read-write is the
            # only thing it can do, and the caller is told which it got.
            self.database = module.Database(path)
            read_only = False
        self.connection = module.Connection(self.database)
        self.path = path
        return {"path": path, "read_only": read_only}

    def close(self):
        for attribute in ("connection", "database"):
            handle = getattr(self, attribute, None)
            setattr(self, attribute, None)
            if handle is None:
                continue
            closer = getattr(handle, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        self.path = None
        return {}

    # -- queries --------------------------------------------------------

    def query(self, statement, parameters=None, max_rows=DEFAULT_MAX_ROWS):
        if self.connection is None:
            raise RuntimeError("no database is open")

        raw = (
            self.connection.execute(statement, parameters)
            if parameters
            else self.connection.execute(statement)
        )
        # A statement list -- "a; b" -- yields one result per statement on every
        # version that accepts it; a single statement yields the result itself.
        results = raw if isinstance(raw, list) else [raw]

        payload = []
        for result in results:
            payload.append(self._read_result(result, max_rows))
        return {"results": payload}

    @staticmethod
    def _read_result(result, max_rows):
        try:
            columns = [str(name) for name in result.get_column_names()]
        except Exception:
            columns = []
        try:
            types = [str(name) for name in result.get_column_data_types()]
        except Exception:
            types = []

        rows = []
        truncated = False
        while True:
            try:
                if not result.has_next():
                    break
            except Exception:
                break
            if len(rows) >= max_rows:
                truncated = True
                break
            rows.append([_to_json_value(cell) for cell in result.get_next()])

        return {"columns": columns, "types": types, "rows": rows, "truncated": truncated}


class LatticeSession(object):
    """One LatticeDB build, one open database."""

    def __init__(self, engine):
        self.engine = engine
        self.module = None
        self.database = None
        self.path = None

    # -- lifecycle ------------------------------------------------------

    def load(self):
        if self.module is None:
            self.module = __import__(self.engine)
        return self.module

    def info(self):
        module = self.load()
        return {
            "version": str(getattr(module, "__version__", "") or ""),
            "storage_version": self._storage_version(module),
        }

    @staticmethod
    def _storage_version(module):
        """
        The format this build writes, found by writing one.

        LatticeDB exposes no ``storage_version`` attribute the way Kuzu and Ladybug
        do, and the seeded map only knows the releases that existed when it was
        written. Creating a scratch store and reading its own header costs one file
        and answers exactly, which is what lets the map learn a release published
        after this code.
        """
        import shutil
        import tempfile

        directory = tempfile.mkdtemp(prefix="gxrproxy-lattice-")
        try:
            path = os.path.join(directory, "probe.db")
            database = module.Database(path, create=True)
            database.open()
            database.close()
            with open(path, "rb") as handle:
                header = handle.read(6)
            if len(header) < 6 or header[:4] != b"BDTL":
                return None
            return int(header[4]) | (int(header[5]) << 8)
        except Exception:
            return None
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def open(self, path, read_only=True):
        module = self.load()
        if self.database is not None and self.path == path:
            return {"path": path}
        self.close()
        try:
            self.database = module.Database(path, read_only=read_only)
        except TypeError:
            # A build old enough not to take the keyword; it can only open
            # read-write, and the caller is told which it got.
            self.database = module.Database(path)
            read_only = False
        # Unlike the Kuzu family, constructing the object does not open the file.
        self.database.open()
        self.path = path
        return {"path": path, "read_only": read_only}

    def close(self):
        handle = self.database
        self.database = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        self.path = None
        return {}

    # -- queries --------------------------------------------------------

    def query(self, statement, parameters=None, max_rows=DEFAULT_MAX_ROWS):
        """
        One statement, flattened into the positional shape the Kuzu family returns.

        LatticeDB hands back a ``QueryResult`` whose rows are dicts keyed by column
        name, and which holds no column types at all. Projecting the dict through
        ``columns`` keeps the row order the statement asked for -- a caller reading
        by index gets what it wrote -- and leaves the mapping layer with one shape
        to read rather than two.

        There is no statement-list form here: LatticeDB parses one statement per
        call, so the reply always carries exactly one result set.
        """
        if self.database is None:
            raise RuntimeError("no database is open")

        result = (
            self.database.query(statement, parameters)
            if parameters
            else self.database.query(statement)
        )
        columns = [str(name) for name in (getattr(result, "columns", None) or [])]

        rows = []
        truncated = False
        for row in result:
            if len(rows) >= max_rows:
                truncated = True
                break
            rows.append([_to_json_value(row.get(name)) for name in columns])

        return {
            "results": [
                {"columns": columns, "types": [], "rows": rows, "truncated": truncated}
            ]
        }


#: Which session class runs which engine. Anything not named here is a Kuzu-family
#: build, which is what ``EngineSession`` speaks.
SESSION_CLASSES = {"latticedb": LatticeSession}


def session_for(engine):
    """The session class that knows how to drive this engine."""
    return SESSION_CLASSES.get(str(engine), EngineSession)(engine)


#: Marks the start of a frame. Long enough not to collide with an engine's banner.
FRAME_PREFIX = "GXRPROXY "


def _write(message):
    """
    One framed reply.

    Written to the binary buffer rather than through ``sys.stdout``: on Windows the
    text layer turns every newline into a carriage-return pair, which would put the
    byte count in the header out of step with what the reader counts off.
    """
    payload = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    header = (FRAME_PREFIX + str(len(payload)) + "\n").encode("ascii")

    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:  # pragma: no cover - a stdout replaced by a text-only object
        sys.stdout.write(header.decode("ascii"))
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.write("\n")
        sys.stdout.flush()
        return

    stream.write(header)
    stream.write(payload)
    stream.write(b"\n")
    stream.flush()


def main(argv):
    if len(argv) < 2:
        _write({"ok": False, "error": "usage: worker.py <engine>"})
        return 2

    session = session_for(argv[1])
    handlers = {
        "ping": lambda _request: {"pong": True},
        "info": lambda _request: session.info(),
        "open": lambda request: session.open(
            request["path"], bool(request.get("read_only", True))
        ),
        "query": lambda request: session.query(
            request["statement"],
            request.get("parameters") or None,
            int(request.get("max_rows") or DEFAULT_MAX_ROWS),
        ),
        "close": lambda _request: session.close(),
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _write({"ok": False, "error": "malformed request"})
            continue

        operation = str(request.get("op") or "")
        request_id = request.get("id")
        if operation == "shutdown":
            session.close()
            _write({"ok": True, "id": request_id})
            return 0

        handler = handlers.get(operation)
        if handler is None:
            _write({"ok": False, "id": request_id, "error": "unknown op: " + operation})
            continue

        try:
            result = handler(request)
        except Exception as exc:  # the engine's own errors are answers, not crashes
            traceback.print_exc(file=sys.stderr)
            _write(
                {
                    "ok": False,
                    "id": request_id,
                    "error": str(exc) or exc.__class__.__name__,
                    "error_type": exc.__class__.__name__,
                }
            )
            continue

        message = {"ok": True, "id": request_id}
        message.update(result or {})
        _write(message)

    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
