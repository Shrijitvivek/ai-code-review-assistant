"""Small cart example used by the review assistant's sample repository."""


class Cart:
    """A basic shopping cart."""

    def __init__(self) -> None:
        self.items: list[tuple[str, float]] = []

    def add_item(self, name: str, price: float) -> None:
        self.items.append((name, price))

    def total(self) -> float:
        return sum(price for _, price in self.items)
