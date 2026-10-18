"""Request, response, and action shapes. Pydantic validates the LLM output
so a malformed or unexpected action can never reach the executor."""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=500, description="Natural language question")


class Action(BaseModel):
    """The structured action the LLM must return. Nothing else is accepted."""
    verb: str = Field(..., description="e.g. get, describe, logs, top")
    resource: str = Field(..., description="e.g. pods, nodes, services")
    namespace: str | None = Field(default=None, description="optional namespace")
    name: str | None = Field(default=None, description="optional object name")


class QueryResponse(BaseModel):
    allowed: bool
    action: Action | None = None
    output: str | None = None
    message: str | None = None
