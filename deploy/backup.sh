#!/usr/bin/env bash
# Daily backup of the ClearPay database and the uploaded invoices. Keeps 14 days.
# Schedule it:  crontab -e   then add:   0 2 * * * /home/clearpay/clearpay/deploy/backup.sh
set -euo pipefail
APP=/home/clearpay/clearpay
OUT=/home/clearpay/backups
STAMP=$(date +%Y%m%d-%H%M)
mkdir -p "$OUT"
sqlite3 "$APP/data/ap_agent.db" ".backup '$OUT/ap_agent-$STAMP.db'"
tar -czf "$OUT/uploads-$STAMP.tar.gz" -C "$APP/data" uploads
find "$OUT" -type f -mtime +14 -delete
echo "Backup written to $OUT ($STAMP)"
