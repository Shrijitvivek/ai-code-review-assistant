from cart import subtotal, apply_discount, free_shipping


def test_subtotal():
    assert subtotal(100, 2) == 200


def test_apply_discount():
    assert apply_discount(1000, 10) == 900


def test_free_shipping_at_threshold():
    assert free_shipping(1000) is True


def test_free_shipping_below_threshold():
    assert free_shipping(999) is False