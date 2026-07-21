# Authentication & Authorization System Report

**Date:** 2026-07-21  
**Repos:** `odos-mobile-expo` · `ODOS_MOBILE_BACKEND` · `ODOS_ADMIN`

---

## 1. Existing architecture

| Layer | Design |
|-------|--------|
| Mobile session | Single HS256 JWT in SecureStore (`odos_access_token`); hydrate via `GET /auth/me` |
| Admin session | Same JWT family in `localStorage`; hydrate via `GET /admin/auth/me` |
| Refresh tokens | Not used (long-lived access JWT) |
| Roles | `customer` / `vendor` / `admin`; dual-role sellers via `vendor_status=approved` |
| Social auth | Google via `expo-auth-session` → `POST /auth/google` |
| Password reset | Email OTP → short-lived JWT (`purpose=password_reset`) → set password |
| Email verify | Authed OTP; does not gate most commerce APIs |
| Phone verify | OTP + verified-phones list |
| Vendor lifecycle | apply → pending → admin approve/reject → Seller Center via workspace mode |
| Admin RBAC | Permission bands (`super_admin` … `analyst`) + feature matrix |

---

## 2. Issues discovered (audit)

### Critical
1. Password-reset JWTs were accepted as normal Bearer session tokens
2. Missing `create_notification_event` import could crash signup / verify / reset side-effects
3. EAS builds previously omitted Google iOS client IDs → Web-client fallback → Google `invalid_request` (fixed earlier in session)

### High
4. Cold-start hydrate cleared SecureStore on any network error (unexpected logouts)
5. Unverified users trapped by “Use a different account” without signing out
6. Admin permission bands mostly UI-only; APIs accepted any admin
7. Admin deep links had no permission route guard
8. No token revocation after password change / account block
9. Google could auto-link to unverified email/password accounts (takeover risk)
10. Reset OTP reusable until expiry (multiple reset JWTs)

### Medium / UX
11. `useRequireAuth` treated hydrating as authenticated
12. Vendor status default invented `"approved"` from missing data
13. `approveVendorLocally` dead/dangerous client API
14. Admin `canAccessRoute` failed open for unmapped paths
15. Admin API 401 did not clear session
16. Google errors reused “email or password incorrect”

---

## 3. Improvements implemented

### Backend
- Reject non-session JWTs in `get_current_user` / optional auth / websockets (`purpose` / `typ`)
- `users.token_version` + JWT claim `tv`; bump on password reset and admin block
- Import `create_notification_event` in auth controller
- Clear password-reset OTP when minting reset JWT (one-time)
- Google auto-link only when existing account is already verified
- Admin feature deps on users, vendors, applications, finance, payouts, orders, dashboard
- Guard admin self-lockout / non–super-admin changing other admins
- Unknown admin permission bands fail closed to `analyst`
- Allow Google-only accounts to set a password via forgot-password (earlier)
- Auth security unit tests (`tests/test_auth_security.py`)
- Migration `u1v2w3x4y5z6_add_user_token_version`

### Mobile
- Hydrate keeps token on network/5xx; clears only on 401 / blocked
- Verification “different account” now signs out first
- Dedicated Google auth error mapping
- `useRequireAuth` returns false while hydrating
- `normalizeVendorStatus` never invents approved
- Removed `approveVendorLocally`
- Password create screen no longer accepts URL `resetToken`
- Logout clears workspace mode + sends Bearer on logout attempt
- Google client IDs in `eas.json` (prior fix); no Web→native client fallback

### Admin
- `ProtectedRoute` enforces `canAccessRoute`
- Unmapped routes fail closed
- Landing page respects permissions
- Global 401 → clear token + redirect to login
- Unknown permission strings → `analyst`

---

## 4. API / database changes

| Change | Detail |
|--------|--------|
| JWT access claims | `typ=access`, `tv=<token_version>` |
| JWT reset claims | `purpose=password_reset`, `typ=password_reset` (rejected as Bearer) |
| DB | `users.token_version INTEGER NOT NULL DEFAULT 0` |
| Admin routes | Feature-gated Depends on sensitive endpoints |

No breaking response-shape changes for clients. Existing tokens without `tv` still work until password change (treated as `tv=0`).

---

## 5. Security enhancements

- Reset-token session privilege escalation closed
- Password change / block invalidates prior sessions via `token_version`
- OTP one-time for reset
- Google account-linking hardened
- Admin RBAC partially enforced on backend (critical surfaces)
- Admin frontend route + fail-closed permissions

---

## 6. Remaining recommendations

1. Short access TTL (15–60m) + refresh-token rotation
2. Apply `require_admin_feature` to remaining admin routes (products, stores, markets, support, …)
3. Super-admin permission management UI (endpoint exists)
4. Move admin JWT off `localStorage` / WS query string → HttpOnly cookie or short WS tickets
5. In-process rate-limit fallback when Redis is down
6. Optional: require `is_verified` for vendor apply / payouts
7. Create Android Google OAuth client + `EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID` for APK Google Sign-In
8. Run Alembic `u1v2w3x4y5z6` on Render and rebuild mobile for Google env

---

## 7. Test plan

- [ ] `pytest tests/test_auth_security.py`
- [ ] Signup → verify email → browse
- [ ] Forgot password → OTP → set password → old session rejected
- [ ] Google Sign-In on new TestFlight build (iOS client ID baked in)
- [ ] Unverified user: “Use a different account” reaches sign-in
- [ ] Airplane mode with valid token: app no longer wipes session on hydrate
- [ ] Admin analyst: cannot open `/finance` or call finance APIs
- [ ] Admin block user: target’s existing JWT stops working
- [ ] Vendor approve: mobile gains Seller Center after refresh
