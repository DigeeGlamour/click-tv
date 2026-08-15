"""Small, dependency-free helpers for keeping runtime secrets out of logs."""

from __future__ import annotations

import os
from typing import Iterable, Mapping
from urllib.parse import quote, quote_plus


SENSITIVE_ENV_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "AUTHORIZATION",
    "CREDENTIAL",
)


def _secret_values(environment: Mapping[str, str] | None = None) -> Iterable[str]:
    source = os.environ if environment is None else environment
    values: set[str] = set()
    for name, raw_value in source.items():
        upper_name = str(name).upper()
        if not any(marker in upper_name for marker in SENSITIVE_ENV_MARKERS):
            continue
        value = str(raw_value or "").strip()
        # Very short values create false positives and are not useful tokens.
        if len(value) >= 8:
            values.add(value)
    return sorted(values, key=len, reverse=True)


def redact_sensitive_text(
    value: object,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Replace known environment-secret values and encoded forms in text."""
    clean = str(value)
    for secret in _secret_values(environment):
        variants = {secret, quote(secret, safe=""), quote_plus(secret)}
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                clean = clean.replace(variant, "[REDACTED]")
    return clean
