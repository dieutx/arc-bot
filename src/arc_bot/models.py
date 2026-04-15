from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AccountResult:
    account_key: str
    score_before: int | None = None
    score_after: int | None = None
    tasks_done: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def gained(self) -> int | None:
        if self.score_before is None or self.score_after is None:
            return None
        return self.score_after - self.score_before
