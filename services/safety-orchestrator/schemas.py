from pydantic import BaseModel


class ScanInputRequest(BaseModel):
    session_id: str
    agent_name: str
    message: str
    thread_id: str | None = None


class ScanInputResponse(BaseModel):
    allowed: bool
    blocked: bool
    reason: str | None = None
    anonymized_message: str | None = None
    pii_detected: bool = False
    scores: dict[str, float] = {}


class ScanOutputRequest(BaseModel):
    session_id: str
    agent_name: str
    message: str
    thread_id: str | None = None


class ScanOutputResponse(BaseModel):
    allowed: bool
    blocked: bool
    reason: str | None = None
    deanonymized_message: str | None = None
    scores: dict[str, float] = {}


class ReadinessResponse(BaseModel):
    ready: bool
    scanners: dict[str, bool] = {}
