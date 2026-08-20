"""HTML → PDF rendering of the report.

The template is the professional one from the frontend repo (templates/report-professional.html),
turned into Jinja. Fonts are the system stack instead of Google Fonts: the PDF is rendered
inside the worker container, which has no reason to reach the internet mid-render — and a
missing webfont would silently reflow a document that is meant to be citable.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.core.logger import get_logger
from app.reports import findings as interpret

logger = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _es_number(value: float | None, decimals: int = 0) -> str:
    """Spanish formatting: thousands with dot, decimals with comma."""
    if value is None:
        return "—"
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _es_percent(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.{decimals}f}".replace(".", ",") + " %"


_env.filters["es_number"] = _es_number
_env.filters["es_percent"] = _es_percent
# La misma regla que en los hallazgos: el SNCZI encadena varias denominaciones en un campo
# y la tabla las heredaba enteras.
_env.filters["es_name"] = interpret.named_feature

SEVERITY_CLASS = {
    "INCIDENCIA": "c-crit",
    "AFECCIÓN": "c-info",
    "OBSERVACIÓN": "c-warn",
    "CONFORME": "c-ok",
}


# NDVI chart geometry, matching the professional template's plot box.
_CHART = {"width": 760, "height": 185, "left": 46, "right": 742, "top": 24, "bottom": 160}
_NDVI_MAX = 0.8


def ndvi_chart(series: list[dict]) -> dict | None:
    """Precompute the NDVI polyline so the template stays free of arithmetic."""
    if len(series) < 6:
        return None

    span = max(len(series) - 1, 1)
    step = (_CHART["right"] - _CHART["left"]) / span
    height = _CHART["bottom"] - _CHART["top"]

    points = []
    for index, point in enumerate(series):
        value = min(max(point["value"], 0.0), _NDVI_MAX)
        x = _CHART["left"] + index * step
        y = _CHART["bottom"] - (value / _NDVI_MAX) * height
        points.append(f"{x:.0f},{y:.0f}")

    year_labels = [
        {"x": _CHART["left"] + index * step, "label": point["month"][:4]}
        for index, point in enumerate(series)
        if point["month"].endswith("-01")
    ]

    return {
        **_CHART,
        "points": " ".join(points),
        "year_labels": year_labels,
        "gridlines": [
            {"y": _CHART["bottom"] - (value / _NDVI_MAX) * height, "label": _es_number(value, 1)}
            for value in (0.2, 0.4, 0.6)
        ],
        "first_month": series[0]["month"],
        "last_month": series[-1]["month"],
    }


DEMO_NOTICE = "DOCUMENTO DE DEMOSTRACIÓN — datos e imágenes simulados"
SAMPLE_NOTICE = (
    "INFORME REAL DE EJEMPLO — parcela y datos auténticos, emitido en la fecha que consta. "
    "Publicado con fines de muestra; cualquiera puede comprobar sus fuentes."
)


def render_html(
    payload: dict, *, demo: bool = False, notice: str | None = None, checkout_url: str = ""
) -> str:
    """`notice` sustituye al banner de demostración: mismo hueco, otro texto.

    Publicar como ejemplo un informe de una parcela real obliga a decir dos cosas a la vez
    —que los datos no están inventados y que el documento no se ha generado para quien lo
    está leyendo—, y ninguna de las dos cabe en el aviso de «datos simulados».
    """
    template = _env.get_template("report.html.j2")
    return template.render(
        **payload,
        chart=ndvi_chart(payload.get("ndvi") or []),
        severity_class=SEVERITY_CLASS,
        demo=demo or bool(notice),
        notice=notice or DEMO_NOTICE,
        checkout_url=checkout_url,
        price_label=f"{settings.report_price_eur} €",
    )


def render_pdf(html: str) -> bytes:
    # Imported lazily: WeasyPrint pulls in cairo/pango at import time, and the API
    # container never renders — only the worker does.
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


def render_report(payload: dict, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(render_pdf(render_html(payload)))
    logger.info("PDF written to %s (%d bytes)", destination, destination.stat().st_size)
    return destination
