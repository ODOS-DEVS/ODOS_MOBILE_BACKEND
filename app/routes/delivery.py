from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.delivery_controller import get_delivery_quote
from app.core.database import get_db
from app.schemas.delivery import DeliveryQuoteRead, DeliveryQuoteRequest

router = APIRouter(prefix="/delivery", tags=["delivery"])


@router.post("/quote", response_model=DeliveryQuoteRead)
def delivery_quote(payload: DeliveryQuoteRequest, db: Session = Depends(get_db)):
    return get_delivery_quote(payload, db)
