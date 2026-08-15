#!/usr/bin/env python
"""Descarga y carga en PostGIS las capas de referencia de toda España.

Sustituye al load_layers.sh manual para las capas que el MITECO publica a nivel nacional.
Las que no lo son (ZFP por demarcación, montes de UP por comunidad autónoma) siguen
cargándose con load_layers.sh a partir de ficheros descargados a mano.

    python scripts/fetch_layers.py --plan            # qué se va a bajar y cuánto ocupa
    python scripts/fetch_layers.py --urls            # URLs para bajarlos a mano
    python scripts/fetch_layers.py --skip-download   # cargar los zip que ya están en data/
    python scripts/fetch_layers.py --only natura2000_zepa natura2000_zec enp
    python scripts/fetch_layers.py --list rn2000.zip # qué shapefiles trae un zip ya bajado

Requiere ogr2ogr (gdal-bin, ya en la imagen) y las variables POSTGRES_* del entorno.

LA DESCARGA AUTOMÁTICA NO FUNCIONA, y no por un fallo nuestro: desde 2025 el MITECO sirve
esos ficheros tras una página con captcha Altcha (proof-of-work) y token antiforgery, que
es justamente una medida para impedir la descarga automatizada. El servicio ATOM de
INSPIRE —el canal pensado para máquinas— responde 502 (comprobado agosto 2026), y no hay
WFS: wfs.mapama.gob.es ni siquiera resuelve.

Así que el camino bueno es --urls + navegador + --skip-download. No es un apaño: estas
capas se actualizan una o dos veces al año, así que son seis descargas manuales anuales.
Si el ATOM vuelve, ese es el sitio por donde automatizarlo.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.catalog import CATALOG, DOWNLOADABLE, MANUAL_ONLY, LayerSpec  # noqa: E402
from app.shared.geo import SRID_STORAGE  # noqa: E402

DATA_DIR = Path(os.getenv("LAYERS_DATA_DIR", "data"))
USER_AGENT = "informefinca.es/1.0 (+https://informefinca.es)"
# Big flood polygons make a GiST index useless: one feature can cover a whole basin, so
# its bounding box matches every query. Subdividing bounds each piece tightly.
SUBDIVIDE_VERTICES = 512
# Vértices por sentencia. Repartir por número de filas no vale: una lámina del SNCZI puede
# traer un polígono de 5,9 millones de vértices junto a otros de mil, y ese solo basta para
# que ST_Subdivide agote la memoria del contenedor. El lote se cierra por vértices
# acumulados, así que los monstruos acaban solos en el suyo.
VERTEX_BUDGET = 400_000
# Por encima de esto la geometría se guarda entera, sin trocear: ST_Subdivide sobre un
# polígono de 5,9 millones de vértices no cabe en 3,8 GB ni estando solo en su lote. Son 35
# de 5.115 en la lámina T=100, así que el coste en consultas es acotado — pero si el
# contenedor tuviera más memoria, lo suyo sería trocearlas también.
OVERSIZE_VERTICES = 400_000
# Celdas por lado al recortar las geometrías descomunales.
GRID = 8


@dataclass(slots=True)
class DbConfig:
    host: str = os.getenv("POSTGRES_HOST", "db")
    port: str = os.getenv("POSTGRES_PORT", "5432")
    name: str = os.getenv("POSTGRES_DB", "informefinca")
    user: str = os.getenv("POSTGRES_USER", "postgres")
    password: str = os.getenv("POSTGRES_PASSWORD", "postgres")

    @property
    def ogr(self) -> str:
        return (
            f"PG:host={self.host} port={self.port} dbname={self.name} "
            f"user={self.user} password={self.password}"
        )

    @property
    def uri(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


def human_mb(value: float) -> str:
    return f"{value:,.0f} MB".replace(",", ".")


def _by_file(specs) -> dict[str, list]:
    """Agrupa por fichero: rn2000 sirve dos capas y sus bytes se cuentan una vez."""
    grouped: dict[str, list] = {}
    for spec in specs:
        grouped.setdefault(spec.download.filename, []).append(spec)
    return grouped


def plan() -> None:
    automatic = _by_file(s for s in DOWNLOADABLE if not s.download.gated)
    gated = _by_file(s for s in DOWNLOADABLE if s.download.gated)

    print("Descarga automática (ficheros estáticos del BDN, sin captcha):")
    for filename, specs in automatic.items():
        codes = ", ".join(spec.code for spec in specs)
        print(f"  {filename:<20} {human_mb(specs[0].download.approx_mb):>10}   {codes}")
    total_auto = sum(specs[0].download.approx_mb for specs in automatic.values())
    print(f"  → {human_mb(total_auto)}, los baja `fetch_layers.py` solo\n")

    print("Descarga a mano (el MITECO las sirve tras un captcha Altcha):")
    for filename, specs in gated.items():
        codes = ", ".join(spec.code for spec in specs)
        print(f"  {filename:<20} {human_mb(specs[0].download.approx_mb):>10}   {codes}")
    total_gated = sum(specs[0].download.approx_mb for specs in gated.values())
    print(f"  → {human_mb(total_gated)}, con `--urls` y el navegador\n")

    print("Sin capa nacional (se cargan por comunidad o demarcación):")
    for spec in MANUAL_ONLY:
        print(f"  {spec.code:<18} {spec.source}")
    print("\n  Se cargan con: bash scripts/load_layers.sh <fichero.shp> <codigo> [campo_nombre]")


def urls() -> None:
    """Solo las que hay que bajar a mano; el resto se descargan solas."""
    gated = _by_file(s for s in DOWNLOADABLE if s.download.gated)
    if not gated:
        print("Nada que bajar a mano.")
        return
    print(f"Abre cada una, pulsa «Descargar fichero» y guarda el zip en {DATA_DIR}/")
    print("con EXACTAMENTE el nombre de la primera columna:\n")
    for filename, specs in gated.items():
        print(f"  {filename:<22} {specs[0].download.url}")
    print("\nEl resto se descargan solas. Luego:  python scripts/fetch_layers.py")


class DownloadGated(SystemExit):
    """The endpoint answered with the captcha page instead of the file."""

    def __init__(self, spec: LayerSpec) -> None:
        super().__init__(
            f"\n  ✗ {spec.code}: el MITECO ha devuelto la página del captcha, no el fichero.\n"
            f"    Es intencionado por su parte (Altcha, proof-of-work) y no se automatiza.\n\n"
            f"    Ábrelo en el navegador, pulsa «Descargar fichero» y deja el zip en {DATA_DIR}/:\n"
            f"      {spec.download.url}\n\n"
            f"    Después:  python scripts/fetch_layers.py --skip-download\n"
        )


def download(spec: LayerSpec, force: bool = False) -> Path:
    destination = DATA_DIR / spec.download.filename
    if destination.exists() and not force:
        size = destination.stat().st_size / 1_048_576
        print(f"  ya descargado: {destination} ({human_mb(size)})")
        return destination

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    print(f"  descargando {spec.download.url} (~{human_mb(spec.download.approx_mb)})")

    request = Request(spec.download.url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=120) as response:
        # The gate answers 200 with an HTML page. Without this check the "zip" would be
        # 3 KB of HTML and the failure would surface much later, as a corrupt archive.
        if "html" in (response.headers.get("Content-Type") or ""):
            raise DownloadGated(spec)
        with partial.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)

    with partial.open("rb") as handle:  # 2 bytes, not the whole 1,2 GB
        is_zip = handle.read(2) == b"PK"
    if not is_zip:
        partial.unlink()
        raise DownloadGated(spec)

    partial.rename(destination)
    print(f"  guardado en {destination} ({human_mb(destination.stat().st_size / 1_048_576)})")
    return destination


def list_shapefiles(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        return sorted(name for name in zf.namelist() if name.lower().endswith(".shp"))


def resolve_shapefiles(archive: Path, pattern: str) -> list[str]:
    """Rutas /vsizip/ de todos los .shp del archivo que casan con el patrón.

    Sin descomprimir: GDAL lee dentro del zip, y las láminas del SNCZI ocupan el doble
    descomprimidas que en el zip — descomprimir las tres cuesta unos 7 GB de disco y un
    buen rato, para nada.

    Devuelve varios, no uno: Red Natura reparte el mismo conjunto en dos ficheros por
    territorio (Península+Baleares y Canarias), y quedarse con el primero perdería medio
    país en silencio.
    """
    inner = list_shapefiles(archive)
    matching = sorted(name for name in inner if re.match(pattern, Path(name).name))
    if not matching:
        available = "\n    ".join(inner) or "(ninguno)"
        raise SystemExit(
            f"Ningún shapefile de {archive.name} casa con {pattern!r}.\n"
            f"  Disponibles:\n    {available}\n"
            f"  Ajusta shapefile_match en app/layers/catalog.py."
        )
    return [f"/vsizip/{archive}/{name}" for name in matching]


# Field holding the official name of the feature, when the configured one is not there.
# The report quotes it ("el espacio más próximo, «Montes Aquilanos»"), so it is worth
# looking for — but never worth failing the load over.
NAME_FALLBACKS = ("nombre", "site_name", "sitename", "name", "denominaci", "descripcio")


def query(db: DbConfig, sql: str) -> list[str]:
    result = subprocess.run(
        ["psql", db.uri, "-tAc", sql], check=True, capture_output=True, text=True
    )
    return [line for line in result.stdout.splitlines() if line]


def assign_batches(staging: str, db: DbConfig) -> int:
    """Reparte las filas en lotes de ~VERTEX_BUDGET vértices y devuelve cuántos hay.

    La suma acumulada va por `id` y resta los vértices de la propia fila, de modo que una
    geometría más grande que el presupuesto se lleva su lote entera para ella sola.
    """
    query(db, f"ALTER TABLE {staging} ADD COLUMN IF NOT EXISTS _batch integer")
    query(
        db,
        f"""
        UPDATE {staging} s SET _batch = t.batch FROM (
            SELECT id,
                   floor((sum(ST_NPoints(geom)) OVER (ORDER BY id) - ST_NPoints(geom))
                         / {VERTEX_BUDGET}::float)::int AS batch
            FROM {staging} WHERE geom IS NOT NULL
        ) t WHERE s.id = t.id
        """,
    )
    total = query(db, f"SELECT coalesce(max(_batch), 0) + 1 FROM {staging}")[0]
    return int(total)


def staging_columns(staging: str, db: DbConfig) -> list[str]:
    return query(
        db,
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_name = '{staging}' AND table_schema = 'public' ORDER BY ordinal_position",
    )


def attributes_expr(staging: str, db: DbConfig) -> str:
    """JSONB con los atributos originales, nombrando las columnas una a una.

    Nunca `to_jsonb(s) - 'geom'`: eso serializa la fila entera —geometría incluida— a
    JSON antes de descartar la clave, y un polígono de 337.000 vértices se convierte en
    megabytes de WKB hexadecimal escapado. Con las láminas del SNCZI eso tumbó el
    contenedor de Postgres por falta de memoria (OOM, señal 9).
    """
    columns = [c for c in staging_columns(staging, db) if c not in {"geom", "id", "_batch"}]
    if not columns:
        return "'{}'::jsonb"
    pairs = ", ".join(f"'{column}', s.\"{column}\"" for column in columns)
    return f"jsonb_build_object({pairs})"


def resolve_name_column(spec: LayerSpec, staging: str, db: DbConfig) -> str:
    """SQL expression for layer_features.name, resolved against the real staging table.

    Two traps, both found the hard way: ogr2ogr lowercases column names (NOMBRE → nombre),
    and the field names in this catalog are guesses until someone has opened the archive.
    So the column is looked up case-insensitively, common alternatives are tried, and a
    miss degrades to NULL with a loud warning instead of aborting a multi-GB load.
    """
    lookup = {column.lower(): column for column in staging_columns(staging, db)}

    configured = (spec.download.name_field or "").lower()
    for candidate in (configured, *NAME_FALLBACKS):
        if candidate and candidate in lookup:
            actual = lookup[candidate]
            if configured and actual.lower() != configured:
                print(
                    f'  aviso: no existe el campo "{spec.download.name_field}"; '
                    f'uso "{actual}" en su lugar'
                )
            return f's."{actual}"::varchar'

    print(
        f"  aviso: sin campo de nombre utilizable en {staging}; name quedará NULL.\n"
        f"    Columnas disponibles: {', '.join(sorted(lookup)) or '(ninguna)'}\n"
        f"    Ajusta name_field en app/layers/catalog.py si alguna sirve."
    )
    return "NULL"


# Índices GiST de layer_features, tal y como los crea la migración 0001. Se sueltan
# durante la carga masiva y se reconstruyen al final: medido sobre las láminas del SNCZI,
# mantenerlos fila a fila cuesta 83,6 s contra 51,3 s sin ellos, y reconstruirlos en
# bloque tarda 1,3 s. La definición vive aquí duplicada a propósito: si cambia en la
# migración, este script debe fallar de forma evidente y no recrear un índice distinto.
GIST_INDEXES = {
    "ix_layer_features_geom": (
        "CREATE INDEX ix_layer_features_geom ON layer_features USING gist (geom)"
    ),
    "ix_layer_features_code_geom": (
        "CREATE INDEX ix_layer_features_code_geom ON layer_features USING gist (layer_code, geom)"
    ),
}


@contextmanager
def indexes_dropped(db: DbConfig):
    """Suelta los índices GiST mientras dura la carga y los reconstruye después.

    Si algo revienta a mitad, el `finally` los deja como estaban: una tabla sin índice
    espacial no da error, solo consultas lentísimas, que es la peor forma de romperse.
    """
    print("· soltando índices GiST para la carga")
    for name in GIST_INDEXES:
        query(db, f"DROP INDEX IF EXISTS {name}")
    try:
        yield
    finally:
        print("· reconstruyendo índices GiST")
        for statement in GIST_INDEXES.values():
            query(db, statement)
        query(db, "ANALYZE layer_features")


def insert_sql(
    spec: LayerSpec,
    staging: str,
    name_expr: str,
    attrs_expr: str,
    geom_expr: str,
    extra: str,
    batches: int,
    shard: int,
    oversize: str = "all",
) -> str:
    """INSERT de un trozo del staging.

    El reparto por resto del id sirve para dos cosas: repartir el trabajo entre varias
    sesiones (PostgreSQL no paraleliza un `INSERT ... SELECT`) y, sobre todo, acotar la
    memoria — cada sentencia libera al terminar, en vez de acumular sobre las 5.000
    geometrías de una lámina entera. Con 4 GB de contenedor eso era la diferencia entre
    cargar y morir por OOM.

    ST_Force2D: la RGVP viene en 3D y la columna es 2D. Además la Z estorba —
    ST_Length sobre una línea 3D mide la longitud inclinada, no la que cruza la parcela.
    """
    shard_filter = f"AND s._batch = {shard}" if batches > 1 else ""
    size_filter = (
        f"AND ST_NPoints(s.geom) <= {OVERSIZE_VERTICES}"
        if oversize == "skip"
        else f"AND ST_NPoints(s.geom) > {OVERSIZE_VERTICES}"
        if oversize == "only"
        else ""
    )
    return f"""
        INSERT INTO layer_features (layer_code, name, attributes, geom, created_at, updated_at)
        SELECT '{spec.code}',
               {name_expr},
               {attrs_expr},
               {geom_expr},
               now(), now()
        FROM {staging} s
        WHERE s.geom IS NOT NULL AND NOT ST_IsEmpty(s.geom) {extra} {shard_filter} {size_filter};
    """


def insert_oversized(
    spec: LayerSpec,
    staging: str,
    name_expr: str,
    attrs_expr: str,
    extra: str,
    db: DbConfig,
) -> None:
    """Mete las geometrías descomunales recortándolas contra una rejilla.

    Ni troceadas con ST_Subdivide ni enteras caben: sobre 5,9 millones de vértices hasta
    `ST_IsValid` agota los 3,8 GB del contenedor, porque construye la topología completa.
    `ST_ClipByBox2D` no la construye —es un recorte de coordenadas— así que trocea barato,
    y sobre cada celda ya sí se puede validar.

    Una sentencia por celda: el objetivo es acotar la memoria, no ir rápido. Son un puñado
    de geometrías en toda España.
    """
    oversized = query(
        db,
        f"SELECT id FROM {staging} WHERE geom IS NOT NULL "
        f"AND ST_NPoints(geom) > {OVERSIZE_VERTICES} ORDER BY ST_NPoints(geom)",
    )
    if not oversized:
        return
    print(f"  {len(oversized)} geometrías enormes: recortadas por rejilla {GRID}x{GRID}")

    for row_id in oversized:
        for cell in range(GRID * GRID):
            column, row = cell % GRID, cell // GRID
            sql = f"""
                WITH src AS (
                    SELECT * FROM {staging} WHERE id = {row_id}
                ), box AS (
                    SELECT ST_MakeEnvelope(
                        ST_XMin(g) + (ST_XMax(g) - ST_XMin(g)) * {column} / {GRID}.0,
                        ST_YMin(g) + (ST_YMax(g) - ST_YMin(g)) * {row} / {GRID}.0,
                        ST_XMin(g) + (ST_XMax(g) - ST_XMin(g)) * {column + 1} / {GRID}.0,
                        ST_YMin(g) + (ST_YMax(g) - ST_YMin(g)) * {row + 1} / {GRID}.0,
                        {SRID_STORAGE}
                    ) AS b
                    FROM (SELECT ST_Envelope(geom) AS g FROM src) e
                )
                INSERT INTO layer_features
                    (layer_code, name, attributes, geom, created_at, updated_at)
                SELECT '{spec.code}', {name_expr}, {attrs_expr}, piece, now(), now()
                FROM src s, box,
                     LATERAL (SELECT ST_MakeValid(
                         ST_ClipByBox2D(ST_Force2D(s.geom), box.b)
                     ) AS piece) c
                WHERE piece IS NOT NULL AND NOT ST_IsEmpty(piece) {extra};
            """
            subprocess.run(["psql", db.uri, "-q", "-v", "ON_ERROR_STOP=1", "-c", sql], check=True)


def load(
    spec: LayerSpec,
    shapefiles: list[str],
    db: DbConfig,
    jobs: int = 1,
    batches: int = 16,
    subdivide: bool = True,
) -> None:
    # El borrado va una vez, fuera del bucle: si no, cada shapefile borraría lo que
    # acaba de cargar el anterior y solo sobreviviría el último territorio.
    query(db, f"DELETE FROM layer_features WHERE layer_code = '{spec.code}'")

    for index, shapefile in enumerate(shapefiles):
        staging = f"staging_{spec.code}_{index}"
        print(f"  ogr2ogr {shapefile.rsplit('/', 1)[-1]} → {staging}")
        subprocess.run(
            [
                "ogr2ogr",
                "-f",
                "PostgreSQL",
                db.ogr,
                shapefile,
                "-nln",
                staging,
                "-overwrite",
                "-t_srs",
                f"EPSG:{SRID_STORAGE}",
                "-nlt",
                "PROMOTE_TO_MULTI",
                "-lco",
                "GEOMETRY_NAME=geom",
                "-lco",
                "FID=id",
                "-lco",
                "PRECISION=NO",
                "-gt",
                "20000",
            ],
            check=True,
        )

        if batches > 1:
            batches = assign_batches(staging, db)
        name_expr = resolve_name_column(spec, staging, db)
        attrs_expr = attributes_expr(staging, db)
        extra = f"AND ({spec.download.where})" if spec.download.where else ""
        # ST_MakeValid solo sobre las inválidas: es la operación que dispara la memoria
        # (construye una topología nodada) y en el SNCZI solo el 13% lo necesita. Correrla
        # sobre las 4.966 tumbaba el contenedor por OOM.
        base_geom = "ST_Force2D(s.geom)"
        geom_expr = (
            f"CASE WHEN ST_IsValid({base_geom}) THEN {base_geom} ELSE ST_MakeValid({base_geom}) END"
        )
        if spec.download.subdivide and subdivide:
            geom_expr = f"ST_Subdivide({geom_expr}, {SUBDIVIDE_VERTICES})"

        troceando = spec.download.subdivide and subdivide
        troceo = (
            f", ST_Subdivide {SUBDIVIDE_VERTICES}"
            if (spec.download.subdivide and subdivide)
            else ""
        )
        paralelo = f", {batches} lotes" + (f" x {jobs} procesos" if jobs > 1 else "")
        print(f"  normalizando{troceo}{paralelo}{' ' + extra if extra else ''}")

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(
                    subprocess.run,
                    [
                        "psql",
                        db.uri,
                        "-q",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-c",
                        insert_sql(
                            spec,
                            staging,
                            name_expr,
                            attrs_expr,
                            geom_expr,
                            extra,
                            batches,
                            shard,
                            oversize="skip" if troceando else "all",
                        ),
                    ],
                    check=True,
                )
                for shard in range(batches)
            ]
            for future in futures:
                future.result()  # propaga el fallo de cualquier trozo

        if troceando:
            insert_oversized(spec, staging, name_expr, attrs_expr, extra, db)

        query(db, f"DROP TABLE {staging}")

    stats = query(
        db,
        "SELECT count(*) || '|' || count(name) || '|' || "
        "coalesce(pg_size_pretty(sum(pg_column_size(geom))), '0') "
        f"FROM layer_features WHERE layer_code = '{spec.code}'",
    )[0].split("|")
    total, named, size = int(stats[0]), int(stats[1]), stats[2]
    print(f"  ✓ {spec.code}: {total:,} features ({named:,} con nombre), {size}".replace(",", "."))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="Solo mostrar qué se descargaría")
    parser.add_argument("--urls", action="store_true", help="URLs a abrir en el navegador")
    parser.add_argument("--only", nargs="+", metavar="CODIGO", help="Cargar solo estas capas")
    parser.add_argument("--skip-download", action="store_true", help="Usar los zip ya de data/")
    parser.add_argument("--force", action="store_true", help="Volver a descargar aunque exista")
    parser.add_argument("--list", metavar="FICHERO.ZIP", help="Listar shapefiles de un zip")
    parser.add_argument(
        "--no-subdivide",
        action="store_true",
        help="Cargar los polígonos enteros (~60x más rápido; mide antes las consultas)",
    )
    parser.add_argument(
        "--jobs", type=int, default=1, help="Procesos concurrentes (ojo con la RAM; def. 1)"
    )
    parser.add_argument(
        "--batches", type=int, default=16, help="Lotes por capa: acota la memoria (def. 16)"
    )
    args = parser.parse_args()

    if args.plan:
        plan()
        return

    if args.urls:
        urls()
        return

    if args.list:
        for name in list_shapefiles(DATA_DIR / args.list):
            print(name)
        return

    codes = set(args.only) if args.only else {spec.code for spec in DOWNLOADABLE}
    unknown = codes - {spec.code for spec in CATALOG}
    if unknown:
        raise SystemExit(f"Códigos desconocidos: {', '.join(sorted(unknown))}")

    db = DbConfig()
    pending: dict[str, str] = {}
    pendientes_de_carga: list[tuple] = []

    for spec in DOWNLOADABLE:
        if spec.code not in codes:
            continue
        print(f"\n▶ {spec.code} — {spec.label}")
        archive = DATA_DIR / spec.download.filename

        if archive.exists():
            print(f"  ya en {archive} ({human_mb(archive.stat().st_size / 1_048_576)})")
        elif spec.download.gated:
            # Saltar, no abortar: que las automáticas se carguen igual y al final se diga
            # de una vez cuáles faltan, en vez de parar en la primera.
            print("  ⏭  requiere descarga manual (captcha del MITECO)")
            pending[spec.download.filename] = spec.download.url
            continue
        elif args.skip_download:
            print(f"  ⏭  falta {archive} y se ha pedido --skip-download")
            pending[spec.download.filename] = spec.download.url
            continue
        else:
            archive = download(spec, force=args.force)

        pendientes_de_carga.append((spec, archive))

    if pendientes_de_carga:
        # Sin `indexes_dropped`: daba 1,6x, pero si la carga muere a mitad la tabla se
        # queda sin índice espacial y eso no da error, solo consultas lentísimas. Dos
        # caídas por OOM ya lo demostraron. No compensa.
        for spec, archive in pendientes_de_carga:
            print(f"\n· cargando {spec.code}")
            load(
                spec,
                resolve_shapefiles(archive, spec.download.shapefile_match),
                db,
                jobs=max(1, args.jobs),
                batches=max(1, args.batches),
                subdivide=not args.no_subdivide,
            )

    if pending:
        print(f"\nFaltan por bajar a mano (guárdalos en {DATA_DIR}/ con ese nombre):")
        for filename, url in pending.items():
            print(f"  {filename:<22} {url}")
        print("\nDespués vuelve a ejecutar este script; lo ya cargado no se repite.")

    skipped = [spec.code for spec in MANUAL_ONLY if spec.code in codes]
    if skipped:
        print(f"\nSin capa nacional, pendientes de carga manual: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
