from cart import Cart


def test_cart_total() -> None:
    cart = Cart()
    cart.add_item("notebook", 3.5)
    cart.add_item("pen", 1.5)

    assert cart.total() == 5.0
