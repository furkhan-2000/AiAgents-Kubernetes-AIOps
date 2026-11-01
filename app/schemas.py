"""Request, response, and action shapes. Pydantic validates every structure the
LLM produces, so a malformed or unexpected action can never reach the executor."""

from pydantic import BaseModel, Field


# ----- Simple single-command mode (/query) -------------------------------------

class QueryRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=500, description="Natural language question")


class Action(BaseModel):
    """The structured action the LLM must return for /query. Nothing else is accepted."""
    verb: str = Field(..., description="e.g. get, describe, logs, top")
    resource: str = Field(..., description="e.g. pods, nodes, services")
    namespace: str | None = Field(default=None, description="optional namespace")
    name: str | None = Field(default=None, description="optional object name")


class QueryResponse(BaseModel):
    allowed: bool
    action: Action | None = None
    output: str | None = None
    message: str | None = None


# ----- Conversational investigate mode (/investigate) --------------------------

class InvestigateRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=1000, description="Natural language question")


class ToolCall(BaseModel):
    """One read-only step the LLM asks the loop to run. The LLM chooses only a
    tool NAME from the catalog; it never writes raw kubectl. 'final' ends the loop."""
    tool: str = Field(..., description="tool name from the catalog, or 'final' to stop")
    namespace: str | None = Field(default=None, max_length=253)
    name: str | None = Field(default=None, max_length=253)
    tail_lines: int | None = Field(default=None, ge=1, le=2000, description="for get_logs")
    previous: bool | None = Field(default=None, description="for get_logs: previous container")
    container: str | None = Field(default=None, max_length=253, description="for get_logs: container name")
    reason: str | None = Field(default=None, max_length=300, description="why this step")


class Step(BaseModel):
    """A record of one executed tool call and what it returned (for the audit trail)."""
    tool: str
    args: dict = Field(default_factory=dict)
    observation: str


class InvestigateResponse(BaseModel):
    allowed: bool
    answer: str | None = None          # plain-English explanation for the user
    steps: list[Step] = Field(default_factory=list)
    message: str | None = None
