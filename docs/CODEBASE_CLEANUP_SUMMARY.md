# Codebase Cleanup Summary — ODOS_MOBILE_BACKEND

**Date:** July 27, 2026  
**Scope:** Dead code removal, dependency cleanup, helper inlining

## Files Removed (2)

| File | Reason |
|------|--------|
| `app/controllers/__init__.py` | Orphan barrel; zero importers |
| `app/schemas/__init__.py` | Orphan barrel; zero importers |

## Code Removed

- **No-op `_require_admin()`** in `campaign_controller.py` — 8 call sites removed; route layer already enforces admin RBAC
- **`GHANA_CAMPAIGN_TAGS` alias** — replaced with direct `PROMO_CAMPAIGN_TAGS` imports from `promo_banner_config`
- **Wrapper functions** `_normalize_slug` / `_normalize_string_list` in `admin_controller.py` — inlined to `_slugify` / `_normalize_list`

## Dependencies Removed

| Package | Reason |
|---------|--------|
| `passlib` | Unused; passwords hashed via `bcrypt` directly |
| `sentry-sdk` | Listed but never integrated |

## Route Module Fix

- Updated `app/routes/__init__.py` to include `delivery` and `reviews` in imports and `__all__`

## Verification

- `python -m pytest` — 50 passed
- `python -m compileall app` — clean

## Remaining Technical Debt

- Large controller files (`admin_controller.py` ~3,715 lines, `vendor_controller.py` ~2,651 lines)
- Duplicate auth guards across admin/vendor/chat controllers
- Duplicate slugify implementations (admin, vendor, campaign, taxonomy)
- Admin vs vendor taxonomy resolution divergence (static vs DB-backed)
- `finance_controller.py` naming mismatch (acts as service layer)
- No ruff/black/mypy CI; pytest in production `requirements.txt`
- Test gaps: payments webhooks, admin CRUD, assistant, chat, delivery

## Recommendations

1. Consolidate `require_admin` / `require_vendor_access` into `app/core/`
2. Extract shared `slugify.py` and list normalization helpers
3. Unify admin product taxonomy on `product_taxonomy.resolve_product_taxonomy()`
4. Split pytest into `requirements-dev.txt`
5. Add ruff + GitHub Actions CI
