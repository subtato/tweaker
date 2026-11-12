DISCOUNTS = {
    "WELCOME10": 0.10,
}


def apply_discount(order, code):
    discount = DISCOUNTS[code]
    return order["total"] * (1 - discount)


def final_total(order, code=None):
    if code:
        return apply_discount(order, code)
    return order["total"]
