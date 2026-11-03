"""Tests for CrashPulse anonymous reporting (basara4_report)."""

import io
import json
import os
from pathlib import Path
import ssl
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

import basara4_report as report


def test_build_feedback_payload_bug_report():
    payload = report.build_feedback_payload(
        category="Bug Report",
        description="The audio stutters during the Osaka stage cutscene.",
        contact="user#1234",
        app_version="1.2.0",
        os_info="Windows 11",
        attach_diagnostics=True,
        settings_summary={"display_mode": "borderless", "resolution": "1920x1080"},
        gpu_name="NVIDIA GeForce RTX 4070",
        log_tail="Line 1\nLine 2\nLine 3",
    )

    assert payload["app"] == "basara4-native-pc"
    assert payload["version"] == "1.2.0"
    assert payload["level"] == "warning"
    assert payload["message"].startswith("[Feedback:Bug Report]")
    assert "audio stutters" in payload["message"]
    assert payload["stack"] == "Line 1\nLine 2\nLine 3"
    assert payload["stacktrace"] == "Line 1\nLine 2\nLine 3"

    ctx = payload["context"]
    assert ctx["type"] == "feedback"
    assert ctx["category"] == "Bug Report"
    assert ctx["contact"] == "user#1234"
    assert ctx["gpu_name"] == "NVIDIA GeForce RTX 4070"
    assert ctx["settings_summary"]["display_mode"] == "borderless"


def test_build_feedback_payload_feature_request_no_diagnostics():
    payload = report.build_feedback_payload(
        category="Feature Request",
        description="Please add 21:9 ultrawide aspect ratio support.",
        contact="test@example.com",
        app_version="1.2.0",
        attach_diagnostics=False,
        log_tail="This log tail should not be attached",
    )

    assert payload["level"] == "info"
    assert payload["message"].startswith("[Feedback:Feature Request]")
    assert payload["stack"] == ""
    assert payload["context"]["type"] == "feedback"
    assert payload["context"]["category"] == "Feature Request"
    assert payload["context"]["contact"] == "test@example.com"
    assert "gpu_name" not in payload["context"]


def test_build_feedback_payload_other():
    payload = report.build_feedback_payload(
        category="Other",
        description="Just wanted to thank you for this awesome port!",
    )
    assert payload["level"] == "info"
    assert payload["message"].startswith("[Feedback:Other]")
    assert payload["context"]["category"] == "Other"


def test_build_crash_payload():
    payload = report.build_crash_payload(
        exit_code=0xC0000005,
        failure_summary="STOP: null pointer dereference in renderer",
        app_version="1.2.0",
        os_info="Windows 10.0.19045",
        gpu_name="AMD Radeon RX 6700 XT",
        settings_summary={"dual_monitor_coop": True, "upscaler": "fsr1"},
        minidump_present=True,
        log_tail="[E] Renderer crashed\nSTOP: null pointer dereference in renderer",
        user_comment="Crashed right after selecting character.",
    )

    assert payload["level"] == "fatal"
    assert "Game exited with code" in payload["message"]
    assert "c0000005" in payload["message"]
    assert "STOP: null pointer dereference" in payload["message"]
    assert "Renderer crashed" in payload["stack"]

    ctx = payload["context"]
    assert "type" not in ctx  # No feedback type for crashes!
    assert ctx["exit_code"] == 0xC0000005
    assert ctx["gpu_name"] == "AMD Radeon RX 6700 XT"
    assert ctx["settings_summary"]["dual_monitor_coop"] is True
    assert ctx["minidump_present"] is True
    assert ctx["user_comment"] == "Crashed right after selecting character."


def test_sanitization_home_and_username(monkeypatch):
    monkeypatch.setattr(report, "get_current_home", lambda: r"C:\Users\SecretAgent")
    monkeypatch.setattr(report, "get_current_username", lambda: "SecretAgent")

    raw = (
        r"Error occurred at C:\Users\SecretAgent\Documents\Projects\game.log "
        r"and posix C:/Users/SecretAgent/AppData/Roaming"
    )
    cleaned = report.sanitize_text(raw)
    assert "SecretAgent" not in cleaned
    assert "<USER_HOME>" in cleaned


def test_sanitization_tokens_and_secrets():
    raw_log = (
        "Authorization: Bearer my_super_secret_bearer_token_123456\n"
        "GitHub token: ghp_1234567890abcdefghijklmnopqrstuvwxyz\n"
        'config = {"api_key": "my_hidden_api_key_xyz", "project_key": "cp_proj_12345"}\n'
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0\n-----END RSA PRIVATE KEY-----\n"
    )
    cleaned = report.sanitize_text(raw_log, custom_secrets=["cp_proj_12345"])
    assert "my_super_secret_bearer_token_123456" not in cleaned
    assert "ghp_1234567890abcdefghijklmnopqrstuvwxyz" not in cleaned
    assert "my_hidden_api_key_xyz" not in cleaned
    assert "cp_proj_12345" not in cleaned
    assert "-----BEGIN RSA PRIVATE KEY-----" not in cleaned
    assert "[REDACTED_PRIVATE_KEY]" in cleaned
    assert "[REDACTED_TOKEN]" in cleaned


def test_key_loading_order_env_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHPULSE_PROJECT_KEY", "env_secret_key_123")
    monkeypatch.setenv("CRASHPULSE_URL", "https://custom.crashpulse.dev/api/v1/report")

    json_file = tmp_path / "crashpulse.json"
    json_file.write_text(json.dumps({"key": "file_key_456", "url": "https://file.url/report"}), encoding="utf-8")

    key, url = report.load_crashpulse_config(base_path=tmp_path)
    assert key == "env_secret_key_123"
    assert url == "https://custom.crashpulse.dev/api/v1/report"


def test_key_loading_order_file_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("CRASHPULSE_PROJECT_KEY", raising=False)
    monkeypatch.delenv("CRASHPULSE_URL", raising=False)

    json_file = tmp_path / "crashpulse.json"
    json_file.write_text(json.dumps({"key": "file_key_789"}), encoding="utf-8")

    key, url = report.load_crashpulse_config(base_path=tmp_path)
    assert key == "file_key_789"
    assert url == report.DEFAULT_CRASHPULSE_URL


def test_key_loading_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("CRASHPULSE_PROJECT_KEY", raising=False)
    monkeypatch.delenv("CRASHPULSE_URL", raising=False)

    key, url = report.load_crashpulse_config(base_path=tmp_path)
    # When no env var and no valid json file exists
    assert key is None or key == ""
    assert url == report.DEFAULT_CRASHPULSE_URL


def test_send_report_missing_key():
    success, msg = report.send_report({"app": "basara4"}, key="")
    assert success is False
    assert "not configured" in msg.lower()


def test_send_report_mocked_success():
    payload = {"app": "basara4", "message": "test", "version": "1.2.0"}
    mock_resp = MagicMock()
    mock_resp.status = 201
    mock_resp.read.return_value = json.dumps({"status": "ok", "fingerprint": "a1b2c3d4e5f6"}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        success, msg = report.send_report(payload, key="cp_proj_test")
        assert success is True
        assert "Report accepted" in msg
        assert "a1b2c3d4e5" in msg
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.headers["X-project-key"] == "cp_proj_test"
        assert req.headers["Content-type"] == "application/json"


def test_send_report_mocked_401_unauthorized():
    payload = {"app": "basara4", "message": "test"}
    err = urllib.error.HTTPError(
        url="https://crashpulse.example/api/v1/report",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b'{"error": "invalid or missing project key"}'),
    )

    with patch("urllib.request.urlopen", side_effect=err):
        success, msg = report.send_report(payload, key="wrong_key")
        assert success is False
        assert "authentication failed" in msg.lower() or "invalid project key" in msg.lower()


def test_send_report_mocked_429_rate_limit():
    payload = {"app": "basara4", "message": "test"}
    err = urllib.error.HTTPError(
        url="https://crashpulse.example/api/v1/report",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(b'{"error": "rate limit exceeded"}'),
    )

    with patch("urllib.request.urlopen", side_effect=err):
        success, msg = report.send_report(payload, key="some_key")
        assert success is False
        assert "rate limit" in msg.lower()


def test_send_report_mocked_timeout():
    payload = {"app": "basara4", "message": "test"}
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
        success, msg = report.send_report(payload, key="some_key")
        assert success is False
        assert "timed out" in msg.lower()


def test_extract_failure_summary_and_log_tail(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(
        "Line 1\nLine 2\nLine 3\nSTOP: guest memory allocation failed (3072 MiB)\nTrailing line\n",
        encoding="utf-8",
    )
    summary = report.extract_failure_summary(log)
    assert "STOP: guest memory allocation failed" in summary

    tail = report.get_sanitized_log_tail(log, max_lines=2)
    assert "STOP" in tail
    assert "Trailing line" in tail
    assert "Line 1" not in tail

