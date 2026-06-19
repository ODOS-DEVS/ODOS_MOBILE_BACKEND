from pydantic import BaseModel

from app.schemas.catalog import ProductRead


class RecommendationFeedRead(BaseModel):
    title: str
    subtitle: str | None = None
    personalized: bool = False
    products: list[ProductRead]
