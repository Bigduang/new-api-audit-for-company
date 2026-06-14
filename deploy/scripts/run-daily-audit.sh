#!/bin/sh
set -eu

AUDIT_DATE="${1:-$(TZ=Asia/Shanghai date -d yesterday +%F)}"

docker exec token-audit python -m token_audit.cli classify --start "$AUDIT_DATE" --end "$AUDIT_DATE"
docker exec token-audit python -m token_audit.cli summarize-work --start "$AUDIT_DATE" --end "$AUDIT_DATE"
docker exec token-audit python -m token_audit.cli push-wecom --start "$AUDIT_DATE" --end "$AUDIT_DATE"
docker exec token-audit python -m token_audit.cli cleanup
