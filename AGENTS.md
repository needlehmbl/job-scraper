# AGENTS.md

## Branch model

`dev` is the working branch. `main` is the release branch.

- Do all work on `dev`. Commit as often as you like.
- Merge `dev` into `main` **only when the change should reach users**. That
  merge is the release.

```bash
git checkout dev      # normal working branch
# ... work, commit ...
git checkout main && git merge dev && git push
```

`main` must never be committed to directly. If a task explicitly says the
change belongs on `main`, follow that instruction instead.

### `dev` is local-only and must never be pushed

The repository is **public**, so anything on a pushed branch is world-readable
at `github.com/needlehmbl/job-scraper`. `dev` was deleted from the remote on
purpose: unreleased work should not be public, and GitHub has no way to hide a
single branch inside a public repo.

- `git push origin dev` is **blocked** by `.git/hooks/pre-push` (a local,
  uncommitted hook). If it refuses, that is working as intended -- merge to
  `main` instead. Do not remove or bypass the hook to "fix" it.
- `dev` has no upstream, so a bare `git push` from `dev` will not reach the
  remote either.
- The tradeoff is accepted: unreleased work lives only on this machine until it
  is merged. There is no off-machine backup of `dev`.
- `main` remains the only branch on the remote, and still the default branch.

## Releases

Every push to `main` releases automatically, via
`.github/workflows/auto-release.yml`. Note this workflow is only registered by
GitHub once it has landed on `main` at least once, so the first
`dev` -> `main` merge is also the first auto-release:

1. `auto-release` first runs `pytest tests/` in a `test` job; the tag step
   `needs` it, so a failing test publishes nothing. Then it reads the newest
   tag, bumps it, and pushes an annotated tag at the `main` HEAD. Patch by
   default; a `major:` or `minor:` prefix on the HEAD commit subject bumps that
   level instead. It sets a committer identity first -- `actions/checkout` does
   not provide one, and `git tag -a` needs it.
2. It then dispatches `release.yml` (`workflow_dispatch`, input `tag`) with that
   tag. The dispatch is not optional: a ref pushed with `GITHUB_TOKEN` does not
   trigger other workflows, so the tag push alone never starts `release.yml`.
3. `release.yml` builds the Linux tarball, the macOS tarball, and the Windows
   installer + portable zip with checksums, and publishes them as a GitHub
   Release. Manual tag pushes (`on: push: tags: v*`) still work the same way.
   The Linux and macOS tarballs are built by `installer/make-tarball.sh`, which
   derives the file list from `git ls-files` and then fails the build if any
   path the shipped `Dockerfile` needs is missing from the archive. Do not
   hand-edit a tarball file list -- add build-time, docs, or dev-only trees to
   the exclude list in that script instead. New runtime files ship by default,
   which is the point: a hand-written list once shipped a release with no
   `api/` and no `requirements.txt`, so `docker compose build` could not work.
   Each job also bakes the tag into a `VERSION` file -- see below.
4. The release body is generated from every commit since the previous release,
   so the commit messages written on `dev` become the changelog.

Consequences worth remembering before merging:

- Each `main` push burns a full 3-platform build; the Windows job (PyInstaller
  + Playwright Chromium + Inno Setup) is the slow one.
- Each release is a fresh download for every user, so merge `dev` -> `main` on
  a cadence that matches "something changed about what users get" -- not per
  commit.

Manual tagging still works if a release is ever needed without a merge:
`git tag vX.Y.Z && git push origin vX.Y.Z`. That path skips the `test` gate.

## The installed version

`GET /updates/check` reports `current`, which comes from
`api/routes/admin.py:resolve_version()`:

1. `JOBSCRAPER_VERSION` in the environment, if set. This is the only channel a
   source checkout or a hand-run process has, so keep it first.
2. A `VERSION` file. A packaged build cannot be told its version any other way:
   the container only gets what `docker-compose.yml` passes in, and a frozen exe
   has no environment at all. So each release bakes the tag into a `VERSION`
   file at the root of the artifact -- `installer/make-tarball.sh` writes it and
   adds it to the archive, the Windows job writes it and the spec ships it as a
   data file (landing in `sys._MEIPASS`).
3. Otherwise `"dev"`, and `needs_update()` never nags for a `"dev"` build.

The file is gitignored and only trusted when it starts with `v`, so a stale one
left behind by a local build cannot make a source checkout claim to be a
release. `tests/test_version_resolution.py` covers the precedence.

## Tests

`pytest tests/` (needs `pip install -r requirements.txt -r requirements-dev.txt`).
`auto-release.yml` runs it in a `test` job that the `tag` job `needs`, so a red
test means no release. Run it before merging `dev` -> `main`.

## The frozen (Windows) build

`installer/JobScraper.spec` freezes `launcher.py`, which serves the app with
`uvicorn.run("api.main:app")`. Two things that are invisible to static analysis
and have to be maintained by hand:

- `api.main` is a hidden import. Nothing imports it statically, so without it
  the exe dies with `ModuleNotFoundError: No module named 'api'`.
- `tls_client` needs `collect_all`, because jobspy -> curl_cffi loads
  `tls_client/dependencies/*.so|dll|dylib` through ctypes at import time.
- The dashboard `Mount("/")` in `api/main.py` must stay the last route
  registered. Starlette matches in order, and a mount at `/` swallows
  everything after it -- that is how `/health` became a 404 in the packaged
  app, and the launcher then waited 60s and quit. `tests/test_frozen_routes.py`
  guards the order.
- Data-file paths in the spec must be anchored to `SPECPATH` (the spec's own
  directory), not to `os.getcwd()`. PyInstaller resolves `datas` against
  `SPECPATH`, but the spec body still runs in the directory the command was
  launched from, so an `os.path.exists("../VERSION")` guard silently evaluates
  False when you build from the repo root and drops the file with no warning.

Do not trust the spec by reading it. PyInstaller builds on Linux, so the whole
graph can be checked before merging:

```bash
./venv/bin/pip install pyinstaller && ./venv/bin/python -m playwright install chromium
./venv/bin/python -m PyInstaller --noconfirm --distpath /tmp/pyi/dist --workpath /tmp/pyi/build installer/JobScraper.spec
cd /tmp/pyi/dist/JobScraper && ./JobScraper --serve-api   # needs 127.0.0.1:8000 free
```

Then curl `/health`, `/updates/check`, `/jobs` and `/` against it. Anything that
raises here raises on Windows too. (Stop `job-dashboard-api.service` first --
it holds port 8000 and has `Restart=always`.)

## Package manager

`bun` (1.4.2), not npm. `dashboard/bun.lock` is the only lockfile;
`dashboard/package-lock.json` was deleted. Use `bun install`,
`bun add <pkg>`, `bun run <script>`, and `bun run build`. CI uses
`bun install --frozen-lockfile`. bun is not on `PATH` by default -- prefix with
`export PATH="$HOME/.bun/bin:$PATH"`.

## Commands

```bash
export PATH="$HOME/.bun/bin:$PATH"
cd dashboard && bun install && bun run build   # frontend build
cd dashboard && bun run lint                  # oxlint
```

The API is FastAPI (`api/`, entry point in `db.py`); the dashboard is React 19
+ Vite + Tailwind v4 in `dashboard/src`. Dark mode is class-based --
`@variant dark (&:where(.dark, .dark *))` in `dashboard/src/index.css`.
