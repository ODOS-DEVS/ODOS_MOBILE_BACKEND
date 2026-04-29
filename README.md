# ODOS Mobile Backend

ODOS Mobile Backend is the FastAPI API for the ODOS mobile app.

It now covers much more than auth: user accounts, saved addresses and payment methods, catalog data, wishlist/cart persistence, orders, notification events, and email-driven verification flows.

The Expo client lives separately at:

`/Users/paul/Desktop/DeV/odos-workspace/odos-mobile-expo`

## Current Status

The backend is in a solid development state for the current mobile app flow.

### Implemented now

- email/password signup
- email verification by code
- sign in with JWT bearer tokens
- current-user lookup
- profile updates
- forgot password
- password reset by code
- logout response
- Google auth backend support
- Brevo transactional emails
- wishlist persistence
- cart persistence
- categories, products, stores, and markets
- order creation and order lifecycle actions
- notification event storage
- notification read state
- Expo push token registration
- saved addresses
- saved payment methods

### Still simplified or not yet production-complete

- live payment gateway integration
- vendor/admin dashboards
- advanced stock/inventory management
- robust push notification delivery testing flow
- automated test coverage

Important reality:

- the mobile app currently uses the email/password flow in Expo Go
- Google auth support exists here, but the current Expo Go client is not using it
- payment methods are persisted for UX flow, but the app is **not** charging real cards or MoMo yet

## Tech Stack

- FastAPI
- Uvicorn
- SQLAlchemy 2
- Alembic
- PostgreSQL
- Psycopg 3
- Pydantic Settings
- bcrypt
- PyJWT
- Brevo transactional email API
- Google ID token verification via `google-auth`

## Project Structure

```text
app/
  main.py
  controllers/
    account_controller.py
    auth_controller.py
    cart_controller.py
    catalog_controller.py
    notification_controller.py
    order_controller.py
    wishlist_controller.py
  core/
    auth.py
    config.py
    database.py
    google_auth.py
    security.py
  models/
    account.py
    catalog.py
    notification.py
    order.py
    user.py
  routes/
    account.py
    auth.py
    cart.py
    catalog.py
    health.py
    notifications.py
    orders.py
    wishlist.py
  schemas/
    account.py
    catalog.py
    notification.py
    order.py
    user.py
  services/
    email_service.py
    push_service.py

alembic/
  versions/
```

## Prerequisites

- Python 3.11+
- PostgreSQL
- pgAdmin optional, for inspection
- a Brevo account if you want verification and password reset emails to deliver

## Environment Setup

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your env file:

```bash
cp .env.example .env
```

Then fill in real values.

## Example Environment

```env
DATABASE_URL=postgresql+psycopg://odos_user:your_password@localhost:5432/odos_mobile
SECRET_KEY=replace-this-with-a-long-random-secret-at-least-32-characters
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080
GOOGLE_CLIENT_IDS=your-google-web-client-id.apps.googleusercontent.com,your-google-ios-client-id.apps.googleusercontent.com

BREVO_API_KEY=your-brevo-api-key
BREVO_SENDER_NAME=ODOS
BREVO_SENDER_EMAIL=your-verified-sender@example.com
EMAIL_VERIFICATION_CODE_EXPIRE_MINUTES=10
PASSWORD_RESET_CODE_EXPIRE_MINUTES=10
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES=15
```

## Database Setup

Create a PostgreSQL database and user, then point `DATABASE_URL` at it.

Typical local setup:

- database: `odos_mobile`
- user: `odos_user`
- password: your local password

Run migrations:

```bash
alembic upgrade head
```

## Run The Backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Useful URLs:

- API docs: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/api/health`

Use `--host 0.0.0.0` if a real phone on the same network will connect to the API.

## Route Groups

### Auth

- `POST /api/auth/signup`
- `POST /api/auth/login`
- `POST /api/auth/google`
- `POST /api/auth/verify-email`
- `POST /api/auth/resend-verification-code`
- `POST /api/auth/forgot-password`
- `POST /api/auth/verify-reset-code`
- `POST /api/auth/reset-password`
- `GET /api/auth/me`
- `PATCH /api/auth/me`
- `POST /api/auth/logout`

### Account

- `GET /api/account/addresses`
- `POST /api/account/addresses`
- `PATCH /api/account/addresses/{address_id}`
- `POST /api/account/addresses/{address_id}/default`
- `DELETE /api/account/addresses/{address_id}`
- `GET /api/account/payment-methods`
- `POST /api/account/payment-methods`
- `POST /api/account/payment-methods/{payment_method_id}/default`
- `DELETE /api/account/payment-methods/{payment_method_id}`

### Shopping State

- `GET /api/wishlist`
- `POST /api/wishlist`
- `DELETE /api/wishlist/{product_id}`
- `GET /api/cart`
- `POST /api/cart`
- `PATCH /api/cart/{product_id}`
- `DELETE /api/cart/{product_id}`
- `DELETE /api/cart`

### Catalog

- `GET /api/catalog/categories`
- `GET /api/catalog/products`
- `GET /api/catalog/products/{product_id}`
- `GET /api/catalog/markets`
- `GET /api/catalog/stores`
- `GET /api/catalog/stores/{store_id}`

### Orders

- `GET /api/orders`
- `GET /api/orders/{order_id}`
- `POST /api/orders`
- `PATCH /api/orders/{order_id}/cancel`
- `PATCH /api/orders/{order_id}/deliver`
- `DELETE /api/orders/{order_id}`

### Notifications

- `GET /api/notifications`
- `GET /api/notifications/read-state`
- `POST /api/notifications/read-state`
- `POST /api/notifications/push-token`

### Support

- `GET /api/health`

## Core Flows

### Signup and Verification

1. frontend calls `POST /api/auth/signup`
2. backend creates the user with a hashed password
3. backend creates a 6-digit email verification code
4. backend stores the hashed code and expiry
5. backend sends the verification email through Brevo
6. user verifies via `POST /api/auth/verify-email`
7. backend sends a verification-success email

### Login

1. frontend calls `POST /api/auth/login`
2. backend verifies password
3. backend returns a bearer token plus current user
4. frontend uses the token for `/api/auth/me`

### Forgot Password

1. frontend calls `POST /api/auth/forgot-password`
2. backend creates a 6-digit reset code
3. backend stores the hashed reset code and expiry
4. backend sends the reset email through Brevo
5. frontend verifies the code through `POST /api/auth/verify-reset-code`
6. backend returns a short-lived reset token
7. frontend calls `POST /api/auth/reset-password`
8. backend updates the password hash
9. backend sends a password-changed confirmation email

### Saved Account Details

The backend persists:

- delivery addresses
- default address
- saved payment methods
- default payment method

For cards, the backend stores display-safe data for the current mock checkout flow:

- cardholder name
- last 4 digits
- expiry

It does **not** permanently store CVV.

### Orders

Orders support:

- `buy now` and cart-sourced checkout
- address and payment snapshots
- order items
- order totals
- processing status
- cancellation
- delivery confirmation
- receipt and activity integration

### Notifications

The backend now stores real notification events for things like:

- account ready
- email verified
- password changed
- order placed
- order delivered
- order cancelled

Read state is stored separately, and the mobile app uses this for its in-app Activity feed.

## User and Domain Models

The system now includes:

- `users`
- `user_auth_accounts`
- `wishlist_items`
- `cart_items`
- `categories`
- `products`
- `markets`
- `stores`
- `orders`
- `order_items`
- `notification_events`
- `notification_reads`
- `saved_addresses`
- `saved_payment_methods`

## Migrations

Apply all migrations:

```bash
alembic upgrade head
```

Create a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe change"
```

## Frontend Connection

The frontend should point its env to this backend:

```env
EXPO_PUBLIC_API_URL=http://YOUR-MAC-LAN-IP:8000/api
```

Example:

```env
EXPO_PUBLIC_API_URL=http://172.20.10.2:8000/api
```

If using a real phone:

- backend must run with `--host 0.0.0.0`
- phone and Mac must be on the same Wi‑Fi

## Troubleshooting

### Mobile app cannot connect

Check:

- backend is running with `--host 0.0.0.0`
- `DATABASE_URL` is correct
- your phone and Mac are on the same network

### Verification or reset emails are not arriving

Check:

- `BREVO_API_KEY` is a real API key, not an SMTP password
- `BREVO_SENDER_EMAIL` is verified in Brevo
- backend has been restarted after `.env` changes
- Brevo transactional logs show successful delivery

### Activity feed is empty

Check:

- `/api/notifications` returns `200`
- `/api/notifications/read-state` returns `200`
- backend is running on the latest code

### Saved address or payment method is not sticking

Check:

- user is signed in
- `alembic upgrade head` has been run
- `/api/account/addresses` and `/api/account/payment-methods` return successfully

### Google auth returns audience/config errors

Check:

- `GOOGLE_CLIENT_IDS` is set in `.env`
- backend has been restarted after changing `.env`
- the ID token audience matches one of the configured client IDs

## Recommended Next Steps

1. add editing support for saved payment methods
2. introduce a real payment gateway
3. add vendor/admin flows for order fulfillment
4. add automated tests for core auth, orders, and account flows
5. revisit a production-ready mobile Google auth path later
