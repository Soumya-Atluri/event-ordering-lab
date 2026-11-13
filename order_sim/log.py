"""Tiny tick-stamped logger so every scenario prints the same readable format."""


class SimLog:
    def __init__(self, sink=print):
        self.sink = sink
        self.lines: list[str] = []

    def __call__(self, tick: int, tag: str, msg: str) -> None:
        line = f"[t={tick:02d}] {tag:<9} {msg}"
        self.lines.append(line)
        self.sink(line)

    def raw(self, msg: str = "") -> None:
        self.lines.append(msg)
        self.sink(msg)
