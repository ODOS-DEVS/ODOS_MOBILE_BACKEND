"""Ghana mobile number helpers (local 10-digit format)."""

from fastapi import HTTPException, status


def extract_phone_digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def normalize_ghana_phone(value: str) -> str:
    digits = extract_phone_digits(value)

    if len(digits) == 10 and digits.startswith("0"):
        return digits

    if len(digits) == 12 and digits.startswith("233"):
        return f"0{digits[3:]}"

    if len(digits) == 9:
        return f"0{digits}"

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Phone number must be 10 digits (e.g. 0541234567).",
    )


def is_valid_ghana_phone(value: str) -> bool:
    try:
        normalize_ghana_phone(value)
    except HTTPException:
        return False
    return True


def to_international_ghana_phone(value: str) -> str:
    """Arkesel-style international number: 233XXXXXXXXX (digits only, no +)."""
    local = normalize_ghana_phone(value)
    return f"233{local[1:]}"
