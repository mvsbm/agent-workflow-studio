"""Small, server-side client for the Nayose admin job API."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class NayoseAPI:
    base_url: str
    token: str
    request_timeout: float = 15.0
    poll_interval: float = 1.0
    job_timeout: float = 43_200.0

    @classmethod
    def from_environment(cls) -> NayoseAPI:
        base_url = os.environ.get("NAYOSE_API_URL", "http://127.0.0.1:8000").rstrip("/")
        token = os.environ.get("NAYOSE_API_TOKEN", "")
        if not token:
            raise RuntimeError(
                "NAYOSE_API_TOKEN must be configured in the ComfyUI server environment"
            )
        return cls(
            base_url=base_url,
            token=token,
            request_timeout=float(os.environ.get("AGENT_STUDIO_API_TIMEOUT", "15")),
            poll_interval=float(os.environ.get("AGENT_STUDIO_POLL_INTERVAL", "1")),
            job_timeout=float(os.environ.get("AGENT_STUDIO_JOB_TIMEOUT", "43200")),
        )

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Do not surface server response bodies: they may contain sensitive details.
            raise RuntimeError(
                f"Nayose API request failed with HTTP {exc.code}"
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(
                f"Nayose API is unavailable: {type(exc).__name__}"
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("Nayose API returned an invalid JSON response") from None
        if not isinstance(result, dict):
            raise TypeError("Nayose API returned an unexpected response type")
        return result

    def preview_generation(self, controls: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/api/admin/generation-preview", {"controls": controls}
        )

    def run_admin_job(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action not in {"generate", "train"}:
            raise ValueError(f"unsupported Nayose job action: {action}")
        job = self._request(
            "POST", "/api/admin/jobs", {"action": action, "params": params}
        )
        job_id = job.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise RuntimeError("Nayose API did not return a job ID")

        deadline = time.monotonic() + self.job_timeout
        while time.monotonic() < deadline:
            stage = job.get("stage")
            if stage == "complete":
                result = job.get("result")
                if isinstance(result, dict):
                    return result
                raise RuntimeError("Nayose job completed without a result")
            if stage in {"failed", "cancelled"}:
                error = job.get("error") or stage
                raise RuntimeError(f"Nayose {action} job {stage}: {error}")
            time.sleep(self.poll_interval)
            job = self._request("GET", f"/api/admin/jobs/{job_id}")

        try:
            self._request("POST", f"/api/admin/jobs/{job_id}/cancel")
        except RuntimeError:
            pass
        raise TimeoutError(
            f"Nayose {action} job exceeded the configured timeout and was cancelled"
        )
