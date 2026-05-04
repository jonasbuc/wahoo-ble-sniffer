"""
HTTP client for outbound calls from the analytics ingest server to the
questionnaire Web API (http://localhost:8090 by default).

This module is the ONLY place in the analytics app that sends pulse data to the
external questionnaire database.  It must never be bypassed.

Usage
-----
    from live_analytics.app.storage import web_api_client

    ok = await web_api_client.send_pulse(session_id, unix_ms, pulse)
    # or fire-and-forget:
    asyncio.ensure_future(web_api_client.send_pulse(session_id, unix_ms, pulse))

Environment variables
---------------------
QS_BASE_URL : str
    Base URL of the questionnaire service.
    Default: ``http://localhost:8090``
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("live_analytics.web_api_client")

_QS_BASE_URL: str = os.getenv("QS_BASE_URL", "http://localhost:8090")
_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=2.0)


async def send_pulse(session_id: str, unix_ms: int, pulse: int) -> bool:
    """POST one heart-rate sample to the questionnaire API.

    Parameters
    ----------
    session_id:
        Active session identifier.
    unix_ms:
        Epoch timestamp of the sample in milliseconds.
    pulse:
        Heart-rate value in BPM.  Values ≤ 0 are silently discarded.

    Returns
    -------
    bool
        ``True`` if the API accepted the sample, ``False`` on any failure.
        This function **never raises** — errors are logged and the ingest
        pipeline continues unaffected.
    """
    if pulse <= 0:
        logger.debug(
            "send_pulse: skipping pulse=%d for session %r (non-positive)", pulse, session_id
        )
        return False

    url = f"{_QS_BASE_URL}/api/pulse"
    payload = {"session_id": session_id, "unix_ms": unix_ms, "pulse": pulse}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return True
    except httpx.ConnectError as exc:
        logger.warning(
            "send_pulse: questionnaire API unreachable at %s — "
            "pulse for session %r not persisted (ConnectError: %s). "
            "Is the questionnaire service running on QS_BASE_URL=%s?",
            url,
            session_id,
            exc,
            _QS_BASE_URL,
        )
    except httpx.TimeoutException as exc:
        logger.warning(
            "send_pulse: request timed out posting pulse to %s "
            "(session=%r, pulse=%d, %s: %s)",
            url,
            session_id,
            pulse,
            type(exc).__name__,
            exc,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "send_pulse: questionnaire API returned HTTP %d for session=%r pulse=%d "
            "(POST %s — response body: %r)",
            exc.response.status_code,
            session_id,
            pulse,
            url,
            exc.response.text[:200],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "send_pulse: unexpected error posting pulse to %s "
            "(session=%r, pulse=%d): %s: %s",
            url,
            session_id,
            pulse,
            type(exc).__name__,
            exc,
        )
    return False
