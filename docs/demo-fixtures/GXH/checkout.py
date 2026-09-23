"""多商品订单结算与优惠券分摊，金额单位为元。"""

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("金额必须为非负有限数值")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def prepare_lines(items):
    if not items:
        raise ValueError("订单不能为空")
    lines, seen = [], set()
    for item in items:
        sku, quantity = item["sku"], item["quantity"]
        if not isinstance(sku, str) or not sku or sku in seen:
            raise ValueError("SKU 必须非空且不可重复")
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("数量必须为正整数")
        price = money(item["unit_price"])
        if price <= ZERO:
            raise ValueError("单价必须大于零")
        eligible = item.get("coupon_eligible", True)
        if type(eligible) is not bool:
            raise ValueError("优惠资格必须为布尔值")
        seen.add(sku)
        lines.append({"sku": sku, "quantity": quantity, "unit_price": price,
                      "subtotal": price * quantity, "coupon_eligible": eligible})
    return lines


def allocate_coupon(lines, coupon):
    eligible_total = sum((line["subtotal"] for line in lines if line["coupon_eligible"]), ZERO)
    budget = min(coupon, eligible_total)
    if budget == ZERO:
        return [ZERO for _ in lines]
    order_total = sum((line["subtotal"] for line in lines), ZERO)
    return [
        (budget * line["subtotal"] / order_total).quantize(CENT, rounding=ROUND_HALF_UP)
        if line["coupon_eligible"] else ZERO
        for line in lines
    ]


def shipping_fee(merchandise_total):
    return ZERO if merchandise_total >= Decimal("99.00") else Decimal("8.00")


def checkout(items, coupon="0.00"):
    lines = prepare_lines(items)
    discounts = allocate_coupon(lines, money(coupon))
    for line, discount in zip(lines, discounts):
        line["discount"] = discount
        line["payable"] = line["subtotal"] - discount
    subtotal = sum((line["subtotal"] for line in lines), ZERO)
    discount = sum(discounts, ZERO)
    shipping = shipping_fee(subtotal)
    return {"lines": lines, "subtotal": subtotal, "discount": discount,
            "shipping": shipping, "total": subtotal - discount + shipping}


if __name__ == "__main__":
    import json

    cart = [
        {"sku": "BOOK-A", "unit_price": "30.00", "quantity": 1},
        {"sku": "BOOK-B", "unit_price": "30.00", "quantity": 1},
        {"sku": "BOOK-C", "unit_price": "30.00", "quantity": 1},
        {"sku": "GIFT-CARD", "unit_price": "30.00", "quantity": 1, "coupon_eligible": False},
    ]
    print(json.dumps(checkout(cart, coupon="30.00"), default=str, ensure_ascii=False, indent=2))
