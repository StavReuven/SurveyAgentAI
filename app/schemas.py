from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class CampaignBase(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    language: str = Field(default="en", max_length=16)
    timezone: str = Field(default="UTC", max_length=64)
    consent_text: str = Field(min_length=5)


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
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
