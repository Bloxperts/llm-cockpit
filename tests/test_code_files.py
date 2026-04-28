"""UC-06 — code working folder router tests.

Covers list / download / save / delete + path-traversal guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from cockpit.adapters.fake_chat import FakeLLMChat
from cockpit.adapters.fake_telemetry import FakeTelemetry
from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.main import create_app
from cockpit.models import User
from cockpit.services.users import hash_password


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "cockpit-data"
    data_dir.mkdir()
    s = Settings(
        data_dir=data_dir,
        host="127.0.0.1",
        port=18080,
        bcrypt_cost=4,
        jwt_secret="test-secret-do-not-use-in-prod",
    )
    upgrade_to_head(s.db_url)
    return s


@pytest.fixture
def session_factory(settings: Settings) -> sessionmaker:
    engine = make_engine(settings.db_url)
    return make_session_factory(engine)


@pytest.fixture
def seeded(settings: Settings) -> dict:
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    out = {
        "bob":   {"role": "code", "password": "CodePW01!"},
        "carol": {"role": "chat", "password": "ChatPW01!"},
    }
    try:
        with factory() as session:
            for name, info in out.items():
                session.add(
                    User(
                        username=name,
                        pw_hash=hash_password(info["password"], cost=settings.bcrypt_cost),
                        role=info["role"],
                        must_change_password=0,
                    )
                )
            session.commit()
    finally:
        engine.dispose()
    return out


def _build_client(settings: Settings) -> TestClient:
    app = create_app(
        settings,
        chat_factory=lambda url: FakeLLMChat(models=[]),
        telemetry_factory=lambda: FakeTelemetry(snapshots=[]),
        skip_db_upgrade=True,
        skip_samplers=True,
    )
    return TestClient(app)


def _login(client: TestClient, seeded: dict, username: str) -> None:
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": seeded[username]["password"]},
    )
    assert r.status_code == 200, r.text


# =========================================================================
# Auth gate
# =========================================================================


def test_list_requires_auth(settings: Settings) -> None:
    client = _build_client(settings)
    with client:
        r = client.get("/api/code/files")
    assert r.status_code == 401


def test_list_requires_at_least_code_role(settings: Settings, seeded: dict) -> None:
    """Chat users get 403 — workspace is gated by `code` (admin satisfies it
    transitively via the role ladder)."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")  # chat
        r = client.get("/api/code/files")
    assert r.status_code == 403


# =========================================================================
# List + create-on-first-call
# =========================================================================


def test_list_creates_user_root_and_returns_empty(
    settings: Settings, seeded: dict
) -> None:
    user_root = settings.resolved_code_files_dir / "bob"
    assert not user_root.exists()
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.get("/api/code/files")
    assert r.status_code == 200
    assert r.json() == []
    assert user_root.is_dir()


# =========================================================================
# Save → list → download → delete
# =========================================================================


def test_save_then_list_then_download_then_delete(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")

        # Save.
        r = client.post(
            "/api/code/files/save",
            json={"path": "report.html", "content": "<h1>Hi</h1>", "overwrite": False},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "report.html"
        assert body["size_bytes"] == len("<h1>Hi</h1>")
        assert body["is_dir"] is False

        # List shows it.
        r = client.get("/api/code/files")
        names = [e["name"] for e in r.json()]
        assert "report.html" in names

        # Download.
        r = client.get("/api/code/files/download?path=report.html")
        assert r.status_code == 200
        assert r.content == b"<h1>Hi</h1>"
        assert "report.html" in r.headers.get("content-disposition", "")

        # Delete.
        r = client.delete("/api/code/files?path=report.html")
        assert r.status_code == 204
        assert client.get("/api/code/files").json() == []


def test_save_in_subdirectory_creates_path(
    settings: Settings, seeded: dict
) -> None:
    """Saving `notes/work/today.md` should create the subdirectories."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.post(
            "/api/code/files/save",
            json={"path": "notes/work/today.md", "content": "# Today\n"},
        )
        assert r.status_code == 200
        # List the subdirectory.
        r = client.get("/api/code/files?dir=notes/work")
        assert [e["name"] for e in r.json()] == ["today.md"]


def test_save_overwrite_false_409_when_exists(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        client.post(
            "/api/code/files/save",
            json={"path": "a.txt", "content": "v1", "overwrite": False},
        )
        r = client.post(
            "/api/code/files/save",
            json={"path": "a.txt", "content": "v2", "overwrite": False},
        )
    assert r.status_code == 409
    assert r.json()["detail"]["detail"] == "file_exists"


def test_save_overwrite_true_replaces_content(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        client.post("/api/code/files/save", json={"path": "a.txt", "content": "v1"})
        r = client.post(
            "/api/code/files/save",
            json={"path": "a.txt", "content": "v2", "overwrite": True},
        )
        assert r.status_code == 200
        r = client.get("/api/code/files/download?path=a.txt")
    assert r.content == b"v2"


def test_save_too_large_returns_413(
    settings: Settings, seeded: dict
) -> None:
    """A single artifact > MAX_FILE_BYTES is rejected."""
    from cockpit.routers import code_files as cf

    client = _build_client(settings)
    big = "x" * (cf.MAX_FILE_BYTES + 1)
    with client:
        _login(client, seeded, "bob")
        r = client.post("/api/code/files/save", json={"path": "big.txt", "content": big})
    assert r.status_code == 413
    assert r.json()["detail"]["detail"] == "file_too_large"


# =========================================================================
# Path traversal guard
# =========================================================================


@pytest.mark.parametrize(
    "evil",
    [
        "../../../etc/passwd",
        "../sibling.txt",
        "/absolute/path",
        "subdir/../../escape",
        "\x00null-byte",
    ],
)
def test_save_rejects_path_traversal(
    settings: Settings, seeded: dict, evil: str
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.post(
            "/api/code/files/save",
            json={"path": evil, "content": "owned"},
        )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "invalid_path"


@pytest.mark.parametrize(
    "evil",
    [
        "../../../etc/passwd",
        "/etc/passwd",
    ],
)
def test_download_rejects_path_traversal(
    settings: Settings, seeded: dict, evil: str
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.get(f"/api/code/files/download?path={evil}")
    assert r.status_code == 400


def test_delete_rejects_path_traversal(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.delete("/api/code/files?path=../../etc/passwd")
    assert r.status_code == 400


def test_users_cant_see_each_others_files(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Bob saves a file; promote Carol to `code` then verify her workspace
    is empty (she sees her own folder, not bob's)."""
    # Promote carol to code in-DB so the role gate passes.
    from sqlalchemy import select

    with session_factory() as session:
        carol = session.execute(select(User).where(User.username == "carol")).scalar_one()
        carol.role = "code"
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        client.post("/api/code/files/save", json={"path": "secret.txt", "content": "ssh"})

    client2 = _build_client(settings)
    with client2:
        _login(client2, seeded, "carol")
        r = client2.get("/api/code/files")
        assert r.status_code == 200
        assert r.json() == []
        # And she can't reach into bob's folder via a relative dir.
        r = client2.get("/api/code/files/download?path=secret.txt")
    assert r.status_code == 404


# =========================================================================
# Misc edge cases
# =========================================================================


def test_download_404_when_missing(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.get("/api/code/files/download?path=ghost.txt")
    assert r.status_code == 404


def test_delete_404_when_missing(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.delete("/api/code/files?path=ghost.txt")
    assert r.status_code == 404


def test_save_path_is_directory_400(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        # First save something inside `notes/`.
        client.post("/api/code/files/save", json={"path": "notes/x.md", "content": "x"})
        # Now try to save *to* `notes/` itself (which is now a directory).
        r = client.post("/api/code/files/save", json={"path": "notes", "content": "y"})
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "path_is_directory"


def test_list_subdir_not_a_directory(
    settings: Settings, seeded: dict
) -> None:
    """?dir= pointing at a file → 400 not_a_directory."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        client.post("/api/code/files/save", json={"path": "x.txt", "content": "x"})
        r = client.get("/api/code/files?dir=x.txt")
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "not_a_directory"


def test_list_missing_subdir_returns_empty(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.get("/api/code/files?dir=does-not-exist")
    assert r.status_code == 200
    assert r.json() == []


def test_delete_empty_subdirectory(settings: Settings, seeded: dict) -> None:
    """Deleting an empty directory rmdirs it."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        client.post("/api/code/files/save", json={"path": "notes/x.md", "content": "x"})
        # Remove the file; `notes/` is now empty.
        client.delete("/api/code/files?path=notes/x.md")
        r = client.delete("/api/code/files?path=notes")
    assert r.status_code == 204
    user_root = settings.resolved_code_files_dir / "bob"
    assert not (user_root / "notes").exists()


def test_delete_non_empty_directory_409(settings: Settings, seeded: dict) -> None:
    """Deleting a directory that still has children → 409 directory_not_empty."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        client.post("/api/code/files/save", json={"path": "notes/x.md", "content": "x"})
        r = client.delete("/api/code/files?path=notes")
    assert r.status_code == 409
    assert r.json()["detail"]["detail"] == "directory_not_empty"


def test_save_atomic_replace_failure_cleans_tmp(
    settings: Settings, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `os.replace` errors after the .tmp is written, the .tmp file is
    cleaned up so the user's workspace doesn't accumulate orphans."""
    from cockpit.routers import code_files as cf

    real_replace = cf.os.replace
    calls = {"n": 0}

    def boom(src, dst):
        calls["n"] += 1
        raise OSError("simulated rename failure")

    monkeypatch.setattr(cf.os, "replace", boom)

    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        with pytest.raises(OSError):
            client.post(
                "/api/code/files/save", json={"path": "doomed.txt", "content": "x"}
            )
    # No `.tmp` orphan should be left in the workspace.
    user_root = settings.resolved_code_files_dir / "bob"
    if user_root.exists():
        leftovers = [p.name for p in user_root.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []
    # Restore (cleanup paranoia for any subsequent test).
    monkeypatch.setattr(cf.os, "replace", real_replace)
