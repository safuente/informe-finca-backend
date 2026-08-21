"""El aviso de interés es la única señal de la lista de espera: si se pierde, se pierde
el lead. De ahí que se pruebe el contrato completo, incluido el fallo del SMTP."""

import pytest

from app.api.deps import get_redis
from app.interest import service
from app.main import app


@pytest.fixture(autouse=True)
def sin_redis():
    """El limitador por IP necesita Redis, que aquí no hay.

    Se sustituye por None a propósito en vez de desactivar el limitador: así se
    ejercita también su tolerancia a fallos, que es lo que evita que un Redis caído
    tumbe el endpoint en producción.
    """
    app.dependency_overrides[get_redis] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def sent(monkeypatch):
    """Captura los correos en vez de enviarlos."""
    calls = []

    def fake_send(to, subject, body, *, attachment=None, reply_to=""):
        calls.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to})
        return True

    monkeypatch.setattr(service, "send_email", fake_send)
    return calls


async def test_notifica_con_refcat_y_correo(client, sent):
    response = await client.post(
        "/api/v1/interest",
        json={"refcat": "24155A11600027", "email": "quien@pregunta.es"},
    )

    assert response.status_code == 202
    assert len(sent) == 1
    assert sent[0]["subject"] == "Interés en informe · 24155A11600027"
    assert "Tiene interés en que el informe sea generado." in sent[0]["body"]
    assert "24155A11600027" in sent[0]["body"]
    # Responder al aviso tiene que escribir a quien preguntó, no a nosotros mismos.
    assert sent[0]["reply_to"] == "quien@pregunta.es"


async def test_acepta_los_nombres_del_formulario_web(client, sent):
    """La web envía `referencia_catastral` y `mensaje`; cambiar el frontend no puede
    ser requisito para que esto funcione."""
    response = await client.post(
        "/api/v1/interest",
        json={
            "referencia_catastral": "24155A11600027",
            "email": "quien@pregunta.es",
            "mensaje": "La quiero para placas",
            "access_key": "sobra y se ignora",
        },
    )

    assert response.status_code == 202
    assert "La quiero para placas" in sent[0]["body"]


async def test_rechaza_referencia_invalida(client, sent):
    response = await client.post(
        "/api/v1/interest",
        json={"refcat": "NO-ES-UNA-REFCAT", "email": "quien@pregunta.es"},
    )

    assert response.status_code == 422
    assert not sent


async def test_si_el_smtp_falla_responde_502(client, monkeypatch):
    """La web no debe llevar a la pantalla de gracias si el aviso no ha salido."""

    def fake_send(*args, **kwargs):
        raise OSError("SMTP caído")

    monkeypatch.setattr(service, "send_email", fake_send)

    response = await client.post(
        "/api/v1/interest",
        json={"refcat": "24155A11600027", "email": "quien@pregunta.es"},
    )

    assert response.status_code == 502
