#!/usr/bin/env bash
# Restaura un volcado en el servidor. Se ejecuta EN EL SERVIDOR, con el stack levantado.
#
#   bash scripts/deploy/restore.sh informefinca-20260820.dump
#
# --no-owner y --no-privileges en el volcado evitan que la restauración falle por roles que
# no existen en la máquina de destino.

set -euo pipefail

DUMP="${1:?Falta el fichero de volcado}"
[ -f "$DUMP" ] || { echo "No existe $DUMP" >&2; exit 1; }

COMPOSE="docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml"

echo "Restaurando $DUMP ($(du -h "$DUMP" | cut -f1))…"

# -j4: pg_restore paraleliza la carga de datos y la creación de índices. Los índices GiST
# sobre 2 millones de geometrías son la parte lenta, y es la que más se beneficia.
$COMPOSE exec -T db pg_restore \
  -U "${POSTGRES_USER:-postgres}" \
  -d "${POSTGRES_DB:-informefinca}" \
  --no-owner --no-privileges --clean --if-exists -j4 < "$DUMP"

echo
echo "Comprobación:"
$COMPOSE exec -T db psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-informefinca}" -c \
  "select layer_code, count(*) from layer_features group by 1 order by 2 desc;"
