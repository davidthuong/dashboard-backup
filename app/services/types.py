from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class NormalizedJobStatus:
    source: str
    job_name: str
    status: str
    message: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    raw_payload: dict[str, Any] | list[Any] | str | None = None

