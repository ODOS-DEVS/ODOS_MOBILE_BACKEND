# ODOS Mobile Backend

ODOS Mobile Backend is the FastAPI API for the ODOS ecosystem. It serves the mobile shopper app and the admin dashboard, including auth, account data, catalog, stores, orders, notifications, and admin management flows.

Mobile repo:

`/Users/paul/Desktop/DeV/odos-workspace/odos-mobile-expo`

Admin repo:

`/Users/paul/Desktop/DeV/odos-workspace/ODOS_ADMIN`

## Stack

- FastAPI
- SQLAlchemy 2
- Alembic
- PostgreSQL
- Pydantic Settings
- JWT auth
- Brevo transactional email

## Current Backend Coverage

- Email/password signup and login
- Email verification and password reset codes
- Google auth backend support
- Profile, address, and payment-method APIs
- Wishlist and cart persistence
- Catalog products, categories, markets, and stores
- Order creation and lifecycle actions
- Notification event storage and read state
- Expo push token registration
- Admin auth, dashboard, vendors, users, orders, notifications, markets, stores, categories, and products

## New Catalog/Admin Capabilities

- Admin-created stores
- Category image uploads
- Category subcategory lists stored in the database
- Product links to one or more category slugs and one or more subcategory slugs
- Dynamic ODOS taxonomy seed data in the migration layer
- Public catalog filtering by category and subcategory for the mobile app

## Prerequisites

- Python 3.11+
- PostgreSQL
- virtualenv support
- Brevo account if you want real email delivery

## Setup

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create environment file:

```bash
cp .env.example .env
```

## Environment Variables

Example:

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

## Database

Run migrations:

```bash
alembic upgrade head
```

Important:

- run migrations before using the latest admin category/store/product features
- the latest migration seeds the ODOS category taxonomy and adds category/product taxonomy fields

## Run

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Useful URLs:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/api/health`

Use `--host 0.0.0.0` for real-device mobile testing on the same network.

## Route Groups

### Shopper-facing

- `/api/auth/*`
- `/api/account/*`
- `/api/cart*`
- `/api/wishlist*`
- `/api/catalog/*`
- `/api/orders*`
- `/api/notifications*`
- `/api/health`

### Admin-facing

- `/api/admin/auth/*`
- `/api/admin/dashboard`
- `/api/admin/users*`
- `/api/admin/vendors*`
- `/api/admin/vendor-applications*`
- `/api/admin/markets*`
- `/api/admin/stores*`
- `/api/admin/categories*`
- `/api/admin/products*`
- `/api/admin/orders*`
- `/api/admin/notifications*`

## Project Structure

```text
app/
  controllers/
  core/
  models/
  routes/
  schemas/
  services/

alembic/
  versions/
```

## Notable Files

- [app/main.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/main.py:1)
- [app/routes/admin.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/routes/admin.py:1)
- [app/controllers/admin_controller.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/controllers/admin_controller.py:1)
- [app/routes/catalog.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/routes/catalog.py:1)
- [app/controllers/catalog_controller.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/controllers/catalog_controller.py:1)
- [app/core/catalog_taxonomy.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/core/catalog_taxonomy.py:1)
- [alembic/versions/c4d8e1b7a2f0_add_category_media_and_product_taxonomy.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/alembic/versions/c4d8e1b7a2f0_add_category_media_and_product_taxonomy.py:1)

## Verification

Syntax check on changed files:

```bash
python3 -m py_compile app/**/*.py
```

Targeted checks I used for the recent catalog/admin change included `app/routes/admin.py`, `app/controllers/admin_controller.py`, `app/routes/catalog.py`, `app/controllers/catalog_controller.py`, and the latest Alembic migration.

## Notes

- Media uploads are used for category images and store assets.
- Admin product creation now accepts store assignment plus multiple category/subcategory links.
- The mobile app uses the backend category taxonomy directly, so category naming and subcategory structure should be managed carefully from admin.
