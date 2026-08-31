"""Token and cost accounting for every model call."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..llm.provider import LLMResponse, estimate_tokens
from ..persistence.repository import LLMCallRecord, Repository
from .budget import BudgetDecision, BudgetGuard
from .pricing import CostEstimate, estimate_cost

#: Assumed output size when projecting the cost of a call that has not run yet.
#: Deliberately generous: under-projecting would let a call slip past the guard
#: and overshoot the budget, which is the failure mode that actually matters.
PROJECTED_OUTPUT_TOKENS = 1024


class BudgetExceededError(RuntimeError):
    """Raised when a model call would breach a configured budget."""

    def __init__(self, decision: BudgetDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


@dataclass(frozen=True, slots=True)
class TrackedCall:
    """The accounting outcome of one model call."""

    record: LLMCallRecord
    estimate: CostEstimate

    @property
    def cost_usd(self) -> Decimal:
        return self.record.cost_usd


class CostTracker:
    """Turns an :class:`LLMResponse` into a persisted, budgeted cost record."""

    def __init__(self, repository: Repository, budget_guard: BudgetGuard) -> None:
        self._repository = repository
        self._budget = budget_guard

    def track(
        self,
        response: LLMResponse,
        *,
        request_id: str,
        trace_id: str,
        agent: str | None = None,
    ) -> TrackedCall:
        """Record usage and cost, and charge it against the request budget."""
        estimate = estimate_cost(
            response.model, response.input_tokens, response.output_tokens
        )
        record = LLMCallRecord(
            request_id=request_id,
            trace_id=trace_id,
            provider=response.provider,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=estimate.amount_usd,
            agent=agent,
            # An estimate is flagged when either the token counts were
            # approximated or the model is missing from the rate card.
            estimated=response.tokens_estimated or not estimate.known_model,
            latency_ms=response.latency_ms,
        )
        self._repository.save_llm_call(record)
        self._budget.record_spend(request_id, estimate.amount_usd)
        return TrackedCall(record=record, estimate=estimate)

    def project_cost(self, model: str, *, input_tokens: int, output_tokens: int) -> CostEstimate:
        """Estimate the cost of a call that has not happened yet."""
        return estimate_cost(model, input_tokens, output_tokens)

    def authorize_call(
        self, *, model: str, prompt: str, system: str | None, request_id: str
    ) -> BudgetDecision:
        """Decide whether a model call may proceed on budget grounds.

        This is checked before *every* model call. Guarding only tool
        invocations would leave the actual spend unguarded: tools here are free
        simulations, while model calls are the thing that costs money, and an
        agent that loops without ever proposing a tool would otherwise run
        unmetered.
        """
        projected = self.project_cost(
            model,
            input_tokens=estimate_tokens(prompt) + estimate_tokens(system or ""),
            output_tokens=PROJECTED_OUTPUT_TOKENS,
        )
        return self._budget.check(projected.amount_usd, request_id=request_id)
