#!/usr/bin/env python
"""Exporta a KMZ lo que el informe dice de una parcela, para verlo sobre un mapa.

Sirve para contrastar el informe contra la realidad: se abre en Google Earth, o se
importa en Google My Maps (Crear mapa → Importar), y se ve si el cauce que detectamos es
el que se ve en la ortofoto y si la lámina de inundación queda donde decimos.

    uv run python scripts/export_kml.py 24155A11600027
    uv run python scripts/export_kml.py 24155A11600027 --radio 2000 --out finca.kmz

Las geometrías salen en EPSG:4326, que es lo que entiende KML, y simplificadas a un metro:
sin eso, media provincia de láminas troceadas no cabe en un mapa de Google.
"""

import argparse
import asyncio
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.database import worker_session  # noqa: E402
from app.datasources import ign_hidrografia  # noqa: E402
from app.layers.catalog import BY_CODE  # noqa: E402
from app.layers.repository import LayerRepository  # noqa: E402
from app.layers.service import LayerService  # noqa: E402
from app.parcels.repository import ParcelRepository  # noqa: E402
from app.parcels.service import ParcelService  # noqa: E402

SIMPLIFY_M = 1.0
MAX_PER_LAYER = 400

# Los colores de KML van en aabbggrr, no en rrggbb.
STYLES = {
    "parcela": ("ff0000ff", "330000ff", 3),
    "cauce": ("ffff9000", "00000000", 3),
    "masa_agua": ("ffff9000", "88ff9000", 2),
    "snczi_t10": ("ffcc6600", "44cc6600", 1),
    "snczi_t100": ("ffdd8833", "33dd8833", 1),
    "snczi_t500": ("ffeeaa66", "22eeaa66", 1),
    "natura2000_zepa": ("ff44aa44", "3344aa44", 1),
    "natura2000_zec": ("ff44aa88", "3344aa88", 1),
    "enp": ("ff338833", "22338833", 1),
    "vias_pecuarias": ("ff00aaff", "00000000", 3),
    "otros": ("ff888888", "22888888", 1),
}


def placemark(name: str, style: str, kml_geometry: str, description: str = "") -> str:
    return (
        f"<Placemark><name>{escape(name)}</name>"
        f"<styleUrl>#{style}</styleUrl>"
        + (f"<description>{escape(description)}</description>" if description else "")
        + f"{kml_geometry}</Placemark>"
    )


def style_block(key: str) -> str:
    line, fill, width = STYLES[key]
    return (
        f'<Style id="{key}"><LineStyle><color>{line}</color><width>{width}</width></LineStyle>'
        f"<PolyStyle><color>{fill}</color></PolyStyle></Style>"
    )


async def build(refcat: str, radius_m: float) -> tuple[str, str]:
    async with worker_session() as session:
        repository = ParcelRepository(session)
        service = ParcelService(repository, LayerService(LayerRepository(session)))
        parcel = await service.get_or_fetch(refcat)
        geometry = await service.geometry_wgs84(parcel)

        folders: list[str] = []

        parcel_kml = (
            await session.execute(
                text("SELECT ST_AsKML(ST_Transform(geom, 4326), 8) FROM parcels WHERE id = :id"),
                {"id": parcel.id},
            )
        ).scalar()
        folders.append(
            "<Folder><name>Parcela</name>"
            + placemark(
                f"{parcel.refcat} · {parcel.municipality}",
                "parcela",
                parcel_kml,
                f"Catastral {parcel.cadastral_area_m2:,.0f} m² · "
                f"medida {parcel.measured_area_m2:,.0f} m²".replace(",", "."),
            )
            + "</Folder>"
        )

        # Cauces: se piden al WFS del IGN igual que en el informe, no están en la base.
        courses = await ign_hidrografia.fetch_watercourses(geometry)
        if courses:
            marks = []
            for index, course in enumerate(courses, 1):
                kml = (
                    await session.execute(
                        text("SELECT ST_AsKML(ST_GeomFromText(:wkt, 4326), 8)"),
                        {"wkt": course.wkt},
                    )
                ).scalar()
                marks.append(placemark(course.name or f"Cauce {index}", "cauce", kml))
            folders.append(
                f"<Folder><name>Cauces IGN ({len(courses)})</name>{''.join(marks)}</Folder>"
            )

        bodies = await ign_hidrografia.fetch_water_bodies(geometry)
        if bodies:
            marks = []
            for index, body in enumerate(bodies, 1):
                kml = (
                    await session.execute(
                        text("SELECT ST_AsKML(ST_GeomFromText(:wkt, 4326), 8)"),
                        {"wkt": body.wkt},
                    )
                ).scalar()
                marks.append(placemark(body.name or f"Masa de agua {index}", "masa_agua", kml))
            folders.append(
                f"<Folder><name>Masas de agua IGN ({len(bodies)})</name>{''.join(marks)}</Folder>"
            )

        # Capas de referencia dentro del radio, disueltas por capa para no reventar el KMZ.
        rows = (
            await session.execute(
                text(
                    """
                    SELECT f.layer_code,
                           count(*) AS n,
                           ST_AsKML(
                               ST_Transform(
                                   ST_SimplifyPreserveTopology(
                                       ST_Union(ST_Intersection(
                                           f.geom, ST_Buffer(p.geom, :radius))), :tol),
                                   4326),
                               7) AS kml
                    FROM layer_features f, parcels p
                    WHERE p.id = :id AND ST_DWithin(f.geom, p.geom, :radius)
                    GROUP BY f.layer_code
                    """
                ),
                {"id": parcel.id, "radius": radius_m, "tol": SIMPLIFY_M},
            )
        ).all()

        for row in rows:
            if not row.kml:
                continue
            spec = BY_CODE.get(row.layer_code)
            label = spec.label if spec else row.layer_code
            style = row.layer_code if row.layer_code in STYLES else "otros"
            folders.append(
                f"<Folder><name>{escape(label)} ({row.n})</name>"
                + placemark(
                    label, style, row.kml, f"{row.n} elementos a menos de {radius_m:,.0f} m"
                )
                + "</Folder>"
            )

    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{escape(refcat)}</name>"
        + "".join(style_block(key) for key in STYLES)
        + "".join(folders)
        + "</Document></kml>"
    )
    return document, f"{refcat} · {parcel.municipality}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("refcat", help="Referencia catastral")
    parser.add_argument("--radio", type=float, default=1000, help="Metros alrededor (def. 1000)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--kmz",
        action="store_true",
        help="Comprimir a KMZ. Por defecto sale KML sin comprimir, que es lo que "
        "Google My Maps importa sin protestar.",
    )
    args = parser.parse_args()

    kml, title = asyncio.run(build(args.refcat.upper(), args.radio))
    suffix = "kmz" if args.kmz else "kml"
    out = args.out or Path(f"parcela-{args.refcat.upper()}.{suffix}")

    if args.kmz:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("doc.kml", kml)
    else:
        out.write_text(kml, encoding="utf-8")

    print(f"{out}  ({out.stat().st_size / 1024:,.0f} KB)  —  {title}")
    print("Google Earth: abrir el fichero. Google My Maps: Crear mapa → Importar.")


if __name__ == "__main__":
    main()
