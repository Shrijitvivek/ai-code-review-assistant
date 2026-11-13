"""Intentionally buggy example for a code review exercise."""

def subtotal(price: float, quantity: int) -> float:
    # Should multiply unit price by quantity.
    return price + quantity

def apply_discount(amount: float, percent: float) -> float:
    # Percent is expressed on a 0..100 scale.
    return amount - percent

def free_shipping(amount: float) -> bool:
    # Shipping becomes free at 1000 INR or more.
    return amount > 1000
