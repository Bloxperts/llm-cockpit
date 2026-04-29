# Claude Code prompt — Sprint 2 fixup + Sprint 3 UC-02 kickoff

Paste this verbatim. Do everything in the order given.

---

## Exact git state right now

```
origin/develop tip:              67d4bfe  [UC-08] cockpit-admin serve + FastAPI main.py + placeholder frontend (Slice B)
origin/feature/UC-09-forced-change:
  d889328  [UC-09] forced password change
  444a18b  [UC-01] auth router: login, JWT cookie, current_user, require_role, rate limit, audit
  fc274ab  [UC-08] cockpit-admin serve + FastAPI main.py + placeholder frontend (Slice B)  ← same code as 67d4bfe on develop, different hash (squash)
```

UC-01 (`444a18b`) and UC-09 (`d889328`) are not on `develop`. They must land there before UC-02 can be built.

---

## Part 1 — Land UC-01 and UC-09 on develop

### Step A — cherry-pick onto a fixup branch

```bash
git fetch origin
git checkout -b hotfix/sprint2-uc01-uc09 origin/develop
git cherry-pick 444a18b   # [UC-01]
git cherry-pick d889328   # [UC-09]
git push -u origin hotfix/sprint2-uc01-uc09
```

If either cherry-pick produces conflicts (unlikely — develop and the feature branch share the same UC-08 base), resolve them to match the feature branch exactly, then `git cherry-pick --continue`.

### Step B — open and merge a single PR

```bash
gh pr create \
  --base develop \
  --head hotfix/sprint2-uc01-uc09 \
  --title "[chore] land UC-01 + UC-09 on develop (cherry-pick from stacked PRs)" \
  --body "UC-01 and UC-09 commits were merged into stacked feature branches but \
not squash-merged into develop. Cherry-picks 444a18b (UC-01) and d889328 (UC-09) \
directly. No code change — identical to the reviewed and accepted PRs #4 and #5."
```

Then merge it:

```bash
gh pr merge --squash \
  --subject "[chore] land UC-01 + UC-09 on develop (sprint 2 fixup)" \
  --delete-branch=false
```

### Step C — verify

```bash
git fetch origin
git log origin/develop --oneline -6
```

Expected: develop tip contains cockpit/main.py, cockpit/routers/auth.py, cockpit/schemas.py, cockpit/deps.py, frontend_dist HTML, and the UC-09 settled gate. Spot-check:

```bash
git show origin/develop:src/cockpit/routers/auth.py | grep "def change_password" | wc -l
# must print 1
git show origin/develop:src/cockpit/deps.py | head -3
# must exist and show the get_session dependency
```

---

## Part 2 — Sprint 3: UC-02 Live dashboard + placement board

Once develop is confirmed clean, proceed with the full UC-02 build.
All instructions are in `CLAUDE_CODE_PROMPT_SPRINT3_UC02.md` in the repo root.
Read it now and execute it in full.

The only addition to that prompt: the `feature/UC-02-dashboard` branch must be
cut from `origin/develop` **after** the fixup in Part 1 is merged. Do not cut
it earlier.

```bash
git checkout develop && git pull
git checkout -b feature/UC-02-dashboard
```

Then follow CLAUDE_CODE_PROMPT_SPRINT3_UC02.md from Step 0 onward.
