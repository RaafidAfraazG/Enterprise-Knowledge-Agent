"""Illustrative USD per million tokens; estimates, not billing reconciliation."""

import logging

from kagent.config import Settings

PRICES = {
    "gpt-4o-mini": (0.15, 0.60, 0.0),
    "gpt-4o": (2.50, 10.0, 0.0),
    "gpt-4.1-mini": (0.40, 1.60, 0.0),
    "gpt-4.1": (2.0, 8.0, 0.0),
    "text-embedding-3-small": (0.0, 0.0, 0.02),
    "text-embedding-3-large": (0.0, 0.0, 0.13),
    "text-embedding-ada-002": (0.0, 0.0, 0.10),
    "fake": (0.0, 0.0, 0.0),
}
_warned: set[str] = set()


def estimate(
    model: str, prompt: int, completion: int, settings: Settings, *, embedding: bool = False
) -> float:
    """Apply configured prices or model-prefix defaults; unknown pricing is zero."""
    known = next((rates for name, rates in PRICES.items() if model.startswith(name)), None)
    overrides = (
        [settings.price_embed_per_m]
        if embedding
        else [settings.price_chat_input_per_m, settings.price_chat_output_per_m]
    )
    if known is None and any(value is None for value in overrides) and model not in _warned:
        logging.getLogger(__name__).warning(
            "Unknown model pricing for %s; unknown rates use zero", model
        )
        _warned.add(model)
    rates = known or (0, 0, 0)
    if embedding:
        rate = rates[2] if settings.price_embed_per_m is None else settings.price_embed_per_m
        return prompt * rate / 1_000_000
    input_rate = (
        rates[0] if settings.price_chat_input_per_m is None else settings.price_chat_input_per_m
    )
    output_rate = (
        rates[1] if settings.price_chat_output_per_m is None else settings.price_chat_output_per_m
    )
    return (prompt * input_rate + completion * output_rate) / 1_000_000
