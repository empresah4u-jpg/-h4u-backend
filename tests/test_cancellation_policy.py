"""Economic numbers here are test fixtures, never seeded business policies."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from app.services.cancellation_policy import CancellationPolicy, evaluate

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def rule(**changes):
    return dict(actors=['tourist'], minimum_notice_seconds=0, outcome='cancel',
                refund_type='percentage', refund_value='100', **changes)


def policy(**changes):
    data = rule()
    data.update(changes)
    return CancellationPolicy(rules=[data])


def decision(p, **changes):
    kwargs = dict(actor='tourist', now=NOW, service_at=NOW+timedelta(days=1),
                  paid=Decimal('100'), refunded=Decimal('0'), currency='PEN')
    kwargs.update(changes)
    return evaluate(p, **kwargs)


def test_missing_policy_and_time_require_review():
    assert decision(None).reason == 'policy_missing'
    assert decision(policy(), service_at=None).reason == 'service_time_missing'


@pytest.mark.parametrize('value,previous,expected', [('100','0','100'),('25','10','15'),('25','40','0')])
def test_entitlement_accounts_for_previous_refunds(value, previous, expected):
    assert decision(policy(refund_value=value), refunded=Decimal(previous)).refund_amount == Decimal(expected)


def test_fixed_refund_capped_and_currency_checked():
    p = policy(refund_type='fixed', refund_value='120', currency='PEN')
    assert decision(p).refund_amount == 100
    assert decision(p, currency='USD').reason == 'currency_mismatch'


def test_no_refund_is_explicit_not_missing_policy():
    d = decision(policy(refund_type='none', refund_value='0'))
    assert d.outcome == 'cancel' and d.refund_amount == 0


def test_rule_order_does_not_change_notice_priority():
    early = rule(); early.update(minimum_notice_seconds=86400)
    late = rule(); late.update(refund_value='25')
    for rules in ([early,late], [late,early]):
        p = CancellationPolicy(rules=rules)
        assert decision(p).refund_amount == 100
        assert decision(p,service_at=NOW+timedelta(seconds=86399)).refund_amount == 25


@pytest.mark.parametrize('change', [{'refund_value':'101'}, {'refund_value':'NaN'},
    {'refund_value':'-1'}, {'refund_type':'fixed'}, {'outcome':'deny'},
    {'currency':'PEN'}, {'minimum_notice_seconds':-1}, {'actors':[]}])
def test_invalid_configuration_rejected(change):
    with pytest.raises(ValidationError): policy(**change)


def test_ambiguous_rules_rejected():
    with pytest.raises(ValidationError): CancellationPolicy(rules=[rule(),rule()])


def test_actor_and_past_service_fall_back_to_review():
    assert decision(policy(),actor='partner').outcome == 'requires_manual_review'
    assert decision(policy(),service_at=NOW-timedelta(seconds=1)).outcome == 'requires_manual_review'


@pytest.mark.parametrize('change', [{'paid':Decimal('-1')}, {'refunded':Decimal('101')},
    {'paid':Decimal('NaN')}, {'now':NOW.replace(tzinfo=None)}])
def test_invalid_basis_rejected(change):
    with pytest.raises(ValueError): decision(policy(),**change)
