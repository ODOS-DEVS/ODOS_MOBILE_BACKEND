# ODOS Mobile Backend

ODOS Mobile Backend is the FastAPI API for the ODOS mobile app.

It currently handles:

- email/password signup
- email verification by code
- email/password login
- bearer-token auth
- current-user lookup
- profile updates
- forgot password
- password reset by code
- logout response
- Google auth backend support

This repository is the backend/API project. The Expo mobile client lives separately at:

`/Users/paul/Desktop/DeV/odos-workspace/odos-mobile-expo`

## Current Status

The backend is in a solid development state for auth and user account flows.

Implemented now:

- FastAPI app bootstrapped and running
- PostgreSQL via SQLAlchemy
- Alembic migrations
- `users` table
- `user_auth_accounts` table for provider-linked auth accounts
- email verification email sending via Brevo
- password reset email sending via Brevo
- verification-success email
- password-changed confirmation email
- JWT bearer auth
- current-user endpoint
- profile update endpoint
- Google ID token verification support

Important current realities:

- the mobile app currently uses email/password auth in Expo Go
- Google auth support exists on the backend, but the current frontend flow is not using it
- products, carts, orders, stores, payments, and catalog entities are still not implemented

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
    auth_controller.py
  core/
    auth.py
    config.py
    database.py
    google_auth.py
    security.py
  models/
    __init__.py
    user.py
  routes/
    auth.py
    health.py
  schemas/
    __init__.py
    user.py
  services/
    email_service.py

alembic/
  versions/

alembic.ini
requirements.txt
.env.example
```

## Prerequisites

- Python 3.11+
- PostgreSQL
- pgAdmin optional, for inspection
- a Brevo account if you want email verification and password reset delivery

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

Start the server:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Useful URLs:

- API docs: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/api/health`

Using `--host 0.0.0.0` is important if a real phone on the same network will connect to the API.

## Auth Endpoints

Current auth endpoints:

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

Support endpoint:

- `GET /api/health`

## Auth Flow

### Email/Password Signup

1. frontend calls `POST /api/auth/signup`
2. backend creates the user with a hashed password
3. backend creates a 6-digit email verification code
4. backend stores the hashed code and expiry
5. backend sends the verification email through Brevo

### Email Verification

1. frontend sends the code to `POST /api/auth/verify-email`
2. backend validates the code and expiry
3. backend marks `is_verified = true`
4. backend clears the verification code fields
5. backend sends an email-verification success email

### Login

1. frontend calls `POST /api/auth/login`
2. backend verifies password
3. backend returns a bearer token plus current user
4. frontend uses the token for `/api/auth/me`

### Forgot Password

1. frontend calls `POST /api/auth/forgot-password`
2. backend generates a 6-digit reset code
3. backend stores the hashed reset code and expiry
4. backend sends the reset email through Brevo
5. frontend calls `POST /api/auth/verify-reset-code`
6. backend returns a short-lived password reset token
7. frontend calls `POST /api/auth/reset-password`
8. backend updates the password hash
9. backend sends a password-changed confirmation email

### Google Auth

Google auth support exists through `POST /api/auth/google`.

The backend:

- verifies the Google ID token
- checks the token audience against configured client IDs
- finds or creates the local ODOS user
- links provider identity in `user_auth_accounts`
- returns the normal ODOS bearer token

This is ready on the backend, but the current Expo Go frontend flow is not using it.

## User Model

The user model currently supports:

- UUID primary key
- full name
- email
- optional phone number
- nullable hashed password
- avatar URL
- date of birth
- gender
- city
- region
- role
- active/verified flags
- email verification code hash / expiry / sent time
- password reset code hash / expiry / sent time
- last login timestamp
- created/updated timestamps

Provider-linked auth accounts are stored separately in `user_auth_accounts`.

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
EXPO_PUBLIC_API_URL=http://10.11.24.79:8000/api
```

If using a real phone:

- backend must run with `--host 0.0.0.0`
- phone and Mac must be on the same Wi‑Fi

## Important Files

- [app/main.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/main.py)
- [app/routes/auth.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/routes/auth.py)
- [app/controllers/auth_controller.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/controllers/auth_controller.py)
- [app/core/security.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/core/security.py)
- [app/core/auth.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/core/auth.py)
- [app/core/google_auth.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/core/google_auth.py)
- [app/models/user.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/models/user.py)
- [app/schemas/user.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/schemas/user.py)
- [app/services/email_service.py](/Users/paul/Desktop/DeV/odos-workspace/ODOS_MOBILE_BACKEND/app/services/email_service.py)

## Troubleshooting

### Backend starts but mobile app cannot connect

Check:

- backend is running with `--host 0.0.0.0`
- frontend is using the correct Mac LAN IP
- your phone and Mac are on the same network

### Verification or reset emails are not arriving

Check:

- `BREVO_API_KEY` is a real API key, not an SMTP password
- `BREVO_SENDER_EMAIL` is verified in Brevo
- backend has been restarted after `.env` changes
- Brevo transactional logs show successful delivery

### `401` on `/api/auth/me`

Check:

- frontend is sending `Authorization: Bearer <token>`
- token was stored correctly after login
- token is not expired
- `SECRET_KEY` is unchanged from when the token was issued

### Database migration issues

Check:

- PostgreSQL is running
- `DATABASE_URL` is correct
- the database user has privileges on the target database

### Google auth returns audience/config errors

Check:

- `GOOGLE_CLIENT_IDS` is set in `.env`
- backend has been restarted after changing `.env`
- the ID token comes from a client whose audience matches one of the configured client IDs

## Recommended Next Steps

1. add product, store, market, and category models
2. add cart and wishlist persistence
3. add order and checkout models/endpoints
4. add automated tests for auth and user flows
5. reintroduce a production-ready mobile Google auth flow later
