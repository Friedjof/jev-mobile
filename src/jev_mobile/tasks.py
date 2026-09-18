"""Declarative task semantics shared by planning, candidates, and verification."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import re
from uuid import uuid4

from pydantic import BaseModel, Field


class FieldRequirement(BaseModel):
    """Content expected in a semantic text-field role, never a device command."""

    role: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=4000)
    required: bool = True


class CompletionRequirement(BaseModel):
    """An observable condition required before a task may be declared complete."""

    type: str = Field(min_length=1, max_length=40)
    field_role: str | None = Field(default=None, max_length=40)
    value: str | None = Field(default=None, max_length=4000)
    output_key: str | None = Field(default=None, min_length=1, max_length=80)


class TaskPolicyMode(StrEnum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"


class TaskPolicy(BaseModel):
    """Durable execution constraints, not a provider prompt."""

    mode: TaskPolicyMode
    external_interactions: bool = False
    sensitive_data: bool = False


class RequestedOutput(BaseModel):
    """One semantic value the parent expects in the final result."""

    key: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=240)
    required: bool = True


class InformationSensitivity(StrEnum):
    NORMAL = "normal"
    PERSONAL = "personal"
    PROHIBITED = "prohibited"


class InformationRequest(BaseModel):
    """One value that must be read from observed UI evidence."""

    key: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=240)
    semantic_hints: list[str] = Field(default_factory=list, max_length=12)
    sensitivity: InformationSensitivity = InformationSensitivity.NORMAL


class QuestionType(StrEnum):
    TEXT = "text"
    SELECT_OPTION = "select_option"
    APPROVAL = "approval"


class AnswerEffectKind(StrEnum):
    SET_TARGET_APP = "set_target_app"
    SET_INFORMATION_REQUEST = "set_information_request"
    SELECT_SEMANTIC_OPTION = "select_semantic_option"
    AUTHORIZE_OPERATION = "authorize_operation"
    AUTHORIZE_INFORMATION = "authorize_information"


class QuestionOption(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=240)


class AnswerSchema(BaseModel):
    type: QuestionType
    min_length: int | None = None
    max_length: int | None = None


class AnswerEffect(BaseModel):
    kind: AnswerEffectKind
    parameter: str = Field(min_length=1, max_length=120)


class QuestionSpec(BaseModel):
    """Controller-authored question; a decision model only selects its ID."""

    id: str = Field(min_length=1, max_length=100)
    type: QuestionType
    reason: str = Field(min_length=1, max_length=120)
    prompt_key: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=500)
    options: list[QuestionOption] = Field(default_factory=list, max_length=20)
    answer_schema: AnswerSchema
    answer_effect: AnswerEffect
    operation: str | None = Field(default=None, max_length=120)
    package: str | None = Field(default=None, max_length=240)


class TaskInputAnswer(BaseModel):
    question_id: str
    prompt_key: str
    answer: str
    effect: AnswerEffect
    answered_at: str


class TaskContractStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    UNSUPPORTED = "unsupported"


class TaskContractValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class RequirementStatus(StrEnum):
    UNSATISFIED = "unsatisfied"
    IN_PROGRESS = "in_progress"
    NEEDS_VERIFICATION = "needs_verification"
    SATISFIED = "satisfied"


class TaskSpec(BaseModel):
    """Small semantic decomposition supplied by a planner or safe local parsing."""

    contract_version: int = 1
    intent: str = Field(default="generic", max_length=80)
    app: str | None = Field(default=None, max_length=160)
    app_package: str | None = Field(default=None, max_length=240)
    content_type: str = Field(default="UNKNOWN", max_length=40)
    title: str | None = Field(default=None, max_length=240)
    fields: list[FieldRequirement] = Field(default_factory=list, max_length=6)
    items: list[str] = Field(default_factory=list, max_length=100)
    completion: list[CompletionRequirement] = Field(default_factory=list, max_length=8)
    policy: TaskPolicy | None = None
    requested_outputs: list[RequestedOutput] = Field(default_factory=list, max_length=12)
    information_requests: list[InformationRequest] = Field(default_factory=list, max_length=12)


def validate_task_contract(task_spec: TaskSpec) -> TaskContractValidation:
    """Validate the durable semantic boundary before Android is touched."""
    errors: list[str] = []
    if task_spec.intent == "generic":
        errors.append("intent is unresolved")
    if not (task_spec.app or task_spec.app_package):
        errors.append("target app is missing")
    if task_spec.policy is None:
        errors.append("execution policy is missing")
    observable = bool(
        task_spec.completion
        or any(field.required for field in task_spec.fields)
        or task_spec.items
        or (task_spec.intent == "create_note" and task_spec.title)
    )
    if not observable:
        errors.append("observable completion criteria are missing")
    if task_spec.policy and task_spec.policy.mode == TaskPolicyMode.READ_ONLY and not task_spec.requested_outputs:
        errors.append("read-only task has no requested outputs")
    if task_spec.intent == "read_information" and not task_spec.information_requests:
        errors.append("read task has no information request")
    if task_spec.intent == "read_information" and task_spec.app and not task_spec.app_package:
        errors.append("read task target package is missing")
    if any(request.sensitivity == InformationSensitivity.PROHIBITED for request in task_spec.information_requests):
        errors.append("prohibited sensitive information request")
    output_keys = [output.key for output in task_spec.requested_outputs]
    if len(output_keys) != len(set(output_keys)):
        errors.append("requested output keys must be unique")
    known_outputs = set(output_keys)
    referenced_outputs = {item.output_key for item in task_spec.completion if item.output_key}
    if any(item.output_key and item.output_key not in known_outputs for item in task_spec.completion):
        errors.append("completion requirement references an unknown output")
    if any(output.required and output.key not in referenced_outputs for output in task_spec.requested_outputs):
        errors.append("required output has no completion requirement")
    return TaskContractValidation(valid=not errors, errors=errors)


def contract_status_for(task_spec: TaskSpec) -> TaskContractStatus:
    return TaskContractStatus.READY if validate_task_contract(task_spec).valid else TaskContractStatus.PENDING


_KNOWN_APPS = {
    "android settings": ("Android Settings", "com.android.settings"),
    "settings": ("Android Settings", "com.android.settings"),
    "einstellungen": ("Android Settings", "com.android.settings"),
    "google keep": ("Google Keep", "com.google.android.keep"),
    "keep": ("Google Keep", "com.google.android.keep"),
    "jev mobile fixture": ("Jev Mobile Fixture", "io.jev.mobile.fixture"),
    "fixture": ("Jev Mobile Fixture", "io.jev.mobile.fixture"),
}


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return normalized[:80] or "requested_information"


def resolve_app(value: str) -> tuple[str, str | None]:
    """Resolve only stable local app aliases; unknown names remain semantic."""
    normalized = " ".join(value.casefold().split())
    if re.fullmatch(r"[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+", value.strip()):
        return value.strip(), value.strip()
    return _KNOWN_APPS.get(normalized, (value.strip(), None))


def make_question(
    *, question_type: QuestionType, reason: str, prompt_key: str, text: str,
    effect: AnswerEffect, options: list[QuestionOption] | None = None,
    operation: str | None = None, package: str | None = None,
) -> QuestionSpec:
    return QuestionSpec(
        id=f"q_{uuid4().hex}", type=question_type, reason=reason, prompt_key=prompt_key,
        text=text, options=options or [],
        answer_schema=AnswerSchema(
            type=question_type,
            min_length=1 if question_type == QuestionType.TEXT else None,
            max_length=240 if question_type == QuestionType.TEXT else None,
        ),
        answer_effect=effect, operation=operation, package=package,
    )


def contract_question(task_spec: TaskSpec, errors: list[str], answered_prompt_keys: set[str]) -> QuestionSpec | None:
    """Build the next deterministic clarification without asking a model to write text."""
    if task_spec.intent != "read_information":
        return None
    if (
        "target app is missing" in errors
        or "read task target package is missing" in errors
    ) and "target_app" not in answered_prompt_keys:
        return make_question(
            question_type=QuestionType.TEXT,
            reason="MISSING_TARGET_APP",
            prompt_key="target_app",
            text="Which app should I read this information from?",
            effect=AnswerEffect(kind=AnswerEffectKind.SET_TARGET_APP, parameter="target_app"),
        )
    if (
        "read task has no information request" in errors
        or "read-only task has no requested outputs" in errors
    ) and "information_request" not in answered_prompt_keys:
        return make_question(
            question_type=QuestionType.TEXT,
            reason="MISSING_INFORMATION_REQUEST",
            prompt_key="information_request",
            text="What information should I read from the app?",
            effect=AnswerEffect(kind=AnswerEffectKind.SET_INFORMATION_REQUEST, parameter="information_request"),
        )
    return None


def information_authorization_question(task_spec: TaskSpec, authorized_keys: set[str]) -> QuestionSpec | None:
    personal = [
        request for request in task_spec.information_requests
        if request.sensitivity == InformationSensitivity.PERSONAL and request.key not in authorized_keys
    ]
    if not personal:
        return None
    keys = ",".join(sorted(request.key for request in personal))
    return make_question(
        question_type=QuestionType.APPROVAL,
        reason="PERSONAL_INFORMATION_DISCLOSURE",
        prompt_key=f"authorize_information:{keys}",
        text="The requested value may contain personal account information. May I read and return it?",
        options=[QuestionOption(id="allow", label="Allow"), QuestionOption(id="deny", label="Deny")],
        effect=AnswerEffect(kind=AnswerEffectKind.AUTHORIZE_INFORMATION, parameter=keys),
    )


def validate_question_answer(question: QuestionSpec, answer: str) -> str:
    """Validate data against the controller-owned schema, never as UI input."""
    normalized = answer.strip()
    if re.search(r"\bs\d+:e\d+\b", normalized, re.I):
        raise ValueError("answers must not contain executable snapshot references")
    if question.type == QuestionType.TEXT:
        minimum = question.answer_schema.min_length or 1
        maximum = question.answer_schema.max_length or 240
        if not minimum <= len(normalized) <= maximum:
            raise ValueError(f"answer length must be between {minimum} and {maximum}")
        return normalized
    allowed = {option.id.casefold(): option.id for option in question.options}
    if normalized.casefold() not in allowed:
        raise ValueError("answer must be one of the question option IDs")
    return allowed[normalized.casefold()]


def apply_answer_effect(task_spec: TaskSpec, question: QuestionSpec, answer: str) -> TaskSpec:
    """Apply only the narrow semantic mutation declared by the question."""
    effect = question.answer_effect
    if effect.kind == AnswerEffectKind.SET_TARGET_APP:
        app, package = resolve_app(answer)
        return task_spec.model_copy(update={"app": app, "app_package": package})
    if effect.kind == AnswerEffectKind.SET_INFORMATION_REQUEST:
        key = _slug(answer)
        request = InformationRequest(key=key, question=answer, semantic_hints=[answer])
        output = RequestedOutput(key=key, description=answer)
        completion = CompletionRequirement(type="information_observed", output_key=key)
        return task_spec.model_copy(update={
            "information_requests": [*task_spec.information_requests, request],
            "requested_outputs": [*task_spec.requested_outputs, output],
            "completion": _unique_completion([*task_spec.completion, completion]),
        })
    return task_spec


def semantic_option_id(output_key: str, label: str, value: object) -> str:
    material = f"{output_key}\0{label.casefold()}\0{value!s}".encode()
    return f"option_{sha256(material).hexdigest()[:16]}"


class TaskInterpretation(BaseModel):
    """Cheap semantic interpretation before optional System-2 planning."""

    purpose: str | None = None
    item_candidates: list[str] = Field(default_factory=list)
    content_type: str = "UNKNOWN"
    title: str = "UNKNOWN"
    task_spec: TaskSpec = Field(default_factory=TaskSpec)

    def to_task_spec(self) -> TaskSpec:
        """Create the one authoritative specification used by the controller."""
        content_type = self.content_type.casefold()
        title = self.title
        if title in {"UNKNOWN", "NO_TITLE", ""}:
            title = None
        elif title == "USE_PURPOSE_AS_TITLE":
            title = self.purpose.title() if self.purpose else None
        spec = self.task_spec.model_copy(update={
            "content_type": content_type,
            "title": title,
            "fields": [] if content_type == "checklist" else self.task_spec.fields,
            "items": self.item_candidates if content_type == "checklist" else [],
        })
        completion = [item for item in spec.completion if not (content_type == "checklist" and item.type == "field_contains")]
        if spec.intent == "create_note":
            if content_type == "checklist":
                completion.extend([
                    CompletionRequirement(type="content_mode", value="checklist"),
                    CompletionRequirement(type="checklist_items"),
                ])
            elif spec.fields:
                completion.extend([
                    CompletionRequirement(type="field_contains", field_role=field.role, value=field.content)
                    for field in spec.fields
                ])
            if title:
                completion.append(CompletionRequirement(type="title_equals", value=title))
            completion.append(CompletionRequirement(type="persisted"))
        return spec.model_copy(update={"completion": _unique_completion(completion)})


def _unique_completion(values: list[CompletionRequirement]) -> list[CompletionRequirement]:
    unique: dict[tuple[str, str | None, str | None, str | None], CompletionRequirement] = {}
    for value in values:
        unique[(value.type, value.field_role, value.value, value.output_key)] = value
    return list(unique.values())


def task_spec_from_goal(goal: str) -> TaskSpec:
    """Infer only unambiguous explicit text; ambiguous language remains an LLM job."""
    normalized = goal.casefold()
    read_terms = (
        "read ", "give me", "what is", "which ", "tell me", "show me",
        "lies ", "lese ", "gib mir", "wie lautet", "welch",
    )
    if any(term in normalized for term in read_terms):
        app_match = next(
            ((name, package) for alias, (name, package) in sorted(_KNOWN_APPS.items(), key=lambda item: -len(item[0]))
             if alias in normalized),
            (None, None),
        )
        app, package = app_match
        request_data: tuple[str, str, list[str], InformationSensitivity] | None = None
        if "device name" in normalized or "gerätename" in normalized or "geraetename" in normalized:
            request_data = (
                "device_name", "What is the device name?",
                ["device name", "about phone", "model", "gerätename"], InformationSensitivity.NORMAL,
            )
        elif "version" in normalized:
            request_data = (
                "version", "What version is shown?",
                ["version", "app version", "software version"], InformationSensitivity.NORMAL,
            )
        elif "account" in normalized or "konto" in normalized:
            request_data = (
                "selected_account", "Which account is currently selected?",
                ["account", "selected account", "konto"], InformationSensitivity.PERSONAL,
            )
        elif any(term in normalized for term in ("password", "passwort", "token", "api key", "secret")):
            request_data = (
                "sensitive_value", "Read the requested sensitive value.",
                ["password", "token", "secret"], InformationSensitivity.PROHIBITED,
            )
        requests: list[InformationRequest] = []
        outputs: list[RequestedOutput] = []
        completion: list[CompletionRequirement] = []
        if request_data:
            key, question, hints, sensitivity = request_data
            requests.append(InformationRequest(
                key=key, question=question, semantic_hints=hints, sensitivity=sensitivity,
            ))
            outputs.append(RequestedOutput(key=key, description=question))
            completion.append(CompletionRequirement(type="information_observed", output_key=key))
        return TaskSpec(
            intent="read_information", app=app, app_package=package,
            content_type="information", policy=TaskPolicy(
                mode=TaskPolicyMode.READ_ONLY,
                sensitive_data=bool(request_data and request_data[3] != InformationSensitivity.NORMAL),
            ),
            information_requests=requests, requested_outputs=outputs, completion=completion,
        )
    if "fixture" in normalized and ("create an item" in normalized or "create item" in normalized):
        title_match = re.search(r"(?:titled|title)\s+[\"']?([^\n\"']+?)[\"']?(?:\s+(?:with|body)\b|$)", goal, re.I)
        body_match = re.search(r"(?:with\s+body|body)\s*:?\s*(.+)$", goal, re.I | re.S)
        title = title_match.group(1).strip() if title_match else None
        body = body_match.group(1).strip() if body_match else None
        fields = [FieldRequirement(role="body", content=body)] if body else []
        completion = [CompletionRequirement(type="field_contains", field_role="body", value=body)] if body else []
        if title: completion.append(CompletionRequirement(type="title_equals", value=title))
        completion.append(CompletionRequirement(type="persisted"))
        return TaskSpec(intent="create_note", app="Jev Mobile Fixture", app_package="io.jev.mobile.fixture",
                        content_type="text_note", title=title, fields=fields, completion=completion,
                        policy=TaskPolicy(mode=TaskPolicyMode.MUTATING))
    # This is language/task parsing only. It never contains UI or app-flow knowledge.
    checklist = "checklist" in normalized or "check list" in normalized
    note_target = "keep" in normalized or "note" in normalized or "notiz" in normalized
    if checklist and note_target:
        title_match = re.search(r"(?:titled|title)\s+[\"']?([^\n\"']+?)[\"']?(?:\s+(?:containing|with|mit)\b|$)", goal, re.I)
        title = title_match.group(1).strip() if title_match else None
        tail = re.split(r"\b(?:containing|with|mit)\b", goal, maxsplit=1, flags=re.I)
        item_text = tail[1] if len(tail) == 2 else ""
        items = [part.strip(" \t\n,-•.") for part in re.split(r"[,\n]|\band\b|\bund\b", item_text, flags=re.I) if part.strip(" \t\n,-•.")]
        return TaskSpec(intent="create_note", app="Google Keep", app_package="com.google.android.keep", content_type="checklist", title=title,
            items=items, completion=[CompletionRequirement(type="content_mode", value="checklist"),
                                      CompletionRequirement(type="checklist_items"),
                                      CompletionRequirement(type="title_equals", value=title) if title else CompletionRequirement(type="persisted"),
                                      CompletionRequirement(type="persisted")],
            policy=TaskPolicy(mode=TaskPolicyMode.MUTATING))
    if note_target and any(token in normalized for token in ("text note", "normal", "body")):
        title_match = re.search(r"(?:titled|title)\s+[\"']?([^\n\"']+?)[\"']?(?:\s+(?:with|mit)\s+(?:the\s+)?body\s*:?|$)", goal, re.I)
        body_match = re.search(r"(?:body\s*:?|text\s*:)\s*(.+)$", goal, re.I | re.S)
        title = title_match.group(1).strip() if title_match else None
        body = body_match.group(1).strip() if body_match else None
        fields = [FieldRequirement(role="body", content=body)] if body else []
        completion = [CompletionRequirement(type="field_contains", field_role="body", value=body)] if body else []
        if title: completion.append(CompletionRequirement(type="title_equals", value=title))
        completion.append(CompletionRequirement(type="persisted"))
        return TaskSpec(intent="create_note", app="Google Keep", app_package="com.google.android.keep",
                        content_type="text_note", title=title, fields=fields, completion=completion,
                        policy=TaskPolicy(mode=TaskPolicyMode.MUTATING))
    _, separator, supplied = goal.partition(":")
    content = supplied.strip()
    creates_note = any(term in normalized for term in ("new note", "create a note", "notiz", "shopping list"))
    if creates_note and content:
        requirement = FieldRequirement(role="body", content=content)
        return TaskSpec(
            intent="create_note", content_type="text_note",
            fields=[requirement],
            completion=[CompletionRequirement(type="field_contains", field_role="body", value=content)],
        )
    return TaskSpec()


def deterministic_interpretation(goal: str) -> TaskInterpretation:
    """Extract explicitly supplied task text without guessing language intent."""
    spec = task_spec_from_goal(goal)
    _, separator, supplied = goal.partition(":")
    items = [item.strip() for item in supplied.split(",") if item.strip()] if separator else []
    normalized = goal.casefold()
    purpose = "shopping" if any(term in normalized for term in ("shopping", "einkauf")) else None
    # A shopping list is normally a checklist. This is a deterministic task
    # interpretation, not a device-specific shortcut.
    content_type = "CHECKLIST" if (purpose == "shopping" or any(term in normalized for term in ("checklist", "check list", "todo"))) else "TEXT_NOTE"
    title = "USE_PURPOSE_AS_TITLE" if purpose and spec.intent == "create_note" else "UNKNOWN"
    return TaskInterpretation(
        purpose=purpose, item_candidates=items, content_type=content_type, title=title, task_spec=spec,
    )
