# SPDX-License-Identifier: MIT
"""Cost estimation from fake price tables. No network, no real prices."""
from __future__ import annotations

from usage_monitor_app.costs import (
    ModelPrice,
    PriceTable,
    ProviderPrices,
    estimate_cost,
    load_price_table,
    normalize_model_name,
    price_table_from_dict,
    sum_estimates,
)

FAKE_TABLE = {
    "version": 1,
    "providers": {
        "fake-usd": {
            "currency": "USD",
            "source": "https://example.test/pricing",
            "models": {
                "fake-chat": {"input": 2.0, "output": 8.0, "cached": 0.5},
                "fake-mini": {"input": 1.0, "output": 4.0},
            },
        },
        "fake-cny": {
            "currency": "CNY",
            "source": "test fixture",
            "models": {
                "fake-chat": {"input": 7.0, "output": 21.0, "cached": 1.75},
            },
        },
        "fake-flat": {"currency": "USD", "source": "legacy entry, no models"},
    },
}


def _table() -> PriceTable:
    return price_table_from_dict(FAKE_TABLE)


def test_normalize_model_name_strips_variants():
    assert normalize_model_name("  Fake-Chat ") == "fake-chat"
    assert normalize_model_name("gpt-4o-2024-08-06") == "gpt-4o"
    assert normalize_model_name("gpt-4o-20240806") == "gpt-4o"
    assert normalize_model_name("models/gemini-2.5-pro") == "gemini-2.5-pro"
    assert normalize_model_name("google/gemini:free") == "google/gemini"
    assert normalize_model_name(None) == ""
    assert normalize_model_name(123) == "123"


def test_table_loads_currency_source_and_models():
    table = _table()
    prov = table.provider("fake-usd")
    assert prov is not None
    assert prov.currency == "USD"
    assert prov.source == "https://example.test/pricing"
    assert prov.models["fake-chat"] == ModelPrice(input=2.0, output=8.0, cached=0.5)
    # Provider ids are normalized too.
    assert table.provider(" Fake-USD ") is prov
    # Legacy flat entries load with no models.
    assert table.provider("fake-flat") is not None
    assert table.provider("fake-flat").models == {}


def test_model_price_lookup_tolerates_provider_prefix():
    table = _table()
    assert table.model_price("fake-usd", "fake-usd/fake-chat") is table.model_price("fake-usd", "fake-chat")
    assert table.model_price("fake-usd", "Fake-Chat-2026-01-01") is not None


def test_estimate_cost_full_breakdown():
    est = estimate_cost(_table(), "fake-usd", "fake-chat", input_tokens=1_000_000, output_tokens=500_000, cached_tokens=2_000_000)
    assert est is not None
    assert est.currency == "USD"
    assert est.input_cost == 2.0
    assert est.output_cost == 4.0
    assert est.cached_cost == 1.0
    assert est.amount == 7.0
    assert est.unpriced == ()


def test_estimate_cached_falls_back_to_input_price():
    est = estimate_cost(_table(), "fake-usd", "fake-mini", cached_tokens=1_000_000)
    assert est is not None
    assert est.cached_cost == 1.0  # no cached price -> input rate
    assert est.amount == 1.0


def test_estimate_partial_prices_marked_unpriced():
    table = price_table_from_dict(
        {"providers": {"p": {"currency": "USD", "models": {"m": {"output": 5.0}}}}}
    )
    est = estimate_cost(table, "p", "m", input_tokens=1000, output_tokens=1_000_000)
    assert est is not None
    assert est.amount == 5.0
    assert est.unpriced == ("input",)


def test_estimate_degrades_gracefully():
    table = _table()
    assert estimate_cost(table, "missing-provider", "fake-chat", input_tokens=10) is None
    assert estimate_cost(table, "fake-usd", "missing-model", input_tokens=10) is None
    assert estimate_cost(table, "fake-flat", "anything", input_tokens=10) is None
    assert estimate_cost(PriceTable(), "fake-usd", "fake-chat", input_tokens=10) is None
    # Model with no applicable price at all.
    table2 = price_table_from_dict({"providers": {"p": {"models": {"m": {}}}}})
    assert estimate_cost(table2, "p", "m", input_tokens=10) is None


def test_estimate_zero_tokens_returns_zero_estimate():
    est = estimate_cost(_table(), "fake-usd", "fake-chat")
    assert est is not None
    assert est.amount == 0.0


def test_sum_estimates_never_mixes_currencies():
    table = _table()
    usd = estimate_cost(table, "fake-usd", "fake-chat", input_tokens=1_000_000)
    cny = estimate_cost(table, "fake-cny", "fake-chat", input_tokens=1_000_000)
    assert usd is not None and cny is not None
    assert sum_estimates([usd, cny]) == {"USD": 2.0, "CNY": 7.0}


def test_load_price_table_from_yaml_file(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(
        "version: 1\n"
        "providers:\n"
        "  t:\n"
        "    currency: EUR\n"
        "    source: fixture\n"
        "    models:\n"
        "      m:\n"
        "        input: 3.0\n"
        "        output: 9.0\n",
        encoding="utf-8",
    )
    table = load_price_table(path)
    est = estimate_cost(table, "t", "m", input_tokens=1_000_000, output_tokens=1_000_000)
    assert est is not None
    assert est.currency == "EUR"
    assert est.amount == 12.0


def test_load_price_table_tolerates_missing_and_corrupt(tmp_path):
    assert load_price_table(tmp_path / "missing.yaml") == PriceTable()
    bad = tmp_path / "prices.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_price_table(bad) == PriceTable()
    weird = tmp_path / "prices.yaml"
    weird.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert load_price_table(weird) == PriceTable()
