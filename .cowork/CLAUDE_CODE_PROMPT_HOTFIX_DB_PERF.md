# Claude Code prompt — Hotfix: SQLite WAL + embedding-model perf crash

Paste this verbatim. Do everything in the order given.

---

## What broke (observed on Neuroforge)

1. **GpuSampler database locked** — every 5-second INSERT into `metrics_snapshot`
   fails with `sqlite3.OperationalError: database is locked`. Root cause: SQLite's
   default journal mode allows only one writer at a time. The background asyncio
   Task holding a session and a concurrent request handler both trying to write
   produces the deadlock.

2. **Perf test crash on embedding models** — `nomic-embed-text:latest` returns
   HTTP 400 `"does not support chat"` (`OllamaResponseError`). The `_drop_model`
   helper and the `cold_load` stage only caught `OllamaModelNotFound` and
   `OllamaUnreachableError`, so the `OllamaResponseError` propagated all the way
   to the ASGI stack and produced a 500-level ASGI ExceptionGroup crash.

---

## Fixes already applied by Cowork (do not re-apply)

Cowork has already edited these two files on `develop`:

- `src/cockpit/db.py` — `make_engine` now enables `PRAGMA journal_mode=WAL`
  and `PRAGMA busy_timeout=5000` for every SQLite connection via a SQLAlchemy
  `connect` event listener.

- `src/cockpit/routers/admin_ollama.py`:
  - `_drop_model` now catches `(OllamaModelNotFound, OllamaResponseError)` — both
    mean "nothing to unload, move on".
  - The `cold_load` stage inside `gen()` now has an `except OllamaResponseError`
    branch that yields `{"event": "error", "data": {"detail": "model_not_supported"}}`
    and returns — so the SSE stream closes cleanly instead of crashing.

---

## Your tasks

### Step 1 — Branch

```bash
git fetch origin
git checkout develop && git pull
git checkout -b hotfix/db-wal-perf-embed
```

### Step 2 — Verify the edits

Confirm the two files already contain the expected changes:

```bash
grep "journal_mode=WAL" src/cockpit/db.py | wc -l
# must print 1

grep "OllamaResponseError" src/cockpit/routers/admin_ollama.py | wc -l
# must be >= 3 (one in _drop_model, one in cold_load except, one existing in _probe_max_context)
```

If either check fails, something went wrong in the Cowork edit — stop and report.

### Step 3 — Run the existing test suite

```bash
pytest -x -q
```

All existing tests must stay green. The WAL pragma fires only on real SQLite
connections (not `:memory:` test databases with no file), so the test fixtures
are unaffected. If any test breaks, fix it before proceeding.

### Step 4 — Commit

```bash
git add src/cockpit/db.py src/cockpit/routers/admin_ollama.py
git commit -m "[chore] hotfix: WAL mode for SQLite + handle embedding models in perf test"
```

### Step 5 — PR + merge onto develop

```bash
gh pr create \
  --base develop \
  --head hotfix/db-wal-perf-embed \
  --title "[chore] hotfix: SQLite WAL + embedding-model perf crash" \
  --body "Two production bugs found on Neuroforge live install.

1. GpuSampler \`database is locked\` — enable WAL journal mode + busy_timeout=5000 on engine creation (db.py).
2. Perf test crash on embedding-only models (nomic-embed-text etc.) — \`_drop_model\` and cold_load stage now catch OllamaResponseError and exit cleanly."

gh pr merge --squash \
  --subject "[chore] hotfix: SQLite WAL + embedding-model perf crash" \
  --delete-branch=false
```

### Step 6 — Build and release patch

```bash
git checkout develop && git pull
git tag v0.1.1
git push origin v0.1.1

# Build wheel
make build
# or manually:
python -m build --wheel

# Create GitHub release
gh release create v0.1.1 dist/llm_cockpit-0.1.1-py3-none-any.whl \
  --title "v0.1.1 — SQLite WAL + embedding model fix" \
  --notes "**Bug fixes**
- GpuSampler no longer produces \`database is locked\` errors under concurrent writes (SQLite WAL mode enabled).
- Performance test no longer crashes on embedding-only models (e.g. nomic-embed-text). They now receive a clean \`model_not_supported\` SSE error event."
```

### Step 7 — Verify on develop

```bash
git show origin/develop:src/cockpit/db.py | grep "journal_mode"
# must show the PRAGMA line

git show origin/develop:src/cockpit/routers/admin_ollama.py | grep -A2 "_drop_model"
# must show the updated except tuple
```

---

## Stop and ask Chris if

- Any existing test breaks after the WAL change.
- The `make build` / `python -m build` step fails (wheel build environment issue).
- The `v0.1.1` tag already exists on origin.
