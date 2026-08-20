"""The wording rules are the product; these tests guard them.

What is checked is not formatting but *epistemics*: that inferences are labelled as
inferences, that a fallow-looking NDVI never becomes a claim of abandonment, and that an
unchecked layer never reads as a clean result.
"""

from app.layers.catalog import GeometryKind, LayerKind
from app.layers.schemas import LayerCoverage, LayerHit
from app.parcels.schemas import AreaComparison
from app.reports import findings as interpret
from app.reports.schemas import Confidence, Severity


def area(cadastral: float, measured: float) -> AreaComparison:
    ratio = (measured - cadastral) / cadastral if cadastral else 0.0
    return AreaComparison(
        cadastral_area_m2=cadastral,
        measured_area_m2=measured,
        difference_ratio=ratio,
        is_significant=abs(ratio) > 0.05,
    )


def test_area_within_margin_is_conforme():
    finding = interpret.area_finding(area(18_430, 18_700))
    assert finding.severity is Severity.CONFORME
    assert finding.confidence is Confidence.ALTA


def test_area_gap_beyond_margin_is_reported():
    finding = interpret.area_finding(area(18_430, 19_610))
    assert finding.severity is Severity.OBSERVACION
    assert "superior" in finding.title


def test_missing_area_never_claims_coherence():
    finding = interpret.area_finding(area(0, 0))
    assert finding.severity is not Severity.CONFORME


def ndvi(values: list[float]) -> list[dict]:
    return [
        {"month": f"2025-{index % 12 + 1:02d}", "value": value}
        for index, value in enumerate(values)
    ]


def test_low_ndvi_is_an_inference_not_a_verdict():
    finding = interpret.ndvi_finding(ndvi([0.18] * 12))
    assert finding.confidence is Confidence.MEDIA
    assert "compatible con" in finding.detail
    assert "barbecho" in finding.detail  # the false positive is named explicitly
    assert "abandonada" not in finding.detail.lower()


def test_on_scrubland_ndvi_does_not_claim_farming():
    """El mismo 0,54 significa cosas distintas según lo que haya en el suelo.

    En monte bajo la vegetación está todo el año: un NDVI alto solo confirma que hay
    matorral —lo que ya declara el Catastro— y no puede insinuar aprovechamiento agrícola.
    """
    monte = [{"crop": "MONTE BAJO", "intensity": "07", "area_m2": 1_269_480.0}]
    finding = interpret.ndvi_finding(ndvi([0.54] * 12), monte)

    assert finding.severity is Severity.CONFORME
    assert finding.confidence is Confidence.MEDIA
    assert "no permite deducir uso agrícola" in finding.detail
    assert "cultivo o pasto en uso" not in finding.detail


def test_on_cropland_ndvi_keeps_its_edge():
    """En labor sí discrimina: ahí es donde el ciclo, o su ausencia, vale dinero."""
    labor = [{"crop": "LABOR O LABRADIO SECANO", "intensity": "03", "area_m2": 15_200.0}]

    activa = interpret.ndvi_finding(ndvi([0.54] * 12), labor)
    assert "compatible con cultivo o pasto en uso" in activa.detail

    plana = interpret.ndvi_finding(ndvi([0.18] * 12), labor)
    assert plana.severity is Severity.OBSERVACION
    assert "barbecho" in plana.detail


def test_without_declared_crop_it_keeps_the_generic_reading():
    finding = interpret.ndvi_finding(ndvi([0.54] * 12), [])
    assert "compatible con cultivo o pasto en uso" in finding.detail


def test_high_ndvi_is_still_an_inference():
    finding = interpret.ndvi_finding(ndvi([0.6] * 12))
    assert finding.severity is Severity.CONFORME
    assert finding.confidence is Confidence.MEDIA


def test_short_ndvi_series_produces_no_finding():
    assert interpret.ndvi_finding(ndvi([0.5] * 6)) is None


def flood_hit(code: str, ratio: float) -> LayerHit:
    return LayerHit(
        layer_code=code,
        label="Zona inundable",
        kind=LayerKind.FLOOD,
        source="SNCZI · MITECO",
        intersects=True,
        area_m2=800,
        area_ratio=ratio,
    )


def test_preferential_flow_zone_outranks_a_return_period():
    zfp = interpret.layer_findings([flood_hit("snczi_zfp", 0.04)])[0]
    t500 = interpret.layer_findings([flood_hit("snczi_t500", 0.04)])[0]
    assert zfp.severity is Severity.INCIDENCIA
    assert t500.severity is Severity.AFECCION


def period(code: str, *, ratio: float = 0.0, area_m2: float = 0.0, distance_m: float | None = None):
    return LayerHit(
        layer_code=code,
        label=f"Zona inundable {code[-4:]}",
        kind=LayerKind.FLOOD,
        source="SNCZI · MITECO",
        intersects=distance_m is None,
        area_m2=area_m2,
        area_ratio=ratio,
        nearest_name="Río Turienzo",
        nearest_distance_m=distance_m,
    )


def test_the_three_return_periods_produce_a_single_finding():
    """Tres párrafos que se diferencian en un metro se dejan de leer.

    Es el mismo fenómeno medido con tres probabilidades: va en un hallazgo, no en tres.
    """
    findings = interpret.layer_findings(
        [
            period("snczi_t10", distance_m=285.0),
            period("snczi_t100", distance_m=284.0),
            period("snczi_t500", distance_m=284.0),
        ]
    )
    assert len(findings) == 1
    assert "284 m" in findings[0].detail
    assert "Río Turienzo" in findings[0].detail


def test_grouping_keeps_the_distinction_that_costs_money():
    """Estar dentro de la lámina de diez años no es rozar la de quinientos."""
    decenal = interpret.layer_findings(
        [
            period("snczi_t10", ratio=0.31, area_m2=5_600),
            period("snczi_t100", ratio=0.44, area_m2=7_900),
            period("snczi_t500", ratio=0.52, area_m2=9_400),
        ]
    )[0]
    assert decenal.severity is Severity.INCIDENCIA
    assert "T=10" in decenal.detail and "T=500" in decenal.detail
    assert "una vez cada década" in decenal.detail

    excepcional = interpret.layer_findings(
        [
            period("snczi_t10", distance_m=120.0),
            period("snczi_t100", distance_m=40.0),
            period("snczi_t500", ratio=0.06, area_m2=1_100),
        ]
    )[0]
    assert excepcional.severity is Severity.AFECCION
    assert "T=500" in excepcional.detail
    assert "T=10" not in excepcional.detail  # no se le atribuye una lámina que no la toca


def test_grouped_flood_areas_use_spanish_separators():
    """El punto de los miles y la coma del decimal no pueden intercambiarse."""
    finding = interpret.layer_findings([period("snczi_t500", ratio=0.155, area_m2=9_400)])[0]
    assert "9.400 m²" in finding.detail
    assert "15,5%" in finding.detail


def test_a_distant_flow_zone_joins_the_group_instead_of_repeating_it():
    """Sin intersección las cuatro láminas dicen lo mismo, con dos metros de diferencia."""
    findings = interpret.layer_findings(
        [
            period("snczi_t10", distance_m=285.0),
            period("snczi_t100", distance_m=284.0),
            period("snczi_t500", distance_m=284.0),
            period("snczi_zfp", distance_m=286.0),
        ]
    )
    assert len(findings) == 1
    assert "flujo preferente" in findings[0].detail
    assert "T=10, T=100, T=500" in findings[0].detail  # ninguna se da por mirada en silencio
    assert "284 m" in findings[0].detail


def test_the_preferential_flow_zone_stays_on_its_own():
    """La ZFP es la única lámina que bloquea por sí sola: no se diluye en el grupo."""
    findings = interpret.layer_findings(
        [
            flood_hit("snczi_zfp", 0.04),
            period("snczi_t10", ratio=0.12, area_m2=2_100),
            period("snczi_t100", ratio=0.20, area_m2=3_500),
            period("snczi_t500", ratio=0.28, area_m2=4_900),
        ]
    )
    assert len(findings) == 2
    zfp = next(f for f in findings if "inundable cartografiada" not in f.title)
    assert zfp.severity is Severity.INCIDENCIA


def test_line_layer_reports_length_and_refuses_to_invent_a_width():
    """Vías pecuarias are published as axes; the legal strip is not in the data."""
    hit = LayerHit(
        layer_code="vias_pecuarias",
        label="Vía pecuaria",
        kind=LayerKind.PUBLIC_DOMAIN,
        geometry=GeometryKind.LINE,
        source="Red General de Vías Pecuarias · MITECO",
        intersects=True,
        length_m=143.0,
    )
    finding = interpret.layer_findings([hit])[0]

    assert finding.severity is Severity.INCIDENCIA  # public domain blocks a purchase
    assert "143 m" in finding.detail
    assert "no la anchura legal" in finding.detail
    assert "m²" not in finding.detail  # never an area we have not measured


def flood_clear(distance_m: float) -> LayerHit:
    return LayerHit(
        layer_code="snczi_t10",
        label="Zona inundable T=10 años",
        kind=LayerKind.FLOOD,
        source="SNCZI · MITECO",
        intersects=False,
        nearest_name="Río Turienzo",
        nearest_distance_m=distance_m,
    )


def test_flood_layer_is_never_silent_when_it_does_not_intersect():
    """Callarse equivale a decir «aquí no hay agua», y no es lo mismo."""
    findings = interpret.layer_findings([flood_clear(8000)])
    assert len(findings) == 1
    assert "Río Turienzo" in findings[0].detail


def test_a_flood_zone_next_door_is_an_observation_not_a_pass():
    finding = interpret.layer_findings([flood_clear(284)])[0]
    assert finding.severity is Severity.OBSERVACION
    assert "284 m" in finding.detail
    assert "no es una frontera física" in finding.detail


def test_a_distant_flood_zone_is_conforme():
    finding = interpret.layer_findings([flood_clear(6200)])[0]
    assert finding.severity is Severity.CONFORME
    assert "6,2 km" in finding.detail


def test_unloaded_layer_becomes_a_caveat_not_a_clean_result():
    from app.layers.catalog import BY_CODE

    caveats = interpret.coverage_caveats(
        [LayerCoverage(layer_code="snczi_t100", loaded=False)], BY_CODE
    )
    assert len(caveats) == 1
    assert "no está cargada" in caveats[0]


def test_an_optional_layer_does_not_apologise_for_being_absent():
    """El informe no se disculpa por lo que nunca ofreció: eso es ruido, no honestidad."""
    from app.layers.catalog import BY_CODE

    caveats = interpret.coverage_caveats(
        [
            LayerCoverage(layer_code="dph_cartografico", loaded=False),
            LayerCoverage(layer_code="dph_deslindado", loaded=False),
        ],
        BY_CODE,
    )
    assert caveats == []


def test_dictamen_escalates_with_the_worst_finding():
    clean = interpret.build_dictamen([interpret.area_finding(area(1000, 1010))])
    assert clean.verdict == "Sin incidencias detectadas"

    blocking = interpret.build_dictamen(interpret.layer_findings([flood_hit("snczi_zfp", 0.2)]))
    assert blocking.verdict == "Requiere aclaración previa"


def test_recommendations_always_include_the_registry_check():
    items = interpret.recommendations([interpret.area_finding(area(1000, 1010))])
    assert any("nota simple" in item.lower() for item in items)


def test_a_watercourse_crossing_the_parcel_is_an_incidencia():
    """El cauce es dominio público: si lo cruza, hay suelo no apropiable dentro."""
    finding = interpret.watercourse_finding(True, 0.0, 118.0, "Arroyo del Valle")
    assert finding.severity is Severity.INCIDENCIA
    assert "118 m" in finding.detail
    assert "dominio público" in finding.detail
    # El deslinde lo fija la confederación, no nosotros.
    assert finding.confidence is Confidence.MEDIA
    assert "deslinde" in finding.detail


def test_a_watercourse_inside_the_police_zone_is_an_observation():
    finding = interpret.watercourse_finding(False, 62.0, 0.0, None)
    assert finding.severity is Severity.OBSERVACION
    assert "62 m" in finding.detail
    assert "autorización" in finding.detail


def test_a_watercourse_beyond_the_police_zone_is_conforme():
    finding = interpret.watercourse_finding(False, 640.0, 0.0, None)
    assert finding.severity is Severity.CONFORME
    assert "640 m" in finding.detail


def test_no_watercourse_data_produces_no_finding():
    """Si el WFS del IGN no responde, se calla: no se inventa un «no hay cauces»."""
    assert interpret.watercourse_finding(False, None, None, None) is None


def test_a_pond_inside_the_parcel_is_an_incidencia():
    finding = interpret.water_body_finding(7191.0, 0.0, "Laguna Cernea")
    assert finding.severity is Severity.INCIDENCIA
    assert "7.191 m²" in finding.detail
    assert "Laguna Cernea" in finding.detail
    # Quién es el titular depende del origen de la masa, y eso no lo dice la cartografía.
    assert finding.confidence is Confidence.MEDIA
    assert "organismo de cuenca" in finding.detail


def test_a_pond_far_away_produces_no_finding():
    assert interpret.water_body_finding(0.0, 850.0, None) is None


def test_no_water_body_data_produces_no_finding():
    assert interpret.water_body_finding(0.0, None, None) is None


def test_solar_without_area_gives_only_the_yield():
    """Sin superficie no se inventa potencia: solo se dice lo que rinde el sitio."""
    finding = interpret.solar_finding(1680.0)
    assert "1.680 kWh por cada kWp" in finding.detail
    assert "cabrían" not in finding.detail
    assert finding.confidence is Confidence.ALTA


def test_solar_with_area_estimates_power_as_a_range():
    # 10 ha: superficie en la que dimensionar una planta sí significa algo.
    finding = interpret.solar_finding(1680.0, area_m2=100_000.0)
    # 100.000 m² entre 25 y 15 m²/kWp
    assert "4.000" in finding.detail and "6.667" in finding.detail
    assert "MWh al año" in finding.detail
    # Es una inferencia nuestra sobre la superficie, no un dato oficial.
    assert finding.confidence is Confidence.MEDIA
    # El ratio se declara para que cualquiera pueda rehacer la cuenta.
    assert "15 a 25 m²" in finding.detail


def test_solar_never_estimates_payback():
    """Amortización y coste dependen del nudo de evacuación y del precio de venta."""
    finding = interpret.solar_finding(1680.0, area_m2=100_000.0)
    assert "No se estima plazo de amortización" in finding.detail
    for palabra in ("años de amortización", "€/kWh", "coste de instalación"):
        assert palabra not in finding.detail


def test_solar_names_what_blocks_the_project():
    monte = [{"crop": "MONTE BAJO", "intensity": "07", "area_m2": 100_000.0}]
    finding = interpret.solar_finding(
        1680.0, area_m2=100_000.0, subplots=monte, near_protected=True
    )
    assert "cambio de uso" in finding.detail
    assert "evaluación ambiental" in finding.detail


def test_an_intersecting_flow_zone_is_never_folded_into_the_group():
    """Si la ZFP toca la parcela es el hallazgo que bloquea: no se cuenta con las demás."""
    findings = interpret.layer_findings(
        [
            flood_hit("snczi_zfp", 0.04),
            period("snczi_t10", distance_m=900.0),
            period("snczi_t100", distance_m=880.0),
            period("snczi_t500", distance_m=880.0),
        ]
    )
    assert len(findings) == 2
    zfp = next(f for f in findings if "inundable cartografiada" not in f.title)
    assert zfp.severity is Severity.INCIDENCIA
    grupo = next(f for f in findings if "inundable" in f.title and f is not zfp)
    assert "flujo preferente" not in grupo.detail


def test_a_feature_with_several_names_does_not_swallow_the_sentence():
    """El SNCZI encadena todas las denominaciones de una lámina en un solo campo."""
    hit = period(
        "snczi_t500",
        distance_m=1_240.0,
    )
    hit.nearest_name = (
        "Arroyo de Calancha antes de arroyo de la Rehoya; "
        "Arroyo de Calancha después de arroyo de la Rehoya; Arroyo de la Rehoya"
    )
    finding = interpret.layer_findings([hit])[0]

    assert "«Arroyo de Calancha antes de arroyo de la Rehoya» y otras 2 denominaciones" in (
        finding.detail
    )
    # No se recorta en silencio: que hay más nombres se dice.
    assert "Arroyo de la Rehoya;" not in finding.detail


def test_a_single_name_is_left_exactly_as_the_cartography_says_it():
    finding = interpret.layer_findings([period("snczi_t500", distance_m=1_240.0)])[0]
    assert "«Río Turienzo»" in finding.detail
    assert "denominaciones" not in finding.detail


def test_a_small_parcel_gets_no_plant_sizing():
    """0,7 ha admiten la cuenta, pero el resultado se leería como una oportunidad falsa."""
    finding = interpret.solar_finding(1680.0, area_m2=7_193.0)
    assert "kWp con estructura fija" not in finding.detail
    assert "autoconsumo" in finding.detail
    assert "288" not in finding.detail


def test_a_mid_sized_parcel_is_sized_but_warned():
    finding = interpret.solar_finding(1680.0, area_m2=30_000.0)
    assert "kWp con estructura fija" in finding.detail
    assert "no estudia una parcela suelta" in finding.detail


def test_a_large_parcel_keeps_the_plain_estimate():
    finding = interpret.solar_finding(1680.0, area_m2=1_270_096.0)
    assert "kWp con estructura fija" in finding.detail
    assert "no estudia una parcela suelta" not in finding.detail


def test_tree_cover_is_stated_as_the_first_obstacle():
    """Decirlo de pasada tras «aptitud elevada» invita a leer como oportunidad un pinar."""
    pinar = [{"crop": "PINAR PINEA O DE FRUTO", "area_m2": 7_014.0, "intensity": "02"}]
    finding = interpret.solar_finding(1680.0, area_m2=7_193.0, subplots=pinar)
    assert "descuaje" in finding.detail
    assert "puede denegarse" in finding.detail


def test_the_preview_never_ships_the_detail_it_withholds():
    """Ocultar el detalle con CSS no es ocultarlo: se lee en el inspector.

    Este test fija el contrato del payload de vista previa —severidad, título y fuente, y
    nada más— para que el detalle no pueda colarse al añadir un campo más adelante.
    """
    from app.reports.schemas import Finding

    finding = Finding(
        severity=Severity.INCIDENCIA,
        title="Un cauce atraviesa la parcela",
        detail="El eje discurre 2.619 m dentro de la parcela…",
        source="IGN",
        confidence=Confidence.MEDIA,
    )
    preview = {"severity": finding.severity.value, "title": finding.title, "source": finding.source}

    assert set(preview) == {"severity", "title", "source"}
    assert finding.detail not in str(preview)
