#!/usr/bin/env bash

CONFIG_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$CONFIG_ENV_DIR/../.." && pwd)"
REPO_NAME="$(basename "$REPO_ROOT")"
export REPO_ROOT REPO_NAME

set -a
# shellcheck source=/dev/null
source "$CONFIG_ENV_DIR/my.env"
set +a

if [[ -n "${BASH_VERSION:-}" && -n "${PS1:-}" ]]; then
  PS1='\[\033[31m\]['"$REPO_NAME"'] \u@\h > \[\033[0m\] '
fi
