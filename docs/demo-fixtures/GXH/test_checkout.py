from decimal import Decimal

import pytest

from checkout import checkout


def item(sku, price, quantity=1, eligible=True):
    return {"sku": sku, "unit_price": price, "quantity": quantity, "coupon_eligible": eligible}


def discounts(order):
    return [line["discount"] for line in order["lines"]]


def test_order_without_coupon():
    order = checkout([item("A", "40", 2), item("B", "30")])
    assert order["subtotal"] == Decimal("110.00")
    assert order["discount"] == Decimal("0.00")
    assert order["shipping"] == Decimal("0.00")
    assert order["total"] == Decimal("110.00")


def test_coupon_uses_line_amount_including_quantity():
    order = checkout([item("A", "20", 2), item("B", "10", 2)], "12")
    assert discounts(order) == [Decimal("8.00"), Decimal("4.00")]


def test_ineligible_goods_do_not_dilute_coupon():
    order = checkout([item("BOOK", "60"), item("GIFT", "40", eligible=False)], "20")
    assert discounts(order) == [Decimal("20.00"), Decimal("0.00")]
    assert order["total"] == Decimal("88.00")


def test_coupon_is_capped_by_eligible_amount():
    order = checkout([item("BOOK", "10"), item("GIFT", "100", eligible=False)], "80")
    assert discounts(order) == [Decimal("10.00"), Decimal("0.00")]
    assert order["total"] == Decimal("100.00")


def test_all_goods_ineligible():
    order = checkout([item("GIFT", "30", eligible=False)], "20")
    assert order["discount"] == Decimal("0.00")
    assert order["total"] == Decimal("38.00")


def test_rounding_preserves_the_full_coupon():
    order = checkout([item("A", "10"), item("B", "10"), item("C", "10")], "1")
    assert discounts(order) == [Decimal("0.34"), Decimal("0.33"), Decimal("0.33")]
    assert order["discount"] == Decimal("1.00")


def test_rounding_never_exceeds_coupon_budget():
    order = checkout([item("A", "0.05"), item("B", "0.05"), item("C", "0.05")], "0.02")
    assert discounts(order) == [Decimal("0.01"), Decimal("0.01"), Decimal("0.00")]
    assert order["discount"] == Decimal("0.02")


def test_largest_fraction_gets_remaining_cent():
    order = checkout([item("A", "1"), item("B", "2"), item("C", "3")], "1")
    assert discounts(order) == [Decimal("0.17"), Decimal("0.33"), Decimal("0.50")]


def test_equal_fractions_follow_input_order_not_sku_sorting():
    order = checkout([item("Z", "10"), item("A", "10"), item("M", "10")], "1")
    assert discounts(order) == [Decimal("0.34"), Decimal("0.33"), Decimal("0.33")]
    assert [line["sku"] for line in order["lines"]] == ["Z", "A", "M"]


@pytest.mark.parametrize("subtotal,coupon,shipping", [
    ("99", "0", "0"), ("109", "10", "0"), ("105", "10", "8"),
    ("99", "0.01", "8"), ("50", "0", "8"), ("20", "20", "8"),
])
def test_shipping_uses_amount_after_coupon(subtotal, coupon, shipping):
    order = checkout([item("A", subtotal)], coupon)
    assert order["shipping"] == Decimal(shipping)
    assert order["total"] == Decimal(subtotal) - Decimal(coupon) + Decimal(shipping)


def test_mixed_order_summary_matches_line_items():
    order = checkout([
        item("A", "30"), item("B", "30"), item("C", "30"),
        item("GIFT", "30", eligible=False),
    ], "30")
    assert order["discount"] == Decimal("30.00")
    assert sum((line["payable"] for line in order["lines"]), Decimal("0")) == Decimal("90.00")
    assert order["shipping"] == Decimal("8.00")
    assert order["total"] == Decimal("98.00")


@pytest.mark.parametrize("items,coupon", [
    ([], "0"), ([item("A", "0")], "0"), ([item("A", "10", 0)], "0"),
    ([item("A", "10", 1.5)], "0"), ([item("A", "10", True)], "0"),
    ([item("A", "10"), item("A", "20")], "0"),
    ([item("A", "NaN")], "0"), ([item("A", "10")], "-1"),
])
def test_invalid_orders_are_rejected(items, coupon):
    with pytest.raises(ValueError):
        checkout(items, coupon)
