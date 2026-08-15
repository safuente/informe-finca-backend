from app.shared.base_repository import BaseRepository


class BaseService:
    """Thin orchestration layer. Subclass per domain and add real business rules."""

    def __init__(self, repository: BaseRepository) -> None:
        self.repository = repository
