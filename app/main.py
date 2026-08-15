from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logger import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

DESCRIPTION = """
API de informefinca.es — due diligence de fincas rústicas a partir de fuentes públicas
oficiales (Catastro, IGN, MITECO, PVGIS, Copernicus).

Sin cuentas de usuario: el pago identifica el pedido y un token opaco identifica el
informe. La vista previa es gratuita y limitada por IP.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("%s starting (env=%s)", settings.app_name, settings.environment)
    yield
    await app.state.redis.aclose()


app = FastAPI(
    title=settings.app_name,
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug or settings.environment != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
