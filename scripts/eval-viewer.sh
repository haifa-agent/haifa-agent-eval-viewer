#!/usr/bin/env sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec uv run --project "$repository_root" haifa-eval-viewer "$@"
