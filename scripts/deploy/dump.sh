#!/usr/bin/env bash
# Vuelca la base de datos entera a un fichero, listo para llevárselo al servidor.
#
# Por qué volcar y no volver a cargar las capas en producción: la carga con ogr2ogr y
# ST_Subdivide tardó horas y tumbó Postgres cuatro veces por memoria. Transferir el volcado
# es más rápido y, sobre todo, no puede fallar a medias dejando la tabla sin índices.
#
# El formato es -Fc (custom): ya viene comprimido, permite restaurar en paralelo y es
# independiente de la arquitectura, así que un volcado hecho en un Mac ARM restaura sin
# problema en un servidor x86.
#
# Uso:
#   bash scripts/deploy/dump.sh                 # todo, a informefinca-AAAAMMDD.dump
#   bash scripts/deploy/dump.sh --business      # solo lo irreemplazable (copia de seguridad)

set -euo pipefail

DEST="${DUMP_DIR:-.}/informefinca-$(date +%Y%m%d).dump"
BUSINESS_ONLY="${1:-}"

# Las tablas de negocio son las únicas que no se pueden regenerar: las capas se recargan
# desde fuentes públicas con fetch_layers.py, y los PDF se re-renderizan desde reports.payload.
if [ "$BUSINESS_ONLY" = "--business" ]; then
  DEST="${DUMP_DIR:-.}/informefinca-negocio-$(date +%Y%m%d).dump"
  TABLES="-t parcels -t reports -t payments -t alembic_version"
  echo "Volcando solo las tablas irreemplazables → $DEST"
else
  TABLES=""
  echo "Volcando la base completa (incluidos ~9,7 GB de capas) → $DEST"
fi

# shellcheck disable=SC2086 — $TABLES debe expandirse en varios argumentos
docker compose exec -T db pg_dump \
  -U "${POSTGRES_USER:-postgres}" \
  -d "${POSTGRES_DB:-informefinca}" \
  -Fc --no-owner --no-privileges $TABLES > "$DEST"

echo "Hecho: $(du -h "$DEST" | cut -f1)"
