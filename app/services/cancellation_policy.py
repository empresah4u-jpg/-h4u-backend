"""Pure, versionable cancellation evaluation; no I/O or economic defaults.

A policy computes an entitlement over the original confirmed payment, not the
remaining balance. Prior refunds reduce that entitlement, preventing double refund.
External execution is a separate concern from this decision.
"""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CancellationRule(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    actors: Tuple[Literal['tourist', 'partner', 'operator', 'admin'], ...] = Field(min_length=1)
    minimum_notice_seconds: int = Field(ge=0)
    outcome: Literal['cancel', 'deny', 'requires_manual_review']
    refund_type: Literal['none', 'percentage', 'fixed']
    refund_value: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: Optional[str] = Field(default=None, pattern=r'^[A-Z]{3}$')

    @model_validator(mode='after')
    def coherent(self):
        if len(set(self.actors)) != len(self.actors):
            raise ValueError('Actors must be unique')
        if self.outcome != 'cancel' and self.refund_type != 'none':
            raise ValueError('Only cancellation can define a refund')
        if self.refund_type == 'none' and self.refund_value != 0:
            raise ValueError('No refund must have zero value')
        if self.refund_type == 'percentage' and self.refund_value > 100:
            raise ValueError('Percentage exceeds 100')
        if (self.refund_type == 'fixed') != (self.currency is not None):
            raise ValueError('Only fixed refunds require currency')
        return self


class CancellationPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal[1] = 1
    rules: Tuple[CancellationRule, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unambiguous(self):
        seen = set()
        for rule in self.rules:
            for actor in rule.actors:
                key = (actor, rule.minimum_notice_seconds)
                if key in seen:
                    raise ValueError('Ambiguous rules for actor and notice boundary')
                seen.add(key)
        return self


class CancellationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    outcome: Literal['cancel', 'deny', 'requires_manual_review']
    reason: str
    refund_amount: Decimal = Decimal('0.00')
    rule_index: Optional[int] = None


def evaluate(policy: Optional[CancellationPolicy], *, actor: str,
             now: datetime, service_at: Optional[datetime], paid: Decimal,
             refunded: Decimal, currency: str) -> CancellationDecision:
    for value in (paid, refunded):
        if not value.is_finite() or value < 0 or value != value.quantize(Decimal('.01')):
            raise ValueError('Invalid financial basis')
    if refunded > paid:
        raise ValueError('Refunds exceed confirmed payment')
    if now.tzinfo is None or (service_at is not None and service_at.tzinfo is None):
        raise ValueError('Timezone-aware instants required')
    if policy is None:
        return CancellationDecision(outcome='requires_manual_review', reason='policy_missing')
    if service_at is None:
        return CancellationDecision(outcome='requires_manual_review', reason='service_time_missing')
    notice = (service_at - now).total_seconds()
    matches = [(i, r) for i, r in enumerate(policy.rules)
               if actor in r.actors and notice >= r.minimum_notice_seconds]
    if not matches:
        return CancellationDecision(outcome='requires_manual_review', reason='no_matching_rule')
    index, rule = max(matches, key=lambda pair: pair[1].minimum_notice_seconds)
    if rule.currency is not None and rule.currency != currency:
        return CancellationDecision(outcome='requires_manual_review', reason='currency_mismatch', rule_index=index)
    entitlement = Decimal('0')
    if rule.refund_type == 'percentage':
        entitlement = (paid * rule.refund_value / 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    elif rule.refund_type == 'fixed':
        entitlement = min(paid, rule.refund_value)
    return CancellationDecision(outcome=rule.outcome, reason='configured_rule',
                                refund_amount=max(Decimal('0.00'), entitlement - refunded), rule_index=index)
