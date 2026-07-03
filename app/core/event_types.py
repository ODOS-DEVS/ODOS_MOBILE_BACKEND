"""Canonical system event type constants."""

# User lifecycle
USER_SIGNUP = "user.signup"
USER_LOGIN = "user.login"
USER_LOGIN_FAILED = "user.login_failed"
USER_LOGOUT = "user.logout"
USER_GOOGLE_AUTH = "user.google_auth"
USER_PROFILE_UPDATED = "user.profile_updated"

# Commerce (also mirrored from behavior where applicable)
PRODUCT_VIEW = "commerce.product_view"
SEARCH_QUERY = "commerce.search_query"
CART_UPDATED = "commerce.cart_updated"
CHECKOUT_STARTED = "commerce.checkout_started"
PAYMENT_ATTEMPT = "commerce.payment_attempt"
ORDER_CREATED = "commerce.order_created"
ORDER_STATUS_CHANGED = "commerce.order_status_changed"

# Admin operations
ADMIN_PRODUCT_MUTATION = "admin.product_mutation"
ADMIN_PRICE_CHANGED = "admin.price_changed"
ADMIN_INVENTORY_CHANGED = "admin.inventory_changed"
ADMIN_USER_MUTATION = "admin.user_mutation"
ADMIN_ROLE_CHANGED = "admin.role_changed"
ADMIN_ORDER_MUTATION = "admin.order_mutation"
ADMIN_REFUND_MUTATION = "admin.refund_mutation"
ADMIN_VENDOR_MUTATION = "admin.vendor_mutation"
ADMIN_FINANCE_MUTATION = "admin.finance_mutation"
ADMIN_SETTINGS_MUTATION = "admin.settings_mutation"

# Promotions
PROMO_CREATED = "promo.created"
PROMO_UPDATED = "promo.updated"
PROMO_DELETED = "promo.deleted"
PROMO_APPLIED = "promo.applied"
PROMO_REJECTED = "promo.rejected"

# System / security
API_REQUEST_FAILED = "system.api_request_failed"
AUTH_FAILURE = "system.auth_failure"
RATE_LIMIT_TRIGGERED = "system.rate_limit_triggered"
SUSPICIOUS_ACTIVITY = "system.suspicious_activity"

ACTOR_USER = "user"
ACTOR_ADMIN = "admin"
ACTOR_SYSTEM = "system"
ACTOR_ANONYMOUS = "anonymous"
