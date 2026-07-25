"""
Tool-layer exceptions. These are caught at the MCP tool boundary (see
tools/server.py) and turned into a structured `{"error": "..."}` result that
gets fed back to the model as a tool result — never a stack trace. The model
then decides how to phrase the refusal to the owner. The refusal itself
(the actual guard) happens here, in code, not in the prompt.
"""


class ToolError(Exception):
    """Base class for all deliberate refusals raised by the tool layer."""


class NotFoundError(ToolError):
    pass


class OversellError(ToolError):
    """Raised when a sale would take stock negative."""


class BelowCostError(ToolError):
    """Raised when a line item's selling price is below cost, without an explicit override."""


class GuardrailError(ToolError):
    """Generic guardrail refusal (bad khata settlement, negative stock delete, etc.)."""


class AmbiguousProductError(ToolError):
    """Raised when a product name matches >1 active SKU and the caller didn't disambiguate.
    Carries the candidate list so the agent can ask the owner a clarifying question."""

    def __init__(self, message: str, candidates: list):
        super().__init__(message)
        self.candidates = candidates
