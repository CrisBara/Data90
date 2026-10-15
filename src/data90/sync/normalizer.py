import re
import unicodedata


def normalize_name(value: str) -> str:
    """Normalize names for exact lookup while preserving meaningful word distinctions."""
    folded = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(character for character in folded if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()
