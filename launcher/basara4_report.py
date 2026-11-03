"""CrashPulse anonymous reporting integration for Basara 4 PC.

Handles payload construction, client-side PII/secret redaction, configuration
loading, and HTTP ingestion over urllib.
"""

import getpass
import json
import os
from pathlib import Path
import platform
import re
import ssl
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

DEFAULT_CRASHPULSE_URL = "https://crashpulse.darktama.dev/api/v1/report"
DEFAULT_APP_ID = "basara4-native-pc"
GITHUB_ISSUES_URL = "https://github.com/DarkTama/basara4-native-pc/issues/new/choose"
MIN_DESCRIPTION_LENGTH = 10
MAX_DESCRIPTION_LENGTH = 4000
DEFAULT_TIMEOUT_SECONDS = 10.0


def get_current_username() -> str:
    """Safely retrieves the current operating system username."""
    for fn in (getpass.getuser, os.getlogin):
        try:
            name = fn()
            if name and name.strip():
                return name.strip()
        except Exception:
            pass
    for var in ("USERNAME", "USER", "LOGNAME"):
        name = os.environ.get(var)
        if name and name.strip():
            return name.strip()
    return ""


def get_current_home() -> str:
    """Safely retrieves the user home directory."""
    try:
        return str(Path.home())
    except Exception:
        return os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""


def sanitize_text(text: str, custom_secrets: Optional[List[str]] = None) -> str:
    """Redacts sensitive user paths, usernames, tokens, and secrets from text."""
    if not text:
        return ""

    sanitized = text

    # Redact explicit custom secrets (e.g. project key)
    if custom_secrets:
        for s in custom_secrets:
            if s and len(s) >= 4:
                sanitized = sanitized.replace(s, "[REDACTED_SECRET]")

    # Redact SSH private key blocks
    sanitized = re.sub(
        r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----",
        "[REDACTED_PRIVATE_KEY]",
        sanitized,
    )

    # Redact GitHub tokens
    sanitized = re.sub(r"gh[pousr]_[A-Za-z0-9_]{20,}", "[REDACTED_TOKEN]", sanitized)
    sanitized = re.sub(r"github_pat_[A-Za-z0-9_]{30,}", "[REDACTED_TOKEN]", sanitized)

    # Redact generic bearer tokens, API keys, password fields
    sanitized = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9_\-\.]{16,}",
        r"\1[REDACTED_TOKEN]",
        sanitized,
    )
    sanitized = re.sub(
        r'(?i)(["\']?(?:api[_-]?key|access[_-]?token|project[_-]?key|secret|password|passwd|auth[_-]?token)["\']?\s*[:=]\s*["\']?)[A-Za-z0-9_\-\.]{8,}(["\']?)',
        r"\1[REDACTED_SECRET]\2",
        sanitized,
    )

    # Redact user home directory
    home = get_current_home()
    if home and len(home) > 3:
        # Standard format
        sanitized = sanitized.replace(home, "<USER_HOME>")
        # Forward-slash format
        home_posix = home.replace("\\", "/")
        sanitized = sanitized.replace(home_posix, "<USER_HOME>")
        # Windows escaped json format
        home_escaped = home.replace("\\", "\\\\")
        sanitized = sanitized.replace(home_escaped, "<USER_HOME>")

    # Redact username from Windows / POSIX user paths
    username = get_current_username()
    if username and len(username) >= 2:
        # Windows user folder pattern
        sanitized = re.sub(
            r"(?i)([A-Za-z]:[/\\](?:Users|Documents and Settings)[/\\])" + re.escape(username),
            r"\1<USERNAME>",
            sanitized,
        )
        # Unix user folder pattern
        sanitized = re.sub(
            r"(?i)(/(?:home|Users)/)" + re.escape(username),
            r"\1<USERNAME>",
            sanitized,
        )

    return sanitized


def sanitize_context_value(val: Any, custom_secrets: Optional[List[str]] = None) -> Any:
    """Recursively sanitizes context data structures."""
    if isinstance(val, str):
        return sanitize_text(val, custom_secrets=custom_secrets)
    if isinstance(val, dict):
        return {k: sanitize_context_value(v, custom_secrets=custom_secrets) for k, v in val.items()}
    if isinstance(val, list):
        return [sanitize_context_value(item, custom_secrets=custom_secrets) for item in val]
    if isinstance(val, tuple):
        return tuple(sanitize_context_value(item, custom_secrets=custom_secrets) for item in val)
    return val


def load_crashpulse_config(base_path: Optional[Path] = None) -> Tuple[Optional[str], str]:
    """Resolves CrashPulse project key and URL in priority order.

    Order:
      1. CRASHPULSE_PROJECT_KEY & CRASHPULSE_URL environment variables
      2. launcher/crashpulse.json or crashpulse.json relative to launcher / executable / bundled MEIPASS
    """
    env_key = os.environ.get("CRASHPULSE_PROJECT_KEY", "").strip()
    env_url = os.environ.get("CRASHPULSE_URL", "").strip() or DEFAULT_CRASHPULSE_URL
    if env_key:
        return env_key, env_url

    # Check candidates for crashpulse.json
    candidates = []
    if base_path:
        candidates.append(base_path / "crashpulse.json")
        candidates.append(base_path / "launcher" / "crashpulse.json")

    launcher_dir = Path(__file__).resolve().parent
    candidates.append(launcher_dir / "crashpulse.json")

    repo_dir = launcher_dir.parent
    candidates.append(repo_dir / "launcher" / "crashpulse.json")

    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / "launcher" / "crashpulse.json")
    candidates.append(exe_dir / "crashpulse.json")

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "launcher" / "crashpulse.json")
        candidates.append(Path(meipass) / "crashpulse.json")

    for path in candidates:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                key = str(data.get("key", "")).strip()
                url = str(data.get("url", "")).strip() or DEFAULT_CRASHPULSE_URL
                if key:
                    return key, url
            except Exception:
                continue

    return None, env_url


def get_sanitized_log_tail(log_path: Path, max_lines: int = 50) -> str:
    """Reads and sanitizes the tail of the specified log file."""
    if not log_path.is_file():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-max_lines:] if len(lines) > max_lines else lines
        return sanitize_text("\n".join(tail))
    except Exception:
        return ""


def extract_failure_summary(log_path: Path) -> str:
    """Extracts a single concise failure line (STOP, ASSERT, VK_ERROR, etc.) from the log."""
    if not log_path.is_file():
        return ""
    patterns = [
        r"(STOP: .*)",
        r"(Fault: .*)",
        r"(ASSERT_MSG.*)",
        r"(vk::Result=.*)",
        r"(VK_ERROR_\w+.*)",
        r"(GPU \[Render\.Vulkan\] <Error> .*)",
        r"(GPU \[Lib\.\w+\] <Error> .*)",
        r"(guest raised debug exception .*)",
        r"(cannot create \d+ MiB of guest memory .*)",
        r"(no rewrite table matches the image.*)",
    ]
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            for pat in patterns:
                m = re.search(pat, line)
                if m:
                    return sanitize_text(m.group(1).strip())
    except Exception:
        pass
    return ""


def build_feedback_payload(
    category: str,
    description: str,
    contact: str = "",
    app_version: str = "1.1.8",
    os_info: str = "",
    attach_diagnostics: bool = False,
    settings_summary: Optional[Dict[str, Any]] = None,
    gpu_name: str = "",
    log_tail: str = "",
) -> Dict[str, Any]:
    """Constructs a normalized CrashPulse payload for user feedback / bug reports."""
    cat_lower = category.lower().strip()
    if "bug" in cat_lower:
        canonical_category = "Bug Report"
        level = "warning"
    elif "feature" in cat_lower:
        canonical_category = "Feature Request"
        level = "info"
    else:
        canonical_category = "Other"
        level = "info"

    clean_desc = description.strip()
    message = f"[Feedback:{canonical_category}] {clean_desc}"

    context: Dict[str, Any] = {
        "type": "feedback",
        "category": canonical_category,
    }
    if contact.strip():
        context["contact"] = contact.strip()

    if attach_diagnostics:
        context["os_version"] = os_info or platform.platform()
        if gpu_name:
            context["gpu_name"] = gpu_name
        context["launcher_version"] = app_version
        if settings_summary:
            context["settings_summary"] = settings_summary

    stack = log_tail.strip() if attach_diagnostics else ""

    payload: Dict[str, Any] = {
        "app": DEFAULT_APP_ID,
        "version": app_version,
        "os": os_info or platform.platform(),
        "level": level,
        "message": message,
        "stack": stack,
        "stacktrace": stack,
        "context": context,
    }

    return sanitize_context_value(payload)


def build_crash_payload(
    exit_code: int,
    failure_summary: str = "",
    app_version: str = "1.1.8",
    os_info: str = "",
    gpu_name: str = "",
    settings_summary: Optional[Dict[str, Any]] = None,
    minidump_present: bool = False,
    log_tail: str = "",
    user_comment: str = "",
) -> Dict[str, Any]:
    """Constructs a normalized CrashPulse payload for an unexpected game crash."""
    code_hex = f"{exit_code & 0xFFFFFFFF:#x}" if isinstance(exit_code, int) else str(exit_code)
    msg = f"Game exited with code {code_hex}"
    if failure_summary and failure_summary.strip():
        msg += f": {failure_summary.strip()}"

    context: Dict[str, Any] = {
        "exit_code": exit_code,
        "os_version": os_info or platform.platform(),
        "gpu_name": gpu_name or "Unknown",
        "launcher_version": app_version,
        "settings_summary": settings_summary or {},
        "minidump_present": bool(minidump_present),
    }
    if user_comment and user_comment.strip():
        context["user_comment"] = user_comment.strip()

    stack = log_tail.strip()

    payload: Dict[str, Any] = {
        "app": DEFAULT_APP_ID,
        "version": app_version,
        "os": os_info or platform.platform(),
        "level": "fatal",
        "message": msg,
        "stack": stack,
        "stacktrace": stack,
        "context": context,
    }

    return sanitize_context_value(payload)


def send_report(
    payload: Dict[str, Any],
    key: str,
    url: str = DEFAULT_CRASHPULSE_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Tuple[bool, str]:
    """Transmits a sanitized crash or feedback payload to the CrashPulse server."""
    if not key or not key.strip():
        return False, "Crash reporting is not configured in this build."

    # Final sanitization with the actual project key redacted if it appears anywhere in values
    clean_payload = sanitize_context_value(payload, custom_secrets=[key.strip()])

    data = json.dumps(clean_payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Project-Key": key.strip(),
            "User-Agent": f"Basara4-Launcher/{payload.get('version', '1.0.0')}",
        },
        method="POST",
    )

    ctx = ssl.create_default_context()

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            if 200 <= status < 300:
                try:
                    res_json = json.loads(body)
                    fp = res_json.get("fingerprint", "")
                    if fp:
                        return True, f"Report accepted (ID: {fp[:10]})."
                except Exception:
                    pass
                return True, "Report sent successfully. Thank you!"
            return False, f"Server responded with status {status}: {body}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if e.code == 401 or e.code == 403:
            return False, "Authentication failed (invalid project key)."
        if e.code == 429:
            return False, "Rate limit reached. Please wait a minute before submitting again."
        if e.code == 400:
            return False, f"Bad request: {body or e.reason}"
        return False, f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}"
    except TimeoutError:
        return False, "Connection timed out after 10 seconds."
    except Exception as e:
        return False, f"Failed to send report: {e}"
