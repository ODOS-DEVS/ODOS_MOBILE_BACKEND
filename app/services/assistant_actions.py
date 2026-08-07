from __future__ import annotations

from app.models import User
from app.schemas.assistant import AssistantActionRead
from app.services.assistant_context import AssistantUserSnapshot


def derive_suggested_actions(
    *,
    message: str,
    reply: str,
    screen: str | None,
    reference_context: dict[str, str] | None,
    is_vendor: bool,
    user: User | None,
    snapshot: AssistantUserSnapshot | None,
) -> list[AssistantActionRead]:
    """Deterministically pick 1-3 relevant deep-link actions from screen context,
    keywords, and account state.

    Used for the streamed reply path, where the model no longer emits structured
    suggested_actions JSON (it streams plain natural-language text instead). This
    is also more reliable than trusting free-text model output for navigation
    targets, since routes are picked from a fixed, validated set.
    """
    text = f"{message}\n{reply}".lower()
    actions: list[AssistantActionRead] = []

    def add(label: str, route: str, params: dict[str, str] | None = None) -> None:
        if len(actions) >= 3 or any(a.route == route for a in actions):
            return
        actions.append(AssistantActionRead(label=label, route=route, params=params))

    store_id = reference_context.get("store_id") if reference_context else None
    store_name = reference_context.get("store_name") if reference_context else None

    if user is None and any(
        word in text for word in ("order", "voucher", "account", "wallet", "sign in", "log in")
    ):
        add("Sign in", "/(root)/(auth)/signin")

    if store_id:
        add(
            "Open store",
            "/screens/stores/[id]",
            {"id": store_id, **({"title": store_name} if store_name else {})},
        )

    if is_vendor and any(
        word in text
        for word in ("vendor", "seller", "payout", "my store", "inventory", "stock")
    ):
        add("Vendor dashboard", "/vendor/dashboard")
        if "order" in text:
            add("Vendor orders", "/vendor/orders")
        if "wallet" in text or "payout" in text or "earning" in text:
            add("Vendor wallet", "/vendor/wallet")

    if any(word in text for word in ("track", "order", "delivery status", "where is my")):
        if user is not None and snapshot and snapshot.latest_order_id:
            add(
                f"Order #{snapshot.latest_order_number}"
                if snapshot.latest_order_number
                else "Latest order",
                "/(root)/screens/profileScreens/orders/[orderId]",
                {"orderId": snapshot.latest_order_id},
            )
        else:
            add("View orders", "/screens/profileScreens/orders")

    if any(word in text for word in ("voucher", "coupon", "promo", "discount code")):
        add("My vouchers", "/screens/profileScreens/Account/Vouchers")

    if any(word in text for word in ("return", "refund", "exchange")):
        add("Returns", "/screens/profileScreens/Account/Returns")

    if any(word in text for word in ("checkout", "pay now", "place order")):
        add("Go to checkout", "/(root)/screens/Checkout")
    elif "cart" in text:
        add("View cart", "/(root)/(tabs)/cart")

    if any(word in text for word in ("deal", "flash sale", "discount", "offer")):
        add("Browse deals", "/screens/deals")

    if store_id and store_name and any(
        word in text for word in ("message", "chat", "ask the store", "contact the store")
    ):
        add(
            "Message store",
            "/screens/productDetails/chat/[vendorId]",
            {"vendorId": store_id, "vendorName": store_name},
        )

    if not actions and any(word in text for word in ("find", "search", "looking for", "shop for")):
        add("Search ODOS", "/screens/search")

    return actions[:3]
