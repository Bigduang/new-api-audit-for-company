#!/bin/sh
set -eu

cleanup_on_exit() {
    exit_status=$?
    trap - EXIT
    docker exec token-audit python -m token_audit.cli cleanup || {
        cleanup_status=$?
        if [ "$exit_status" -eq 0 ]; then
            exit_status=$cleanup_status
        fi
    }
    exit "$exit_status"
}
trap cleanup_on_exit EXIT

AUDIT_DATE="${1:-$(TZ=Asia/Shanghai date -d yesterday +%F)}"

docker exec token-audit python -m token_audit.cli classify --start "$AUDIT_DATE" --end "$AUDIT_DATE"
docker exec token-audit python -m token_audit.cli summarize-work --start "$AUDIT_DATE" --end "$AUDIT_DATE"
docker exec token-audit python -m token_audit.cli push-wecom --start "$AUDIT_DATE" --end "$AUDIT_DATE"
