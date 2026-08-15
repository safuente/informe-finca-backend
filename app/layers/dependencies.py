from app.api.deps import DbSession
from app.layers.repository import LayerRepository
from app.layers.service import LayerService


def get_layer_service(db: DbSession) -> LayerService:
    return LayerService(LayerRepository(db))
