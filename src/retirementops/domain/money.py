"""Money: Decimal-only, cent-quantised, one rounding policy, one display formatter (ADR-006).

Nothing here accepts binary floating point. Every intermediate calculation is exact; rounding happens
exactly once, in `quantize_money`, using ROUND_HALF_UP (fictional demo policy).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, Inexact, InvalidOperation, localcontext

CENT = Decimal("0.01")
ROUNDING_POLICY = "ROUND_HALF_UP"
SUPPORTED_CURRENCIES = frozenset({"USD"})
_MONEY_STR = re.compile(r"^-?\d{1,9}\.\d{2}$")


def _require_decimal(value: object, what: str) -> Decimal:
    # bool is a subclass of int; float is never acceptable for money.
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise TypeError(f"{what} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{what} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class Money:
    """A settled monetary amount, always an exact number of cents."""

    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        amount = _require_decimal(self.amount, "Money.amount")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported currency {self.currency!r}")
        if amount != amount.quantize(CENT):
            raise ValueError("Money must be whole cents; use quantize_money() to round explicitly")
        # Normalise representation so equal amounts compare and hash identically.
        object.__setattr__(self, "amount", amount.quantize(CENT))

    @classmethod
    def from_cents(cls, cents: int, currency: str = "USD") -> Money:
        if isinstance(cents, bool) or not isinstance(cents, int):
            raise TypeError(f"cents must be int, got {type(cents).__name__}")
        return cls(Decimal(cents).scaleb(-2), currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> Money:
        return cls(Decimal("0.00"), currency)

    def to_cents(self) -> int:
        return int(self.amount.scaleb(2))

    def _check_same(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError("can only combine Money with Money")
        if other.currency != self.currency:
            raise ValueError("currency mismatch")

    def __add__(self, other: Money) -> Money:
        self._check_same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_same(other)
        return Money(self.amount - other.amount, self.currency)

    def is_zero(self) -> bool:
        return self.amount == 0


def exact_product(*factors: Decimal) -> Decimal:
    """Multiply Decimals with `Inexact` trapped, proving no hidden rounding before quantisation."""
    for i, f in enumerate(factors):
        _require_decimal(f, f"factor[{i}]")
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.traps[Inexact] = True
        result = Decimal(1)
        for f in factors:
            result = result * f
        return result


def exact_percent(pct: Decimal) -> Decimal:
    """Convert a percentage (e.g. 5.25) to a fraction (0.0525) exactly."""
    _require_decimal(pct, "pct")
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.traps[Inexact] = True
        return pct / Decimal(100)


def quantize_money(value: Decimal, currency: str = "USD") -> Money:
    """The single rounding step: full precision -> cents, ROUND_HALF_UP."""
    _require_decimal(value, "value")
    try:
        return Money(value.quantize(CENT, rounding=ROUND_HALF_UP), currency)
    except InvalidOperation as exc:  # pragma: no cover - only for absurd magnitudes
        raise ValueError("amount out of range") from exc


def format_money(m: Money) -> str:
    """Single display formatter: $1,234.56 / -$1.23. Validators compare drafts to this exactly."""
    if m.currency != "USD":
        raise ValueError("only USD formatting is defined")
    sign = "-" if m.amount < 0 else ""
    return f"{sign}${abs(m.amount):,.2f}"


def format_percent(pct: Decimal) -> str:
    """5 -> '5.00%', 7.5 -> '7.50%'. Percentages displayed to two decimals, ROUND_HALF_UP."""
    _require_decimal(pct, "pct")
    return f"{pct.quantize(CENT, rounding=ROUND_HALF_UP)}%"


def parse_money(text: str, currency: str = "USD") -> Money:
    """Strict parser for machine input such as uploads: `-?digits.dd` only, no symbols or commas."""
    if not isinstance(text, str) or not _MONEY_STR.match(text):
        raise ValueError("money must look like 1234.56")
    return Money(Decimal(text), currency)
