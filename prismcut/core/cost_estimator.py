"""Turns core/pricing.py's raw {model_key: pricing} data into an actual
dollar estimate for a specific generation call, or a short human-readable
rate label for model pickers. Qt-free by design, same style as
core/pricing.py.

Deliberately returns None (never a fabricated number) whenever the inputs
needed to compute a real estimate aren't available - e.g. a per-1k-chars
TTS price with no narration text length given, or a per-1M-token chat
price with no token estimate given. Callers should render "price unknown"
rather than guess, matching how the rest of this app treats missing data.
"""
from __future__ import annotations

from typing import Optional

from . import pricing
from .registry import ModelSpec


def _duration_seconds(params: dict, override: Optional[float]) -> float:
    """Video/lip-sync models mostly key their length as "duration"; Sora
    uses "seconds" - check both. Falls back to a representative 5s guess
    (matching most models' own default) rather than refusing to estimate
    at all when neither is present."""
    if override is not None:
        return float(override)
    for key in ("duration", "seconds"):
        v = params.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 5.0


def estimate_cost(model: ModelSpec, params: Optional[dict] = None, count: int = 1, *,
                  duration_seconds: Optional[float] = None, text_len: Optional[int] = None,
                  input_tokens: Optional[int] = None, output_tokens: Optional[int] = None
                  ) -> Optional[float]:
    """Estimated $ for `count` generations of this model with these
    params. duration_seconds/text_len/input_tokens/output_tokens are
    optional overrides for callers that know the real value (a scene's
    narration length, a probed clip duration, ...) rather than relying on
    what's in params - most params dicts don't carry narration text or
    token counts at all, only generation settings like resolution."""
    price = pricing.get_prices().get(model.key)
    if not price:
        return None
    params = params or {}
    unit = price.get("unit")

    if unit == "per_second":
        return price["amount"] * _duration_seconds(params, duration_seconds) * count
    if unit in ("per_image", "per_generation"):
        return price["amount"] * count
    if unit == "per_minute":
        secs = duration_seconds if duration_seconds is not None else params.get("duration")
        try:
            secs = float(secs) if secs is not None else 60.0
        except (TypeError, ValueError):
            secs = 60.0
        return price["amount"] * (secs / 60.0) * count
    if unit == "per_1k_chars":
        if text_len is None:
            return None
        return price["amount"] * (text_len / 1000.0) * count
    if unit == "per_1m_tokens":
        if input_tokens is None and output_tokens is None:
            return None
        total = 0.0
        if input_tokens:
            total += price.get("input", 0.0) * (input_tokens / 1_000_000.0)
        if output_tokens:
            total += price.get("output", price.get("input", 0.0)) * (output_tokens / 1_000_000.0)
        return total * count
    return None


def format_rate(model: ModelSpec) -> Optional[str]:
    """A short human-readable price-PER-UNIT label for model pickers, e.g.
    "$0.40/s", "$2.00/1M in · $10.00/1M out", "$0.045/img". None if this
    model has no known pricing. A "~" prefix flags low-confidence
    (reseller-derived, not from the provider's own docs) figures."""
    price = pricing.get_prices().get(model.key)
    if not price:
        return None
    unit = price.get("unit")
    approx = "~" if price.get("confidence") == "low" else ""
    if unit == "per_second":
        return f"{approx}${price['amount']:.3g}/s"
    if unit == "per_image":
        return f"{approx}${price['amount']:.3g}/img"
    if unit == "per_generation":
        return f"{approx}${price['amount']:.3g}/gen"
    if unit == "per_minute":
        return f"{approx}${price['amount']:.3g}/min"
    if unit == "per_1k_chars":
        return f"{approx}${price['amount']:.3g}/1K chars"
    if unit == "per_1m_tokens":
        out = price.get("output")
        if out is not None:
            return f"{approx}${price['input']:.3g}/1M in · ${out:.3g}/1M out"
        return f"{approx}${price['input']:.3g}/1M tok"
    return None
