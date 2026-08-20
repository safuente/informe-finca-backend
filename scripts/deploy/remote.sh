#!/usr/bin/env bash
# Se ejecuta EN EL SERVIDOR. Lo invoca GitHub Actions por SSH al mergear a main, y sirve
# igual para desplegar a mano el día que CI esté caído:
#
#     ssh root@159.195.115.11 'bash -s' < scripts/deploy/remote.sh
#
# El orden de los pasos no es casual. Se construye primero, se migra la base de datos con el
# código nuevo pero sin que la API nueva esté sirviendo todavía, y solo entonces se recambian
# app y worker. Al revés se abre una ventana —corta, pero real— en la que el código nuevo
# consulta un esquema viejo, y eso aparece como errores 500 sueltos que después no hay manera
# de reproducir.

set -euo pipefail

cd /opt/informefinca

COMPOSE="docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml"

# reset --hard en vez de pull: el servidor es un destino de despliegue, no un sitio donde se
# trabaja. Si alguien tocó un fichero por SSH para depurar y se le olvidó, un pull fallaría
# por conflicto y dejaría el despliegue a medias; así gana siempre lo que hay en main. Los
# ficheros ignorados no se tocan, que es lo que salva a .env.production.
echo "→ Trayendo main…"
git fetch --prune origin
git reset --hard origin/main
git --no-pager log -1 --oneline

echo "→ Construyendo la imagen…"
$COMPOSE build

# Postgres y Redis primero, para poder migrar. `run --rm` levanta un contenedor de usar y
# tirar con el código nuevo, y depends_on espera a que la base de datos esté sana.
echo "→ Aplicando migraciones…"
$COMPOSE up -d db redis
$COMPOSE run --rm app uv run alembic upgrade head

echo "→ Recambiando servicios…"
$COMPOSE up -d --remove-orphans

# Cada construcción deja capas huérfanas. En un disco de 160 GB que ya carga con ~10 GB de
# capas catastrales, eso se come el espacio en pocas semanas sin que nadie lo mire.
echo "→ Limpiando imágenes huérfanas…"
docker image prune -f

$COMPOSE ps
