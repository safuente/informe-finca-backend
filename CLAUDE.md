# CLAUDE.md — informe-finca-backend

Backend de informefinca.es (Fase 1). El producto, las fases y las fuentes están
decididos en `SPEC.md` del repo hermano `informe-finca-frontend`: **leerlo antes de
cualquier tarea y no re-decidir lo que ya está decidido ahí**. Este repo implementa la
Fase 1: vista previa gratuita + webhook de Stripe → Celery → PDF → correo.

## Convenciones

- Vertical slice por dominio (`parcels`, `reports`, `payments`, `layers`), según el skill
  fastapi-scaffold: router → service → repository → model dentro de la misma carpeta.
- Async en todo: `asyncpg`, SQLAlchemy 2.0, `httpx`. Python 3.12, uv, ruff (línea 100).
- Imports absolutos con prefijo `app.`. API versionada bajo `/api/v1`; el prefijo vive en
  el ensamblador, no en cada router.
- Código, nombres y comentarios en inglés; textos visibles al usuario, en castellano
  (misma regla que el frontend).
- PostGIS en SRID 25830. La reproyección desde 4326 ocurre al entrar el dato.
- Tests obligatorios en lo que toca dinero (webhook) y en la redacción de hallazgos
  (`app/reports/findings.py`).

## Reglas de redacción del informe (no negociables)

Cada hallazgo lleva severidad (INCIDENCIA/OBSERVACIÓN/AFECCIÓN/CONFORME) y confianza
(ALTA = dato oficial, MEDIA = inferencia). Nunca un score. Las inferencias se redactan
como inferencias («compatible con», «requiere verificación»); el NDVI menciona siempre el
falso positivo del barbecho. Una capa no cargada se declara como salvedad, jamás como
ausencia de afección. Disclaimer legal en todos los informes. Toda fuente se cita:
Dirección General del Catastro, IGN CC-BY 4.0, PVGIS © UE, Copernicus.

## Anti-scope-creep

Fase 1 NO incluye: cuentas de usuario, panel de cliente, API pública B2B, multi-idioma,
LLM para redacción, más provincias que las cargadas. Si una tarea lo pide, señalarlo y
proponer posponerlo a Fase 2 según el SPEC.
