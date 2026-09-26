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

1. `auto-release` reads the newest tag, bumps it, and pushes an annotated tag
   at the `main` HEAD. Patch by default; a `major:` or `minor:` prefix on the
   HEAD commit subject bumps that level instead. It sets a committer identity
   first -- `actions/checkout` does not provide one, and `git tag -a` needs it.
2. It then dispatches `release.yml` (`workflow_dispatch`, input `tag`) with that
   tag. The dispatch is not optional: a ref pushed with `GITHUB_TOKEN` does not
   trigger other workflows, so the tag push alone never starts `release.yml`.
3. `release.yml` builds the Linux tarball, the macOS tarball, and the Windows
   installer + portable zip with checksums, and publishes them as a GitHub
   Release. Manual tag pushes (`on: push: tags: v*`) still work the same way.
4. The release body is generated from every commit since the previous release,
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
