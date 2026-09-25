"""Public web pages: support, privacy and terms.

These exist because the App Store requires a Support URL and a Privacy Policy
URL that a reviewer can open, and both have to lead somewhere real -- a parked
domain or a dead link is a rejection under Guideline 1.5.

They are served from the API rather than a separate site so there is one thing
to deploy and one certificate to keep valid. The content mirrors the in-app
Legal & Policies screen; if one changes, change the other.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["public"], include_in_schema=False)

SUPPORT_EMAIL = "agyekumpaul07@gmail.com"

_STYLE = """
:root {
  --bg: #ffffff; --fg: #12151a; --muted: #5b6472;
  --line: #e6e9ef; --card: #f7f8fa; --accent: #12151a;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d0f13; --fg:#f2f4f7; --muted:#9aa3b2; --line:#232833; --card:#151920; --accent:#f2f4f7; }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 680px; margin: 0 auto; padding: 56px 22px 80px; }
h1 { font-size: 30px; line-height: 1.2; margin: 0 0 8px; letter-spacing: -0.02em; }
h2 { font-size: 19px; margin: 36px 0 10px; letter-spacing: -0.01em; }
p, li { color: var(--muted); }
a { color: var(--accent); }
.lede { font-size: 17px; margin: 0 0 4px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin: 18px 0; }
.card p:last-child, .card ul:last-child { margin-bottom: 0; }
ul { padding-left: 20px; }
li { margin: 7px 0; }
.meta { font-size: 13px; color: var(--muted); border-top: 1px solid var(--line); margin-top: 48px; padding-top: 18px; }
.nav { display: flex; gap: 18px; font-size: 14px; margin-bottom: 34px; }
.nav a { color: var(--muted); text-decoration: none; }
.nav a:hover { color: var(--fg); }
code { background: var(--card); border: 1px solid var(--line); border-radius: 5px; padding: 1px 6px; font-size: 14px; }
"""

_NAV = """<div class="nav">
  <a href="/support">Support</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a>
</div>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · ODOS</title>
<style>{_STYLE}</style>
</head><body><div class="wrap">{_NAV}{body}
<p class="meta">ODOS Market · Ghana · <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a></p>
</div></body></html>""")


@router.get("/support", response_class=HTMLResponse)
def support_page() -> HTMLResponse:
    return _page("Support", f"""
<h1>ODOS Support</h1>
<p class="lede">A marketplace for Ghana. Here's how to get help.</p>

<div class="card">
  <h2 style="margin-top:0">Contact us</h2>
  <p>Email <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a> and we'll reply,
  usually within one business day. Include your order number if your question is
  about an order — it's on the order's detail screen and starts with
  <code>ORD-</code>.</p>
</div>

<h2>Where is my order?</h2>
<p>Open the app, go to your profile and choose Orders. Each order shows its own
progress. If your order contains items from more than one shop, each shop
delivers its own part, so those parts can arrive separately and each shows its
own status.</p>

<h2>My order hasn't arrived</h2>
<p>You confirm delivery yourself — a seller cannot mark your order as delivered
on your behalf. If an order is marked out for delivery and nothing has arrived,
open the order and report the problem there. It goes to the seller and to us.</p>

<h2>Returns and refunds</h2>
<p>Eligible items can be returned within the stated return period in the
condition described. Start a return from the order screen. Refund timing depends
on how you paid — Mobile Money and card refunds are processed by the payment
provider after the return is verified.</p>

<h2>Payments</h2>
<p>We accept Mobile Money and cards. Every order total is calculated on our
servers, so the amount you are charged always matches the items in your order.
Your ODOS wallet holds refunds and store credit.</p>

<h2>Selling on ODOS</h2>
<p>Apply from your profile in the app. Once approved you get a storefront,
product and stock management, order handling, customer messaging and a wallet to
withdraw from. You arrange your own delivery and keep the delivery fee.</p>

<h2>Deleting your account</h2>
<p>In the app: Profile, then <strong>Delete account</strong>. It shows what is
removed and what is kept before you confirm.</p>
<p>Your personal details are removed. Past orders are kept without your name,
because they are also the seller's record of the sale and are needed for tax.
Deletion is not possible while an order is still in progress or while money
remains in your wallet — the app will tell you which applies.</p>
""")


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> HTMLResponse:
    return _page("Privacy Policy", f"""
<h1>Privacy Policy</h1>
<p class="lede">What ODOS collects, why, and what you can do about it.</p>

<h2>What we collect</h2>
<ul>
  <li><strong>Account details</strong> — your name, email and phone number, so
  you can sign in and so sellers can reach you about an order.</li>
  <li><strong>Delivery addresses</strong> — so orders can reach you.</li>
  <li><strong>Orders and payments</strong> — what you bought, from whom, and
  whether payment succeeded.</li>
  <li><strong>How you use the app</strong> — products viewed and searched, used
  to improve what we recommend. You can turn personalisation off in the app.</li>
  <li><strong>Location</strong> — only if you allow it, to estimate delivery and,
  for sellers, to place a shop on a map. The app works without it.</li>
</ul>

<h2>What we don't do</h2>
<p>We do not sell your personal information. We do not use it for advertising
outside ODOS.</p>

<h2>Who else sees it</h2>
<ul>
  <li><strong>Sellers you buy from</strong> — your name, delivery address and
  phone number, so they can deliver the order. Nothing else.</li>
  <li><strong>Payment providers</strong> — to take payment and issue refunds.
  Card details are handled by the provider and never stored by us.</li>
  <li><strong>Messaging and email providers</strong> — to send verification
  codes, receipts and order updates.</li>
</ul>

<h2>Your choices</h2>
<ul>
  <li>Edit or correct your details in the app at any time.</li>
  <li>Turn off personalisation, analytics and each type of notification
  separately in the app.</li>
  <li>Delete your account from Profile, then <strong>Delete account</strong>.
  Your personal details are removed. Past orders are kept without your name,
  because they are also the seller's record of the sale and are required for
  tax and accounting.</li>
</ul>

<h2>Keeping it safe</h2>
<p>Traffic is encrypted in transit. Passwords are stored hashed, never in a form
we can read. Access to production data is limited to what operating the service
requires.</p>

<h2>Children</h2>
<p>ODOS is not intended for children under 13, and we do not knowingly collect
their information.</p>

<h2>Questions</h2>
<p>Email <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
""")


@router.get("/terms", response_class=HTMLResponse)
def terms_page() -> HTMLResponse:
    return _page("Terms of Service", f"""
<h1>Terms of Service</h1>
<p class="lede">The agreement between you, ODOS and the sellers on it.</p>

<h2>What ODOS is</h2>
<p>ODOS is a marketplace. Independent sellers list and sell their own products,
and each one fulfils and delivers the orders they receive. Your purchase
contract for the goods is with the seller. ODOS runs the platform, handles
payment, and holds both sides to the rules below.</p>

<h2>Your account</h2>
<p>Keep your sign-in details to yourself and give accurate information,
particularly your delivery address and phone number. You can delete your
account from the app at any time.</p>

<h2>Orders and payment</h2>
<p>An order total is calculated on our servers at checkout and is what you are
charged. One order may include items from several sellers; each part is
fulfilled and delivered separately by the seller concerned.</p>
<p>You confirm delivery yourself. A seller cannot mark your order delivered on
your behalf, and a seller is only paid once delivery is confirmed, automatically
released after the stated window, or resolved by us where there is a dispute.</p>

<h2>Returns</h2>
<p>Eligible items may be returned within the stated period in the condition
described. Refunds are issued to the original payment method or to your ODOS
wallet once a return is verified.</p>

<h2>If you sell on ODOS</h2>
<p>Describe what you sell accurately, keep stock and prices current, fulfil
orders you accept, and handle returns within the stated terms. You arrange
delivery and keep the delivery fee. Commission is deducted from your settlement
at the rate shown in your seller wallet. We may suspend a shop that repeatedly
fails these.</p>

<h2>Acceptable use</h2>
<p>Don't list illegal or counterfeit goods, misrepresent what you are selling,
interfere with the service, or attempt to access accounts that are not yours.</p>

<h2>Changes</h2>
<p>These terms may change as the service does. Continuing to use ODOS after a
change means you accept the revised terms.</p>

<h2>Contact</h2>
<p>Email <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
""")
