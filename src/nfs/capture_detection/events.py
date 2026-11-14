"""UI-independent progress messages and thread-safe user decisions."""

from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Callable


class DetectorDecision:
    """A worker waits while the UI supplies a choice; stopping releases the wait."""

    def __init__(self):
        self._ready = Event()
        self._lock = Lock()
        self.choice = None

    def choose(self, choice: str) -> None:
        with self._lock:
            if not self._ready.is_set():
                self.choice = choice
                self._ready.set()

    def wait(self, should_continue: Callable[[], bool] | None = None) -> str:
        while not self._ready.wait(.05):
            if should_continue is not None and not should_continue():
                self.choose('cancel')
        return self.choice


@dataclass(frozen=True)
class DetectorEvent:
    kind: str
    title: str
    message: str
    detector: str = ''
    details: str = ''
    completed: int = 0
    total: int = 0
    duration_s: float = 0.0
    attempt: int = 0
    max_attempts: int = 0
    point: str = ''
    actions: tuple[tuple[str, str], ...] = ()
    hold_seconds: float = 0.0
    decision: DetectorDecision | None = field(default=None, compare=False)
