"""Schema of the human-label file used to calibrate the claim, support and abstention judges."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Split = Literal["few_shot", "dev", "test"]


class LabeledSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    passages: list[str] = Field(default_factory=list)  # sentences cited for this segment (verbatim)


class LabeledPair(BaseModel):
    """One (item, answer) pair. A `*_label` of null means "not labeled yet"; unlabeled entries are skipped."""

    model_config = ConfigDict(extra="forbid")
    pair_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    stratum: str
    source_doc: str
    split: Split
    failed: bool = False  # the failure signal the pair was oversampled on
    question: str
    answer: str
    segments: list[LabeledSegment] = Field(default_factory=list)
    required_claims: list[str] = Field(default_factory=list)  # shown to the labeler, copied from the item
    claim_labels: list[bool | None] = Field(default_factory=list)  # human: is claim k entailed by the answer
    support_labels: list[bool | None] = Field(
        default_factory=list
    )  # human: is segment s entailed by its passages
    abstain_label: bool | None = (
        None  # human: did the answer behave correctly (decline / correct the premise)
    )
    notes: str = ""

    @model_validator(mode="after")
    def _lengths(self) -> LabeledPair:
        if len(self.claim_labels) not in (0, len(self.required_claims)):
            raise ValueError("claim_labels must have one entry per required claim")
        if len(self.support_labels) not in (0, len(self.segments)):
            raise ValueError("support_labels must have one entry per segment")
        return self


class LabelFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    seed: int = 0
    pairs: list[LabeledPair] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> LabelFile:
        ids = [p.pair_id for p in self.pairs]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate pair_id")
        return self
