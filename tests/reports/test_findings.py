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
