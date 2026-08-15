from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.base_model import Base


class BaseRepository[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, id_: int) -> ModelT | None:
        return await self.db.get(self.model, id_)

    async def list(self, limit: int = 50, offset: int = 0) -> list[ModelT]:
        result = await self.db.execute(select(self.model).limit(limit).offset(offset))
        return list(result.scalars().all())

    async def create(self, obj: ModelT) -> ModelT:
        self.db.add(obj)
        await self.db.commit()
        await self.db.refresh(obj)
        return obj

    async def update(self, obj: ModelT, **fields: object) -> ModelT:
        for key, value in fields.items():
            setattr(obj, key, value)
        await self.db.commit()
        await self.db.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.db.delete(obj)
        await self.db.commit()
