# ODOS Mobile Backend

ODOS Mobile Backend is a FastAPI backend for the ODOS mobile app. It currently provides authentication, user persistence, JWT session handling, database migrations, and the base structure for growing into a full marketplace backend.

This repository is the backend/API project. The Expo mobile client lives in a separate frontend repository/folder.

## Current Status

The backend is currently set up with:

- FastAPI app bootstrapped and running
- PostgreSQL database connection through SQLAlchemy
- Alembic migrations
- `users` table
- `user_auth_accounts` table for provider-linked auth accounts
- email/password signup
- email/password login
- JWT bearer auth
- current-user endpoint
- logout endpoint
- Google auth backend support

Important current realities:

- the mobile app is currently using normal email/password auth
- Google auth support exists on the backend, but the frontend is not using it in the current Expo Go flow
- broader marketplace entities like products, carts, orders, stores, and payments are not implemented yet

## Tech Stack

- FastAPI
- Uvicorn
- SQLAlchemy 2
- Alembic
- PostgreSQL
- Psycopg 3
- Pydantic Settings
- bcrypt / JWT auth
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

alembic/
  versions/

alembic.ini
requirements.txt
.env.example
```

## Prerequisites

- Python 3.11+
- PostgreSQL
- pgAdmin optional, for database inspection

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

Create your env file from the example:

```bash
cp .env.example .env
```

Then fill in real values.

Example env:

```env
DATABASE_URL=postgresql+psycopg://odos_user:your_password@localhost:5432/odos_mobile
SECRET_KEY=replace-this-with-a-long-random-secret-at-least-32-characters
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080
GOOGLE_CLIENT_IDS=your-google-web-client-id.apps.googleusercontent.com,your-google-ios-client-id.apps.googleusercontent.com
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

Using `--host 0.0.0.0` is important if the mobile app will connect from a real phone on the same network.

## Auth Endpoints

Current auth endpoints:

- `POST /api/auth/signup`
- `POST /api/auth/login`
- `POST /api/auth/google`
- `GET /api/auth/me`
- `POST /api/auth/logout`

Support endpoint:

- `GET /api/health`

## Auth Flow

### Email/Password

1. frontend calls `POST /api/auth/signup`
2. backend creates the user with a hashed password
3. frontend calls `POST /api/auth/login`
4. backend returns a JWT bearer token
5. frontend stores the token and uses it in the `Authorization` header
6. frontend calls `GET /api/auth/me` to restore/load the user

### Google

Google auth support exists on the backend through `POST /api/auth/google`.

The backend:

- verifies the Google ID token
- checks the token audience against configured Google client IDs
- finds or creates the local ODOS user
- links provider identity in `user_auth_accounts`
- returns the normal ODOS JWT bearer token

This endpoint is ready on the backend, but the current Expo Go frontend flow is not using it.

## User Model

The user model currently supports:

- UUID primary key
- full name
- email
- optional phone number
- nullable hashed password
- avatar URL
- date of birth
- role
- active/verified flags
- timestamps
- last login timestamp

Linked provider accounts are stored separately in `user_auth_accounts`.

## Migrations

Alembic is already set up.

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

## Troubleshooting

### Backend starts but mobile app cannot connect

Check:

- backend is running with `--host 0.0.0.0`
- frontend is using the correct Mac LAN IP
- your phone and Mac are on the same network

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

1. add user profile update endpoints
2. add password reset flow
3. add product, store, market, and category models
4. add cart and order models
5. add checkout/order creation endpoints
6. add tests for auth and user flows
