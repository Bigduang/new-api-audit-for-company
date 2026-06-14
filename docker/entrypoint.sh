#!/bin/sh
set -eu

python -m token_audit.cli migrate
exec "$@"
