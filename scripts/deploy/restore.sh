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

# El volcado se copia DENTRO del contenedor en vez de mandarlo por la entrada estándar.
# pg_restore en paralelo necesita poder saltar por el fichero, y un flujo por stdin no se
# puede rebobinar: falla con «parallel restore from standard input is not supported».
# Se podría quitar el -j4 y restaurar en serie, pero sale mucho más caro que los minutos que
# cuesta la copia: reconstruir los índices GiST sobre dos millones de geometrías es la parte
# lenta de todo esto, y es precisamente la que se reparte entre núcleos.
REMOTE_DUMP="/tmp/$(basename "$DUMP")"
cleanup() { $COMPOSE exec -T db rm -f "$REMOTE_DUMP" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "Copiando al contenedor…"
$COMPOSE cp "$DUMP" "db:$REMOTE_DUMP"

# -j4: pg_restore paraleliza la carga de datos y la creación de índices.
$COMPOSE exec -T db pg_restore \
  -U "${POSTGRES_USER:-postgres}" \
  -d "${POSTGRES_DB:-informefinca}" \
  --no-owner --no-privileges --clean --if-exists -j4 \
  "$REMOTE_DUMP"

echo
echo "Comprobación:"
$COMPOSE exec -T db psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-informefinca}" -c \
  "select layer_code, count(*) from layer_features group by 1 order by 2 desc;"
