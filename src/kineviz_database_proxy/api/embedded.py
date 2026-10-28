# -*- coding: utf-8 -*-
"""
Configuring an embedded store: identify it, upload it, and fetch its engine.

These are management endpoints, behind the admin token like the rest of the
project surface -- they read paths on the proxy's own filesystem and start
downloads, which is not something an API key holder should be able to do.

The prefix is literal, and this router has to be included **before**
``database_router``: the generic ``/api/{database_type}/{project_name}`` route
would otherwise swallow ``/api/embedded/...``. That is the same reason
``projects_router`` is included where it is.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..drivers.embedded.engine_service import ENGINE_SERVICE, EngineResolutionError
from ..drivers.embedded.pypi import PackageIndexError
from ..drivers.embedded.runtime import EngineInstallError, engines_dir, installed_runtime
from ..drivers.embedded.store_probe import (
    ENGINES,
    HEADER_SIZE,
    StoreProbeError,
    parse_header,
    probe_store,
)
from ..services import store_library
from ..services.project_service import ProjectService
from ..services.store_library import StoreLibraryError, stores_dir
from .auth import verify_admin_token

router = APIRouter(prefix="/api/embedded", tags=["embedded"])

#: Read and written a megabyte at a time: a store is a database file, and buffering
#: one in memory to check twelve bytes at the front of it would be absurd.
CHUNK_BYTES = 1024 * 1024

#: A ceiling so a stray upload cannot fill the disk. Generous -- these are real
#: databases -- and overridable.
DEFAULT_MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def get_project_service() -> ProjectService:
    """Injectable the same way the project and database routers make it."""
    return ProjectService()


def max_upload_bytes() -> int:
    raw = os.getenv("GRAPHXR_PROXY_MAX_UPLOAD_BYTES")
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_UPLOAD_BYTES
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES


def safe_name(value: str, fallback: str) -> str:
    """
    A filesystem-safe single path segment.

    Everything outside the allowlist is dropped rather than escaped, and the result
    can contain no separator and no ``..`` -- these names come from a form and end
    up as a path.
    """
    cleaned = _SAFE_NAME_RE.sub("", str(value or "").strip())
    cleaned = cleaned.strip(".") or fallback
    return cleaned[:128]


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class InspectRequest(BaseModel):
    path: str = Field(..., description="A store file, or the directory holding one")
    engine_version: Optional[str] = Field(
        None, description='Optional pin, e.g. "0.19" or "0.19.1"'
    )


class EngineStatusOut(BaseModel):
    engine: str
    version: str
    status: str
    detail: str = ""
    error: Optional[str] = None
    installed: bool = False


class InspectResponse(BaseModel):
    """What a path turned out to be, and what it would take to open it."""

    success: bool
    path: Optional[str] = None
    engine: Optional[str] = None
    storage_version: Optional[int] = None
    layout: Optional[str] = None
    description: Optional[str] = None
    #: Releases that can open it, best first. Empty when none is known.
    candidates: List[str] = Field(default_factory=list)
    resolved_version: Optional[str] = None
    engine_status: Optional[EngineStatusOut] = None
    error: Optional[str] = None


def _status_out(engine: str, version: str) -> EngineStatusOut:
    state = ENGINE_SERVICE.status(engine, version)
    return EngineStatusOut(
        engine=state.engine,
        version=state.version,
        status=state.status,
        detail=state.detail,
        error=state.error,
        installed=installed_runtime(engine, version) is not None,
    )


async def _inspect(path: str, pin: Optional[str] = None) -> InspectResponse:
    """The shared body of ``/inspect`` and the tail of ``/upload``."""
    try:
        fingerprint = probe_store(path)
    except StoreProbeError as exc:
        return InspectResponse(success=False, path=str(path), error=str(exc))

    try:
        candidates = await ENGINE_SERVICE.candidates(
            fingerprint.engine, fingerprint.storage_version, pin
        )
    except (PackageIndexError, EngineResolutionError) as exc:
        candidates = []
        error = str(exc)
    else:
        error = None

    resolved = candidates[0] if candidates else None
    return InspectResponse(
        success=True,
        path=str(fingerprint.path),
        engine=fingerprint.engine,
        storage_version=fingerprint.storage_version,
        layout=fingerprint.layout,
        description=fingerprint.describe(),
        candidates=candidates,
        resolved_version=resolved,
        engine_status=_status_out(fingerprint.engine, resolved) if resolved else None,
        error=(
            error
            if error
            else (
                None
                if resolved
                else (
                    f"No {fingerprint.engine} release is known to read storage version "
                    f"{fingerprint.storage_version}"
                )
            )
        ),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/inspect", response_model=InspectResponse)
async def inspect_path(
    request: InspectRequest,
    _: str | None = Depends(verify_admin_token),
):
    """
    Identify the store at a path the user typed.

    The same answer the drop route gives, so configuring a directory and dragging a
    file report the same thing in the same words. A path that is not a store is a
    ``success: false`` body rather than an HTTP error: it is a normal outcome of the
    user still typing.
    """
    return await _inspect(request.path, request.engine_version)


@router.post("/upload", response_model=InspectResponse)
async def upload_store(
    file: UploadFile = File(..., description="The store file"),
    project: str = Form("", description="Project name; decides the subdirectory"),
    folder: str = Form("", description="Library folder; overrides project when given"),
    overwrite: bool = Form(False),
    _: str | None = Depends(verify_admin_token),
):
    """
    Accept a dropped store file.

    Streamed to disk a megabyte at a time -- these are databases, not attachments --
    and rejected as soon as the first twelve bytes say it is not one, so a large
    wrong file is not written out in full before anyone notices.

    An existing file at the destination is **not** replaced unless ``overwrite`` is
    set. Silently writing over a database because the names matched is not something
    to do on a user's behalf.
    """
    # The form files an upload under the project being configured; the store
    # library files it under a folder the user picked. Same directory either way.
    directory = stores_dir() / safe_name(folder or project, "unassigned")
    filename = safe_name(file.filename or "", "store.kz")
    destination = directory / filename

    if destination.exists() and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{destination} already exists. Send overwrite=true to replace it, "
                f"or upload under a different name."
            ),
        )

    directory.mkdir(parents=True, exist_ok=True)
    partial = directory / f".{filename}.part"
    limit = max_upload_bytes()
    header = b""
    written = 0

    try:
        with open(partial, "wb") as handle:
            while True:
                chunk = await file.read(CHUNK_BYTES)
                if not chunk:
                    break

                if len(header) < HEADER_SIZE:
                    header += chunk[: HEADER_SIZE - len(header)]
                    if len(header) >= HEADER_SIZE and parse_header(header) is None:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"{file.filename} does not start with a Kuzu, Ladybug "
                                f"or LatticeDB magic number, so it is not an embedded "
                                f"store. A store written by Kuzu 0.10 or older is a "
                                f"directory -- configure it by path instead of dragging "
                                f"it."
                            ),
                        )

                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"The upload is larger than the {limit} byte limit.",
                    )
                handle.write(chunk)

        if len(header) < HEADER_SIZE:
            raise HTTPException(
                status_code=400, detail=f"{file.filename} is too small to be a store."
            )

        os.replace(partial, destination)
    except HTTPException:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Could not save the upload: {exc}") from exc

    return await _inspect(str(destination))


class StoreEntryOut(BaseModel):
    """One file in the store library, as far as its first bytes identify it."""

    relative_path: str
    path: str
    folder: str
    name: str
    size: int
    modified: float
    layout: str
    #: "kuzu", "ladybug", "latticedb", "sqlite", "duckdb" or "unknown".
    kind: str
    engine: Optional[str] = None
    storage_version: Optional[int] = None
    description: str
    servable: bool
    #: Resolved from the map the proxy already holds, never over the network.
    resolved_version: Optional[str] = None
    engine_installed: bool = False
    #: Projects pointing at this file. What makes a delete answerable rather than
    #: a surprise the next time GraphXR opens a graph.
    used_by: List[str] = Field(default_factory=list)


class ExternalStoreOut(BaseModel):
    """A store a project names that is kept outside the library."""

    path: str
    database_type: str
    used_by: List[str] = Field(default_factory=list)


class StoreListResponse(BaseModel):
    root: str
    items: List[StoreEntryOut]
    total: int
    offset: int
    limit: int
    folders: List[str] = Field(default_factory=list)
    external: List[ExternalStoreOut] = Field(default_factory=list)


def _entry_out(entry: store_library.LibraryEntry, usage: Dict[str, List[str]]) -> StoreEntryOut:
    resolved = None
    if entry.engine and entry.storage_version is not None:
        candidates = ENGINE_SERVICE.local_candidates(entry.engine, entry.storage_version)
        resolved = candidates[0] if candidates else None
    return StoreEntryOut(
        relative_path=entry.relative_path,
        path=entry.path,
        folder=entry.folder,
        name=entry.name,
        size=entry.size,
        modified=entry.modified,
        layout=entry.layout,
        kind=entry.kind,
        engine=entry.engine,
        storage_version=entry.storage_version,
        description=entry.description,
        servable=entry.servable,
        resolved_version=resolved,
        engine_installed=bool(
            entry.engine and resolved and installed_runtime(entry.engine, resolved) is not None
        ),
        used_by=store_library.used_by(usage, entry.path),
    )


@router.get("/stores", response_model=StoreListResponse)
async def list_stores(
    offset: int = 0,
    limit: int = 25,
    search: str = "",
    include_external: bool = False,
    projects_service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_admin_token),
):
    """
    The store library, one page at a time.

    Paged at the walk rather than after it: the tree is enumerated lazily and
    abandoned once the page is full, so only the rows being returned are stat-ed,
    read and resolved. A directory of ten thousand files costs ten thousand names,
    not ten thousand header reads.

    ``include_external`` adds the stores projects point at from elsewhere on the
    disk. They are not part of the library and cannot be deleted through it, but a
    path field offering to reuse one is the whole point of asking.
    """
    root = stores_dir()
    offset = max(0, offset)
    limit = max(1, min(limit, 200))

    projects = await projects_service.list_projects()
    usage = store_library.usage_index(projects)

    entries = store_library.page(root, offset, limit, search)
    return StoreListResponse(
        root=str(root),
        items=[_entry_out(entry, usage) for entry in entries],
        total=store_library.count_paths(root, search),
        offset=offset,
        limit=limit,
        folders=store_library.folders(root),
        external=(
            [
                ExternalStoreOut(path=path, database_type=kind, used_by=list(names))
                for path, kind, names in store_library.external_paths(projects, root)
            ]
            if include_external
            else []
        ),
    )


@router.delete("/stores")
async def delete_store(
    path: str,
    force: bool = False,
    projects_service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_admin_token),
):
    """
    Remove one store from the library.

    A store a project still points at is refused with the names of the projects,
    unless ``force`` is set: deleting the file would not break the project until
    the next query, which is exactly the kind of delayed failure worth one extra
    click to avoid.
    """
    root = stores_dir()
    try:
        target = store_library.resolve(root, path)
    except StoreLibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    projects = await projects_service.list_projects()
    holders = store_library.used_by(store_library.usage_index(projects), str(target))
    if holders and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{path} is in use by {', '.join(holders)}. Send force=true to delete "
                f"it anyway; those projects will stop working."
            ),
        )

    try:
        removed = store_library.remove(root, path)
    except StoreLibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not delete {path}: {exc}") from exc

    return {"removed": removed, "path": path, "used_by": holders}


@router.get("/engines", response_model=List[EngineStatusOut])
async def list_engines(_: str | None = Depends(verify_admin_token)):
    """
    Every engine build the proxy knows the state of.

    What the configure form polls while a download runs, and what tells it whether
    a store's engine is already there.
    """
    seen = {(state.engine, state.version) for state in ENGINE_SERVICE.statuses()}
    out = [
        EngineStatusOut(
            engine=state.engine,
            version=state.version,
            status=state.status,
            detail=state.detail,
            error=state.error,
            installed=installed_runtime(state.engine, state.version) is not None,
        )
        for state in ENGINE_SERVICE.statuses()
    ]

    # Installs from an earlier run have no in-memory status yet; they are still
    # ready, and a form that only listed this session's would offer to download
    # something already on the disk.
    root = engines_dir()
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or "-" not in child.name:
                continue
            engine, _, version = child.name.partition("-")
            if engine not in ENGINES or (engine, version) in seen:
                continue
            if installed_runtime(engine, version) is not None:
                out.append(_status_out(engine, version))
    return out


@router.post("/engines/{engine}/{version}/install", response_model=EngineStatusOut)
async def install_engine(
    engine: str,
    version: str,
    _: str | None = Depends(verify_admin_token),
):
    """
    Start fetching an engine build, and return without waiting for it.

    The eager half of the install story: the form fires this the moment a store is
    identified, then polls ``/engines``. A query that arrives mid-download joins the
    same install rather than starting a second one.
    """
    if engine not in ENGINES:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {engine}")
    state = ENGINE_SERVICE.start_install(engine, version)
    return EngineStatusOut(
        engine=state.engine,
        version=state.version,
        status=state.status,
        detail=state.detail,
        error=state.error,
        installed=installed_runtime(engine, version) is not None,
    )


@router.delete("/engines/{engine}/{version}")
async def uninstall_engine(
    engine: str,
    version: str,
    _: str | None = Depends(verify_admin_token),
):
    """Remove an installed build. Nothing in flight is interrupted; the files go."""
    if engine not in ENGINES:
        raise HTTPException(status_code=400, detail=f"Unknown engine: {engine}")
    removed = await ENGINE_SERVICE.uninstall(engine, version)
    return {"removed": removed, "engine": engine, "version": version}


@router.get("/known-formats")
async def known_formats(_: str | None = Depends(verify_admin_token)) -> Dict[str, Any]:
    """
    The storage formats the proxy can currently place, per engine.

    Useful when a store will not open: it says in one call whether the format is
    unknown or the engine merely missing.
    """
    version_map = ENGINE_SERVICE.version_map
    return {
        engine: {
            "formats": sorted(version_map.known_formats(engine)),
            "newestKnownRelease": version_map.newest_known_release(engine),
        }
        for engine in ENGINES
    }
