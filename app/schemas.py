from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class CampaignBase(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None)
    language: str = Field(default="en", max_length=16)
    timezone: str = Field(default="UTC", max_length=64)
    consent_text: str = Field(min_length=5)


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = None
    language: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    consent_text: str | None = Field(default=None, min_length=5)
    status: str | None = None


class CampaignOut(CampaignBase):
    id: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class QuestionBase(BaseModel):
    key: str = Field(min_length=2, max_length=128)
    prompt: str = Field(min_length=4)
    question_type: str
    required: bool = True
    config: dict = Field(default_factory=dict)


class QuestionCreate(QuestionBase):
    pass


class QuestionUpdate(BaseModel):
    key: str | None = Field(default=None, min_length=2, max_length=128)
    prompt: str | None = Field(default=None, min_length=4)
    question_type: str | None = None
    required: bool | None = None
    config: dict | None = None


class QuestionOut(QuestionBase):
    id: int
    campaign_id: int
    order_index: int

    model_config = {"from_attributes": True}


class QuestionReorder(BaseModel):
    question_ids: list[int] = Field(min_length=1)


class RuleBase(BaseModel):
    source_question_id: int
    operator: str
    value: str = Field(min_length=1, max_length=256)
    action: str
    target_question_id: int | None = None
    priority: int = 100

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "goto" and not self.target_question_id:
            raise ValueError("target_question_id is required for goto action")
        if self.action != "goto" and self.target_question_id is not None:
            raise ValueError("target_question_id is only valid for goto action")
        return self


class RuleCreate(RuleBase):
    pass


class RuleUpdate(BaseModel):
    source_question_id: int | None = None
    operator: str | None = None
    value: str | None = Field(default=None, min_length=1, max_length=256)
    action: str | None = None
    target_question_id: int | None = None
    priority: int | None = None


class RuleOut(RuleBase):
    id: int
    campaign_id: int

    model_config = {"from_attributes": True}


class ParticipantOut(BaseModel):
    id: int
    campaign_id: int
    phone_number: str
    full_name: str | None = None
    locale: str | None = None
    opt_in: bool
    status: str
    meta: dict

    model_config = {"from_attributes": True}


class CampaignSummary(BaseModel):
    id: int
    name: str
    language: str
    timezone: str
    status: str
    question_count: int
    participant_count: int


class CampaignExecutionOut(BaseModel):
    campaign_id: int
    state: str
    started_at: datetime | None = None
    paused_at: datetime | None = None
    stopped_at: datetime | None = None
    last_tick_at: datetime | None = None


class CallingPolicyUpdate(BaseModel):
    window_start_hour: int = Field(ge=0, le=23)
    window_end_hour: int = Field(ge=1, le=24)
    max_attempts: int = Field(ge=1, le=10)
    retry_delay_minutes: int = Field(ge=1, le=1440)
    cooldown_hours: int = Field(ge=0, le=168)
    max_calls_per_minute: int = Field(ge=1, le=500)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_window(self):
        if self.window_end_hour <= self.window_start_hour:
            raise ValueError("window_end_hour must be greater than window_start_hour")
        return self


class CallingPolicyOut(CallingPolicyUpdate):
    campaign_id: int


class CallAttemptOut(BaseModel):
    id: int
    participant_id: int
    participant_phone: str
    attempt_number: int
    outcome: str
    started_at: datetime
    finished_at: datetime
    note: str | None = None
