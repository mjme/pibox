#!/usr/bin/env bash
# Installs pibox. Either:
#   curl -fsSL https://raw.githubusercontent.com/mjme/pibox/main/install.sh | bash
# or, from a clone of the repo:
#   bash install.sh
set -euo pipefail

REPO="mjme/pibox"
BRANCH="main"
DEST="$HOME/.local/share/pibox"
BIN="$HOME/.local/bin"
FILES=(pibox Dockerfile pibox-init.py pibox-proxy.py pibox-entry)
tmp=""

ok()   { printf '\e[32m✓\e[0m %s\n' "$*"; }
warn() { printf '\e[33m!\e[0m %s\n' "$*"; }
die()  { printf '\e[31m✗\e[0m %s\n' "$*"; exit 1; }

# Everything runs inside main(), called on the last line, so a half-downloaded
# script piped into bash does nothing at all.
main() {
  # 1. Get the files: from this folder if run from a clone, otherwise from GitHub.
  local src here=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  if [ -n "$here" ] && [ -f "$here/pibox" ]; then
    src="$here"
  else
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" \
      | tar -xz -C "$tmp" --strip-components=1 \
      || die "Couldn't download pibox from github.com/$REPO."
    src="$tmp"
  fi

  local updating=""; [ -f "$DEST/pibox" ] && updating=1
  mkdir -p "$DEST" "$BIN"
  for f in "${FILES[@]}"; do cp "$src/$f" "$DEST/"; done
  chmod +x "$DEST/pibox" "$DEST/pibox-entry"
  ln -sf "$DEST/pibox" "$BIN/pibox"
  if [ -n "$updating" ]; then ok "Updated pibox"; else ok "Installed pibox"; fi

  # 2. Put ~/.local/bin on the PATH for zsh (macOS default) and bash.
  # shellcheck disable=SC2016  # $HOME/$PATH are meant literally here
  local line='export PATH="$HOME/.local/bin:$PATH"' rc
  for rc in "$HOME/.zshrc" "$HOME/.bash_profile"; do
    if [ "$rc" = "$HOME/.zshrc" ] || [ -f "$rc" ]; then
      touch "$rc"
      grep -qsF "$line" "$rc" || printf '\n# pibox\n%s\n' "$line" >> "$rc"
    fi
  done

  # 3. Check the two prerequisites, and build the box if Docker is ready.
  if ! command -v docker >/dev/null; then
    warn "You still need Docker Desktop: https://www.docker.com/products/docker-desktop/"
  elif ! docker info >/dev/null 2>&1; then
    warn "Docker Desktop isn't open. Open it, then run: pibox rebuild"
  else
    ok "Building the box (takes a few minutes the first time)…"
    "$BIN/pibox" rebuild
  fi
  if ! command -v ollama >/dev/null; then
    warn "You still need Ollama: brew install ollama && brew services start ollama"
  fi

  echo
  if [ -n "$updating" ]; then
    ok "Done! Open pi sessions switch to the new version once you quit and restart them."
  else
    ok "Done! Open a new Terminal window, go to a project folder, and type: pibox"
  fi
}

main "$@"
