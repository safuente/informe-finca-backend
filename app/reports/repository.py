from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.reports.models import Report
from app.shared.base_repository import BaseRepository


class ReportRepository(BaseRepository[Report]):
    model = Report

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)

    async def get_by_token(self, token: str) -> Report | None:
        result = await self.db.execute(select(Report).where(Report.token == token))
        return result.scalar_one_or_none()

    async def list_by_email(self, email: str, limit: int = 20) -> list[Report]:
        result = await self.db.execute(
            select(Report)
            .where(Report.email == email)
            .order_by(Report.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
