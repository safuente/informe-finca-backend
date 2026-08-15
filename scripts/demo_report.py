#!/usr/bin/env python
"""Renderiza un informe de demostración sin tocar red ni base de datos.

Sirve para dos cosas: comprobar la plantilla al tocarla, y regenerar el informe de
ejemplo que la web pública sirve en /sample/ (con su banner de demostración).

    uv run python scripts/demo_report.py --out /tmp/demo.html
    uv run python scripts/demo_report.py --pdf /tmp/demo.pdf
"""

import argparse
import math
from datetime import UTC, datetime
from pathlib import Path

from app.layers.catalog import LayerKind
from app.layers.schemas import LayerCoverage, LayerHit
from app.parcels.schemas import AreaComparison
from app.reports import findings as interpret
from app.reports.renderer import render_html, render_pdf


def demo_payload() -> dict:
    area = AreaComparison(
        cadastral_area_m2=18_430,
        measured_area_m2=19_610,
        difference_ratio=0.064,
        is_significant=True,
    )
    hits = [
        LayerHit(
            layer_code="snczi_t500",
            label="Zona inundable T=500 años",
            kind=LayerKind.FLOOD,
            source="SNCZI · MITECO",
            intersects=True,
            area_m2=800,
            area_ratio=0.041,
        ),
        LayerHit(
            layer_code="natura2000_zepa",
            label="Red Natura 2000 · ZEPA",
            kind=LayerKind.PROTECTED,
            source="Banco de Datos de la Naturaleza · MITECO",
            intersects=False,
            nearest_name="Montes Aquilanos y Sierra del Teleno",
            nearest_distance_m=2100,
        ),
    ]
    ndvi = [
        {
            "month": f"{2018 + index // 12}-{index % 12 + 1:02d}",
            "value": round(
                0.28 + 0.18 * abs(math.sin(index / 12 * math.pi * 2)) - (0.10 if index > 60 else 0),
                3,
            ),
        }
        for index in range(96)
    ]

    findings = [interpret.area_finding(area)]
    findings.extend(interpret.layer_findings(hits))
    findings.append(interpret.built_area_finding(0, has_imagery=False))
    findings.append(interpret.ndvi_finding(ndvi))
    findings.append(interpret.solar_finding(1612.4))
    findings = [finding for finding in findings if finding]
    dictamen = interpret.build_dictamen(findings)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "reference": "IF-DEMO-00001",
        "parcel": {
            "refcat": "24145A00500123",
            "municipality": "Santa Colomba de Somoza",
            "province": "León",
            "use": "Agrario",
            "cadastral_area_m2": 18_430,
            "built_area_m2": 0,
            "measured_area_m2": 19_610,
            "lat": 42.45120,
            "lon": -6.24830,
            "subplots": [
                {"crop": "Labor o labradío secano", "intensity": "03", "area_m2": 15_200},
                {"crop": "Pastos", "intensity": "02", "area_m2": 3_230},
            ],
        },
        "area": area.model_dump(),
        "dictamen": {"verdict": dictamen.verdict, "summary": dictamen.summary},
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "layers": [hit.model_dump(mode="json") for hit in hits],
        "caveats": interpret.coverage_caveats(
            [LayerCoverage(layer_code="montes_up", loaded=False)],
            {"montes_up": "Monte de Utilidad Pública"},
        ),
        "orthophotos": [],
        "ndvi": ndvi,
        "solar": {"kwh_per_kwp_year": 1612.4, "optimal_slope_deg": 35.0},
        "recommendations": interpret.recommendations(findings),
        "sources": [
            "Dirección General del Catastro (SEC)",
            "PNOA © Instituto Geográfico Nacional de España (CC BY 4.0, ign.es)",
            "PVGIS © Unión Europea, 2001-2024 (JRC)",
            "Copernicus Sentinel-2",
            "SNCZI · MITECO",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("demo-report.html"))
    parser.add_argument("--pdf", type=Path, default=None)
    args = parser.parse_args()

    html = render_html(demo_payload(), demo=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"HTML: {args.out} ({len(html):,} bytes)")

    if args.pdf:
        args.pdf.write_bytes(render_pdf(html))
        print(f"PDF:  {args.pdf}")


if __name__ == "__main__":
    main()
