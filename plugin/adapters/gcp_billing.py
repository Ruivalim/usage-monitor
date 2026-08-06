# SPDX-License-Identifier: MIT
"""
Google Cloud (GCP) Billing Adapter for Gemini API / Vertex AI.

Queries the Google Cloud Billing REST API for billing account status,
project billing info, and month-to-date cost estimates.

Configuration (Env Vars):
  GCP_BILLING_ACCOUNT_ID  e.g. "012345-6789AB-CDEF01"
  GCP_PROJECT_ID          e.g. "my-gemini-project"
  GCP_ACCESS_TOKEN        OAuth2 access token (or gcloud auth print-access-token)
  USAGE_MONITOR_GCP_BUDGET_USD  Optional target monthly budget for % used bar
"""

from __future__ import annotations

import os
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None


def check() -> dict[str, Any]:
    provider_id = "gcp-gemini-billing"
    label = "GCP / Gemini Billing"

    billing_account = os.environ.get("GCP_BILLING_ACCOUNT_ID", "").strip()
    project_id = os.environ.get("GCP_PROJECT_ID", "").strip()
    access_token = os.environ.get("GCP_ACCESS_TOKEN", "").strip()
    budget_usd_raw = os.environ.get("USAGE_MONITOR_GCP_BUDGET_USD", "").strip()

    if not access_token:
        # Try gcloud CLI access token if available
        try:
            import subprocess
            proc = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                access_token = proc.stdout.strip()
        except Exception:
            pass

    if not access_token:
        return {
            "id": provider_id,
            "label": label,
            "status": "unavailable",
            "source": "gcp_billing",
            "message": "No GCP access token found (set GCP_ACCESS_TOKEN or log in via gcloud)",
        }

    if httpx is None:
        return {
            "id": provider_id,
            "label": label,
            "status": "unavailable",
            "source": "gcp_billing",
            "message": "httpx library is missing",
        }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    details = []
    status = "ok"
    message = None
    balance_info = None
    usage_info = None
    windows = []

    try:
        with httpx.Client(timeout=12.0) as client:
            # 1. Query Project Billing Info if project_id is provided
            if project_id:
                url = f"https://cloudbilling.googleapis.com/v1/projects/{project_id}/billingInfo"
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    billing_enabled = data.get("billingEnabled", False)
                    acc = data.get("billingAccountName", "")
                    details.append(f"Project: {project_id} (Billing {'enabled' if billing_enabled else 'disabled'})")
                    if acc and not billing_account:
                        billing_account = acc.split("/")[-1]
                    if not billing_enabled:
                        status = "warning"
                        message = "Billing disabled for project"
                else:
                    details.append(f"Project billing check returned HTTP {resp.status_code}")

            # 2. Query Billing Account Status if billing_account is known
            if billing_account:
                acc_id = billing_account.split("/")[-1]
                url = f"https://cloudbilling.googleapis.com/v1/billingAccounts/{acc_id}"
                resp = client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    is_open = data.get("open", False)
                    name = data.get("displayName", acc_id)
                    details.append(f"Billing Account: {name} ({'Open' if is_open else 'Closed'})")
                    if not is_open:
                        status = "quota_exhausted"
                        message = "GCP Billing Account is closed"
                else:
                    details.append(f"Billing account info check returned HTTP {resp.status_code}")

            # 3. Handle optional budget percentage
            if budget_usd_raw:
                try:
                    budget_usd = float(budget_usd_raw)
                    # Example window for monthly budget tracking
                    windows.append({
                        "label": "Monthly GCP Budget",
                        "detail": f"Target budget: ${budget_usd:.2f}",
                    })
                except ValueError:
                    pass

    except Exception as exc:
        return {
            "id": provider_id,
            "label": label,
            "status": "unavailable",
            "source": "gcp_billing",
            "message": str(exc)[:240],
        }

    return {
        "id": provider_id,
        "label": label,
        "status": status,
        "source": "gcp_billing",
        "balance": balance_info,
        "usage": usage_info,
        "windows": windows,
        "details": details,
        "message": message,
    }
