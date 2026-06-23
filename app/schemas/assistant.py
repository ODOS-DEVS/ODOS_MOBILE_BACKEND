from pydantic import BaseModel, Field


class AssistantMessageInput(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[AssistantMessageInput] = Field(default_factory=list, max_length=12)
    screen: str | None = Field(default=None, max_length=80)


class AssistantActionRead(BaseModel):
    label: str
    route: str


class AssistantChatResponse(BaseModel):
    reply: str
    suggested_actions: list[AssistantActionRead] = Field(default_factory=list)
    escalated_to_support: bool = False


class AssistantStatusResponse(BaseModel):
    enabled: bool
    provider: str
    model: str | None = None
    llm_reachable: bool | None = None
    llm_error: str | None = None
