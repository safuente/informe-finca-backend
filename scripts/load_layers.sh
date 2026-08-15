#!/usr/bin/env bash
# Carga las capas de referencia en PostGIS con ogr2ogr.
#
# Las capas son grandes y cambian poco: se cargan una vez y se consultan en cada informe.
# Nunca se piden por petición a los WMS/WFS oficiales — eso haría el informe lento y
# dependiente de la disponibilidad de terceros.
#
# Uso:
#   bash scripts/load_layers.sh data/snczi_t500.shp snczi_t500 "NOMBRE"
#   make load-layers          (todas las de data/, según el mapa de abajo)
#
# Descargas (fuera de este script, son de varios GB):
#   SNCZI  https://sig.mapama.gob.es/snczi/
#   Red Natura / ENP  https://www.miteco.gob.es/es/biodiversidad/servicios/banco-datos-naturaleza/
#   SIGPAC https://www.fega.gob.es/  (fase 2)

set -euo pipefail

SRID=25830
PGCONN="PG:host=${POSTGRES_HOST:-db} port=${POSTGRES_PORT:-5432} dbname=${POSTGRES_DB:-informefinca} user=${POSTGRES_USER:-postgres} password=${POSTGRES_PASSWORD:-postgres}"

load_layer() {
  local source_file="$1" layer_code="$2" name_field="${3:-}"
  local staging="staging_${layer_code}"

  echo "→ Cargando ${source_file} como ${layer_code}"

  ogr2ogr -f PostgreSQL "$PGCONN" "$source_file" \
    -nln "$staging" -overwrite \
    -t_srs "EPSG:${SRID}" \
    -nlt PROMOTE_TO_MULTI \
    -lco GEOMETRY_NAME=geom -lco FID=id -lco PRECISION=NO

  # El staging conserva los campos originales; layer_features los normaliza y guarda el
  # resto en attributes, para poder citar el nombre oficial del espacio en el informe.
  local name_expr="NULL"
  [ -n "$name_field" ] && name_expr="s.\"${name_field}\"::varchar"

  psql "postgresql://${POSTGRES_USER:-postgres}:${POSTGRES_PASSWORD:-postgres}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-informefinca}" <<SQL
DELETE FROM layer_features WHERE layer_code = '${layer_code}';
INSERT INTO layer_features (layer_code, name, attributes, geom, created_at, updated_at)
SELECT '${layer_code}',
       ${name_expr},
       to_jsonb(s) - 'geom' - 'id',
       ST_Multi(ST_MakeValid(s.geom)),
       now(), now()
FROM ${staging} s
WHERE s.geom IS NOT NULL;
DROP TABLE ${staging};
ANALYZE layer_features;
SQL

  echo "✓ ${layer_code} cargada"
}

if [ $# -ge 2 ]; then
  load_layer "$@"
  exit 0
fi

# Sin argumentos: carga lo que haya en data/ con los nombres esperados.
declare -A LAYERS=(
  ["data/snczi_zfp.shp"]="snczi_zfp"
  ["data/snczi_t10.shp"]="snczi_t10"
  ["data/snczi_t100.shp"]="snczi_t100"
  ["data/snczi_t500.shp"]="snczi_t500"
  ["data/natura2000_zepa.shp"]="natura2000_zepa|SITE_NAME"
  ["data/natura2000_zec.shp"]="natura2000_zec|SITE_NAME"
  ["data/enp.shp"]="enp|NOMBRE"
  ["data/montes_up.shp"]="montes_up|NOMBRE"
  ["data/vias_pecuarias.shp"]="vias_pecuarias|NOMBRE"
)

found=0
for source_file in "${!LAYERS[@]}"; do
  [ -f "$source_file" ] || continue
  found=1
  IFS='|' read -r code name_field <<< "${LAYERS[$source_file]}"
  load_layer "$source_file" "$code" "${name_field:-}"
done

if [ "$found" -eq 0 ]; then
  echo "No hay shapefiles en data/. Descarga las capas y vuelve a ejecutar." >&2
  echo "Sin capas cargadas, los informes salen con la salvedad de 'capa no comprobada'." >&2
  exit 1
fi
