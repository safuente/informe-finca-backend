# informefinca.es — backend (Fase 1)

API que automatiza el flujo que hoy es manual: vista previa gratuita de una parcela,
webhook de Stripe → cola → informe PDF → entrega por correo. El producto, las fases y las
fuentes de datos están decididos en `SPEC.md` del repo `informe-finca-frontend`; aquí solo
se implementan.

## Qué hace

| Endpoint | Para qué |
|---|---|
| `GET /health` | liveness |
| `GET /api/v1/parcels/lookup?lat=&lon=` | geolocalización inversa → referencia catastral |
| `GET /api/v1/parcels/{refcat}` | datos catastrales cacheados |
| `GET /api/v1/parcels/{refcat}/preview` | **vista previa gratuita** (gancho SEO, limitada por IP) |
| `POST /api/v1/payments/stripe/webhook` | pago confirmado → informe en cola |
| `POST /api/v1/payments/checkout-session` | alternativa al Payment Link |
| `GET /api/v1/reports/{token}` | estado del informe |
| `GET /api/v1/reports/{token}/download` | PDF |

Sin cuentas de usuario: el pago identifica el pedido y un token opaco de 24 bytes
identifica el informe. Es lo que decide el SPEC para el MVP.

## Arranque

```bash
cp .env.example .env          # rellenar claves de Stripe y SMTP
make build && make upd
make exec-migration           # crea la extensión postgis y las tablas
open http://localhost:8000/docs
```

**Mientras no se carguen las capas, los informes salen con la salvedad explícita de «capa
no comprobada»**: una capa vacía nunca se presenta como ausencia de afección. Ver abajo.

## Capas de referencia: volumen y almacenamiento

```bash
make layers-plan     # qué hay que bajar y cuánto pesa, sin bajar nada
make layers-urls     # las 6 URLs a abrir en el navegador
#   ... guardar los zip en data/ ...
make load-national-layers  # descomprime, carga en PostGIS y normaliza
make layers-size     # cuánto ocupan ya cargadas, por capa
```

### Por qué la descarga es manual

No es un apaño ni un problema de red: **el MITECO sirve estos ficheros detrás de un
captcha Altcha** (proof-of-work) con token antiforgery, que es exactamente una medida para
impedir la descarga automatizada. Pedir la URL devuelve 200 con la página del captcha, no
el zip. Los otros dos canales tampoco valen hoy (comprobado en agosto de 2026):

| Canal | Estado |
|---|---|
| `gis.miteco.gob.es/descargas/app/DescargaFichero` | 200, pero devuelve la página del captcha |
| Servicio ATOM de INSPIRE (el canal para máquinas) | **502 Bad Gateway** |
| WFS | `wfs.mapama.gob.es` no resuelve; no existe |

Así que: `make layers-urls`, pinchar seis veces, dejar los zip en `data/`. Estas capas se
actualizan una o dos veces al año, o sea seis descargas manuales anuales — automatizarlo
no compensaría aunque se pudiera. Si el ATOM vuelve, ese es el sitio por donde hacerlo.

`fetch_layers.py` detecta la página del captcha y aborta con las instrucciones, en vez de
guardar 3 KB de HTML llamados `enp.zip` y fallar mucho más tarde al descomprimir.

En la primera carga, dos cosas del catálogo son suposiciones hasta que alguien abra los
archivos: qué shapefile trae cada zip (`shapefile_match`) y en qué campo viene el nombre
oficial (`name_field`). El cargador no te deja a ciegas con ninguna:

```bash
docker compose exec app uv run python scripts/fetch_layers.py --list rn2000.zip
```

Si el patrón no casa, aborta listando los shapefiles que sí hay. Si el campo de nombre no
existe, prueba alternativas habituales y, si tampoco, deja `name` a NULL con un aviso y las
columnas disponibles — no tumba una carga de gigabytes por un nombre bonito. Ojo: **ogr2ogr
pasa los nombres de columna a minúsculas**, así que `NOMBRE` llega como `nombre`; la
resolución es insensible a mayúsculas justo por eso.

### Qué hay para toda España

| Capa | Fichero | Descarga | Geometría | Cómo |
|---|---|---:|---|---|
| Zona inundable T=10 | `laminasPB-q10.zip` | 954 MB | polígonos | a mano |
| Zona inundable T=100 | `laminasPB-q100.zip` | 1,15 GB | polígonos | a mano |
| Zona inundable T=500 | `laminasPB-q500.zip` | 1,22 GB | polígonos | a mano |
| Red Natura 2000 (ZEPA + ZEC/LIC) | `n2000_2025_shp.zip` | 126 MB | polígonos | **automática** |
| Espacios Naturales Protegidos | `Enp2025_shp.zip` | 52 MB | polígonos | **automática** |
| Vías pecuarias (RGVP) | `RGVP_SHP.zip` | 35 MB | **líneas (ejes)** | **automática** |

Total **≈ 3,6 GB comprimidos**. Tamaños leídos de las páginas de descarga del MITECO
(agosto 2026); T=50 se omite porque no aporta nada al dictamen que no diga T=100.

### Cuánto ocupa cargado (medido, no estimado)

Las tres capas de biodiversidad, ya cargadas:

| Capa | Features | Geometrías |
|---|---:|---:|
| `natura2000_zec` | 1.479 | 156 MB |
| `enp` | 1.785 | 83 MB |
| `natura2000_zepa` | 659 | 80 MB |
| `vias_pecuarias` | 51.939 | 48 MB |
| **`layer_features` con índices** | | **493 MB** |

O sea: 213 MB comprimidos → 493 MB en PostGIS, un factor de ~2,3×. Extrapolando a los
3,4 GB de inundabilidad, cuenta con **unos 8 GB en total**. Mídelo con `make layers-size`.

ZEPA sale 659 y ZEC 1.479 porque un espacio con doble figura (`TIPO = C`) cuenta en las
dos: son 261 peninsulares y 12 canarios declarados a la vez ZEPA y ZEC. No es duplicado.

### Dos huecos que no se resuelven descargando

- **Zona de Flujo Preferente**: el SNCZI no publica un archivo nacional; va por
  demarcación hidrográfica desde el visor. Y es la capa que más pesa en el dictamen —
  la ZFP es de las pocas afecciones que marcan INCIDENCIA.
- **Montes de Utilidad Pública**: el catálogo es competencia autonómica. No hay capa
  estatal: son 17 portales con 17 esquemas.

Ambas se cargan a mano con `bash scripts/load_layers.sh <fichero.shp> <codigo> [campo]`.
Mientras falten, salen como salvedad en cada informe, que es lo correcto.

### Cómo se almacenan

Todo va a **una sola tabla, `layer_features`** (`layer_code`, `name`, `attributes` JSONB,
`geom`), no una tabla por fuente. Así una única consulta `ST_Intersects` responde a todas
las afecciones y añadir SIGPAC más adelante es un trabajo de carga, no una migración. Los
atributos originales del shapefile sobreviven en `attributes`, que es de donde sale el
nombre oficial de la ZEPA cuando el informe la cita.

Dos decisiones del cargador que importan al rendimiento y a la corrección:

- **`ST_Subdivide` a 512 vértices, pero solo en el SNCZI.** Una lámina de inundación de una
  demarcación entera es un polígono cuyo *bounding box* cubre media España: el índice GiST
  lo devolvería en todas las consultas y no serviría de nada. En Red Natura o ENP, en
  cambio, cada polígono es un espacio concreto ya acotado, y trocearlo cuesta muchísimo
  más de lo que ahorra — un polígono de 149.651 vértices tardaba más de once minutos. Por
  eso `subdivide` es un ajuste por capa, no una regla general.
- **La columna es `geometry(Geometry, 25830)`, no `MultiPolygon`.** Las vías pecuarias se
  publican como ejes; una columna solo de polígonos rechazaría justo la capa cuyo interés
  es que *cruza* la parcela. Los polígonos se miden por área y las líneas por longitud —
  `ST_Area` de una línea es 0, que se leería como «sin afección».

Sobre las vías pecuarias, el informe dice la longitud del eje dentro de la parcela y
advierte de que la anchura legal la fija el acto de clasificación (cañada 75 m, cordel
37,5 m, vereda 20 m). No inventamos la superficie afectada.

Dos avisos sobre los campos de la RGVP, aprendidos a base de mirarlos: el campo `NOMBRE`
es **el municipio**, no la vía — el nombre real está en `nb_via`. Y `cd_tipo_vp` trae el
tipo (`CA`/`COR`/`VE`), que queda en `attributes`: con él se podría decir la anchura legal
concreta en vez de recitar las tres. Está sin usar todavía.

SIGPAC queda fuera: es Fase 2 en el SPEC y además se descarga por municipio (unos 8.100),
con un volumen de otro orden.

Para probar el webhook en local: `make stripe-listen` (CLI de Stripe en el host).

```bash
make test                     # pytest
make lint && make format      # ruff
uv run python scripts/demo_report.py --out demo.html --pdf demo.pdf
```

El último comando renderiza un informe de demostración sin red ni base de datos: es la
forma de revisar la plantilla y de regenerar el ejemplo que la web sirve en `/sample/`.

## Estructura

El modelo de datos —qué contiene cada tabla y de dónde sale cada dato— está en
[MODELO-DATOS.md](MODELO-DATOS.md).

```
app/
├── main.py              FastAPI + CORS + /health
├── core/                config, database, logger, celery_app, mailer
├── api/v1/router.py     ensambla los routers de dominio
├── shared/              base_model, base_repository, base_service, geo
├── datasources/         clientes de fuentes públicas (Catastro, IGN, PVGIS, Copernicus)
├── parcels/             parcela catastral, caché y vista previa
├── layers/              capas de referencia en PostGIS (sin router: son datos)
├── reports/             findings → pipeline → renderer → tasks
└── payments/            Stripe checkout y webhook
alembic/                 migraciones (0001 crea PostGIS y el esquema)
scripts/                 load_layers.sh, demo_report.py
```

Cada dominio es una rebanada vertical: router → service → repository → model. Solo
`layers` es importado por otros dominios, porque «qué polígonos cubren esta parcela» no es
una pregunta que `reports` pueda responder por su cuenta.

## Decisiones que conviene no deshacer

**PostGIS en SRID 25830 (ETRS89 / UTM 30N).** Métrico: superficies, distancias e
intersecciones las calcula la base de datos. La geometría llega en 4326 del WFS INSPIRE y
se reproyecta al entrar, en `ParcelRepository.set_geometry`.

**Las capas grandes no se consultan por petición.** SNCZI, Red Natura o SIGPAC se cargan
en bloque con `ogr2ogr`. Depender del WMS oficial en cada informe lo haría lento y frágil.

**El OVC del Catastro sí es en tiempo real, pero cacheado 30 días** y detrás de un límite
por IP en la vista previa. El OVC no documenta sus límites y perder el acceso pararía el
producto.

**La interpretación vive en `app/reports/findings.py`,** separada de la obtención de
datos. Son funciones puras y están testeadas: es donde se cumplen las reglas de redacción
del SPEC (severidad + confianza, inferencias con lenguaje prudente, NDVI sin afirmar
abandono, disclaimer siempre).

**No se detectan edificaciones automáticamente.** El informe pone la superficie construida
declarada junto a la serie de ortofotos y señala la pregunta. Afirmar una detección que no
hemos hecho sería exactamente lo que este producto denuncia de la competencia.

**El webhook es idempotente** por índice único sobre `stripe_event_id`: Stripe reintenta
hasta recibir un 2xx y un reintento no puede generar un segundo informe. Un pago con
datos inservibles se guarda como `needs_attention` y avisa por correo — nunca se descarta
en silencio.

**Los informes fallidos por parcela imposible** (catastros forales, sin geometría) quedan
en `refund_due` y disparan aviso: la web promete devolución íntegra en ese caso.

## Pendiente antes de producción

- Cargar las capas (ver arriba: son seis descargas manuales, una vez al año).
- Cargar a mano ZFP y montes de UP, que no tienen capa nacional (ver arriba).
- Credenciales CDSE para la serie NDVI (sin ellas el informe se genera, pero sin esa sección).
- SMTP real: sin `SMTP_HOST` el informe se genera y queda descargable, pero no se envía.
- Devoluciones automáticas: hoy `refund_due` avisa y el reembolso se hace a mano en Stripe.
