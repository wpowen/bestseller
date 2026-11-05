"""All pytest filesystem mutations must stay outside repository book libraries."""

import os
from tools.books_guard import install_write_guard

os.environ["BOOKWEB_TEST"] = "1"
install_write_guard()

import pytest
from tools.books_guard import repository_books
from tools.redact import Redactor

_scrub = Redactor(
    *(p.parent / ".env" for p in repository_books()), os.environ.get("BOOKENGINE_ENV", "/nonexistent/bookweb.env")
)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.longrepr:
        report.longrepr = _scrub(str(report.longrepr))
    report.sections = [(name, _scrub(value)) for name, value in report.sections]
