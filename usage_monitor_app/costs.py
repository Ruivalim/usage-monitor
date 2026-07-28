"""Price table loading and token-cost estimation.

Pure module: no network, no credentials, no Hermes imports. Price tables are
plain YAML/JSON files (see ``examples/prices.yaml``) loaded through
``core.load_prices``, which already tolerates missing/corrupt files.

Price table schema (``prices.yaml``):

.. code-block:: yaml

    version: 1
    providers:
      <provider-id>:
        currency: USD                      # ISO code; required for estimates
        source: https://provider/pricing   # optional provenance URL/note
        models:
          <model-name>:
            input: 2.50                    # price per 1M input tokens
            output: 10.00                  # price per 1M output tokens
            cached: 1.25                   # optional, per 1M cached input tokens

All prices are per 1M tokens in the provider's single declared currency.
Estimates never mix currencies: every ``CostEstimate`` carries exactly one
currency, and aggregation is keyed by currency (see ``sum_estimates``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .core import PRICES_FILE, load_prices

TOKENS_PER_UNIT = 1_000_000  # prices are quoted per 1M tokens

_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$|-\d{8}$")


def normalize_model_name(name: Any) -> str:
    """Normalize a model name for price-table lookup.

    Steps: lowercase, strip all whitespace, drop a leading ``models/``
    prefix (Google style), drop a ``:tag`` suffix (OpenRouter variants such
    as ``:free``), and drop a trailing date stamp (``-2024-08-06`` or
    ``-20240806``).
    """
    n = re.sub(r"\s+", "", str(name or "").lower())
    if n.startswith("models/"):
        n = n[len("models/"):]
    n = n.split(":", 1)[0]
    return _DATE_SUFFIX.sub("", n)


def _normalize_provider_id(name: Any) -> str:
    return re.sub(r"\s+", "", str(name or "").lower())


@dataclass(frozen=True)
class ModelPrice:
    """Prices for one model, per 1M tokens, in the provider's currency."""

    input: Optional[float] = None
    output: Optional[float] = None
    cached: Optional[float] = None


@dataclass(frozen=True)
class ProviderPrices:
    """Price table for one provider. All models share one currency."""

    id: str
    currency: str = "USD"
    source: Optional[str] = None
    models: dict[str, ModelPrice] = field(default_factory=dict)


@dataclass(frozen=True)
class PriceTable:
    """Immutable view over a loaded price table."""

    providers: dict[str, ProviderPrices] = field(default_factory=dict)

    def provider(self, provider_id: Any) -> Optional[ProviderPrices]:
        return self.providers.get(_normalize_provider_id(provider_id))

    def model_price(self, provider_id: Any, model: Any) -> Optional[ModelPrice]:
        """Look up a model price, tolerating provider-prefixed names.

        Tries the full normalized name first (``openai/gpt-4o``), then the
        last path segment only (``gpt-4o``).
        """
        prov = self.provider(provider_id)
        if prov is None:
            return None
        norm = normalize_model_name(model)
        if norm in prov.models:
            return prov.models[norm]
        if "/" in norm:
            short = norm.rsplit("/", 1)[1]
            if short in prov.models:
                return prov.models[short]
        return None


@dataclass(frozen=True)
class CostEstimate:
    """Result of a cost estimate. Always single-currency."""

    provider: str
    model: str
    currency: str
    amount: float
    input_cost: float = 0.0
    output_cost: float = 0.0
    cached_cost: float = 0.0
    # Token components that had no applicable price and were skipped.
    unpriced: tuple[str, ...] = ()


def _price_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def price_table_from_dict(data: dict[str, Any]) -> PriceTable:
    """Build a ``PriceTable`` from parsed YAML/JSON data.

    Unknown shapes are ignored; a provider without a ``models`` mapping
    (for example the legacy currency/source-only entries) loads with an
    empty model table.
    """
    providers: dict[str, ProviderPrices] = {}
    raw_providers = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(raw_providers, dict):
        return PriceTable()
    for raw_id, raw in raw_providers.items():
        if not isinstance(raw, dict):
            continue
        pid = _normalize_provider_id(raw_id)
        if not pid:
            continue
        models: dict[str, ModelPrice] = {}
        raw_models = raw.get("models")
        if isinstance(raw_models, dict):
            for raw_model, prices in raw_models.items():
                if not isinstance(prices, dict):
                    continue
                name = normalize_model_name(raw_model)
                if not name:
                    continue
                models[name] = ModelPrice(
                    input=_price_float(prices.get("input")),
                    output=_price_float(prices.get("output")),
                    cached=_price_float(prices.get("cached")),
                )
        providers[pid] = ProviderPrices(
            id=pid,
            currency=str(raw.get("currency") or "USD").upper(),
            source=str(raw["source"]) if raw.get("source") else None,
            models=models,
        )
    return PriceTable(providers=providers)


def load_price_table(path: Path = PRICES_FILE) -> PriceTable:
    """Load a ``PriceTable`` from a YAML/JSON file.

    Never raises and never touches the network: missing or corrupt files
    yield an empty table, in which case all estimates return ``None``.
    """
    try:
        data = load_prices(path)
    except Exception:
        data = {}
    return price_table_from_dict(data if isinstance(data, dict) else {})


def estimate_cost(
    table: PriceTable,
    provider: Any,
    model: Any,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
) -> Optional[CostEstimate]:
    """Estimate cost for one request's token counts.

    Returns ``None`` when the provider is unknown, the model has no price
    entry, or no applicable price exists at all. Cached tokens are billed
    at the ``cached`` price when present, otherwise at the ``input`` price.
    Token components with no applicable price contribute 0 and are listed
    in ``unpriced`` instead of failing the estimate.
    """
    prov = table.provider(provider)
    if prov is None:
        return None
    price = table.model_price(provider, model)
    if price is None:
        return None

    in_tok = max(0, int(input_tokens or 0))
    out_tok = max(0, int(output_tokens or 0))
    cached_tok = max(0, int(cached_tokens or 0))

    input_cost = 0.0
    output_cost = 0.0
    cached_cost = 0.0
    unpriced: list[str] = []

    if in_tok:
        if price.input is not None:
            input_cost = in_tok / TOKENS_PER_UNIT * price.input
        else:
            unpriced.append("input")
    if out_tok:
        if price.output is not None:
            output_cost = out_tok / TOKENS_PER_UNIT * price.output
        else:
            unpriced.append("output")
    if cached_tok:
        cached_rate = price.cached if price.cached is not None else price.input
        if cached_rate is not None:
            cached_cost = cached_tok / TOKENS_PER_UNIT * cached_rate
        else:
            unpriced.append("cached")

    if in_tok + out_tok + cached_tok > 0 and not (input_cost or output_cost or cached_cost):
        return None

    return CostEstimate(
        provider=prov.id,
        model=normalize_model_name(model),
        currency=prov.currency,
        amount=input_cost + output_cost + cached_cost,
        input_cost=input_cost,
        output_cost=output_cost,
        cached_cost=cached_cost,
        unpriced=tuple(unpriced),
    )


def sum_estimates(estimates: Iterable[CostEstimate]) -> dict[str, float]:
    """Aggregate estimates keyed by currency.

    Never sums across currencies: the result maps each currency to its own
    total, e.g. ``{"USD": 1.23, "CNY": 4.56}``.
    """
    totals: dict[str, float] = {}
    for est in estimates:
        totals[est.currency] = totals.get(est.currency, 0.0) + est.amount
    return totals
