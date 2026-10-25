"""CSP v režime hlásenia a príjem hlásení od prehliadača."""

import json
import logging
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_rate_limit():

    main._csp_report_times.clear()

    yield

    main._csp_report_times.clear()


def test_report_only_policy_is_sent_with_html_and_is_strict_for_scripts():

    response = client.get("/login")

    report_only = response.headers["content-security-policy-report-only"]

    assert "script-src 'self'" in report_only and "script-src 'self' 'unsafe-inline'" not in report_only
    assert "script-src-attr 'none'" in report_only
    assert "report-uri /csp-report" in report_only

    # hlavná (vynucovaná) politika sa zatiaľ nemení
    assert "script-src 'self' 'unsafe-inline'" in response.headers["content-security-policy"]


def test_report_only_header_is_not_sent_with_non_html():

    assert "content-security-policy-report-only" not in client.get("/health").headers


def test_report_is_logged_with_sanitized_values(caplog):

    payload = {"csp-report": {
        "violated-directive": "script-src-elem",
        "blocked-uri": "inline",
        "document-uri": "https://appka.example.com/reset-password/SECRET-TOKEN?x=1#frag",
        "line-number": 12,
    }}

    with caplog.at_level(logging.WARNING, logger="main"):
        response = client.post("/csp-report", content=json.dumps(payload),
                               headers={"content-type": "application/csp-report"})

    assert response.status_code == 204

    # query a fragment sa do logu nedostanú
    assert "script-src-elem" in caplog.text
    assert "x=1" not in caplog.text and "#frag" not in caplog.text


def test_log_injection_is_neutralised(caplog):

    payload = {"csp-report": {"violated-directive": "script-src\nFAKE LOG LINE", "blocked-uri": "x\r\ny"}}

    with caplog.at_level(logging.WARNING, logger="main"):
        client.post("/csp-report", content=json.dumps(payload))

    assert "\nFAKE LOG LINE" not in caplog.text and "\r" not in caplog.text


@pytest.mark.parametrize("body", [b"", b"nie-json", b"[]", b"null", b'{"csp-report": 5}', b"\xff\xfe"])
def test_garbage_never_crashes(body):

    assert client.post("/csp-report", content=body).status_code == 204


def test_oversized_report_is_ignored(caplog):

    huge = json.dumps({"csp-report": {"violated-directive": "A" * 20000}})

    with caplog.at_level(logging.WARNING, logger="main"):
        assert client.post("/csp-report", content=huge).status_code == 204

    assert "[CSP]" not in caplog.text


def test_reports_are_rate_limited(caplog):

    payload = json.dumps({"csp-report": {"violated-directive": "script-src"}})

    with caplog.at_level(logging.WARNING, logger="main"):

        for _ in range(main.CSP_REPORTS_PER_MINUTE + 20):
            assert client.post("/csp-report", content=payload).status_code == 204

    assert caplog.text.count("[CSP]") == main.CSP_REPORTS_PER_MINUTE
