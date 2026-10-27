import pytest

from bestseller.services.budget import ExecutionBudgetError, ExecutionProviderNotSentError, ExecutionOperationBusyError
from bestseller.services.retry_ledger import execution_terminal_code

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("kind", [ExecutionProviderNotSentError, ExecutionOperationBusyError])
def test_trusted_nonterminal_admission_does_not_inherit_inner_financial_error(kind):
    try:
        try:
            raise ExecutionBudgetError("execution_ledger_unavailable", "scope:fixture")
        except ExecutionBudgetError as inner:
            raise kind("execution_provider_not_sent", "scope:fixture") from inner
    except kind as error:
        assert execution_terminal_code(error) is None
        wrapped = RuntimeError("ordinary planning wrapper")
        wrapped.__cause__ = error
        assert execution_terminal_code(wrapped) is None


@pytest.mark.parametrize("code", ["execution_result_unknown", "execution_cancelled", "execution_budget_exhausted"])
def test_ordinary_wrapper_still_preserves_true_terminal_cause(code):
    outer = RuntimeError("advisory wrapper")
    outer.__cause__ = ExecutionBudgetError(code, "scope:fixture")
    assert execution_terminal_code(outer) == code
