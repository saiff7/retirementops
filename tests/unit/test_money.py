from decimal import Decimal, Inexact

import pytest
from hypothesis import given
from hypothesis import strategies as st

from retirementops.domain.money import (
    Money,
    exact_percent,
    exact_product,
    format_money,
    format_percent,
    parse_money,
    quantize_money,
)


@pytest.mark.parametrize("bad", [1.5, 0.1, True, 1, "1.00", None])
def test_money_rejects_float_and_non_decimal(bad: object) -> None:
    with pytest.raises(TypeError):
        Money(bad)  # type: ignore[arg-type]


def test_money_rejects_float_everywhere() -> None:
    with pytest.raises(TypeError):
        quantize_money(0.1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        exact_product(Decimal("1.00"), 0.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money.from_cents(1.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money.from_cents(True)


def test_money_requires_whole_cents_and_finite() -> None:
    with pytest.raises(ValueError, match="whole cents"):
        Money(Decimal("1.005"))
    with pytest.raises(ValueError):
        Money(Decimal("NaN"))
    with pytest.raises(ValueError):
        Money(Decimal("1.00"), "EUR")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.005", "0.01"),
        ("0.0049999", "0.00"),
        ("2.675", "2.68"),  # binary float would give 2.67
        ("1.125", "1.13"),
        ("1.135", "1.14"),
        ("-0.005", "-0.01"),  # half-up rounds away from zero
        ("92.59275", "92.59"),
        ("49.3828", "49.38"),
        ("100", "100.00"),
    ],
)
def test_round_half_up_table(raw: str, expected: str) -> None:
    assert quantize_money(Decimal(raw)).amount == Decimal(expected)


def test_exact_product_traps_hidden_rounding() -> None:
    third = Decimal("0.3333333333333333333333333333")
    with pytest.raises(Inexact):
        exact_product(third, third)


def test_exact_percent() -> None:
    assert exact_percent(Decimal("7.5")) == Decimal("0.075")


@pytest.mark.parametrize(
    ("cents", "text"),
    [
        (0, "$0.00"),
        (123450, "$1,234.50"),
        (-123, "-$1.23"),
        (100000000, "$1,000,000.00"),
        (1, "$0.01"),
    ],
)
def test_format_money(cents: int, text: str) -> None:
    assert format_money(Money.from_cents(cents)) == text


def test_format_percent() -> None:
    assert format_percent(Decimal("5")) == "5.00%"
    assert format_percent(Decimal("7.5000")) == "7.50%"


@pytest.mark.parametrize("bad", ["12.5", "1,234.00", "$1.00", "1.001", "", " 1.00", "1e3"])
def test_parse_money_is_strict(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_money(bad)


def test_parse_money_ok() -> None:
    assert parse_money("1234.56").to_cents() == 123456


@given(st.integers(min_value=-(10**12), max_value=10**12))
def test_cents_round_trip(cents: int) -> None:
    assert Money.from_cents(cents).to_cents() == cents


@given(
    st.decimals(
        min_value=-(10**9), max_value=10**9, places=6, allow_nan=False, allow_infinity=False
    )
)
def test_quantize_is_cents_and_within_half_cent(x: Decimal) -> None:
    q = quantize_money(x).amount
    assert q.as_tuple().exponent == -2
    assert abs(q - x) <= Decimal("0.005")


def test_money_arithmetic_same_currency_only() -> None:
    a, b = Money.from_cents(150), Money.from_cents(51)
    assert (a - b).to_cents() == 99
    assert (a + b).to_cents() == 201
