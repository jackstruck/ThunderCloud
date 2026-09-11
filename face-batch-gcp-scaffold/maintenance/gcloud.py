"""Administrative gcloud credentials via existing ADC; never place tokens in arguments."""

import os
import shutil


def _gcloud() -> tuple[str, dict[str, str]]:
    binary = os.getenv("FACE_GCLOUD_BIN") or shutil.which("gcloud")
    fallback = "/workspaces/ThunderCloud/google-cloud-sdk/bin/gcloud"
    if binary is None and os.path.isfile(fallback):
        binary = fallback
    if binary is None:
        raise RuntimeError("gcloud is not installed or configured")
    # The interactive SDK login may expire independently from ADC. Refresh ADC in
    # memory and give gcloud the short-lived token without placing it in arguments.
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default()
    credentials.refresh(Request())
    environment = os.environ.copy()
    environment["CLOUDSDK_AUTH_ACCESS_TOKEN"] = credentials.token
    return binary, environment
