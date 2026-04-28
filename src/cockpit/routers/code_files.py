"""Per-user code workspace — UC-06 §Code working folder.

Each authenticated user with the `code` role (or `admin`) gets a folder
at `<data_dir>/code_files/<username>/`. The folder is created lazily on
first access. Files are written atomically (`.tmp` → rename) so a
mid-write crash never produces a partial file the user might trust.

Security: every operation runs through `_safe_user_path` which resolves
the requested relative path inside the user root and rejects anything
that escapes (`..` ladders, absolute paths, symlinks pointing out).

This router does **not** use the LLM port; it's pure local filesystem.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from cockpit.config import Settings
from cockpit.deps import get_settings
from cockpit.models import User
from cockpit.routers.auth import require_role_settled
from cockpit.schemas import FileEntry, SaveFileRequest

log = logging.getLogger(__name__)
router = APIRouter()

# Reasonable upper bound for a single artifact. Big enough for source files,
# small enough that a runaway LLM can't fill the disk in one save.
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB


def _user_root(settings: Settings, username: str) -> Path:
    """Resolve the user's workspace root, creating it (mkdir -p) if missing.
    Username is already validated at the auth layer (UC-01 regex).
    """
    root = (settings.resolved_code_files_dir / username).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_user_path(settings: Settings, username: str, rel: str) -> Path:
    """Resolve `rel` inside the user's workspace. Raises HTTPException(400)
    on any attempt to escape the root (../ ladders, absolute paths, etc.).
    """
    if not rel or rel.startswith("/") or "\x00" in rel:
        raise HTTPException(400, detail={"detail": "invalid_path"})
    root = _user_root(settings, username)
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(400, detail={"detail": "invalid_path"}) from None
    return target


def _entry_for(target: Path, root: Path) -> FileEntry:
    stat = target.stat()
    rel = target.relative_to(root).as_posix()
    return FileEntry(
        name=target.name,
        path=rel,
        size_bytes=stat.st_size if not target.is_dir() else 0,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        is_dir=target.is_dir(),
    )


@router.get("", response_model=list[FileEntry], summary="List the user's workspace.")
def list_files(
    dir: str = Query(default="", description="Relative subdirectory (default: root)."),
    user: User = Depends(require_role_settled("code")),
    settings: Settings = Depends(get_settings),
) -> list[FileEntry]:
    """Non-recursive listing. Pass `?dir=subpath` to walk into a subdirectory.

    Creates the user's folder on first call so the frontend doesn't have to
    do a separate "init workspace" round-trip.
    """
    root = _user_root(settings, user.username)
    target_dir = _safe_user_path(settings, user.username, dir) if dir else root
    if not target_dir.exists():
        return []
    if not target_dir.is_dir():
        raise HTTPException(400, detail={"detail": "not_a_directory"})

    out: list[FileEntry] = []
    for child in sorted(target_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            out.append(_entry_for(child, root))
        except FileNotFoundError:
            # Child vanished between iterdir() and stat() — skip silently.
            continue
    return out


@router.get("/download", summary="Download a file from the workspace.")
def download_file(
    path: str = Query(..., min_length=1),
    user: User = Depends(require_role_settled("code")),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    target = _safe_user_path(settings, user.username, path)
    if not target.exists() or target.is_dir():
        raise HTTPException(404, detail={"detail": "file_not_found"})
    return FileResponse(
        path=target,
        filename=target.name,
        media_type="application/octet-stream",
    )


@router.post("/save", response_model=FileEntry, summary="Save a file to the workspace.")
def save_file(
    body: SaveFileRequest,
    user: User = Depends(require_role_settled("code")),
    settings: Settings = Depends(get_settings),
) -> FileEntry:
    encoded = body.content.encode("utf-8")
    if len(encoded) > MAX_FILE_BYTES:
        raise HTTPException(
            413,
            detail={"detail": "file_too_large", "max_bytes": MAX_FILE_BYTES},
        )

    target = _safe_user_path(settings, user.username, body.path)
    if target.is_dir():
        raise HTTPException(400, detail={"detail": "path_is_directory"})
    if target.exists() and not body.overwrite:
        raise HTTPException(409, detail={"detail": "file_exists"})

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_bytes(encoded)
        os.replace(tmp, target)  # atomic on POSIX
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    root = _user_root(settings, user.username)
    return _entry_for(target, root)


@router.delete("", status_code=204, summary="Delete a file from the workspace.")
def delete_file(
    path: str = Query(..., min_length=1),
    user: User = Depends(require_role_settled("code")),
    settings: Settings = Depends(get_settings),
) -> None:
    target = _safe_user_path(settings, user.username, path)
    if not target.exists():
        raise HTTPException(404, detail={"detail": "file_not_found"})
    if target.is_dir():
        # Only a non-empty directory needs explicit handling; keep the
        # delete API file-only for v0.1 simplicity.
        if any(target.iterdir()):
            raise HTTPException(409, detail={"detail": "directory_not_empty"})
        target.rmdir()
    else:
        target.unlink()


# Suppress unused-import lint while keeping shutil available for future
# bulk operations.
_ = shutil
