from datetime import datetime
from pydantic import BaseModel, Field, field_validator


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


def _as_list(value):
    # PowerShell's ConvertTo-Json turns a one-element array into a bare object.
    if value is None:
        return []
    return [value] if isinstance(value, dict) else value


class AgentJobIn(BaseModel):
    job_name: str = Field(max_length=120)
    log_path: str = Field(default="", max_length=400)


class AgentInfoIn(BaseModel):
    hostname: str = Field(default="", max_length=255)
    os_info: str = Field(default="", max_length=255)
    agent_version: str = Field(default="", max_length=40)
    jobs: list[AgentJobIn] = Field(default_factory=list, max_length=20)

    @field_validator("jobs", mode="before")
    @classmethod
    def _jobs_list(cls, value):
        return _as_list(value)


class AgentEnrollIn(AgentInfoIn):
    enroll_token: str = Field(max_length=200)
    name: str = Field(default="", max_length=64)


class AgentReportItemIn(BaseModel):
    job_name: str = Field(max_length=120)
    status: str = Field(max_length=40)
    source: str = Field(default="rclone", max_length=30)
    message: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    raw_payload: dict | list | str | None = None


class AgentReportIn(BaseModel):
    run_id: str | None = Field(default=None, max_length=32)
    items: list[AgentReportItemIn] = Field(max_length=50)

    @field_validator("items", mode="before")
    @classmethod
    def _items_list(cls, value):
        return _as_list(value)


class AgentEnrollmentCreateIn(BaseModel):
    name: str = Field(default="", max_length=64)


class AgentNodeUpdateIn(BaseModel):
    enabled: bool
