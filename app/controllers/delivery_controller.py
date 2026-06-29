from app.schemas.delivery import DeliveryOptionRead, DeliveryQuoteRead, DeliveryQuoteRequest
from app.services.delivery_service import get_delivery_config, quote_delivery


def get_delivery_quote(payload: DeliveryQuoteRequest, db) -> DeliveryQuoteRead:
    config = get_delivery_config(db)
    quote = quote_delivery(
        subtotal=payload.subtotal,
        region=payload.region,
        selected_method=payload.selected_method,
        config=config,
    )
    return DeliveryQuoteRead(
        options=[
            DeliveryOptionRead(
                id=option.id,
                title=option.title,
                subtitle=option.subtitle,
                eta=option.eta,
                amount=option.amount,
                badge=option.badge,
                available=option.available,
                unavailable_reason=option.unavailable_reason,
            )
            for option in quote["options"]
        ],
        selected_method=quote["selected_method"],
        shipping_amount=float(quote["shipping_amount"]),
        free_shipping_threshold=float(quote["free_shipping_threshold"]),
        same_day_cutoff_passed=bool(quote["same_day_cutoff_passed"]),
    )
