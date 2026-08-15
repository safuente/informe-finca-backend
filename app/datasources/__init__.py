"""Clients for the public data sources the report is built from.

Infrastructure, not domain logic: every module here knows how to talk to one external
service and returns plain dataclasses. Interpretation lives in app/reports/findings.py,
so changing a provider never changes what the report *says*.

  catastro   — Dirección General del Catastro (OVC + INSPIRE WFS)
  ign        — PNOA orthophotos, current and historical (CC BY 4.0)
  pvgis      — photovoltaic yield (JRC, European Commission)
  copernicus — Sentinel-2 NDVI series (optional, needs a free CDSE account)

Layers that are too big to query per request (flood zones, Natura 2000, SIGPAC, MDT) are
not here: they are bulk-loaded into PostGIS and live in the app/layers domain.
"""
