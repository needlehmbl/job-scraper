#!/usr/bin/env bash
# Package the Linux/macOS release tarball.
#
# Usage: ./installer/make-tarball.sh jobscraper-linux.tar.gz
#
# The file list is derived from `git ls-files`, not written out by hand. The
# hand-written list that used to live in release.yml named only the shell
# scripts and the built dashboard, so every release since v0.2.0 shipped
# without api/ or requirements.txt and `docker compose build` could not work on
# a clean machine. Deriving the list from git means a new runtime file ships by
# default and the only way to leave something out is to add it to the exclude
# list below, which is also where a reviewer will look.
set -euo pipefail

out=${1:?usage: make-tarball.sh OUTPUT.tar.gz}
cd "$(dirname "$0")/.."

# Build-time, docs, Windows-only, and dev-only trees. dashboard/ is excluded
# piecemeal because only its build output ships -- see the add below.
excludes=(
  ':(exclude).github'        # CI workflows
  ':(exclude)docs'           # internal design notes
  ':(exclude).archive-tailoring'
  ':(exclude)demo'           # ~5 MB of screen recordings
  ':(exclude)installer'      # Windows build inputs (this script ships nowhere)
  ':(exclude)tests'          # dev-only
  ':(exclude)conftest.py'
  ':(exclude)requirements-dev.txt'
  ':(exclude)dashboard/src'
  ':(exclude)dashboard/public'
  ':(exclude)dashboard/index.html'
  ':(exclude)dashboard/vite.config.js'
  ':(exclude)dashboard/package.json'
  ':(exclude)dashboard/bun.lock'
  ':(exclude)dashboard/.oxlintrc.json'
  ':(exclude)dashboard/.gitignore'
  ':(exclude)dashboard/README.md'
)

list=$(mktemp)
trap 'rm -f "$list"' EXIT

git ls-files -z -- . "${excludes[@]}" > "$list"

# dashboard/dist is the one thing that is not tracked: it is gitignored and
# produced by the `bun run build` step that runs just before this script.
[ -f dashboard/dist/index.html ] || {
  echo "make-tarball: dashboard/dist/index.html missing -- run 'bun run build' first" >&2
  exit 1
}
printf 'dashboard/dist\0' >> "$list"

tar -czf "$out" --null --files-from="$list"

# Guard the bug this script exists to prevent: everything the shipped
# Dockerfile and docker-compose.yml read at build or run time has to be in the
# archive. Dockerfile does `COPY requirements.txt .`, `COPY . .`, and
# `CMD uvicorn api.main:app`; compose bind-mounts ./dashboard/dist.
shipped=$(tar -tzf "$out")
missing=0
for required in \
  requirements.txt Dockerfile docker-compose.yml .dockerignore .env.example \
  api/main.py db.py main.py notifier.py config.yaml schema.sql \
  run.sh stop.sh setup.sh setup_wizard.py launcher.py JobScraper.desktop \
  README.md dashboard/dist/index.html
do
  if ! grep -qxF "$required" <<<"$shipped"; then
    echo "make-tarball: MISSING from $out: $required" >&2
    missing=1
  fi
done
[ "$missing" -eq 0 ] || exit 1

echo "make-tarball: $out has $(tr -dc '\0' <"$list" | wc -c) entries, all required paths present"
