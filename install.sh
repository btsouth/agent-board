#!/usr/bin/env bash
# Pin the bootstrap to the release whose setup contract this script knows.
set -euo pipefail
VERSION=v0.2.6
REPOSITORY=https://github.com/btsouth/agent-board.git

fail() { printf 'AgentBoard setup: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help ]]; then
  printf '%s\n' 'Install Agent Board and its background bridge for your Linux login.' \
    'Requires T3 Code and/or Hermes, Python 3.10+, git and a systemd user session.' \
    'T3 Code also requires Node.js 24 or newer.' \
    'Usage: bash install.sh [--hermes-home PATH] [--addon-dir PATH] [--yes]'
  exit 0
fi
[[ $(uname -s) == Linux ]] || fail 'Automatic setup currently supports Linux only.'
for program in git python3 systemctl; do
  command -v "$program" >/dev/null || fail "Install $program, then run setup again."
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail 'Python 3.10 or newer is required.'
base="${XDG_DATA_HOME:-$HOME/.local/share}/agent-board"
[[ $base == /* ]] || fail 'XDG_DATA_HOME must be an absolute path.'
app="$base/app"
[[ ! -L $app ]] || fail "The managed app path is a symlink: $app. Leave it intact and choose a separate XDG_DATA_HOME."
mkdir -p "$base"
# Only one bootstrap may claim the managed directory. A failed download never
# becomes an installed checkout, and cleanup is limited to this invocation.
lock="$base/.bootstrap-lock"
mkdir "$lock" 2>/dev/null || fail "Another setup is running. If it exited unexpectedly, remove $lock and retry."
work=''
cleanup() { [[ -z $work ]] || rm -rf -- "$work"; rmdir "$lock" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 130' INT TERM

if [[ -e $app ]]; then
  [[ -d $app/.git && -x $app/bin/agent-board ]] || fail "An unrelated directory already exists at $app; it was left untouched."
  origin=$(git -C "$app" remote get-url origin)
  [[ $origin == "$REPOSITORY" ]] || fail "The existing checkout has a different origin; it was left untouched."
  printf 'Using your existing AgentBoard installation. Run agent-board update to update it.\n'
else
  printf 'Downloading AgentBoard %s...\n' "$VERSION"
  work=$(mktemp -d "$base/.download.XXXXXX")
  git clone --quiet --depth 1 --branch "$VERSION" "$REPOSITORY" "$work/app" || fail 'Download failed. Check your connection and run setup again.'
  [[ -x $work/app/bin/agent-board ]] || fail 'The downloaded release is missing its launcher.'
  mv -- "$work/app" "$app"
fi

# A curl pipe owns stdin. Questions must still go to the person's terminal;
# headless installs instead use explicit paths and --yes.
if [[ ! -t 0 ]] && { true </dev/tty; } 2>/dev/null; then
  "$app/bin/agent-board" setup "$@" </dev/tty
else
  "$app/bin/agent-board" setup "$@"
fi
