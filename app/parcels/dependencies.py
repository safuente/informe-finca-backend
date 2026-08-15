from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.layers.repository import LayerRepository
from app.layers.service import LayerService
from app.parcels.repository import ParcelRepository
from app.parcels.service import ParcelService


def get_parcel_service(db: DbSession) -> ParcelService:
    return ParcelService(ParcelRepository(db), LayerService(LayerRepository(db)))


ParcelServiceDep = Annotated[ParcelService, Depends(get_parcel_service)]
