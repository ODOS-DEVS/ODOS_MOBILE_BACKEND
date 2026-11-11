from __future__ import annotations

import re
from html import escape, unescape
from textwrap import dedent

import requests

from app.core.config import settings

BREVO_TRANSACT_ENDPOINT = "https://api.brevo.com/v3/smtp/email"

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _strip_html_to_text(fragment: str) -> str:
    """Reduce a caller-built HTML fragment (e.g. a heading with <strong>
    around already-escaped values) to plain text for the text/plain part
    of an email."""
    return unescape(_HTML_TAG_PATTERN.sub("", fragment))


# --------------------------------------------------------------------------
# Shared layout
#
# One shell for every email ODOS sends, in the storefront's own palette --
# warm paper, ink text, ember accent -- so the inbox and the site read as the
# same brand. Tables rather than divs because Outlook still ignores most CSS,
# and every style inline because email clients strip <style> blocks.
# --------------------------------------------------------------------------

STOREFRONT_URL = "https://odos.market"
SUPPORT_EMAIL = "info@odos.market"

_INK = "#16140F"
_BODY = "#403C33"
_MUTED = "#6B675E"
_PAPER = "#FBFAF7"
_CARD = "#FFFFFF"
_LINE = "#E8E4DA"
_EMBER = "#CF4310"
_EMBER_SOFT = "#FDEBE3"
_EMBER_LINE = "#F5D0BD"

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _shell(*, preheader: str, body: str, audience_line: str) -> str:
    """The chrome around every email: preheader, wordmark, card, footer.

    `preheader` is the line inboxes show after the subject -- hidden in the
    body itself. `audience_line` is the footer's "why you got this".
    """
    safe_preheader = escape(preheader)
    return dedent(
        f"""\
        <!doctype html>
        <html lang="en">
          <body style="margin:0;padding:0;background:{_PAPER};">
            <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{safe_preheader}&nbsp;&#8199;&#847;&nbsp;&#8199;&#847;&nbsp;&#8199;&#847;&nbsp;&#8199;&#847;&nbsp;&#8199;&#847;&nbsp;&#8199;&#847;&nbsp;&#8199;&#847;&nbsp;&#8199;&#847;</div>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_PAPER};">
              <tr>
                <td align="center" style="padding:36px 16px 28px;">
                  <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="width:100%;max-width:560px;">
                    <tr>
                      <td style="background:{_CARD};border:1px solid {_LINE};border-radius:20px;padding:36px 40px 38px;font-family:{_FONT};">
                        <div style="font-size:21px;line-height:24px;font-weight:800;letter-spacing:-0.3px;color:{_INK};">ODOS<span style="color:{_EMBER};">.</span></div>
                        <div style="height:1px;background:{_LINE};margin:24px 0 28px;font-size:0;line-height:0;">&nbsp;</div>
        {body}
                      </td>
                    </tr>
                    <tr>
                      <td style="padding:22px 12px 0;font-family:{_FONT};font-size:12px;line-height:19px;color:{_MUTED};text-align:center;">
                        {escape(audience_line)}<br>
                        <a href="{STOREFRONT_URL}" style="color:{_MUTED};text-decoration:underline;">odos.market</a>
                        &nbsp;&middot;&nbsp;
                        <a href="mailto:{SUPPORT_EMAIL}" style="color:{_MUTED};text-decoration:underline;">{SUPPORT_EMAIL}</a>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </body>
        </html>"""
    )


def _h1(text_html: str) -> str:
    return f'<h1 style="margin:0 0 14px;font-size:23px;line-height:30px;font-weight:700;letter-spacing:-0.3px;color:{_INK};">{text_html}</h1>'


def _p(text_html: str, *, last: bool = False) -> str:
    margin = "0" if last else "0 0 16px"
    return f'<p style="margin:{margin};font-size:15px;line-height:24px;color:{_BODY};">{text_html}</p>'


def _fine(text_html: str, *, last: bool = False) -> str:
    """Small print -- the ignore-this / wasn't-you lines."""
    margin = "0" if last else "0 0 10px"
    return f'<p style="margin:{margin};font-size:13px;line-height:21px;color:{_MUTED};">{text_html}</p>'


def _code_panel(code: str) -> str:
    """The one-time code as the centrepiece of the email."""
    return dedent(
        f"""\
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:26px 0;">
          <tr>
            <td align="center" style="background:{_EMBER_SOFT};border:1px solid {_EMBER_LINE};border-radius:14px;padding:22px 16px 24px;">
              <div style="font-family:{_FONT};font-size:11px;line-height:16px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{_EMBER};">One-time code</div>
              <div style="margin-top:8px;font-family:{_FONT};font-size:38px;line-height:44px;font-weight:700;letter-spacing:9px;color:{_INK};">{escape(code)}</div>
            </td>
          </tr>
        </table>"""
    )


def _cta(label: str, url: str) -> str:
    return dedent(
        f"""\
        <table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0 26px;">
          <tr>
            <td style="border-radius:999px;background:{_EMBER};">
              <a href="{escape(url)}" style="display:inline-block;padding:13px 30px;font-family:{_FONT};font-size:15px;line-height:20px;font-weight:600;color:#FFFFFF;text-decoration:none;border-radius:999px;">{escape(label)}</a>
            </td>
          </tr>
        </table>"""
    )


def _detail_panel(rows: list[tuple[str, str]]) -> str:
    """Key/value facts -- an application summary, an alert's particulars."""
    tr = "".join(
        f'<tr>'
        f'<td style="padding:9px 18px 9px 0;font-family:{_FONT};font-size:13px;line-height:20px;color:{_MUTED};vertical-align:top;white-space:nowrap;">{escape(label)}</td>'
        f'<td style="padding:9px 0;font-family:{_FONT};font-size:14px;line-height:20px;font-weight:600;color:{_INK};vertical-align:top;">{escape(value)}</td>'
        f"</tr>"
        for label, value in rows
    )
    return dedent(
        f"""\
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:22px 0;background:{_PAPER};border:1px solid {_LINE};border-radius:14px;">
          <tr>
            <td style="padding:10px 20px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{tr}</table>
            </td>
          </tr>
        </table>"""
    )


def _greeting_name(full_name: str | None) -> str:
    """First name only -- 'Hi Paul', never 'Hi Paul Kwame Agyekum'."""
    cleaned = (full_name or "").strip()
    return escape(cleaned.split()[0]) if cleaned else "there"


def render_email_verification_email(
    *,
    full_name: str | None,
    code: str,
    expires_in_minutes: int,
) -> tuple[str, str, str]:
    name = _greeting_name(full_name)
    # The code up front in the subject: it shows in the inbox row and in
    # notification previews, so most people never have to open the email.
    subject = f"{code} is your ODOS code"
    body = (
        _h1("Confirm your email")
        + _p(f"Hi {name}, enter this code in the app to finish creating your account.")
        + _code_panel(code)
        + _fine(f"It expires in {expires_in_minutes} minutes. After that, just request a new one.")
        + _fine("Didn\u2019t sign up for ODOS? Ignore this email \u2014 nothing happens without the code.", last=True)
    )
    html_content = _shell(
        preheader=f"Your code is {code}. It expires in {expires_in_minutes} minutes.",
        body=body,
        audience_line="Sent because this address was used to sign up for ODOS.",
    )
    text_content = (
        f"Hi {(full_name or 'there').strip() or 'there'},\n\n"
        f"Your ODOS code: {code}\n\n"
        f"Enter it in the app to finish creating your account. It expires in {expires_in_minutes} minutes.\n\n"
        "Didn't sign up for ODOS? Ignore this email - nothing happens without the code."
    )
    return subject, html_content, text_content


def send_transactional_email(
    *,
    to_email: str,
    to_name: str | None,
    subject: str,
    html_content: str,
    text_content: str,
) -> None:
    if not settings.brevo_is_configured:
        raise RuntimeError("Brevo email sending is not configured.")

    response = requests.post(
        BREVO_TRANSACT_ENDPOINT,
        headers={
            "accept": "application/json",
            "api-key": settings.brevo_api_key,
            "content-type": "application/json",
        },
        json={
            "sender": {
                "name": settings.brevo_sender_name,
                "email": settings.brevo_sender_email,
            },
            "to": [
                {
                    "email": to_email,
                    **({"name": to_name} if to_name else {}),
                }
            ],
            "subject": subject,
            "htmlContent": html_content,
            "textContent": text_content,
        },
        timeout=15,
    )

    if response.status_code != 201:
        raise RuntimeError(
            f"Brevo email send failed with status {response.status_code}: {response.text}"
        )


def send_email_verification_code(
    *,
    to_email: str,
    to_name: str | None,
    code: str,
) -> None:
    subject, html_content, text_content = render_email_verification_email(
        full_name=to_name,
        code=code,
        expires_in_minutes=settings.email_verification_code_expire_minutes,
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def render_password_reset_email(
    *,
    full_name: str | None,
    code: str,
    expires_in_minutes: int,
) -> tuple[str, str, str]:
    name = _greeting_name(full_name)
    subject = "Reset your ODOS password"
    body = (
        _h1("Reset your password")
        + _p(f"Hi {name}, someone asked to reset the password for this ODOS account. If that was you, enter this code in the app.")
        + _code_panel(code)
        + _fine(f"It expires in {expires_in_minutes} minutes.")
        + _fine(
            "If this wasn\u2019t you, you can ignore it \u2014 your password doesn\u2019t change unless the code is used.",
            last=True,
        )
    )
    html_content = _shell(
        preheader=f"Your reset code is {code}. It expires in {expires_in_minutes} minutes.",
        body=body,
        audience_line="Sent because a password reset was requested for your ODOS account.",
    )
    text_content = (
        f"Hi {(full_name or 'there').strip() or 'there'},\n\n"
        f"Your ODOS password reset code: {code}\n\n"
        f"It expires in {expires_in_minutes} minutes.\n\n"
        "If this wasn't you, ignore this email - your password doesn't change unless the code is used."
    )
    return subject, html_content, text_content


def send_password_reset_code(
    *,
    to_email: str,
    to_name: str | None,
    code: str,
) -> None:
    subject, html_content, text_content = render_password_reset_email(
        full_name=to_name,
        code=code,
        expires_in_minutes=settings.password_reset_code_expire_minutes,
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def render_email_verified_success_email(
    *,
    full_name: str | None,
) -> tuple[str, str, str]:
    name = _greeting_name(full_name)
    subject = "Your email is verified"
    body = (
        _h1("You\u2019re all set")
        + _p(f"Hi {name}, your email is confirmed and your ODOS account is ready.")
        + _p("Markets, stores and deals from all over Ghana are waiting \u2014 and everything you order is delivered to your door.")
        + _cta("Start shopping", STOREFRONT_URL)
        + _fine("If you didn\u2019t verify an ODOS account just now, write to us \u2014 this address may have been used by someone else.", last=True)
    )
    html_content = _shell(
        preheader="Your ODOS account is ready.",
        body=body,
        audience_line="Sent to confirm a change on your ODOS account.",
    )
    text_content = (
        f"Hi {(full_name or 'there').strip() or 'there'},\n\n"
        "Your email is confirmed and your ODOS account is ready.\n\n"
        f"Start shopping: {STOREFRONT_URL}\n\n"
        f"If you didn't verify an ODOS account just now, write to {SUPPORT_EMAIL}."
    )
    return subject, html_content, text_content


def send_email_verified_success(
    *,
    to_email: str,
    to_name: str | None,
) -> None:
    subject, html_content, text_content = render_email_verified_success_email(
        full_name=to_name,
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def render_password_changed_success_email(
    *,
    full_name: str | None,
) -> tuple[str, str, str]:
    name = _greeting_name(full_name)
    subject = "Your ODOS password was changed"
    body = (
        _h1("Password changed")
        + _p(f"Hi {name}, the password on your ODOS account was just changed. If that was you, you\u2019re done \u2014 nothing else to do.")
        + _p(
            f'If it <strong style="color:{_INK};">wasn\u2019t</strong> you, someone else may have your old password. '
            f'Reset it from the sign-in screen straight away, then write to '
            f'<a href="mailto:{SUPPORT_EMAIL}" style="color:{_EMBER};font-weight:600;text-decoration:none;">{SUPPORT_EMAIL}</a> so we can check the account with you.'
        )
        + _fine("We send this note on every password change, every time. That\u2019s the point of it.", last=True)
    )
    html_content = _shell(
        preheader="If this was you, you\u2019re done. If not, reset your password now.",
        body=body,
        audience_line="Sent to confirm a change on your ODOS account.",
    )
    text_content = (
        f"Hi {(full_name or 'there').strip() or 'there'},\n\n"
        "The password on your ODOS account was just changed. If that was you, you're done.\n\n"
        f"If it wasn't you, reset your password from the sign-in screen straight away, then write to {SUPPORT_EMAIL} so we can check the account with you."
    )
    return subject, html_content, text_content


def send_password_changed_success(
    *,
    to_email: str,
    to_name: str | None,
) -> None:
    subject, html_content, text_content = render_password_changed_success_email(
        full_name=to_name,
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def render_vendor_application_pending_email(
    *,
    full_name: str | None,
    store_name: str,
    business_category: str,
    submitted_at_label: str,
) -> tuple[str, str, str]:
    name = _greeting_name(full_name)
    clean_store = store_name.strip() or "your store"
    subject = f"We\u2019ve received your application for {clean_store}"
    body = (
        _h1("Application received")
        + _p(f"Hi {name}, thanks for applying to sell on ODOS \u2014 here\u2019s what you sent us.")
        + _detail_panel(
            [
                ("Store", clean_store),
                ("Category", business_category.strip() or "\u2014"),
                ("Submitted", submitted_at_label),
            ]
        )
        + _p("A person on our team reviews every store \u2014 we check each application by hand rather than approving automatically. You\u2019ll get an email from us either way, usually within a couple of days.")
        + _fine("Nothing to do in the meantime. If any of the details above are wrong, just reply to this email.", last=True)
    )
    html_content = _shell(
        preheader=f"We\u2019re reviewing {clean_store} \u2014 you\u2019ll hear from us soon.",
        body=body,
        audience_line="Sent because you applied to sell on ODOS.",
    )
    text_content = (
        f"Hi {(full_name or 'there').strip() or 'there'},\n\n"
        f"Thanks for applying to sell on ODOS.\n\n"
        f"Store: {clean_store}\n"
        f"Category: {business_category}\n"
        f"Submitted: {submitted_at_label}\n\n"
        "A person on our team reviews every store. You'll get an email either way, usually within a couple of days.\n\n"
        "If any of the details above are wrong, just reply to this email."
    )
    return subject, html_content, text_content


def send_vendor_application_pending_email(
    *,
    to_email: str,
    to_name: str | None,
    store_name: str,
    business_category: str,
    submitted_at_label: str,
) -> None:
    subject, html_content, text_content = render_vendor_application_pending_email(
        full_name=to_name,
        store_name=store_name,
        business_category=business_category,
        submitted_at_label=submitted_at_label,
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def render_vendor_application_approved_email(
    *,
    full_name: str | None,
    store_name: str,
) -> tuple[str, str, str]:
    name = _greeting_name(full_name)
    clean_store = store_name.strip() or "your store"
    safe_store = escape(clean_store)
    subject = f"{clean_store} is approved \u2014 welcome to ODOS"
    body = (
        _h1(f"{safe_store} is live")
        + _p(f"Hi {name}, we\u2019ve reviewed your application and approved it. Your Seller Center is open from today.")
        + _p("Add your products, set stock and prices, and you\u2019re trading \u2014 orders, chats with shoppers, and your earnings all live in one place.")
        + _cta("Open your Seller Center", f"{STOREFRONT_URL}/seller")
        + _fine(
            f'Questions about getting set up? Reply to this email or write to <a href="mailto:{SUPPORT_EMAIL}" style="color:{_MUTED};">{SUPPORT_EMAIL}</a> \u2014 a real person answers.',
            last=True,
        )
    )
    html_content = _shell(
        preheader="Your Seller Center is open \u2014 add products and start taking orders.",
        body=body,
        audience_line="Sent because you applied to sell on ODOS.",
    )
    text_content = (
        f"Hi {(full_name or 'there').strip() or 'there'},\n\n"
        f"{clean_store} has been approved. Your Seller Center is open from today:\n\n"
        f"{STOREFRONT_URL}/seller\n\n"
        "Add your products, set stock and prices, and you're trading - orders, chats with shoppers, and your earnings all live in one place.\n\n"
        f"Questions? Reply to this email or write to {SUPPORT_EMAIL}."
    )
    return subject, html_content, text_content


def send_vendor_application_approved_email(
    *,
    to_email: str,
    to_name: str | None,
    store_name: str,
) -> None:
    subject, html_content, text_content = render_vendor_application_approved_email(
        full_name=to_name,
        store_name=store_name,
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def _render_admin_alert_email(
    *,
    eyebrow: str,
    heading: str,
    summary_title: str,
    summary_rows: list[tuple[str, str]],
    cta_label: str,
    cta_url: str | None,
) -> tuple[str, str]:
    """Internal alerts to the ODOS team: something needs a decision.

    `heading` arrives as an HTML fragment (callers bold the key names), so it
    is trusted here and escaped by the callers.
    """
    body = (
        f'<div style="font-family:{_FONT};font-size:11px;line-height:16px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{_EMBER};margin-bottom:10px;">{escape(eyebrow)}</div>'
        + f'<h1 style="margin:0 0 6px;font-size:19px;line-height:27px;font-weight:600;letter-spacing:-0.2px;color:{_INK};">{heading}</h1>'
        + _detail_panel(summary_rows)
        + (_cta(cta_label, cta_url) if cta_url else "")
        + _fine("Internal notification for the ODOS team.", last=True)
    )
    html_content = _shell(
        preheader=_strip_html_to_text(heading),
        body=body,
        audience_line="Sent to ODOS staff. Not a customer email.",
    )
    text_lines = [_strip_html_to_text(heading), "", f"{summary_title}:"]
    text_lines.extend(f"{label}: {value}" for label, value in summary_rows)
    if cta_url:
        text_lines.extend(["", f"{cta_label}: {cta_url}"])
    return html_content, "\n".join(text_lines)


def send_admin_vendor_application_email(
    *,
    to_email: str,
    to_name: str | None,
    store_name: str,
    business_category: str,
    applicant_name: str,
    city: str,
    region: str,
    submitted_at_label: str,
    application_id: str,
    admin_panel_url: str,
) -> None:
    subject = f"New vendor application: {store_name}"
    html_content, text_content = _render_admin_alert_email(
        eyebrow="Vendor application",
        heading=f"<strong>{escape(applicant_name)}</strong> submitted a new vendor application for <strong>{escape(store_name)}</strong> — it needs your review.",
        summary_title="Application summary",
        summary_rows=[
            ("Store name", store_name),
            ("Category", business_category),
            ("Location", f"{city}, {region}"),
            ("Applicant", applicant_name),
            ("Submitted", submitted_at_label),
        ],
        cta_label="Review application",
        cta_url=(
            f"{admin_panel_url.rstrip('/')}/vendor-applications/full/{application_id}"
            if admin_panel_url
            else None
        ),
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def send_admin_withdrawal_request_email(
    *,
    to_email: str,
    to_name: str | None,
    vendor_name: str,
    store_name: str,
    amount_label: str,
    payout_method: str,
    submitted_at_label: str,
    withdrawal_id: str,
    admin_panel_url: str,
) -> None:
    subject = f"Vendor withdrawal request: {amount_label}"
    html_content, text_content = _render_admin_alert_email(
        eyebrow="Withdrawal request",
        heading=f"<strong>{escape(vendor_name)}</strong> ({escape(store_name)}) requested a withdrawal of <strong>{escape(amount_label)}</strong>.",
        summary_title="Withdrawal summary",
        summary_rows=[
            ("Vendor", vendor_name),
            ("Store", store_name),
            ("Amount", amount_label),
            ("Payout method", payout_method),
            ("Submitted", submitted_at_label),
        ],
        cta_label="Review withdrawal",
        cta_url=(
            f"{admin_panel_url.rstrip('/')}/payouts/full/{withdrawal_id}"
            if admin_panel_url
            else None
        ),
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def send_admin_delivery_problem_email(
    *,
    to_email: str,
    to_name: str | None,
    order_number: str,
    customer_name: str,
    store_name: str,
    reason_label: str,
    details: str | None,
    reported_at_label: str,
    order_id: str,
    admin_panel_url: str,
) -> None:
    subject = f"Delivery problem reported: order #{order_number}"
    html_content, text_content = _render_admin_alert_email(
        eyebrow="Delivery problem",
        heading=(
            f"<strong>{escape(customer_name)}</strong> reported a delivery problem on order "
            f"<strong>#{escape(order_number)}</strong> — settlement is on hold until this is resolved."
        ),
        summary_title="Report summary",
        summary_rows=[
            ("Order", f"#{order_number}"),
            ("Store", store_name),
            ("Customer", customer_name),
            ("Reason", reason_label),
            ("Details", details or "—"),
            ("Reported", reported_at_label),
        ],
        cta_label="Open Delivery Ops",
        cta_url=(
            f"{admin_panel_url.rstrip('/')}/orders/full/{order_id}" if admin_panel_url else None
        ),
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def send_admin_return_request_email(
    *,
    to_email: str,
    to_name: str | None,
    customer_name: str,
    order_number: str,
    item_title: str,
    request_type: str,
    quantity: int,
    submitted_at_label: str,
    return_request_id: str,
    admin_panel_url: str,
) -> None:
    subject = f"Return request: order #{order_number}"
    html_content, text_content = _render_admin_alert_email(
        eyebrow="Return request",
        heading=(
            f"<strong>{escape(customer_name)}</strong> requested a {escape(request_type)} on order "
            f"<strong>#{escape(order_number)}</strong> — it needs your review."
        ),
        summary_title="Request summary",
        summary_rows=[
            ("Order", f"#{order_number}"),
            ("Item", item_title),
            ("Type", request_type.capitalize()),
            ("Quantity", str(quantity)),
            ("Customer", customer_name),
            ("Submitted", submitted_at_label),
        ],
        cta_label="Review request",
        cta_url=(
            f"{admin_panel_url.rstrip('/')}/returns/full/{return_request_id}"
            if admin_panel_url
            else None
        ),
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def send_admin_flash_sale_nomination_email(
    *,
    to_email: str,
    to_name: str | None,
    store_name: str,
    product_title: str,
    submitted_at_label: str,
    admin_panel_url: str,
) -> None:
    subject = f"Flash sale nomination: {product_title}"
    html_content, text_content = _render_admin_alert_email(
        eyebrow="Flash sale nomination",
        heading=(
            f"<strong>{escape(store_name)}</strong> nominated <strong>{escape(product_title)}</strong> "
            "for a flash sale."
        ),
        summary_title="Nomination summary",
        summary_rows=[
            ("Store", store_name),
            ("Product", product_title),
            ("Submitted", submitted_at_label),
        ],
        cta_label="Review nominations",
        cta_url=(
            f"{admin_panel_url.rstrip('/')}/flash-sale-events/full" if admin_panel_url else None
        ),
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def send_admin_campaign_opt_in_email(
    *,
    to_email: str,
    to_name: str | None,
    vendor_name: str,
    campaign_title: str,
    product_title: str,
    submitted_at_label: str,
    admin_panel_url: str,
) -> None:
    subject = f"Campaign opt-in: {campaign_title}"
    html_content, text_content = _render_admin_alert_email(
        eyebrow="Campaign opt-in",
        heading=(
            f"<strong>{escape(vendor_name)}</strong> submitted <strong>{escape(product_title)}</strong> "
            f"for the <strong>{escape(campaign_title)}</strong> campaign."
        ),
        summary_title="Opt-in summary",
        summary_rows=[
            ("Campaign", campaign_title),
            ("Product", product_title),
            ("Vendor", vendor_name),
            ("Submitted", submitted_at_label),
        ],
        cta_label="Review campaign",
        cta_url=(
            f"{admin_panel_url.rstrip('/')}/merchandising-campaigns/full"
            if admin_panel_url
            else None
        ),
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def send_admin_voucher_review_email(
    *,
    to_email: str,
    to_name: str | None,
    store_name: str,
    voucher_code: str,
    voucher_title: str,
    reward_text: str,
    submitted_at_label: str,
    voucher_id: str,
    admin_panel_url: str,
) -> None:
    subject = f"Vendor voucher awaiting approval: {voucher_code}"
    html_content, text_content = _render_admin_alert_email(
        eyebrow="Voucher review",
        heading=f"<strong>{escape(store_name)}</strong> created a voucher that needs approval before it can go live.",
        summary_title="Voucher summary",
        summary_rows=[
            ("Store", store_name),
            ("Code", voucher_code),
            ("Title", voucher_title),
            ("Reward", reward_text),
            ("Submitted", submitted_at_label),
        ],
        cta_label="Review voucher",
        cta_url=(
            f"{admin_panel_url.rstrip('/')}/vouchers/full/{voucher_id}"
            if admin_panel_url
            else None
        ),
    )
    send_transactional_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )
