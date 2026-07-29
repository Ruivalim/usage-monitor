# SPDX-License-Identifier: MIT
from __future__ import annotations

# Backward-compatible shim for Hermes plugin installs and old external adapters.
# The actual standalone core lives in usage_monitor_app.core and has no Hermes
# sys.path mutation on import.
from usage_monitor_app.core import *  # noqa: F401,F403
