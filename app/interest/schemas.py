from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field

from app.parcels.schemas import REFCAT_PATTERN


class InterestCreate(BaseModel):
    """Aviso de interés desde la lista de espera de la web.

    Acepta los nombres de campo del formulario tal cual los envía hoy y también los
    canónicos: la web se escribió para un servicio de formularios externo, y aceptar
    ambos permite cambiar de proveedor —o volver a él si esto se cae— sin desplegar
    el frontend. Los campos que sobren se ignoran.
    """

    model_config = ConfigDict(extra="ignore")

    refcat: str = Field(
        ...,
        pattern=REFCAT_PATTERN,
        validation_alias=AliasChoices("refcat", "referencia_catastral"),
        description="Referencia catastral de la parcela que interesa",
    )
    email: EmailStr = Field(..., description="Correo de quien pregunta")
    message: str = Field(
        "",
        max_length=2000,
        validation_alias=AliasChoices("message", "mensaje"),
        description="Texto libre; lo rellena la web",
    )
