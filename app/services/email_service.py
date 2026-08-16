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


def render_email_verification_email(
    *,
    full_name: str | None,
    code: str,
    expires_in_minutes: int,
) -> tuple[str, str, str]:
    safe_name = escape((full_name or "there").strip() or "there")
    safe_code = escape(code)
    subject = "Verify your ODOS email address"
    html_content = dedent(
        f"""
        <html>
          <body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;color:#374151;">
            <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
              <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:24px;overflow:hidden;">
                <div style="background:#66797F;padding:24px 28px;">
                  <div style="font-size:24px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">ODOS</div>
                  <div style="margin-top:8px;font-size:14px;line-height:22px;color:#E9EEF0;">
                    Your marketplace account is almost ready.
                  </div>
                </div>
                <div style="padding:28px;">
                  <p style="margin:0 0 16px;font-size:16px;line-height:26px;color:#374151;">
                    Hi {safe_name},
                  </p>
                  <p style="margin:0 0 18px;font-size:15px;line-height:26px;color:#4B5563;">
                    Use the verification code below to confirm your email address and finish setting up your ODOS account.
                  </p>
                  <div style="margin:24px 0;padding:20px 16px;border-radius:18px;background:#F1F3F5;border:1px solid #E5E7EB;text-align:center;">
                    <div style="font-size:12px;line-height:18px;color:#66797F;letter-spacing:1.8px;text-transform:uppercase;">
                      Verification code
                    </div>
                    <div style="margin-top:10px;font-size:34px;line-height:40px;font-weight:700;letter-spacing:8px;color:#696969;">
                      {safe_code}
                    </div>
                  </div>
                  <p style="margin:0 0 14px;font-size:14px;line-height:24px;color:#4B5563;">
                    This code expires in {expires_in_minutes} minutes.
                  </p>
                  <p style="margin:0;font-size:14px;line-height:24px;color:#6B7280;">
                    If you didn’t create an ODOS account, you can safely ignore this email.
                  </p>
                </div>
              </div>
            </div>
          </body>
        </html>
        """
    ).strip()
    text_content = (
        f"Hi {full_name or 'there'},\n\n"
        f"Use this verification code to confirm your ODOS email address: {code}\n\n"
        f"This code expires in {expires_in_minutes} minutes.\n\n"
        "If you didn't create an ODOS account, you can ignore this email."
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
    safe_name = escape((full_name or "there").strip() or "there")
    safe_code = escape(code)
    subject = "Reset your ODOS password"
    html_content = dedent(
        f"""
        <html>
          <body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;color:#374151;">
            <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
              <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:24px;overflow:hidden;">
                <div style="background:#696969;padding:24px 28px;">
                  <div style="font-size:24px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">ODOS</div>
                  <div style="margin-top:8px;font-size:14px;line-height:22px;color:#ECECEC;">
                    Secure password recovery for your account.
                  </div>
                </div>
                <div style="padding:28px;">
                  <p style="margin:0 0 16px;font-size:16px;line-height:26px;color:#374151;">
                    Hi {safe_name},
                  </p>
                  <p style="margin:0 0 18px;font-size:15px;line-height:26px;color:#4B5563;">
                    Use the code below to continue resetting your ODOS password.
                  </p>
                  <div style="margin:24px 0;padding:20px 16px;border-radius:18px;background:#F1F3F5;border:1px solid #E5E7EB;text-align:center;">
                    <div style="font-size:12px;line-height:18px;color:#66797F;letter-spacing:1.8px;text-transform:uppercase;">
                      Password reset code
                    </div>
                    <div style="margin-top:10px;font-size:34px;line-height:40px;font-weight:700;letter-spacing:8px;color:#696969;">
                      {safe_code}
                    </div>
                  </div>
                  <p style="margin:0 0 14px;font-size:14px;line-height:24px;color:#4B5563;">
                    This code expires in {expires_in_minutes} minutes.
                  </p>
                  <p style="margin:0;font-size:14px;line-height:24px;color:#6B7280;">
                    If you didn’t request this, you can ignore the email and your password will stay the same.
                  </p>
                </div>
              </div>
            </div>
          </body>
        </html>
        """
    ).strip()
    text_content = (
        f"Hi {full_name or 'there'},\n\n"
        f"Use this code to reset your ODOS password: {code}\n\n"
        f"This code expires in {expires_in_minutes} minutes.\n\n"
        "If you didn't request this, you can ignore this email."
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
    safe_name = escape((full_name or "there").strip() or "there")
    subject = "Your ODOS email has been verified"
    html_content = dedent(
        f"""
        <html>
          <body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;color:#374151;">
            <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
              <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:24px;overflow:hidden;">
                <div style="background:#66797F;padding:24px 28px;">
                  <div style="font-size:24px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">ODOS</div>
                  <div style="margin-top:8px;font-size:14px;line-height:22px;color:#E9EEF0;">
                    Your account is ready to go.
                  </div>
                </div>
                <div style="padding:28px;">
                  <p style="margin:0 0 16px;font-size:16px;line-height:26px;color:#374151;">
                    Hi {safe_name},
                  </p>
                  <p style="margin:0 0 18px;font-size:15px;line-height:26px;color:#4B5563;">
                    Your email address has been verified successfully. You can now continue exploring ODOS with full access to your account.
                  </p>
                  <div style="margin:24px 0;padding:18px 16px;border-radius:18px;background:#F1F3F5;border:1px solid #E5E7EB;text-align:center;">
                    <div style="font-size:16px;line-height:24px;font-weight:700;color:#696969;">
                      Verification complete
                    </div>
                  </div>
                  <p style="margin:0;font-size:14px;line-height:24px;color:#6B7280;">
                    If you did not complete this verification, please secure your account immediately.
                  </p>
                </div>
              </div>
            </div>
          </body>
        </html>
        """
    ).strip()
    text_content = (
        f"Hi {full_name or 'there'},\n\n"
        "Your ODOS email address has been verified successfully.\n\n"
        "If you did not complete this verification, please secure your account immediately."
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
    safe_name = escape((full_name or "there").strip() or "there")
    subject = "Your ODOS password was changed"
    html_content = dedent(
        f"""
        <html>
          <body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;color:#374151;">
            <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
              <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:24px;overflow:hidden;">
                <div style="background:#696969;padding:24px 28px;">
                  <div style="font-size:24px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">ODOS</div>
                  <div style="margin-top:8px;font-size:14px;line-height:22px;color:#ECECEC;">
                    Your account security has been updated.
                  </div>
                </div>
                <div style="padding:28px;">
                  <p style="margin:0 0 16px;font-size:16px;line-height:26px;color:#374151;">
                    Hi {safe_name},
                  </p>
                  <p style="margin:0 0 18px;font-size:15px;line-height:26px;color:#4B5563;">
                    Your ODOS password was changed successfully.
                  </p>
                  <div style="margin:24px 0;padding:18px 16px;border-radius:18px;background:#F1F3F5;border:1px solid #E5E7EB;text-align:center;">
                    <div style="font-size:16px;line-height:24px;font-weight:700;color:#696969;">
                      Password updated
                    </div>
                  </div>
                  <p style="margin:0;font-size:14px;line-height:24px;color:#6B7280;">
                    If you did not make this change, reset your password again right away and contact support.
                  </p>
                </div>
              </div>
            </div>
          </body>
        </html>
        """
    ).strip()
    text_content = (
        f"Hi {full_name or 'there'},\n\n"
        "Your ODOS password was changed successfully.\n\n"
        "If you did not make this change, reset your password again right away and contact support."
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
    safe_name = escape((full_name or "there").strip() or "there")
    safe_store_name = escape(store_name.strip() or "your store")
    safe_category = escape(business_category.strip() or "your category")
    safe_submitted_at = escape(submitted_at_label.strip())
    subject = "Your ODOS vendor application is pending review"
    html_content = dedent(
        f"""
        <html>
          <body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;color:#374151;">
            <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
              <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:24px;overflow:hidden;">
                <div style="background:#66797F;padding:24px 28px;">
                  <div style="font-size:24px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">ODOS</div>
                  <div style="margin-top:8px;font-size:14px;line-height:22px;color:#E9EEF0;">
                    We’ve received your vendor application and it is now under review.
                  </div>
                </div>
                <div style="padding:28px;">
                  <p style="margin:0 0 16px;font-size:16px;line-height:26px;color:#374151;">
                    Hi {safe_name},
                  </p>
                  <p style="margin:0 0 18px;font-size:15px;line-height:26px;color:#4B5563;">
                    Your ODOS vendor application for <strong>{safe_store_name}</strong> has been submitted successfully and is currently pending review.
                  </p>
                  <div style="margin:24px 0;padding:20px 18px;border-radius:18px;background:#F1F3F5;border:1px solid #E5E7EB;">
                    <div style="font-size:12px;line-height:18px;color:#66797F;letter-spacing:1.8px;text-transform:uppercase;">
                      Application summary
                    </div>
                    <div style="margin-top:12px;font-size:14px;line-height:24px;color:#374151;">
                      <div><strong>Store name:</strong> {safe_store_name}</div>
                      <div><strong>Category:</strong> {safe_category}</div>
                      <div><strong>Submitted:</strong> {safe_submitted_at}</div>
                      <div><strong>Status:</strong> Pending review</div>
                    </div>
                  </div>
                  <p style="margin:0 0 14px;font-size:14px;line-height:24px;color:#4B5563;">
                    Our team will review your store details and notify you once a decision has been made. If we need anything else, we’ll reach out directly.
                  </p>
                  <p style="margin:0;font-size:14px;line-height:24px;color:#6B7280;">
                    Thank you for choosing ODOS to grow your business.
                  </p>
                </div>
              </div>
            </div>
          </body>
        </html>
        """
    ).strip()
    text_content = (
        f"Hi {full_name or 'there'},\n\n"
        f"Your ODOS vendor application for {store_name} has been submitted successfully and is now pending review.\n\n"
        f"Category: {business_category}\n"
        f"Submitted: {submitted_at_label}\n"
        "Status: Pending review\n\n"
        "Our team will review your store details and notify you once a decision has been made.\n\n"
        "Thank you for choosing ODOS."
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
    safe_name = escape((full_name or "there").strip() or "there")
    safe_store_name = escape(store_name.strip() or "your store")
    subject = "Your ODOS vendor application has been approved"
    html_content = dedent(
        f"""
        <html>
          <body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;color:#374151;">
            <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
              <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:24px;overflow:hidden;">
                <div style="background:#696969;padding:24px 28px;">
                  <div style="font-size:24px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">ODOS</div>
                  <div style="margin-top:8px;font-size:14px;line-height:22px;color:#ECECEC;">
                    Your store is approved and ready for vendor management.
                  </div>
                </div>
                <div style="padding:28px;">
                  <p style="margin:0 0 16px;font-size:16px;line-height:26px;color:#374151;">
                    Hi {safe_name},
                  </p>
                  <p style="margin:0 0 18px;font-size:15px;line-height:26px;color:#4B5563;">
                    Great news. Your ODOS vendor application for <strong>{safe_store_name}</strong> has been approved.
                  </p>
                  <div style="margin:24px 0;padding:20px 18px;border-radius:18px;background:#F1F3F5;border:1px solid #E5E7EB;">
                    <div style="font-size:12px;line-height:18px;color:#66797F;letter-spacing:1.8px;text-transform:uppercase;">
                      What you can do now
                    </div>
                    <div style="margin-top:12px;font-size:14px;line-height:24px;color:#374151;">
                      <div>• Sign in to your vendor dashboard</div>
                      <div>• Add and manage products</div>
                      <div>• Update your store profile</div>
                      <div>• Start receiving and fulfilling orders</div>
                    </div>
                  </div>
                  <p style="margin:0 0 14px;font-size:14px;line-height:24px;color:#4B5563;">
                    Your store can now be managed inside ODOS, and approved products will be able to go live for shoppers.
                  </p>
                  <p style="margin:0;font-size:14px;line-height:24px;color:#6B7280;">
                    Welcome to the ODOS vendor community.
                  </p>
                </div>
              </div>
            </div>
          </body>
        </html>
        """
    ).strip()
    text_content = (
        f"Hi {full_name or 'there'},\n\n"
        f"Your ODOS vendor application for {store_name} has been approved.\n\n"
        "You can now sign in to your vendor dashboard, add products, update your store profile, and start receiving orders.\n\n"
        "Welcome to the ODOS vendor community."
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
    """Shared shell for admin-facing "this needs your attention" alerts —
    vendor applications, withdrawal requests, voucher review, etc. Returns
    (html_content, text_content); callers own their own subject line.

    `heading` is caller-built HTML (e.g. with <strong> around already-escaped
    values), not a plain string — it must NOT be escaped again here, or the
    emphasis tags render as literal text instead of bold."""
    safe_heading = heading
    safe_eyebrow = escape(eyebrow)
    rows_html = "".join(
        f'<div style="margin-top:8px;"><strong>{escape(label)}:</strong> {escape(value)}</div>'
        for label, value in summary_rows
    )
    cta_html = (
        f"""
        <div style="margin:26px 0 4px;text-align:center;">
          <a href="{escape(cta_url)}" style="display:inline-block;padding:13px 28px;border-radius:999px;background:#696969;color:#FFFFFF;font-size:14px;font-weight:700;text-decoration:none;">
            {escape(cta_label)}
          </a>
        </div>
        """
        if cta_url
        else ""
    )
    html_content = dedent(
        f"""
        <html>
          <body style="margin:0;padding:0;background:#F5F7FA;font-family:Arial,Helvetica,sans-serif;color:#374151;">
            <div style="max-width:560px;margin:0 auto;padding:32px 16px;">
              <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:24px;overflow:hidden;">
                <div style="background:#0F172A;padding:24px 28px;">
                  <div style="font-size:22px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;">ODOS Admin</div>
                  <div style="margin-top:8px;font-size:13px;line-height:20px;color:#CBD5E1;text-transform:uppercase;letter-spacing:1px;">
                    {safe_eyebrow}
                  </div>
                </div>
                <div style="padding:28px;">
                  <p style="margin:0 0 18px;font-size:16px;line-height:26px;color:#374151;">
                    {safe_heading}
                  </p>
                  <div style="margin:20px 0;padding:20px 18px;border-radius:18px;background:#F1F3F5;border:1px solid #E5E7EB;">
                    <div style="font-size:12px;line-height:18px;color:#66797F;letter-spacing:1.8px;text-transform:uppercase;">
                      {escape(summary_title)}
                    </div>
                    <div style="margin-top:4px;font-size:14px;line-height:22px;color:#374151;">
                      {rows_html}
                    </div>
                  </div>
                  {cta_html}
                </div>
              </div>
            </div>
          </body>
        </html>
        """
    ).strip()
    text_lines = [_strip_html_to_text(heading), "", summary_title + ":"]
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
