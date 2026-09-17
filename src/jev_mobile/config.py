"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    mobile_mcp_command: tuple[str, ...]
    device_serial: str | None
    trace_dir: Path
    minimum_stable_time_seconds: float
    stabilizer_timeout_seconds: float
    confidence_threshold: float
    single_safe_action_confidence_threshold: float = 0.70
    minimum_probability_margin: float = 0.15
    action_page_size: int = 10
    max_action_pages: int = 3
    capture_escalation_screenshots: bool = True
    max_steps: int = 20
    max_runtime_seconds: float = 90.0
    max_same_state_count: int = 3
    max_action_retries: int = 2
    max_escalations: int = 3
    enable_llm_planning: bool = False
    max_plan_recoveries: int = 1
    max_jev_steps_without_progress: int = 20
    max_safe_exploration_branches: int = 5
    max_group_revisits: int = 2
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    typesafe_api_key: str | None = None
    agent_system_prompt: str | None = None
    bridge_port: int = 8765
    bridge_token: str | None = None
    text_input_strategy: str = "accessibility"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        command = os.getenv("MOBILE_MCP_COMMAND_JSON", '["npx", "-y", "@mobilenext/mobile-mcp@1.0.4"]')
        try:
            parsed = tuple(json.loads(command)) if command else ()
        except json.JSONDecodeError as error:
            raise ValueError("MOBILE_MCP_COMMAND_JSON must be a JSON string array") from error
        if not all(isinstance(item, str) and item for item in parsed):
            raise ValueError("MOBILE_MCP_COMMAND_JSON must contain non-empty strings")
        action_page_size = int(os.getenv("ACTION_PAGE_SIZE", "10"))
        max_action_pages = int(os.getenv("MAX_ACTION_PAGES", "3"))
        if action_page_size < 1 or max_action_pages < 1:
            raise ValueError("ACTION_PAGE_SIZE and MAX_ACTION_PAGES must be positive")
        max_plan_recoveries = int(os.getenv("MAX_PLAN_RECOVERIES", "1"))
        if max_plan_recoveries < 0:
            raise ValueError("MAX_PLAN_RECOVERIES must not be negative")
        text_input_strategy = os.getenv("TEXT_INPUT_STRATEGY", "accessibility").casefold()
        if text_input_strategy not in {"accessibility", "ime"}:
            raise ValueError("TEXT_INPUT_STRATEGY must be accessibility or ime")
        return cls(
            mobile_mcp_command=parsed, device_serial=os.getenv("MOBILE_DEVICE_SERIAL") or None,
            trace_dir=Path(os.getenv("TRACE_DIR", "traces")),
            minimum_stable_time_seconds=float(os.getenv("MINIMUM_STABLE_TIME_SECONDS", "0.25")),
            stabilizer_timeout_seconds=float(os.getenv("STABILIZER_TIMEOUT_SECONDS", "5")),
            confidence_threshold=float(os.getenv("DECISION_CONFIDENCE_THRESHOLD", "0.80")),
            single_safe_action_confidence_threshold=float(os.getenv("SINGLE_SAFE_ACTION_CONFIDENCE_THRESHOLD", "0.70")),
            minimum_probability_margin=float(os.getenv("MINIMUM_PROBABILITY_MARGIN", "0.15")),
            action_page_size=action_page_size,
            max_action_pages=max_action_pages,
            capture_escalation_screenshots=os.getenv("CAPTURE_ESCALATION_SCREENSHOTS", "true").casefold() in {"1", "true", "yes"},
            enable_llm_planning=os.getenv("ENABLE_LLM_PLANNING", "false").casefold() in {"1", "true", "yes"},
            max_plan_recoveries=max_plan_recoveries,
            max_jev_steps_without_progress=int(os.getenv("MAX_JEV_STEPS_WITHOUT_PROGRESS", "20")),
            max_safe_exploration_branches=int(os.getenv("MAX_SAFE_EXPLORATION_BRANCHES", "5")),
            max_group_revisits=int(os.getenv("MAX_GROUP_REVISITS", "2")),
            llm_base_url=os.getenv("LLM_BASE_URL") or None,
            llm_api_key=os.getenv("LLM_API_KEY") or None,
            llm_model=os.getenv("LLM_MODEL") or None,
            typesafe_api_key=os.getenv("TYPESAFE_API_KEY") or None,
            agent_system_prompt=os.getenv("MOBILE_AGENT_SYSTEM_PROMPT") or None,
            bridge_port=int(os.getenv("JEV_MOBILE_BRIDGE_PORT", "8765")),
            bridge_token=os.getenv("JEV_MOBILE_BRIDGE_TOKEN") or None,
            text_input_strategy=text_input_strategy,
        )
