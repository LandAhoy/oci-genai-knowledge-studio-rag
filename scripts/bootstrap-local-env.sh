#!/usr/bin/env sh
set -eu

if [ -e docker/.env ]; then
  echo 'docker/.env already exists; refusing to overwrite it.' >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path
import secrets

template = Path('docker/.env.example').read_text()
for name in (
    'ELASTIC_PASSWORD',
    'OPENSEARCH_PASSWORD',
    'MYSQL_PASSWORD',
    'MINIO_PASSWORD',
    'REDIS_PASSWORD',
    'SERENEDB_PASSWORD',
):
    template = template.replace(f'{name}=CHANGE_ME', f'{name}={secrets.token_urlsafe(32)}')
Path('docker/.env').write_text(template)
PY

chmod 600 docker/.env
echo 'Created docker/.env with locally generated service passwords.'
