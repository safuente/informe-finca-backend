"""Which reference layers exist, what they mean, who to cite and where to get them.

Adding a layer is: an entry here + `make load-layers`. Nothing else in the codebase needs
to know the layer exists — the report walks this catalog.

Sizes and URLs verified against the MITECO download pages (agosto 2026). The download host
is gis.miteco.gob.es; the pages that document each dataset live on www.miteco.gob.es.
"""

from dataclasses import dataclass
from enum import StrEnum

# Dos rutas distintas, y la diferencia importa:
#   GATED_BASE  — pasa por una página con captcha Altcha. No se automatiza.
#   BDN_BASE    — ficheros estáticos del Banco de Datos de la Naturaleza, sin captcha.
# Las capas de biodiversidad están en las dos; la del BDN es la que sirve para automatizar.
# Ojo: los nombres del BDN llevan el año (Enp2025_shp.zip), así que cambian en cada
# actualización anual y hay que revisarlos aquí.
GATED_BASE = "https://gis.miteco.gob.es/descargas/app/DescargaFichero?f="
BDN_BASE = (
    "https://www.miteco.gob.es/content/dam/miteco/es/biodiversidad/servicios/"
    "banco-datos-naturaleza/"
)


class LayerKind(StrEnum):
    FLOOD = "flood"
    PROTECTED = "protected"
    PUBLIC_DOMAIN = "public_domain"


class GeometryKind(StrEnum):
    """How an intersection with this layer is measured.

    AREA layers are polygons: the finding is "X m² (Y% de la parcela)". LINE layers are
    axes — the Red General de Vías Pecuarias publishes centrelines, not the legal strip —
    so the finding is "la atraviesa en X m" and the width has to come from the acto de
    clasificación. Measuring a line's "area" would give zero and read as no affection.
    """

    AREA = "area"
    LINE = "line"


@dataclass(frozen=True, slots=True)
class LayerDownload:
    filename: str  # cómo se guarda en data/, estable aunque el origen cambie de nombre
    url: str
    approx_mb: float
    # Regex matched against the .shp names inside the archive. The archives are not
    # inspectable without downloading them, so selection is by pattern and
    # `fetch_layers.py --list` prints what a downloaded archive actually contains.
    shapefile_match: str
    name_field: str | None = None
    # Filtro SQL sobre la tabla de staging, para las fuentes que meten varias figuras en
    # el mismo fichero y las distinguen por atributo. Las columnas llegan en minúsculas
    # porque ogr2ogr las normaliza.
    where: str | None = None
    # Trocear con ST_Subdivide solo donde hace falta: en el SNCZI una sola lámina cubre
    # una demarcación entera y su bounding box haría inútil el índice GiST. En Red Natura
    # o ENP cada polígono es un espacio concreto, ya acotado, y subdividirlo cuesta más de
    # lo que ahorra: un polígono de 150.000 vértices tarda minutos en trocearse y las
    # consultas van igual de rápidas sin hacerlo.
    subdivide: bool = False
    notes: str = ""

    @property
    def gated(self) -> bool:
        """True si hay que pasar por el captcha, es decir: descarga a mano."""
        return "DescargaFichero" in self.url


@dataclass(frozen=True, slots=True)
class LayerSpec:
    code: str
    label: str
    kind: LayerKind
    source: str
    geometry: GeometryKind = GeometryKind.AREA
    # Nearest feature is only worth reporting for layers where proximity matters
    # (a Natura 2000 site 300 m away constrains permits; a flood polygon does not).
    report_nearest: bool = False
    # Below this share of the parcel the intersection is treated as cartographic noise.
    min_area_ratio: float = 0.001
    # Una capa opcional se comprueba si está cargada, pero su ausencia no se disculpa en
    # el informe. La salvedad existe para que el silencio sobre algo que el producto sí
    # promete no se lea como "sin afección"; disculparse por lo que nunca se ofreció solo
    # llena de ruido un documento que se vende por su criterio.
    optional: bool = False
    # Qué significa la sigla, en una línea. El informe lo imprime bajo el nombre de la
    # capa: quien compra una finca no tiene por qué saber qué es una ZEPA, y una tabla
    # llena de acrónimos sin desarrollar obliga a buscarlos fuera del documento que ha
    # pagado. Vacío en las capas cuyo nombre ya se explica solo.
    meaning: str = ""
    download: LayerDownload | None = None


CATALOG: tuple[LayerSpec, ...] = (
    LayerSpec(
        code="snczi_zfp",
        label="Zona de Flujo Preferente (ZFP)",
        kind=LayerKind.FLOOD,
        source="SNCZI · MITECO",
        meaning=(
            "Franja por la que circularía la mayor parte del caudal en una crecida de 100 años. Es "
            "la única lámina en la que no se permite edificar."
        ),
        report_nearest=True,
        download=LayerDownload(
            filename="laminas-zfp-PB.zip",
            url=f"{GATED_BASE}laminas-zfp-PB.zip",
            subdivide=True,
            approx_mb=500,
            shapefile_match=r".*\.shp$",
            name_field="rio",
            # Se publica en su propia página, aparte de las láminas por periodo de retorno:
            # descargas/agua/laminas-zona-flujo-preferente.html
            notes=(
                "La ZFP es la única afección de inundabilidad que por sí sola bloquea: "
                "en ella no se permite edificar."
            ),
        ),
    ),
    LayerSpec(
        code="snczi_t10",
        label="Zona inundable T=10 años",
        kind=LayerKind.FLOOD,
        source="SNCZI · MITECO",
        meaning=("Superficie que la crecida cubre, en promedio, una vez cada 10 años."),
        # Sí interesa la distancia: una lámina a 300 m es información material para quien
        # compra, y sin esto la capa desaparecía del informe cuando no había intersección
        # —el silencio se leía como "aquí no hay agua"—.
        report_nearest=True,
        download=LayerDownload(
            filename="laminasPB-q10.zip",
            url=f"{GATED_BASE}laminasPB-q10.zip",
            subdivide=True,
            approx_mb=954,
            shapefile_match=r".*\.shp$",
            # El cauce que provoca la inundación. Convierte "zona inundable" en
            # "zona inundable (Río Segura)": el comprador necesita saber qué cauce le
            # afecta para dirigirse a la confederación hidrográfica correcta.
            name_field="rio",
        ),
    ),
    LayerSpec(
        code="snczi_t100",
        label="Zona inundable T=100 años",
        kind=LayerKind.FLOOD,
        source="SNCZI · MITECO",
        meaning=("Superficie que la crecida cubre, en promedio, una vez cada 100 años."),
        report_nearest=True,
        download=LayerDownload(
            filename="laminasPB-q100.zip",
            url=f"{GATED_BASE}laminasPB-q100.zip",
            subdivide=True,
            approx_mb=1178,
            shapefile_match=r".*\.shp$",
            name_field="rio",
        ),
    ),
    LayerSpec(
        code="snczi_t500",
        label="Zona inundable T=500 años",
        kind=LayerKind.FLOOD,
        source="SNCZI · MITECO",
        meaning=("Superficie que la crecida cubre, en promedio, una vez cada 500 años."),
        report_nearest=True,
        download=LayerDownload(
            filename="laminasPB-q500.zip",
            url=f"{GATED_BASE}laminasPB-q500.zip",
            subdivide=True,
            approx_mb=1249,
            shapefile_match=r".*\.shp$",
            name_field="rio",
        ),
    ),
    LayerSpec(
        code="dph_cartografico",
        # Opcional: la posición del cauce ya la da el WFS del IGN en cada informe, y el
        # propio hallazgo advierte de que el deslinde lo fija la confederación. Esta capa
        # solo afinaría el límite legal.
        optional=True,
        label="Cauce con dominio público hidráulico",
        kind=LayerKind.PUBLIC_DOMAIN,
        source="SNCZI · MITECO",
        geometry=GeometryKind.LINE,
        report_nearest=True,
        download=LayerDownload(
            filename="dph-cartografico-probable.zip",
            url=f"{GATED_BASE}dph-cartografico-probable.zip",
            subdivide=True,
            approx_mb=647,
            shapefile_match=r".*\.shp$",
            name_field="rio",
            notes=(
                "El cauce en sí, no la lámina de inundación. El SNCZI solo cartografía "
                "láminas de los tramos estudiados, así que un arroyo sin estudio no "
                "aparecía en ninguna capa aunque cruzara la parcela."
            ),
        ),
    ),
    LayerSpec(
        code="dph_deslindado",
        # Opcional: la posición del cauce ya la da el WFS del IGN en cada informe, y el
        # propio hallazgo advierte de que el deslinde lo fija la confederación. Esta capa
        # solo afinaría el límite legal.
        optional=True,
        label="Cauce con dominio público hidráulico deslindado",
        kind=LayerKind.PUBLIC_DOMAIN,
        source="SNCZI · MITECO",
        geometry=GeometryKind.LINE,
        report_nearest=True,
        download=LayerDownload(
            filename="dph-deslindado.zip",
            url=f"{GATED_BASE}dph-deslindado.zip",
            approx_mb=25,
            shapefile_match=r".*\.shp$",
            name_field="rio",
            notes="Deslinde ya aprobado: el límite del dominio público es firme, no probable.",
        ),
    ),
    LayerSpec(
        code="natura2000_zepa",
        label="Red Natura 2000 · ZEPA",
        kind=LayerKind.PROTECTED,
        source="Banco de Datos de la Naturaleza · MITECO",
        meaning=(
            "Zona de Especial Protección para las Aves (Directiva Aves): protege hábitats de cría, "
            "invernada y descanso de aves silvestres."
        ),
        report_nearest=True,
        download=LayerDownload(
            filename="rn2000_shp.zip",
            url=f"{BDN_BASE}3-rn2000/n2000_2025_shp.zip",
            approx_mb=126,
            shapefile_match=r".*\.shp$",  # dos ficheros: Península+Baleares y Canarias
            name_field="SITE_NAME",
            # TIPO es el código estándar de la Comisión: A = solo ZEPA (Directiva Aves),
            # B = solo LIC/ZEC (Directiva Hábitats), C = ambas figuras sobre el mismo
            # espacio. Por eso C cuenta en las dos capas y no es un error de duplicado.
            where="tipo IN ('A', 'C')",
        ),
    ),
    LayerSpec(
        code="natura2000_zec",
        label="Red Natura 2000 · ZEC/LIC",
        kind=LayerKind.PROTECTED,
        source="Banco de Datos de la Naturaleza · MITECO",
        meaning=(
            "Zona Especial de Conservación / Lugar de Importancia Comunitaria (Directiva "
            "Hábitats): protege tipos de hábitat y especies que no son aves. LIC es la fase "
            "previa a ZEC, ya con los mismos efectos."
        ),
        report_nearest=True,
        download=LayerDownload(
            filename="rn2000_shp.zip",
            url=f"{BDN_BASE}3-rn2000/n2000_2025_shp.zip",
            approx_mb=126,
            shapefile_match=r".*\.shp$",
            name_field="SITE_NAME",
            where="tipo IN ('B', 'C')",
        ),
    ),
    LayerSpec(
        code="enp",
        label="Espacio Natural Protegido",
        kind=LayerKind.PROTECTED,
        source="Banco de Datos de la Naturaleza · MITECO",
        meaning=(
            "Espacio Natural Protegido declarado por el Estado o la comunidad autónoma (parque, "
            "reserva, monumento natural o paisaje protegido)."
        ),
        report_nearest=True,
        download=LayerDownload(
            filename="enp_shp.zip",
            url=f"{BDN_BASE}enp/Enp2025_shp.zip",
            approx_mb=52,
            shapefile_match=r".*\.shp$",  # Enp2025_c.shp (Canarias) y Enp2025_p.shp
            name_field="SITE_NAME",  # comprobado en el fichero, no es NOMBRE
        ),
    ),
    LayerSpec(
        code="montes_up",
        label="Monte de Utilidad Pública",
        kind=LayerKind.PUBLIC_DOMAIN,
        source="Inventario Español de Patrimonios Forestales · MITECO",
        meaning=(
            "Monte de Utilidad Pública: monte de titularidad pública catalogado, gestionado por la "
            "comunidad autónoma e inalienable, imprescriptible e inembargable."
        ),
        report_nearest=True,
        download=LayerDownload(
            filename="iepf_cmup_shp.zip",
            url=f"{BDN_BASE}informacion-disponible/iepf/IEPF_CMUP_Shp.zip",
            approx_mb=218,
            shapefile_match=r".*\.shp$",
            # El nombre del monte va en "monte"; "cmup" es su número de catálogo.
            name_field="monte",
            # Catalogar montes es competencia autonómica, pero el MITECO recopila los
            # catálogos de las 17 comunidades en el Inventario Español de Patrimonios
            # Forestales. Descarga directa, sin captcha.
            notes=(
                "Monte de utilidad pública: dominio público forestal, inalienable, "
                "imprescriptible e inembargable."
            ),
        ),
    ),
    LayerSpec(
        code="vias_pecuarias",
        label="Vía pecuaria",
        kind=LayerKind.PUBLIC_DOMAIN,
        source="Red General de Vías Pecuarias · MITECO",
        meaning=(
            "Ruta histórica de trashumancia. Es dominio público: no se puede adquirir por el paso "
            "del tiempo ni cerrar."
        ),
        geometry=GeometryKind.LINE,
        report_nearest=True,
        download=LayerDownload(
            filename="rgvp_shp.zip",
            url=f"{BDN_BASE}vias-pecuarias/RGVP_SHP.zip",
            approx_mb=35,
            shapefile_match=r".*\.shp$",
            # nb_via, no NOMBRE: en la RGVP el campo NOMBRE es el municipio, y citar
            # «Santa Colomba de Somoza» como si fuera el nombre de la cañada es falso.
            # El tipo (cañada/cordel/vereda) viaja en cd_tipo_vp dentro de attributes.
            name_field="nb_via",
            notes=(
                "Ejes, no la anchura legal: la clasificación fija "
                "cañada 75 m, cordel 37,5 m, vereda 20 m."
            ),
        ),
    ),
)

BY_CODE = {spec.code: spec for spec in CATALOG}

DOWNLOADABLE = tuple(spec for spec in CATALOG if spec.download is not None)
MANUAL_ONLY = tuple(spec for spec in CATALOG if spec.download is None)
