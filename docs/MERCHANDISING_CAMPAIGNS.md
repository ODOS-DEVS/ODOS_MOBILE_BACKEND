# Merchandising Campaigns — Phase 16 Report

Promotions in this document mean **marketing campaigns** (Summer Sale, Back To School, Vendor Spotlight), **not** checkout vouchers/discount codes.

## 1. Existing implementation found

Before this work, marketing discovery was fragmented:

| Primitive | Role |
|-----------|------|
| `PromoBanner` | Hero creatives with schedule + deep links |
| `FlashSaleEvent` | Timed price overrides |
| `Product.placement_tags` / `section` | Ad-hoc catalog placements |
| `Voucher.campaign_tag` | Seasonal grouping for **discount codes** only |
| Hardcoded `PROMO_CAMPAIGN_TAGS` | Tag labels duplicated in deals hub |

There was **no first-class campaign entity** for curated landing pages with product/category/store targeting.

## 2. Improvements made

### Backend
- Added `MerchandisingCampaign` + junction tables for products, categories, stores, and vendor opt-ins.
- Optional `PromoBanner.campaign_id` FK for linking banners to campaigns.
- Dynamic product resolution (no product copies): manual + pinned, categories, stores, approved opt-ins, legacy `placement_tags`, optional marketplace-wide include.
- Schedule-aware visibility (`starts_at` / `ends_at`, featured, priority, hide OOS).
- Customer APIs: `GET /catalog/campaigns`, `GET /catalog/campaigns/{slug}`.
- Admin APIs under `/admin/merchandising-campaigns*` (CRUD, duplicate, archive, opt-in review) gated by `promotions`.
- Vendor APIs: list open campaigns + submit product opt-ins.
- Deals hub now returns live `campaigns` and campaign sections.
- Consolidated duplicate tag lists to `PROMO_CAMPAIGN_TAGS`.
- Cache invalidation for campaign keys.

### Admin
- New Marketing → **Campaigns** brief + full studio.
- Create/edit/schedule/feature/priority, assign products (pin), categories, stores, duplicate, archive.

### Mobile
- Campaign landing: `screens/campaigns/[slug]`.
- Home featured campaigns strip.
- Deals hub campaign carousel.
- Banner `campaign` / `merchandising_campaign` links navigate to campaign landings.
- Image-ready gating + prefetch on campaign pages.

## 3. Database changes

Migration: `r8s9t0u1v2w3_add_merchandising_campaigns.py`

Tables:
- `merchandising_campaigns`
- `merchandising_campaign_products` (sort_order, is_pinned)
- `merchandising_campaign_categories`
- `merchandising_campaign_stores`
- `merchandising_campaign_opt_ins`

Column:
- `promo_banners.campaign_id` → FK `merchandising_campaigns.id`

Seed: seasonal tag campaigns inserted as inactive drafts so admins can activate/enrich them.

## 4. New relationships

```
MerchandisingCampaign
  ├── many MerchandisingCampaignProduct → Product
  ├── many MerchandisingCampaignCategory → category_slug
  ├── many MerchandisingCampaignStore → Store
  ├── many MerchandisingCampaignOptIn → Product (vendor pending/approved)
  └── referenced by PromoBanner.campaign_id (optional)
```

Flash sales and vouchers remain separate subsystems (pricing / checkout).

## 5. APIs modified / added

**Customer**
- `GET /api/catalog/campaigns?featured=&limit=`
- `GET /api/catalog/campaigns/{slug}?limit=&offset=`
- `GET /api/catalog/deals-hub` → includes `campaigns`

**Admin** (`RequirePromotionsAdmin`)
- `GET/POST /api/admin/merchandising-campaigns`
- `GET/PATCH/DELETE /api/admin/merchandising-campaigns/{id}`
- `POST /api/admin/merchandising-campaigns/{id}/duplicate`
- `GET /api/admin/merchandising-campaign-opt-ins`
- `POST /api/admin/merchandising-campaign-opt-ins/{id}/review`

**Vendor**
- `GET /api/vendor/merchandising-campaigns/open`
- `POST /api/vendor/merchandising-campaign-opt-ins`

## 6. Mobile changes

- `hooks/useMerchandisingCampaigns.ts`
- `app/(root)/screens/campaigns/[slug].tsx`
- Home + Deals discovery placements
- `utils/promoNavigation.ts` campaign routing

## 7. Admin changes

- `/merchandising-campaigns` + `/merchandising-campaigns/full`
- Sidebar entry under Marketing
- Permission feature: `promotions`

## 8. Performance improvements

- Campaign list/detail cache keys under `catalog:campaigns*`
- Product resolution capped and paginated
- Mobile prefetch of first product image batch
- Avoids duplicating product rows — always joins live catalog

## 9. Security improvements

- Admin campaign routes require `promotions` feature
- Vendor opt-ins only for own products + open campaigns (`allow_vendor_opt_in`)
- Opt-ins require admin approval before entering the product set
- Input validation on status/sort/visibility/schedule windows

## 10. Future extension points

- Sponsored / paid placements (`campaign_type`)
- Personalized ranking hooks in `resolve_campaign_products`
- Location-based visibility filters
- Rich analytics (impressions/clicks) without schema rewrite
- Image upload studio (URLs accepted today; upload helpers already exist)
- AI-curated collections can write into the same junction tables
- Link flash events via optional `flash_event_id` on campaigns later

## Apply migration

```bash
cd ODOS_MOBILE_BACKEND
alembic upgrade head
```
