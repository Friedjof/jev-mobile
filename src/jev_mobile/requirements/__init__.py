"""TaskSpec-derived, inspectable completion requirements."""

from .generators import generate_requirements
from .evaluator import RequirementEvaluator
from .requirement import Requirement

__all__ = ["Requirement", "RequirementEvaluator", "generate_requirements"]
