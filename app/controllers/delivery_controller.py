from app.schemas.delivery import (
    DeliveryOptionRead,
    DeliveryPackageQuoteRead,
    DeliveryQuoteRead,
    DeliveryQuoteRequest,
)
from app.services.delivery_service import (
    get_delivery_config,
    quote_delivery,
    resolve_active_delivery_method,
)
from app.services.order_package_service import (
    group_checkout_items,
    group_products_for_cart,
)
from app.services.package_pricing_service import (
    PackageGroup,
    build_package_delivery_options,
    packages_shipping_total,
    quote_packages,
)


def get_delivery_quote(payload: DeliveryQuoteRequest, db) -> DeliveryQuoteRead:
    """Price delivery for a cart.

    Two paths, and the difference is whether the caller told us what is in the
    basket. With items we group them by shop and price each shop's package on
    its own terms — which is what the customer will actually be charged. Without
    them we fall back to the old flat quote, so a client that hasn't been
    updated still gets a sane answer instead of an error.
    """
    config = get_delivery_config(db)

    if not payload.items:
        quote = quote_delivery(
            subtotal=payload.subtotal,
            region=payload.region,
            city=payload.city,
            selected_method=payload.selected_method,
            config=config,
        )
        options = quote["options"]
        selected_method = quote["selected_method"]
        shipping_amount = float(quote["shipping_amount"])
        package_reads: list[DeliveryPackageQuoteRead] = []
        same_day_cutoff_passed = bool(quote["same_day_cutoff_passed"])
    else:
        snapshot = group_products_for_cart(
            db, [item.product_id for item in payload.items]
        )
        groups = group_checkout_items(db, payload.items, snapshot)
        if not groups:
            # Every product in the cart is unknown or has no vendor. Price it
            # as a single platform-default package rather than as free.
            groups = [
                PackageGroup(
                    vendor_user_id=None,
                    store_id=None,
                    store_name=None,
                    store=None,
                    items_subtotal=payload.subtotal,
                )
            ]

        options = build_package_delivery_options(
            groups=groups,
            region=payload.region,
            city=payload.city,
            config=config,
        )
        selected_method = resolve_active_delivery_method(options, payload.selected_method)
        # The per-package sum is authoritative, not the option's headline
        # amount: they are built from the same quotes, and this is the figure
        # the breakdown below itemises.
        package_quotes = quote_packages(groups, selected_method, config)
        shipping_amount = packages_shipping_total(package_quotes)
        package_reads = [
            DeliveryPackageQuoteRead(
                store_id=quote.store_id,
                store_name=quote.store_name,
                items_subtotal=quote.items_subtotal,
                delivery_fee=quote.delivery_fee,
                fee_waived=quote.fee_waived,
                free_threshold=quote.free_threshold,
                amount_to_free_delivery=quote.amount_to_free_delivery,
            )
            for quote in package_quotes
        ]
        same_day_option = next((o for o in options if o.id == "same_day"), None)
        same_day_cutoff_passed = bool(
            same_day_option is not None and not same_day_option.available
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
            for option in options
        ],
        selected_method=selected_method,
        shipping_amount=shipping_amount,
        free_shipping_threshold=float(config.free_shipping_threshold),
        same_day_cutoff_passed=same_day_cutoff_passed,
        packages=package_reads,
    )
