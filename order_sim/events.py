"""Event model shared by the simulation."""
from dataclasses import dataclass

# The legal lifecycle of an order. Version N always carries LIFECYCLE[N].
LIFECYCLE = {1: "CREATED", 2: "PAID", 3: "SHIPPED"}


@dataclass(frozen=True)
class OrderEvent:
    """A single versioned update for one order."""

    order_id: str
    version: int
    status: str

    def __str__(self) -> str:
        return f"{self.order_id} v{self.version} {self.status}"


def event(order_id: str, version: int) -> OrderEvent:
    """Convenience constructor that fills in the status for a version."""
    return OrderEvent(order_id, version, LIFECYCLE[version])
