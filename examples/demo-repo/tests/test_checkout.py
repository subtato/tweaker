from app.checkout import apply_discount, final_total


def test_known_discount():
    assert apply_discount({"total": 100}, "WELCOME10") == 90


def test_unknown_discount_is_ignored():
    assert apply_discount({"total": 100}, "SPRING25") == 100


def test_final_total_without_code():
    assert final_total({"total": 100}) == 100
