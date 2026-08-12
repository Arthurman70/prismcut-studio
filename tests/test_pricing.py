"""core/pricing.py + core/cost_estimator.py - Qt-free, no real network calls
(http.request_json is monkeypatched, same spirit as every other test file
in this suite never hitting a real provider)."""
from prismcut.core import cost_estimator, pricing
from prismcut.core.registry import ModelSpec


class _FakeSettings:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value

    def get_bool(self, key, default=False):
        return bool(self._d.get(key, default))


# ------------------------------------------------------------------- pricing

def test_load_bundled_returns_priced_models():
    data = pricing.load_bundled()
    assert data["prices"]
    assert "google::veo-3.1-generate-preview" in data["prices"]


def test_get_prices_falls_back_to_bundled_when_no_remote_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(pricing, "_cache", None)
    monkeypatch.setattr(pricing.paths, "cache_dir", lambda: tmp_path)
    prices = pricing.get_prices()
    assert "google::veo-3.1-generate-preview" in prices


def test_fetch_remote_updates_cache_and_persists_to_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(pricing, "_cache", None)
    monkeypatch.setattr(pricing.paths, "cache_dir", lambda: tmp_path)
    fake_payload = {"version": 1, "prices": {"acme::widget": {"unit": "per_image", "amount": 1.0}}}
    monkeypatch.setattr(pricing.http, "request_json", lambda method, url, **kw: fake_payload)

    result = pricing.fetch_remote()
    assert result == fake_payload["prices"]
    assert pricing.get_prices() == fake_payload["prices"]
    assert (tmp_path / "pricing_remote.json").exists()


def test_fetch_remote_returns_none_on_failure(monkeypatch):
    def raise_it(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(pricing.http, "request_json", raise_it)
    assert pricing.fetch_remote() is None


def test_fetch_remote_returns_none_on_malformed_payload(monkeypatch):
    monkeypatch.setattr(pricing.http, "request_json", lambda method, url, **kw: {"no": "prices key"})
    assert pricing.fetch_remote() is None


def test_should_refresh_respects_toggle_and_throttle():
    s = _FakeSettings()
    assert pricing.should_refresh(s) is True   # never checked before -> due

    pricing.mark_refreshed(s)
    assert pricing.should_refresh(s) is False   # just refreshed -> not due yet

    s.set("pricing/auto_refresh", False)
    s.set("pricing/last_refresh_ts", 0)   # would otherwise be "due"
    assert pricing.should_refresh(s) is False   # toggle off wins


# -------------------------------------------------------------- cost_estimator

FAKE_PRICES = {
    "prov::video-model": {"unit": "per_second", "amount": 0.10},
    "prov::sora-like": {"unit": "per_second", "amount": 0.20},
    "prov::image-model": {"unit": "per_image", "amount": 0.05},
    "prov::tts-model": {"unit": "per_1k_chars", "amount": 0.10},
    "prov::chat-model": {"unit": "per_1m_tokens", "input": 2.0, "output": 10.0},
    "prov::lipsync-model": {"unit": "per_minute", "amount": 5.0},
    "prov::low-confidence": {"unit": "per_image", "amount": 1.0, "confidence": "low"},
}


def _spec(key: str) -> ModelSpec:
    provider, model_id = key.split("::", 1)
    return ModelSpec(id=model_id, provider=provider)


def test_estimate_cost_per_second_reads_duration_param(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    cost = cost_estimator.estimate_cost(_spec("prov::video-model"), {"duration": 8})
    assert cost == 0.10 * 8


def test_estimate_cost_per_second_falls_back_to_seconds_param(monkeypatch):
    """Sora keys its length as "seconds", not "duration"."""
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    cost = cost_estimator.estimate_cost(_spec("prov::sora-like"), {"seconds": "4"})
    assert cost == 0.20 * 4


def test_estimate_cost_per_second_override_wins_over_params(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    cost = cost_estimator.estimate_cost(_spec("prov::video-model"), {"duration": 8},
                                        duration_seconds=2.0)
    assert cost == 0.10 * 2.0


def test_estimate_cost_per_image_is_flat_times_count(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    assert cost_estimator.estimate_cost(_spec("prov::image-model"), {}, count=3) == 0.05 * 3


def test_estimate_cost_per_1k_chars_needs_text_len(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    spec = _spec("prov::tts-model")
    assert cost_estimator.estimate_cost(spec, {}) is None   # no text_len given -> unknown
    assert cost_estimator.estimate_cost(spec, {}, text_len=2000) == 0.10 * 2.0


def test_estimate_cost_per_1m_tokens_needs_token_counts(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    spec = _spec("prov::chat-model")
    assert cost_estimator.estimate_cost(spec, {}) is None   # no counts given -> unknown
    cost = cost_estimator.estimate_cost(spec, {}, input_tokens=500_000, output_tokens=100_000)
    assert cost == 2.0 * 0.5 + 10.0 * 0.1


def test_estimate_cost_per_minute_reads_duration_seconds(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    cost = cost_estimator.estimate_cost(_spec("prov::lipsync-model"), {}, duration_seconds=30)
    assert cost == 5.0 * 0.5


def test_estimate_cost_returns_none_for_unpriced_model(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    assert cost_estimator.estimate_cost(_spec("nobody::has-this"), {}) is None


def test_format_rate_covers_each_unit(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    assert cost_estimator.format_rate(_spec("prov::video-model")) == "$0.1/s"
    assert cost_estimator.format_rate(_spec("prov::image-model")) == "$0.05/img"
    assert cost_estimator.format_rate(_spec("prov::tts-model")) == "$0.1/1K chars"
    assert cost_estimator.format_rate(_spec("prov::chat-model")) == "$2/1M in · $10/1M out"
    assert cost_estimator.format_rate(_spec("prov::lipsync-model")) == "$5/min"
    assert cost_estimator.format_rate(_spec("nobody::has-this")) is None


def test_format_rate_flags_low_confidence_with_tilde(monkeypatch):
    monkeypatch.setattr(cost_estimator.pricing, "get_prices", lambda: FAKE_PRICES)
    assert cost_estimator.format_rate(_spec("prov::low-confidence")) == "~$1/img"
