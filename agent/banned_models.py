"""Reader for /opt/hermes-clean/home/policy/banned_models.json.

A list of {pattern, reason} (or {id, reason}) entries with glob patterns matched
against provider/model. Used by delegation runtime and the model picker.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(
    os.environ.get("HERMES_BANNED_MODELS_PATH")
    or "/opt/hermes-clean/home/policy/banned_models.json"
)


def _pattern(entry: dict) -> str:
    for key in ("pattern", "id", "glob", "model"):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def load_banned_models(path: Optional[Any] = None) -> list[dict]:
    p = Path(path) if path is not None else DEFAULT_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("banned_models: cannot read %s: %s", p, exc)
        return []
    entries = data.get("banned") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        pat = _pattern(e)
        if not pat:
            continue
        out.append({"pattern": pat, "reason": str(e.get("reason") or "")})
    return out


def _candidates(provider: Optional[str], model: Optional[str]) -> list[str]:
    cands = []
    if model:
        cands.append(str(model))
        # provider/model form for combined matching
    if provider and model:
        cands.append(f"{provider}/{model}")
        cands.append(f"{provider}:{model}")
    if provider:
        cands.append(str(provider))
    return cands


def is_banned(provider: Optional[str] = None, model: Optional[str] = None,
              path: Optional[Any] = None) -> tuple[bool, Optional[str]]:
    """Return (banned, reason)."""
    for entry in load_banned_models(path):
        pat = entry["pattern"]
        for cand in _candidates(provider, model):
            if fnmatch.fnmatch(cand, pat) or fnmatch.fnmatch(cand.lower(), pat.lower()):
                return True, entry["reason"]
        # also try pattern against model basename
    return False, None


def banned_reason(provider: Optional[str] = None, model: Optional[str] = None,
                  path: Optional[Any] = None) -> Optional[str]:
    banned, reason = is_banned(provider, model, path=path)
    return reason if banned else None


# Back-compat aliases
def is_banned_model(model: str, path: Optional[Any] = None) -> tuple[bool, Optional[str]]:
    return is_banned(None, model, path=path)


def check_banned(provider: Optional[str], model: Optional[str], path: Optional[Any] = None) -> Optional[str]:
    return banned_reason(provider, model, path=path)
