#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p /app/bin /app/out
cp "$SCRIPT_DIR/gates.py" /app/bin/gates.py
cat > /app/bin/worker.sh <<'WK'
#!/bin/bash
set -euo pipefail
DB="${1:-/app/data/queue.db}"
OUTDIR="${2:-/app/out}"
python /app/bin/gates.py evidence --db "$DB" --outdir "$OUTDIR"
WK
chmod +x /app/bin/worker.sh
bash /app/bin/worker.sh /app/data/queue.db /app/out
