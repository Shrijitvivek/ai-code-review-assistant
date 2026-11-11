def subtotal(price: float, quantity: int) -> float:
    """Return the total for a quantity of items at the given unit price."""
    return price * quantity


def apply_discount(amount: float, percent: float) -> float:
    """Apply a discount percentage expressed on a 0..100 scale."""
    return amount * (1 - percent / 100)


def free_shipping(amount: float) -> bool:
    """Return whether the order meets the 1000 INR free-shipping threshold."""
    return amount >= 1000
