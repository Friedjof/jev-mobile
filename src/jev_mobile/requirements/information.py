"""Deterministic semantic evidence extraction for read-only task outputs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from ..state.models import SemanticElement, SemanticState
from ..tasks import InformationRequest, InformationSensitivity, semantic_option_id


class InformationMatchStatus(StrEnum):
    OBSERVED = "observed"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    PROHIBITED = "prohibited"


class InformationCandidate(BaseModel):
    id: str
    label: str
    value: str | bool
    semantic_role: str
    confidence: float


class InformationMatch(BaseModel):
    status: InformationMatchStatus
    candidate: InformationCandidate | None = None
    candidates: list[InformationCandidate] = Field(default_factory=list)


_PROHIBITED_TERMS = ("password", "passwort", "token", "secret", "api key", "auth code", "pin")


def _text(element: SemanticElement) -> str:
    return " ".join(
        part for part in (element.label, element.value, element.state_description, element.hint) if part
    ).strip()


def _is_sensitive(element: SemanticElement) -> bool:
    text = _text(element).casefold()
    return element.password or element.field_role == "password" or any(term in text for term in _PROHIBITED_TERMS)


def _hint_score(text: str, hints: list[str]) -> float:
    folded = text.casefold()
    normalized_hints = [" ".join(hint.casefold().split()) for hint in hints if hint.strip()]
    if any(hint in folded for hint in normalized_hints):
        return 1.0
    tokens = {token for hint in normalized_hints for token in hint.split() if len(token) > 2}
    if not tokens:
        return 0.0
    matched = sum(token in folded for token in tokens)
    return matched / len(tokens)


def _candidate(request: InformationRequest, label: str, element: SemanticElement, confidence: float) -> InformationCandidate | None:
    if _is_sensitive(element):
        return None
    value: str | bool | None = element.state_description or element.value or element.label
    if element.checkable:
        value = element.checked
    if value is None or (isinstance(value, str) and value.casefold().strip() == label.casefold().strip()):
        return None
    return InformationCandidate(
        id=semantic_option_id(request.key, label, value),
        label=label,
        value=value,
        semantic_role=element.field_role or element.role,
        confidence=round(confidence, 2),
    )


def extract_information(
    request: InformationRequest,
    state: SemanticState,
    *, selected_candidate_id: str | None = None,
) -> InformationMatch:
    """Associate visible labels and values without letting a model invent data."""
    if request.sensitivity == InformationSensitivity.PROHIBITED:
        return InformationMatch(status=InformationMatchStatus.PROHIBITED)
    visible = [element for element in state.elements if element.visible and not _is_sensitive(element)]
    labels = [element for element in visible if _hint_score(_text(element), request.semantic_hints) >= 0.5]
    candidates: list[InformationCandidate] = []
    for label_element in labels:
        label = label_element.label or label_element.field_name or request.question
        direct = _candidate(request, label, label_element, 0.98)
        if direct:
            candidates.append(direct)
        relatives = [
            element for element in visible
            if element.id != label_element.id and (
                element.parent_id == label_element.parent_id
                or element.parent_id == label_element.id
                or label_element.parent_id == element.id
            )
        ]
        if not relatives:
            relatives = [
                element for element in visible
                if element.id != label_element.id and 0 < element.raw_index - label_element.raw_index <= 2
                and abs(element.depth - label_element.depth) <= 1
            ]
        for element in relatives:
            match = _candidate(request, label, element, 0.96 if element.parent_id == label_element.parent_id else 0.9)
            if match:
                candidates.append(match)
    unique = {candidate.id: candidate for candidate in candidates}
    candidates = list(unique.values())
    if selected_candidate_id:
        selected = next((candidate for candidate in candidates if candidate.id == selected_candidate_id), None)
        return InformationMatch(
            status=InformationMatchStatus.OBSERVED if selected else InformationMatchStatus.NOT_FOUND,
            candidate=selected,
            candidates=candidates,
        )
    unique_values = {str(candidate.value).casefold() for candidate in candidates}
    if len(unique_values) == 1 and candidates:
        return InformationMatch(status=InformationMatchStatus.OBSERVED, candidate=candidates[0], candidates=candidates)
    if len(unique_values) > 1:
        return InformationMatch(status=InformationMatchStatus.AMBIGUOUS, candidates=candidates)
    return InformationMatch(status=InformationMatchStatus.NOT_FOUND)
