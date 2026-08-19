"""Interpretation layer: turns raw data into findings a buyer can act on.

This is the product. Two rules from the SPEC govern every line here:

  1. Severity and confidence, never a score. A score hides the method; the competitor
     that rated a forestry plot inside Doñana "solar 5/5" did so with a score.
  2. Inferences are worded as inferences. Anything not read straight off an official
     source gets confidence MEDIA and hedged language ("compatible con", "requiere
     verificación"). NDVI is the trap: a fallow year looks exactly like abandonment.

Pure functions on plain data, so the wording is unit-testable without a database.
"""

from dataclasses import dataclass

from app.layers.catalog import GeometryKind, LayerKind
from app.layers.schemas import LayerCoverage, LayerHit
from app.parcels.schemas import AreaComparison
from app.reports.schemas import Confidence, Finding, Severity

CATASTRO_SOURCE = "Dirección General del Catastro"
INSPIRE_SOURCE = "Catastro INSPIRE"
SENTINEL_SOURCE = "Copernicus Sentinel-2"
PNOA_SOURCE = "PNOA · IGN"

# NDVI bands for the last 12 months. Wide on purpose: the middle band is where dryland
# farming, scrub and seasonal use all live, and telling them apart needs a site visit.
NDVI_ACTIVE = 0.45
NDVI_BARE = 0.25

# Cultivos del Catastro sobre los que el NDVI *no* discrimina. En monte o matorral la
# vegetación está ahí todo el año: un NDVI alto significa "hay matorral", que es justo lo
# que ya declara la ficha catastral. El dato que sí vale en una finca de labor —hay ciclo
# de cultivo o se ha perdido— aquí no existe, porque no hay ciclo que perder.
PERMANENT_COVER_HINTS = (
    "monte",
    "matorral",
    "forestal",
    "pinar",
    "encinar",
    "robledal",
    "arbolado",
    "improductivo",
    "erial",
)


def has_permanent_cover(subplots: list[dict] | None) -> bool:
    """¿Lo que declara el Catastro es cubierta permanente y no cultivo?"""
    crops = " ".join((item.get("crop") or "") for item in (subplots or [])).lower()
    return any(hint in crops for hint in PERMANENT_COVER_HINTS)


@dataclass(slots=True)
class Dictamen:
    """The one-line verdict in the report header."""

    verdict: str
    summary: str


def area_finding(area: AreaComparison) -> Finding:
    if not area.cadastral_area_m2 or not area.measured_area_m2:
        return Finding(
            severity=Severity.OBSERVACION,
            title="No ha sido posible contrastar la superficie",
            detail=(
                "Falta la superficie catastral declarada o la geometría INSPIRE, por lo que "
                "no se ha podido comparar lo declarado con lo medido. Requiere verificación "
                "en la sede electrónica del Catastro."
            ),
            source=CATASTRO_SOURCE,
            confidence=Confidence.ALTA,
        )

    percentage = f"{area.difference_ratio:+.1%}".replace(".", ",")
    measured = f"{area.measured_area_m2:,.0f}".replace(",", ".")
    declared = f"{area.cadastral_area_m2:,.0f}".replace(",", ".")

    if not area.is_significant:
        return Finding(
            severity=Severity.CONFORME,
            title="Superficie geométrica coherente con la catastral",
            detail=(
                f"{measured} m² medidos sobre la geometría INSPIRE frente a {declared} m² "
                f"declarados ({percentage}). La diferencia está dentro del margen habitual "
                "de la cartografía catastral (±5%)."
            ),
            source=INSPIRE_SOURCE,
            confidence=Confidence.ALTA,
        )

    direction = "superior" if area.difference_ratio > 0 else "inferior"
    return Finding(
        severity=Severity.OBSERVACION,
        title=f"Superficie medida {direction} a la declarada ({percentage})",
        detail=(
            f"{measured} m² medidos sobre la geometría INSPIRE frente a {declared} m² "
            f"declarados. Una discrepancia de este orden afecta al precio por hectárea "
            "pactado y conviene contrastarla con la descripción registral antes de firmar."
        ),
        source=INSPIRE_SOURCE,
        confidence=Confidence.ALTA,
    )


def built_area_finding(built_area_m2: float, has_imagery: bool) -> Finding | None:
    """Flags the *question*, never the answer.

    Automatic detection of undeclared buildings is not in Phase 1: what the report gives
    is the declared built area next to the multitemporal series, so the discrepancy —
    if any — is visible and documentable. Claiming a detection we have not made would be
    exactly the fabrication this product exists to denounce.
    """
    if not has_imagery:
        return None
    if built_area_m2 > 0:
        return Finding(
            severity=Severity.CONFORME,
            title=f"Catastro declara {built_area_m2:,.0f} m² construidos".replace(",", "."),
            detail=(
                "Contraste la superficie construida declarada con la serie de ortofotos de "
                "este informe: cualquier volumen visible y no declarado implica "
                "regularización catastral y verificación urbanística."
            ),
            source=CATASTRO_SOURCE,
            confidence=Confidence.ALTA,
        )
    return Finding(
        severity=Severity.OBSERVACION,
        title="Catastro no declara superficie construida",
        detail=(
            "La descripción catastral no recoge construcciones. La serie de ortofotos "
            "1956→actualidad incluida en este informe permite comprobarlo visualmente: si "
            "aparece algún volumen edificado, la discrepancia con el Catastro requiere "
            "regularización y verificación de su situación urbanística."
        ),
        source=CATASTRO_SOURCE,
        confidence=Confidence.ALTA,
    )


# Reglamento del Dominio Público Hidráulico: las márgenes de todo cauce están sujetas a
# una servidumbre de 5 m y a una zona de policía de 100 m, ambas medidas desde la ribera.
# Son las dos distancias con consecuencia jurídica; más allá, decir algo sería ruido.
SERVIDUMBRE_M = 5
ZONA_POLICIA_M = 100

IGN_HIDROGRAFIA_SOURCE = "Hidrografía INSPIRE · IGN"


def watercourse_finding(
    crosses: bool, distance_m: float | None, length_inside_m: float | None, name: str | None
) -> Finding | None:
    """Qué supone tener un cauce dentro o al lado.

    El eje del cauce lo cartografía el IGN; el deslinde del dominio público lo fija el
    organismo de cuenca. Por eso aquí nunca se afirma dónde acaba exactamente el dominio
    público: se dice que hay un cauce, a qué distancia, y qué obliga eso a comprobar.
    """
    if distance_m is None:
        return None

    named = f" ({name})" if name else ""

    if crosses:
        length = f"{length_inside_m:,.0f}".replace(",", ".") if length_inside_m else "—"
        return Finding(
            severity=Severity.INCIDENCIA,
            title="Un cauce atraviesa la parcela",
            detail=(
                f"El eje de un cauce{named} discurre unos {length} m dentro de la parcela. "
                "El cauce es dominio público hidráulico y no es susceptible de apropiación "
                f"privada: sus márgenes soportan una servidumbre de {SERVIDUMBRE_M} m y una "
                f"zona de policía de {ZONA_POLICIA_M} m en la que cualquier obra, "
                "cerramiento o plantación necesita autorización del organismo de cuenca. "
                "La superficie realmente excluida depende del deslinde, que fija la "
                "confederación hidrográfica."
            ),
            source=IGN_HIDROGRAFIA_SOURCE,
            confidence=Confidence.MEDIA,
        )

    if distance_m <= SERVIDUMBRE_M:
        return Finding(
            severity=Severity.INCIDENCIA,
            title=f"Cauce a menos de {SERVIDUMBRE_M} m del lindero",
            detail=(
                f"Hay un cauce{named} a {distance_m:,.0f} m de la parcela. ".replace(",", ".")
                + f"La franja de {SERVIDUMBRE_M} m desde la ribera es servidumbre de uso "
                "público: no puede cerrarse ni edificarse, y a esta distancia es probable "
                "que alcance a la finca. Requiere comprobar el deslinde."
            ),
            source=IGN_HIDROGRAFIA_SOURCE,
            confidence=Confidence.MEDIA,
        )

    if distance_m <= ZONA_POLICIA_M:
        return Finding(
            severity=Severity.OBSERVACION,
            title=f"Cauce dentro de la zona de policía ({ZONA_POLICIA_M} m)",
            detail=(
                f"Hay un cauce{named} a {distance_m:,.0f} m de la parcela. ".replace(",", ".")
                + f"Dentro de los {ZONA_POLICIA_M} m desde la ribera, toda obra, movimiento "
                "de tierras, cerramiento o plantación requiere autorización previa del "
                "organismo de cuenca. No impide la compra, pero condiciona qué se puede "
                "hacer y en cuánto tiempo."
            ),
            source=IGN_HIDROGRAFIA_SOURCE,
            confidence=Confidence.MEDIA,
        )

    distance = (
        f"{distance_m:,.0f} m".replace(",", ".")
        if distance_m < 1000
        else f"{distance_m / 1000:,.1f} km".replace(".", ",")
    )
    return Finding(
        severity=Severity.CONFORME,
        title="Sin cauces en el entorno inmediato",
        detail=(
            f"El cauce más próximo{named} está a {distance}, fuera de la zona de policía "
            f"de {ZONA_POLICIA_M} m. No constan servidumbres hidráulicas sobre la parcela."
        ),
        source=IGN_HIDROGRAFIA_SOURCE,
        confidence=Confidence.ALTA,
    )


def water_body_finding(
    area_inside_m2: float, distance_m: float | None, name: str | None
) -> Finding | None:
    """Lagunas, charcas o embalses dentro de la parcela.

    No es lo mismo que un cauce cerca: una laguna dentro de la finca resta superficie
    aprovechable, y si asienta sobre cauce público es dominio público hidráulico —art. 2
    del Texto Refundido de la Ley de Aguas—. Quién es el titular depende del origen de la
    masa (natural sobre cauce público, o balsa excavada por el propietario), y eso no lo
    dice la cartografía: por eso el hallazgo señala la pregunta y no la zanja.
    """
    if distance_m is None:
        return None

    named = f" («{name}»)" if name else ""

    if area_inside_m2 > 0:
        area = f"{area_inside_m2:,.0f}".replace(",", ".")
        return Finding(
            severity=Severity.INCIDENCIA,
            title="Hay una masa de agua dentro de la parcela",
            detail=(
                f"La cartografía oficial recoge {area} m² de agua estancada{named} dentro "
                "de los linderos. Además de restar superficie aprovechable, los lagos y "
                "lagunas sobre cauces públicos son dominio público hidráulico y no son "
                "susceptibles de apropiación privada. Conviene aclarar con el organismo de "
                "cuenca si se trata de una masa natural o de una balsa de titularidad "
                "privada, porque de eso depende quién puede usarla."
            ),
            source=IGN_HIDROGRAFIA_SOURCE,
            confidence=Confidence.MEDIA,
        )

    if distance_m <= ZONA_POLICIA_M:
        return Finding(
            severity=Severity.OBSERVACION,
            title=f"Masa de agua a menos de {ZONA_POLICIA_M} m",
            detail=(
                f"Hay agua estancada{named} a {distance_m:,.0f} m de la parcela, dentro de "
                "la zona de policía. Las obras en ese entorno requieren autorización del "
                "organismo de cuenca."
            ).replace(",", "."),
            source=IGN_HIDROGRAFIA_SOURCE,
            confidence=Confidence.MEDIA,
        )

    return None


# Los tres periodos de retorno son el mismo fenómeno en distinta intensidad, así que van
# en un solo hallazgo: tres párrafos que se diferencian en un metro ocupan media página del
# resumen ejecutivo y hacen que el lector deje de leer los que sí importan. La ZFP no entra
# aquí — es la única lámina con efecto jurídico propio, y por eso se cuenta aparte.
RETURN_PERIOD_LAYERS = ("snczi_t10", "snczi_t100", "snczi_t500")
RETURN_PERIOD_LABELS = {"snczi_t10": "T=10", "snczi_t100": "T=100", "snczi_t500": "T=500"}
ZFP_LAYER = "snczi_zfp"
FLOOD_LAYERS = (*RETURN_PERIOD_LAYERS, ZFP_LAYER)


def layer_findings(hits: list[LayerHit]) -> list[Finding]:
    findings: list[Finding] = []
    floods = [hit for hit in hits if hit.layer_code in FLOOD_LAYERS]
    group = [hit for hit in floods if hit.layer_code in RETURN_PERIOD_LAYERS]

    # La ZFP se agrupa con los periodos de retorno solo cuando ninguna lámina toca la
    # parcela: ahí las cuatro dicen lo mismo —«el agua queda cerca pero fuera»— y separarlas
    # produce dos párrafos que se diferencian en dos metros. En cuanto una intersecta,
    # vuelve a contarse sola: es la única con efecto jurídico propio y no puede diluirse.
    zfp = next((hit for hit in floods if hit.layer_code == ZFP_LAYER), None)
    if zfp is not None and not any(hit.intersects for hit in floods):
        group.append(zfp)

    grouped = {hit.layer_code for hit in group}
    for hit in hits:
        if hit.layer_code in grouped:
            continue
        if hit.intersects:
            findings.append(_intersection_finding(hit))
        elif hit.kind is LayerKind.PROTECTED or hit.nearest_distance_m is not None:
            findings.append(_clear_finding(hit))

    if group and (merged := _flood_finding(group)):
        findings.append(merged)
    return findings


def _flood_finding(periods: list[LayerHit]) -> Finding | None:
    """Las tres láminas de inundabilidad, en un solo hallazgo.

    Lo que no puede perderse al agrupar es la gradación: que la parcela esté dentro de la
    lámina de diez años —se inunda cada década— no es comparable a rozar la de quinientos.
    Por eso el texto nombra qué periodos la tocan y con cuánta superficie, y la severidad
    la marca el más frecuente de ellos.
    """
    touching = [hit for hit in periods if hit.intersects]

    if touching:
        detalle = ", ".join(
            f"{RETURN_PERIOD_LABELS[hit.layer_code]} con {_es(hit.area_m2)} m² "
            f"({_pct(hit.area_ratio)})"
            for hit in sorted(touching, key=lambda h: RETURN_PERIOD_LAYERS.index(h.layer_code))
        )
        names = sorted({hit.nearest_name or "" for hit in touching if hit.nearest_name})
        cauce = f" El cauce asociado es {named_feature(names[0])}." if names else ""
        # La lámina más frecuente que toca la parcela manda: T=10 significa que se inunda
        # del orden de una vez cada diez años.
        frequent = min(touching, key=lambda h: RETURN_PERIOD_LAYERS.index(h.layer_code))
        muy_frecuente = frequent.layer_code == "snczi_t10"
        return Finding(
            severity=Severity.INCIDENCIA if muy_frecuente else Severity.AFECCION,
            title="La parcela entra en zona inundable cartografiada",
            detail=(
                f"Intersección con la cartografía oficial de inundabilidad: {detalle}.{cauce} "
                + (
                    "La lámina de periodo de retorno 10 años implica una probabilidad alta de "
                    "inundación —del orden de una vez cada década—, lo que condiciona "
                    "seriamente la edificabilidad y cualquier instalación fija. "
                    if muy_frecuente
                    else "Condiciona la edificabilidad y exige autorización del organismo de "
                    "cuenca sobre la superficie afectada. "
                )
                + "Las obras en esa superficie requieren informe del organismo de cuenca."
            ),
            source=periods[0].source,
            confidence=Confidence.ALTA,
        )

    distances = [h.nearest_distance_m for h in periods if h.nearest_distance_m is not None]
    if not distances:
        return None

    nearest = min(distances)
    names = [h.nearest_name for h in periods if h.nearest_name and h.nearest_distance_m == nearest]
    cauce = f", {named_feature(names[0])}," if names else ""
    medida = (
        f"{_es(nearest)} m" if nearest < 1000 else f"{nearest / 1000:,.1f} km".replace(".", ",")
    )
    laminas = _flood_layer_names(periods)

    if nearest < FLOOD_PROXIMITY_M:
        return Finding(
            severity=Severity.OBSERVACION,
            title=f"Fuera de zona inundable, pero a menos de {FLOOD_PROXIMITY_M} m",
            detail=(
                f"La parcela queda fuera de {laminas}. La más próxima{cauce} está a "
                f"{medida}. El límite de la lámina no es una frontera física y la "
                "cartografía solo cubre los tramos estudiados, así que a esta distancia "
                "conviene comprobar la cota concreta de la parcela antes de proyectar "
                "edificación o instalaciones fijas."
            ),
            source=periods[0].source,
            confidence=Confidence.ALTA,
        )

    return Finding(
        severity=Severity.CONFORME,
        title="Fuera de las zonas inundables cartografiadas",
        detail=f"La parcela queda fuera de {laminas}. La más próxima{cauce} está a {medida}.",
        source=periods[0].source,
        confidence=Confidence.ALTA,
    )


def _flood_layer_names(group: list[LayerHit]) -> str:
    """Qué láminas cubre el hallazgo agrupado, dichas por su nombre.

    Importa nombrarlas: «fuera de zona inundable» a secas deja al lector sin saber si se ha
    mirado la de quinientos años o solo la decenal.
    """
    periodos = ", ".join(
        RETURN_PERIOD_LABELS[hit.layer_code]
        for hit in sorted(
            (h for h in group if h.layer_code in RETURN_PERIOD_LABELS),
            key=lambda h: RETURN_PERIOD_LAYERS.index(h.layer_code),
        )
    )
    laminas = f"las láminas de inundabilidad ({periodos})" if periodos else ""
    if any(hit.layer_code == ZFP_LAYER for hit in group):
        zfp = "la zona de flujo preferente"
        return f"{laminas} y de {zfp}" if laminas else f"{zfp}"
    return laminas


def _intersection_finding(hit: LayerHit) -> Finding:
    names = ", ".join(hit.feature_names[:3]) if hit.feature_names else ""
    named = f" ({names})" if names else ""

    severity = Severity.INCIDENCIA if _is_blocking(hit) else Severity.AFECCION

    if hit.geometry is GeometryKind.LINE:
        length = f"{hit.length_m:,.0f}".replace(",", ".")
        detail = (
            f"El eje de {hit.label.lower()}{named} discurre {length} m dentro de la parcela. "
            "La cartografía publica el eje, no la anchura legal: la anchura la fija el acto "
            "de clasificación (cañada 75 m, cordel 37,5 m, vereda 20 m como máximos "
            "legales), de modo que la superficie realmente afectada requiere verificación."
        )
    else:
        ratio = f"{hit.area_ratio:.1%}".replace(".", ",")
        area = f"{hit.area_m2:,.0f}".replace(",", ".")
        detail = (
            f"Intersección de {area} m² ({ratio} de la parcela) con {hit.label.lower()}{named}."
        )

    if hit.kind is LayerKind.FLOOD:
        detail += (
            " Condiciona la edificabilidad y las autorizaciones del organismo de cuenca "
            "sobre la superficie afectada."
        )
    elif hit.kind is LayerKind.PROTECTED:
        detail += (
            " Cualquier cambio de uso o instalación requiere evaluación ambiental previa "
            "del órgano competente."
        )
    else:
        detail += (
            " El dominio público no es susceptible de apropiación privada: verifique el "
            "deslinde antes de la compra."
        )

    return Finding(
        severity=severity,
        title=f"Afección: {hit.label}",
        detail=detail,
        source=hit.source,
        confidence=Confidence.ALTA,
    )


# Por debajo de esta distancia, quedar fuera de la lámina deja de ser tranquilizador: la
# cartografía del SNCZI se levanta por tramos estudiados y su borde no es una frontera
# física. Se sigue diciendo que la parcela está fuera —lo está—, pero como OBSERVACIÓN y
# con la distancia delante.
FLOOD_PROXIMITY_M = 500


def _clear_finding(hit: LayerHit) -> Finding:
    is_flood = hit.kind is LayerKind.FLOOD
    distance = hit.nearest_distance_m

    if distance is not None and hit.nearest_name:
        noun = "La lámina más próxima" if is_flood else "El espacio más próximo"
        if distance < 1000:
            measure = f"{distance:,.0f} m".replace(",", ".")
        else:
            measure = f"{distance / 1000:,.1f} km".replace(".", ",")
        proximity = f" {noun}, {named_feature(hit.nearest_name)}, está a {measure}."
    else:
        proximity = ""

    near = is_flood and distance is not None and distance < FLOOD_PROXIMITY_M
    if near:
        return Finding(
            severity=Severity.OBSERVACION,
            title=f"Fuera de {hit.label.lower()}, pero a menos de {FLOOD_PROXIMITY_M} m",
            detail=(
                f"La parcela queda fuera de la cartografía oficial de {hit.label}.{proximity} "
                "El límite de la lámina no es una frontera física y la cartografía solo cubre "
                "los tramos estudiados, así que a esta distancia conviene comprobar la cota "
                "concreta de la parcela antes de proyectar edificación o instalaciones fijas."
            ),
            source=hit.source,
            confidence=Confidence.ALTA,
        )

    return Finding(
        severity=Severity.CONFORME,
        title=f"Sin intersección con {hit.label.lower()}",
        detail=f"La parcela queda fuera de la cartografía oficial de {hit.label}.{proximity}",
        source=hit.source,
        confidence=Confidence.ALTA,
    )


def _is_blocking(hit: LayerHit) -> bool:
    """Preferential flow zone and public domain are the ones that stop a purchase."""
    return hit.layer_code == "snczi_zfp" or hit.kind is LayerKind.PUBLIC_DOMAIN


def ndvi_finding(series: list[dict], subplots: list[dict] | None = None) -> Finding | None:
    """Vegetation activity over time. The most useful signal and the easiest to misread.

    El mismo número significa cosas distintas según lo que haya en el suelo. En una finca
    de labor, el ciclo estacional es la firma de que alguien la trabaja, y su desaparición
    es un hallazgo que vale dinero. En monte bajo no hay ciclo que perder: la señal alta
    solo dice que hay matorral, que es lo que ya declara el Catastro. Por eso, sobre
    cubierta permanente, el NDVI se queda en contexto y no afirma uso agrícola.
    """
    if len(series) < 12:
        return None

    last_year = [point["value"] for point in series[-12:]]
    mean = sum(last_year) / len(last_year)
    span = f"{series[0]['month']} → {series[-1]['month']}"
    mean_text = f"{mean:.2f}".replace(".", ",")

    if has_permanent_cover(subplots):
        return Finding(
            severity=Severity.CONFORME,
            title="Cubierta vegetal permanente, coherente con lo declarado",
            detail=(
                f"NDVI medio de {mean_text} en los últimos doce meses (serie {span}), sin "
                "ciclo estacional de cultivo. Es lo esperable en el aprovechamiento que "
                "declara el Catastro para esta parcela: la vegetación permanece todo el "
                "año, así que el índice confirma que hay cubierta vegetal pero no permite "
                "deducir uso agrícola ni su intensidad."
            ),
            source=SENTINEL_SOURCE,
            confidence=Confidence.MEDIA,
        )

    if mean > NDVI_ACTIVE:
        return Finding(
            severity=Severity.CONFORME,
            title="Señal de vegetación activa en el último año",
            detail=(
                f"NDVI medio de {mean_text} en los últimos doce meses (serie {span}), "
                "compatible con cultivo o pasto en uso."
            ),
            source=SENTINEL_SOURCE,
            confidence=Confidence.MEDIA,
        )

    if mean < NDVI_BARE:
        return Finding(
            severity=Severity.OBSERVACION,
            title="Señal de vegetación escasa en el último año",
            detail=(
                f"NDVI medio de {mean_text} en los últimos doce meses (serie {span}), "
                "compatible con suelo desnudo, barbecho prolongado o cese de actividad. "
                "No se descarta barbecho: requiere confirmación en campo o con el "
                "histórico de declaraciones PAC."
            ),
            source=SENTINEL_SOURCE,
            confidence=Confidence.MEDIA,
        )

    return Finding(
        severity=Severity.OBSERVACION,
        title="Señal de vegetación intermedia en el último año",
        detail=(
            f"NDVI medio de {mean_text} en los últimos doce meses (serie {span}), "
            "compatible con secano, matorral o uso estacional. El dato no permite por sí "
            "solo distinguir entre esos usos."
        ),
        source=SENTINEL_SOURCE,
        confidence=Confidence.MEDIA,
    )


# Ocupación de una instalación fotovoltaica fija sobre suelo, contando separación entre
# filas, viales y retranqueos. El rango es real y ancho: depende del diseño, y dar un
# número único fingiría una precisión que no existe. Se declara en el texto para que
# cualquiera pueda rehacer la cuenta o discutirla.
M2_PER_KWP_DENSE = 15
M2_PER_KWP_SPARSE = 25

# Por encima de esto, un promotor fotovoltaico se interesa por el emplazamiento.
DEVELOPER_THRESHOLD = 1600

# Cultivos declarados que obligan a cambio de uso antes de cualquier proyecto: la aptitud
# física no sirve de nada si el suelo no admite la instalación.
RESTRICTED_USE_HINTS = PERMANENT_COVER_HINTS


def _es(value: float) -> str:
    """Número con separador de miles español."""
    return f"{value:,.0f}".replace(",", ".")


def _pct(ratio: float) -> str:
    """Porcentaje con coma decimal."""
    return f"{ratio:.1%}".replace(".", ",")


def named_feature(name: str) -> str:
    """El nombre de un elemento, entrecomillado y listo para meter en una frase.

    El SNCZI encadena en un solo campo todas las denominaciones que recorre una misma
    lámina —«Arroyo de Calancha antes de arroyo de la Rehoya; Arroyo de Calancha después
    de…»—, y lo mismo hacen algunos montes de utilidad pública. Volcarlas enteras en mitad
    de una frase la vuelve ilegible; cortar por la primera y callar sería esconder que el
    elemento tiene más nombres. Se dice la primera y cuántas quedan.
    """
    parts = [part.strip() for part in name.split(";") if part.strip()]
    if len(parts) <= 1:
        return f"«{name.strip()}»"
    resto = "otra denominación" if len(parts) == 2 else f"otras {len(parts) - 1} denominaciones"
    return f"«{parts[0]}» y {resto}"


def solar_finding(
    kwh_per_kwp_year: float | None,
    area_m2: float | None = None,
    subplots: list[dict] | None = None,
    near_protected: bool = False,
) -> Finding | None:
    """Qué rinde el sitio y, si se sabe la superficie, qué cabría en él.

    La potencia se estima; la amortización no. Estimar cuántos kWp entran en una parcela
    es geometría y se puede declarar. Calcular en cuántos años se recupera la inversión
    exige el coste de conexión al nudo de evacuación y el precio de venta de la energía,
    que son justo las dos variables que deciden el resultado y que no conocemos. Ponerles
    un número sería convertir dos suposiciones en una promesa.
    """
    if not kwh_per_kwp_year:
        return None

    level = (
        "elevada"
        if kwh_per_kwp_year > DEVELOPER_THRESHOLD
        else "buena"
        if kwh_per_kwp_year > 1400
        else "moderada"
    )
    yield_text = _es(kwh_per_kwp_year)
    threshold = _es(DEVELOPER_THRESHOLD)

    detail = f"El emplazamiento rinde {yield_text} kWh por cada kWp instalado y año, " + (
        f"por encima del umbral que suelen buscar los promotores (≈{threshold})."
        if kwh_per_kwp_year > DEVELOPER_THRESHOLD
        else f"por debajo del umbral habitual de los promotores (≈{threshold})."
    )

    if area_m2 and area_m2 > 0:
        low = area_m2 / M2_PER_KWP_SPARSE
        high = area_m2 / M2_PER_KWP_DENSE
        detail += (
            f" Sobre los {_es(area_m2)} m² de la parcela cabrían orientativamente entre "
            f"{_es(low)} y {_es(high)} kWp con estructura fija sobre suelo, que producirían "
            f"del orden de {_es(low * kwh_per_kwp_year / 1000)} a "
            f"{_es(high * kwh_per_kwp_year / 1000)} MWh al año. El rango es amplio a "
            f"propósito: la ocupación real va de {M2_PER_KWP_DENSE} a {M2_PER_KWP_SPARSE} m² "
            # Cada número se formatea por su cuenta con _es(): aplicar el cambio de
            # separador sobre el párrafo entero se lleva por delante las comas de la prosa.
            "por kWp según la separación entre filas, los viales y los retranqueos."
        )

    detail += (
        " Es una estimación de capacidad física, no un proyecto: la viabilidad depende de "
        "la capacidad de evacuación del nudo eléctrico más próximo y del planeamiento "
        "urbanístico aplicable"
    )
    if has_permanent_cover(subplots):
        detail += ", y el aprovechamiento que declara el Catastro exigiría cambio de uso"
    if near_protected:
        detail += ", con evaluación ambiental previa por la proximidad de espacios protegidos"
    detail += ". No se estima plazo de amortización: dependería del coste de conexión al nudo "
    detail += "y del precio de venta de la energía, que no se conocen."

    return Finding(
        severity=Severity.CONFORME,
        title=f"Aptitud fotovoltaica {level}",
        detail=detail,
        source="PVGIS © Unión Europea",
        # La potencia estimada es una inferencia nuestra sobre la superficie, no un dato
        # que publique nadie.
        confidence=Confidence.MEDIA if area_m2 else Confidence.ALTA,
    )


def coverage_caveats(coverage: list[LayerCoverage], specs: dict) -> list[str]:
    """Layers that could NOT be checked. Silence is not a clean result.

    Cada capa puede traer su propia redacción (`missing_note`): el aviso genérico sirve
    cuando de verdad no sabemos nada, pero miente cuando otra fuente ya ha respondido la
    pregunta y lo que falta es solo precisión.
    """
    notes: list[str] = []
    for item in coverage:
        if item.loaded:
            continue
        spec = specs.get(item.layer_code)
        if getattr(spec, "optional", False):
            continue
        label = getattr(spec, "label", item.layer_code)
        note = f"No se ha podido comprobar {label}: la capa no está cargada para esta zona."
        # Sin repetir: dos capas pueden compartir salvedad —las dos del DPH dicen lo
        # mismo— y verla dos veces solo resta credibilidad al resto del informe.
        if note not in notes:
            notes.append(note)
    return notes


def build_dictamen(findings: list[Finding]) -> Dictamen:
    incidents = sum(1 for f in findings if f.severity is Severity.INCIDENCIA)
    observations = sum(1 for f in findings if f.severity is Severity.OBSERVACION)
    affections = sum(1 for f in findings if f.severity is Severity.AFECCION)

    if incidents:
        verdict = "Requiere aclaración previa"
    elif observations or affections:
        verdict = "Apta con salvedades"
    else:
        verdict = "Sin incidencias detectadas"

    parts = [
        _count(incidents, "incidencia", "incidencias"),
        _count(affections, "afección", "afecciones"),
        _count(observations, "observación", "observaciones"),
    ]
    parts = [part for part in parts if part]
    parts.append("verificación registral pendiente")

    return Dictamen(verdict=verdict, summary=" · ".join(parts))


def _count(quantity: int, singular: str, plural: str) -> str:
    """Spanish plurals of words ending in -ción drop the accent; no generic rule works."""
    if not quantity:
        return ""
    return f"{quantity} {singular if quantity == 1 else plural}"


def recommendations(findings: list[Finding]) -> list[str]:
    """Concrete pre-purchase steps. The competitor lists the questions; this answers them."""
    items = [
        "Solicitar nota simple actualizada en el Registro de la Propiedad y contrastar "
        "superficie, linderos y cargas con la descripción catastral de este informe.",
        "Comprobar en el ayuntamiento la clasificación y calificación urbanística del suelo, "
        "y la parcela mínima de segregación aplicable.",
    ]
    by_severity = {finding.severity for finding in findings}
    titles = " ".join(finding.title.lower() for finding in findings)

    if Severity.INCIDENCIA in by_severity or Severity.AFECCION in by_severity:
        items.append(
            "Pedir al vendedor la documentación de las afecciones señaladas y, si existen "
            "autorizaciones del organismo de cuenca o del órgano ambiental, su copia."
        )
    if "construida" in titles or "construidos" in titles:
        items.append(
            "Contrastar la serie de ortofotos con la descripción catastral y, ante cualquier "
            "volumen no declarado, valorar el coste de regularización antes de cerrar precio."
        )
    if "vegetación escasa" in titles or "vegetación intermedia" in titles:
        items.append(
            "Solicitar el histórico de declaraciones PAC de los últimos cinco años para "
            "confirmar el uso agrícola real y la existencia de derechos asociados."
        )
    items.append(
        "Verificar sobre el terreno el acceso rodado efectivo y su condición de camino "
        "público, así como la existencia de servidumbres de paso no inscritas."
    )
    return items
