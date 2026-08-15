from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.payments.models import Payment
from app.shared.base_repository import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)

    async def get_by_event_id(self, event_id: str) -> Payment | None:
        result = await self.db.execute(select(Payment).where(Payment.stripe_event_id == event_id))
        return result.scalar_one_or_none()

    async def get_by_session_id(self, session_id: str) -> Payment | None:
        result = await self.db.execute(
            select(Payment).where(Payment.stripe_session_id == session_id)
        )
        return result.scalar_one_or_none()
