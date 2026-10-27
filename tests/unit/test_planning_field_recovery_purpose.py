"""Field corrections consume the existing bounded planning repair allowance."""
import pytest
from bestseller.services.book_request_purpose import classify
from bestseller.services.planning_schema_recovery import TEMPLATE

pytestmark = pytest.mark.unit


def test_exact_planning_field_recovery_is_a_hard_repair():
    assert classify('7:plan', TEMPLATE) == 'hard_repair'


@pytest.mark.parametrize('stage', ['admit', 'write', 'review', 'knowledge', 'ending'])
def test_correction_does_not_gain_permission_in_other_stages(stage):
    with pytest.raises(ValueError, match='finite_unknown_request_family'):
        classify('7:' + stage, TEMPLATE)


def test_unknown_correction_template_remains_denied():
    with pytest.raises(ValueError, match='finite_unknown_request_family'):
        classify('7:plan', TEMPLATE + '_extra')
