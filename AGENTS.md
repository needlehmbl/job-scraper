# AGENTS.md

## Branch model

`dev` is the working branch. `main` is the release branch.

- Do all work on `dev`. Commit and push `dev` as often as you like -- pushes to
  `dev` run no CI and produce no release.
- Merge `dev` into `main` **only when the change should reach users**. That
  merge is the release.

```bash
git checkout dev      # normal working branch
# ... work, commit, push dev ...
git checkout main && git merge dev && git push
```

`main` must never be committed to directly. If a task explicitly says the
change belongs on `main`, follow that instruction instead.

## Releases

Every push to `main` releases automatically, via
`.github/workflows/auto-release.yml`:

1. `auto-release` reads the newest tag, bumps it, and pushes an annotated tag
   at the `main` HEAD. Patch by default; a `major:` or `minor:` prefix on the
   HEAD commit subject bumps that level instead.
2. The tag push trips `release.yml` (`on: push: tags: v*`), which builds the
   Linux tarball, the macOS tarball, and the Windows installer + portable zip
   with checksums, and publishes them as a GitHub Release.
3. The release body is generated from every commit since the previous release,
   so the commit messages written on `dev` become the changelog.

Consequences worth remembering before merging:

- Each `main` push burns a full 3-platform build; the Windows job (PyInstaller
  + Playwright Chromium + Inno Setup) is the slow one.
- Each release is a fresh download for every user, so merge `dev` -> `main` on
  a cadence that matches "something changed about what users get" -- not per
  commit.

Manual tagging still works if a release is ever needed without a merge:
`git tag vX.Y.Z && git push origin vX.Y.Z`.

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
