from datetime import datetime
from pydantic import BaseModel


class JobStatusOut(BaseModel):
    id: int
    source: str
    job_name: str
    status: str
    message: str
    started_at: datetime | None
    ended_at: datetime | None
    collected_at: datetime

    class Config:
        from_attributes = True


class PollingSettingIn(BaseModel):
    polling_interval_seconds: int


class PollingSettingOut(BaseModel):
    polling_interval_seconds: int


class CollectResult(BaseModel):
    total_records: int
    failed_records: int
    details: list[dict]


class IngestStatusItemIn(BaseModel):
    node: str
    source: str
    job_name: str
    status: str
    message: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    raw_payload: dict | list | str | None = None


class IngestStatusIn(BaseModel):
    items: list[IngestStatusItemIn]
