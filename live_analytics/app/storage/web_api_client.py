"""
HTTP client for outbound calls from the analytics ingest server.

Pulse data is written to TWO destinations simultaneously:
  1. Local questionnaire API  (QS_BASE_URL, default http://localhost:8090)
     → questionnaire.db  (our own SQLite, rich schema with session_id etc.)

  2. External research API    (EXTERNAL_API_URL, default https://10.200.130.98:5001)
     → POST /api/cardatasqlite/loglitepd
     → external SQLite PulseData table  { UserId INTEGER, Pulse INTEGER }

Both sends are fire-and-forget — a failure in one never blocks the other,
and neither ever crashes the ingest pipeline.

Environment variables
---------------------
QS_BASE_URL : str
    Base URL of the local questionnaire service.
    Default: ``http://localhost:8090``

EXTERNAL_API_URL : str
    Base URL of the external research API.
    Default: ``https://10.200.130.98:5001``

EXTERNAL_USER_ID : int
    The ``UserId`` (= TestPersonNumber) to use when writing to the external DB.
    Set this to the participant's test-person number before each session.
    Default: ``0``  (indicates "not configured" — a warning is logged)
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("live_analytics.web_api_client")

_QS_BASE_URL: str = os.getenv("QS_BASE_URL", "http://localhost:8090")
_EXTERNAL_API_URL: str = os.getenv("EXTERNAL_API_URL", "https://10.200.130.98:5001")
_EXTERNAL_USER_ID: int = int(os.getenv("EXTERNAL_USER_ID", "0"))

_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)


# ── Internal helpers ──────────────────────────────────────────────────

async def _send_to_questionnaire(client: httpx.AsyncClient, session_id: str, unix_ms: int, pulse: int) -> bool:
    """POST pulse to our own questionnaire API → questionnaire.db."""
    url = f"{_QS_BASE_URL}/api/pulse"
    payload = {"session_id": session_id, "unix_ms": unix_ms, "pulse": pulse}
    try:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return True
    except httpx.ConnectError as exc:
        logger.warning(
            "send_pulse[questionnaire]: API unreachable at %s — "
            "pulse for session %r not persisted (ConnectError: %s). "
            "Is the questionnaire service running on QS_BASE_URL=%s?",
            url, session_id, exc, _QS_BASE_URL,
        )
    except httpx.TimeoutException as exc:
        logger.warning(
            "send_pulse[questionnaire]: request timed out (session=%r, pulse=%d, %s)",
            session_id, pulse, exc,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "send_pulse[questionnaire]: HTTP %d for session=%r pulse=%d (response: %r)",
            exc.response.status_code, session_id, pulse, exc.response.text[:200],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "send_pulse[questionnaire]: unexpected error (session=%r, pulse=%d): %s",
            session_id, pulse, exc,
        )
    return False


async def _send_to_external(client: httpx.AsyncClient, pulse: int) -> bool:
    """POST pulse to the external research API → PulseData table.

    Payload schema (matches external SQLite):
        { "UserId": <TestPersonNumber>, "Pulse": <bpm> }
    """
    user_id = int(os.getenv("EXTERNAL_USER_ID", str(_EXTERNAL_USER_ID)))
    if user_id == 0:
        logger.warning(
            "send_pulse[external]: EXTERNAL_USER_ID is not set (currently 0). "
            "Pulse will be written with UserId=0. "
            "Set the EXTERNAL_USER_ID environment variable to the participant's TestPersonNumber."
        )

    url = f"{_EXTERNAL_API_URL}/api/cardatasqlite/loglitepd"
    payload = {"UserId": user_id, "Pulse": pulse}
    try:
        # verify=False because the research server uses a self-signed certificate.
        resp = await client.post(url, json=payload, extensions={"sni_hostname": "10.200.130.98"})
        resp.raise_for_status()
        logger.debug(
            "send_pulse[external]: OK — UserId=%d pulse=%d → %s",
            user_id, pulse, url,
        )
        return True
    except httpx.ConnectError as exc:
        logger.warning(
            "send_pulse[external]: research API unreachable at %s — "
            "pulse not persisted in external DB (ConnectError: %s). "
            "Is the research server reachable on EXTERNAL_API_URL=%s?",
            url, exc, _EXTERNAL_API_URL,
        )
    except httpx.TimeoutException as exc:
        logger.warning(
            "send_pulse[external]: request timed out posting to %s (pulse=%d, %s)",
            url, pulse, exc,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "send_pulse[external]: HTTP %d for pulse=%d (POST %s — response: %r)",
            exc.response.status_code, pulse, url, exc.response.text[:200],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "send_pulse[external]: unexpected error posting to %s (pulse=%d): %s",
            url, pulse, exc,
        )
    return False


# ── Public API ────────────────────────────────────────────────────────

async def send_pulse(session_id: str, unix_ms: int, pulse: int) -> bool:
    """Send one heart-rate sample to BOTH the questionnaire API and the external research API.

    The two HTTP calls run concurrently.  A failure in either destination is
    logged but never raises — the ingest pipeline always continues.

    Parameters
    ----------
    session_id:
        Active session identifier (used by the questionnaire API).
    unix_ms:
        Epoch timestamp in milliseconds (used by the questionnaire API).
    pulse:
        Heart-rate in BPM.  Values ≤ 0 are silently discarded.

    Returns
    -------
    bool
        ``True`` only if BOTH destinations accepted the sample.
    """
    if pulse <= 0:
        logger.debug(
            "send_pulse: skipping pulse=%d for session %r (non-positive)", pulse, session_id
        )
        return False

    # Share one AsyncClient across both calls (one connection pool, lower overhead).
    # verify=False for the external server which uses a self-signed TLS certificate.
    async with httpx.AsyncClient(timeout=_TIMEOUT, verify=False) as client:
        qs_ok, ext_ok = await asyncio.gather(
            _send_to_questionnaire(client, session_id, unix_ms, pulse),
            _send_to_external(client, pulse),
        )

    if not qs_ok:
        logger.warning(
            "send_pulse: pulse=%d for session %r was NOT saved to questionnaire DB",
            pulse, session_id,
        )
    if not ext_ok:
        logger.warning(
            "send_pulse: pulse=%d for session %r was NOT saved to external research DB",
            pulse, session_id,
        )

    return qs_ok and ext_ok

