# SPDX-License-Identifier: MIT
"""
Claude subscription usage — via `claude -p "/usage"` CLI command.

Parses the human-readable output:
  Current session: 7% used · resets Jul 25 at 9:50pm (America/Sao_Paulo)
  Current week (all models): 19% used · resets Jul 30 at 5am (America/Sao_Paulo)
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow running standalone or as Hermes plugin adapter
try:
    from usage_monitor import ProviderStatus, UsageWindow  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from usage_monitor import ProviderStatus, UsageWindow
    except ImportError:
        ProviderStatus = None  # type: ignore
        UsageWindow = None     # type: ignore


def _parse_percent(text: str) -> float | None:
    m = re.search(r"(\d+)%", text)
    return float(m.group(1)) if m else None


def _parse_reset(text: str) -> str | None:
    """Parse 'resets Jul 25 at 9:50pm (America/Sao_Paulo)' into ISO UTC datetime."""
    pattern = r"resets\s+(\w{3}\s+\d{1,2})\s+(?:at\s+)?(\d{1,2}(?::\d{2})?(?:am|pm))"
    m = re.search(pattern, text)
    if not m:
        return None
    date_part = m.group(1)  # "Jul 25"
    time_part = m.group(2)  # "9:50pm" or "5am"
    year = datetime.now(timezone.utc).year
    try:
        # Normalize "5am" -> "5:00am" for strptime
        normalized = time_part if ":" in time_part else time_part.replace("am", ":00am").replace("pm", ":00pm")
        dt_str = f"{date_part} {year} {normalized}"
        dt_naive = datetime.strptime(dt_str, "%b %d %Y %I:%M%p")
        dt_sp = dt_naive.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
        return dt_sp.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def check():
    label = "Claude / Anthropic"
    provider_id = "claude"

    claude_bin = None
    for candidate in ("claude", "/Users/ruivalim/.local/bin/claude"):
        try:
            subprocess.run([candidate, "--version"], capture_output=True, timeout=5)
            claude_bin = candidate
            break
        except Exception:
            continue

    if not claude_bin:
        return {
            "id": provider_id,
            "label": label,
            "status": "unavailable",
            "source": "cli",
            "message": "claude CLI not found",
        }

    try:
        proc = subprocess.run(
            [claude_bin, "-p", "/usage"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = proc.stdout or ""
        if proc.returncode != 0:
            return {
                "id": provider_id,
                "label": label,
                "status": "unavailable",
                "source": "cli",
                "message": f"claude CLI exited {proc.returncode}: {output[:200]}",
            }

    except subprocess.TimeoutExpired:
        return {
            "id": provider_id,
            "label": label,
            "status": "unavailable",
            "source": "cli",
            "message": "claude CLI timed out",
        }
    except Exception as exc:
        return {
            "id": provider_id,
            "label": label,
            "status": "unavailable",
            "source": "cli",
            "message": str(exc)[:240],
        }

    # Parse windows from output
    windows = []
    details = []

    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue

        # "Current session: 7% used · resets Jul 25 at 9:50pm (America/Sao_Paulo)"
        if "session:" in line.lower() and "%" in line:
            pct = _parse_percent(line)
            reset = _parse_reset(line)
            windows.append({
                "label": "Current session",
                "used_percent": pct,
                "remaining_percent": (max(0.0, 100.0 - pct) if pct is not None else None),
                "reset_at": reset,
            })
        elif "week" in line.lower() and "%" in line:
            pct = _parse_percent(line)
            reset = _parse_reset(line)
            window_label = "Current week (all models)"
            if "all models" in line.lower():
                window_label = "Current week (all models)"
            elif "week" in line.lower():
                window_label = "Current week"
            windows.append({
                "label": window_label,
                "used_percent": pct,
                "remaining_percent": (max(0.0, 100.0 - pct) if pct is not None else None),
                "reset_at": reset,
            })
        elif "subscription" in line.lower() and not windows:
            # "You are currently using your subscription to power your Claude Code usage"
            pass  # header line, skip
        elif "limits usage" in line.lower() or "contributing" in line.lower():
            pass  # section header, skip
        elif line and not line.startswith("Current") and not line.startswith("Last"):
            details.append(line)

    # Determine status
    status = "ok"
    message = None
    for w in windows:
        pct = w.get("used_percent")
        if pct is not None and pct >= 100:
            status = "quota_exhausted"
            message = "Usage exhausted"
        elif pct is not None and pct >= 85 and status == "ok":
            status = "warning"
            message = "Usage nearly exhausted"

    if not windows and not details:
        status = "unavailable"
        message = message or "No usage data parsed from CLI output"

    return {
        "id": provider_id,
        "label": label,
        "status": status,
        "source": "cli",
        "windows": windows,
        "details": details[:10],
        "message": message,
    }
