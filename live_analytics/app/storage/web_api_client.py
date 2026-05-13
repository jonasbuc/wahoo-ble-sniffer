"""
HTTP client for outbound calls from the analytics ingest server.

Pulse data is written to THREE destinations:
  1. Local questionnaire API  (QS_BASE_URL, default http://localhost:8090)
     → questionnaire.db  (our own SQLite, rich schema with session_id etc.)

  2. External research API    (EXTERNAL_API_URL, default https://10.200.130.98:5001)
     → POST /api/cardatasqlite/loglitepd
     → external SQLite PulseData table  { UserId INTEGER, Pulse INTEGER }

  3. Participant log file  (live_analytics/data/participants/<participant_id>/pulse.jsonl)
     → appended locally so each test person has their own pulse log file

All sends are fire-and-forget — a failure in one never blocks the others,
and none ever crash the ingest pipeline.

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
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("live_analytics.web_api_client")

_QS_BASE_URL: str = os.getenv("QS_BASE_URL", "http://localhost:8090")
_EXTERNAL_API_URL: str = os.getenv("EXTERNAL_API_URL", "https://10.200.130.98:5001")
_EXTERNAL_USER_ID: int = int(os.getenv("EXTERNAL_USER_ID", "0"))
# Derive SNI hostname from EXTERNAL_API_URL so it stays correct even when the
# URL is changed via env var (avoids hardcoding "10.200.130.98" in the request).
_EXTERNAL_SNI_HOSTNAME: str = urlparse(_EXTERNAL_API_URL).hostname or "10.200.130.98"

_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)

# ── Participant cache ─────────────────────────────────────────────────
# Maps session_id → participant_id (str) or None (not yet linked).
# Populated lazily on first pulse per session via resolve_participant().
_participant_cache: dict[str, str | None] = {}

# In-flight events: if another coroutine is already resolving a session,
# latecomers wait on the event instead of firing a duplicate HTTP request.
_resolve_in_flight: dict[str, asyncio.Event] = {}

# ── 404 / unlinked cooldown ───────────────────────────────────────────
# When resolve_participant gets a 404 (session not yet linked to a participant)
# we wait _RESOLVE_COOLDOWN_SEC before trying again.  Without this, at 20 Hz
# with batch_size=10, every 0.5 s a new HTTP request would hit the questionnaire
# API for each unlinked session — a constant 2 req/s background noise that
# scales with the number of concurrent unlinked sessions.
#
# Maps session_id → monotonic timestamp (time.monotonic()) of last 404.
_resolve_cooldown_until: dict[str, float] = {}
_RESOLVE_COOLDOWN_SEC: float = float(os.getenv("LA_RESOLVE_COOLDOWN_SEC", "5.0"))

# ── Per-session UserId=0 warning gate ────────────────────────────────
# Prevents the "UserId is 0" warning from firing on every pulse (2/s) for
# sessions where no numeric participant is linked.  Each session_id is added
# here after the first warning; subsequent pulses in the same session log at
# DEBUG instead.
_warned_userid_zero: set[str] = set()


async def resolve_participant(session_id: str) -> str | None:
    """Fetch and cache the participant_id for a session from the questionnaire API.

    Returns the participant_id string (e.g. ``"P001"`` or ``"3"``) when found,
    or ``None`` when no participant has been linked to this session yet.
    Never raises — failures are logged as warnings.

    Automatic linking
    -----------------
    When the ``by-session`` lookup returns 404 (no participant linked yet), this
    function automatically calls ``GET /api/participants/oldest-unlinked`` to
    find the oldest pre-registered participant that has no session yet (FIFO),
    then calls ``PUT /api/participants/{id}/session`` to link them.  FIFO
    ordering is critical: if P1 registered before P2, P1 must be linked to the
    current session — not P2, which may have been created for the *next* session.

    Concurrent callers for the same *session_id* share one HTTP request via an
    asyncio.Event gate — no duplicate 404s or race conditions.

    404 cooldown
    ------------
    When both lookups return 404 (no pre-registered participant at all) we
    record a cooldown so we don't hammer the API every 0.5 s.
    """
    if session_id in _participant_cache:
        return _participant_cache[session_id]

    # Honour 404 cooldown — don't hammer the API for unlinked sessions.
    cooldown_expires = _resolve_cooldown_until.get(session_id, 0.0)
    if time.monotonic() < cooldown_expires:
        return None

    # Another coroutine is already resolving this session → wait for it.
    if session_id in _resolve_in_flight:
        await _resolve_in_flight[session_id].wait()
        return _participant_cache.get(session_id)

    event = asyncio.Event()
    _resolve_in_flight[session_id] = event
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # ── Step 1: direct by-session lookup ─────────────────────
            url = f"{_QS_BASE_URL}/api/participants/by-session/{session_id}"
            resp = await client.get(url)
            if resp.status_code == 404:
                # Session not yet linked — try to auto-link from the latest
                # pre-registered participant (registered before headset goes on).
                logger.debug(
                    "resolve_participant: session %r not yet linked — "
                    "trying auto-link via oldest-unlinked participant (FIFO)",
                    session_id,
                )
                try:
                    ul_resp = await client.get(f"{_QS_BASE_URL}/api/participants/oldest-unlinked")
                    if ul_resp.status_code == 200:
                        unlinked = ul_resp.json()
                        pid = unlinked.get("participant_id")
                        if pid:
                            # Auto-link: write session_id into the participant row.
                            link_resp = await client.put(
                                f"{_QS_BASE_URL}/api/participants/{pid}/session",
                                json={"session_id": session_id},
                            )
                            if link_resp.status_code == 200:
                                _resolve_cooldown_until.pop(session_id, None)
                                _participant_cache[session_id] = pid
                                logger.info(
                                    "resolve_participant: auto-linked session %r → "
                                    "participant %r (oldest-unlinked FIFO)",
                                    session_id, pid,
                                )
                                return pid
                            else:
                                logger.warning(
                                    "resolve_participant: auto-link PUT failed for "
                                    "session %r participant %r: HTTP %d",
                                    session_id, pid, link_resp.status_code,
                                )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "resolve_participant: auto-link attempt failed for session %r: "
                        "%s: %s",
                        session_id, type(exc).__name__, exc,
                    )

                # No pre-registered participant found — apply cooldown.
                _resolve_cooldown_until[session_id] = (
                    time.monotonic() + _RESOLVE_COOLDOWN_SEC
                )
                logger.debug(
                    "resolve_participant: no unlinked participant found for session %r "
                    "(cooldown %.0f s) — register in questionnaire before headset goes on",
                    session_id, _RESOLVE_COOLDOWN_SEC,
                )
                return None

            resp.raise_for_status()
            data = resp.json()
            pid = data.get("participant_id")
            if pid is not None:
                # Participant confirmed — cache and clear any retry cooldown.
                _resolve_cooldown_until.pop(session_id, None)
                _participant_cache[session_id] = pid
                logger.info(
                    "resolve_participant: session %r → participant %r", session_id, pid
                )
                return pid
            # API returned a valid 2xx response but participant_id is null/absent.
            # Do NOT cache None — that would permanently block future resolution.
            _resolve_cooldown_until[session_id] = (
                time.monotonic() + _RESOLVE_COOLDOWN_SEC
            )
            logger.debug(
                "resolve_participant: session %r — API returned null participant_id "
                "(will retry after %.0f s cooldown)",
                session_id, _RESOLVE_COOLDOWN_SEC,
            )
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "resolve_participant: failed for session %r: %s: %s",
            session_id, type(exc).__name__, exc,
        )
        # Apply the same cooldown on network errors so a down questionnaire
        # service doesn't cause a per-pulse HTTP retry storm either.
        _resolve_cooldown_until[session_id] = (
            time.monotonic() + _RESOLVE_COOLDOWN_SEC
        )
        return None
    finally:
        event.set()
        _resolve_in_flight.pop(session_id, None)


def clear_participant_cache(session_id: str | None = None) -> None:
    """Remove a session (or all sessions) from the participant cache.

    Also clears the 404 cooldown so the next pulse triggers a fresh lookup
    immediately — use this after linking a participant to a session.
    """
    if session_id:
        _participant_cache.pop(session_id, None)
        _resolve_cooldown_until.pop(session_id, None)
    else:
        _participant_cache.clear()
        _resolve_cooldown_until.clear()


async def clear_participant_session_link(participant_id: str) -> None:
    """Unlink *participant_id* from their current session in the questionnaire DB.

    Called when a session ends so the participant re-enters the FIFO unlinked
    pool and is auto-linked to the next Unity session without any manual step.
    Fire-and-forget — failures are logged but never raised.
    """
    url = f"{_QS_BASE_URL}/api/participants/{participant_id}/session"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.delete(url)
            if resp.status_code == 200:
                logger.info(
                    "clear_participant_session_link: participant %r unlinked — "
                    "available for next session",
                    participant_id,
                )
            else:
                logger.warning(
                    "clear_participant_session_link: DELETE %s returned HTTP %d",
                    url, resp.status_code,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "clear_participant_session_link: could not reach questionnaire API "
            "for participant %r: %s: %s",
            participant_id, type(exc).__name__, exc,
        )


def get_cached_participant(session_id: str) -> str | None:
    """Return the participant_id for *session_id* from the in-memory cache.

    This is a pure, synchronous cache read — no HTTP call is made.
    Returns ``None`` when the participant has not yet been resolved for this
    session (e.g. questionnaire API not yet reached, or no participant linked).

    Use this when you need the participant_id for *local file persistence* and
    do not want to trigger or wait for an outbound HTTP request.
    """
    return _participant_cache.get(session_id)


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


async def _send_to_external(client: httpx.AsyncClient, session_id: str, pulse: int, user_id: int) -> bool:
    """POST pulse to the external research API → PulseData table.

    Payload schema (matches external SQLite):
        { "UserId": <TestPersonNumber>, "Pulse": <bpm> }
    """
    # UserId=0 means no participant is linked or the ID is non-numeric.
    # Log a WARNING once per session_id; demote subsequent occurrences to DEBUG
    # to avoid flooding the log at 2 req/s for unlinked sessions.
    # session_id is passed in via a keyword arg so the per-session gate works.
    if user_id == 0 and session_id not in _warned_userid_zero:
        _warned_userid_zero.add(session_id)
        logger.warning(
            "send_pulse[external]: UserId is 0 for session %r — pulse written with UserId=0. "
            "Link a participant with a numeric ID to get the correct TestPersonNumber. "
            "(This warning fires once per session; further occurrences suppressed.)",
            session_id,
        )
    elif user_id == 0:
        logger.debug(
            "send_pulse[external]: UserId is 0 for session %r (already warned)", session_id
        )

    url = f"{_EXTERNAL_API_URL}/api/cardatasqlite/loglitepd"
    payload = {"UserId": user_id, "Pulse": pulse}
    try:
        # verify=False because the research server uses a self-signed certificate.
        resp = await client.post(url, json=payload, extensions={"sni_hostname": _EXTERNAL_SNI_HOSTNAME})
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
    """Send one heart-rate sample to the questionnaire API and the external research API.

    This function is responsible for **outbound API/database submission only**.
    Local file persistence (participant pulse.jsonl) is the caller's
    responsibility and must be handled separately before or after this call —
    never inside this function — so that local logs are written regardless of
    API availability.

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

    # Resolve the participant for this session so we can use the correct
    # TestPersonNumber (UserId) when writing to the external research DB.
    participant_id = await resolve_participant(session_id)
    user_id: int
    if participant_id is None:
        user_id = _EXTERNAL_USER_ID
    else:
        try:
            user_id = int(participant_id)
        except (ValueError, TypeError):
            # participant_id is a non-numeric string (e.g. "P007") — the
            # external API only accepts integer UserIds.  Fall back to the
            # configured default and warn once per session so the operator
            # knows to configure a numeric ID if the external DB matters.
            user_id = _EXTERNAL_USER_ID
            if session_id not in _warned_userid_zero:
                _warned_userid_zero.add(session_id)
                logger.warning(
                    "send_pulse: participant_id %r for session %r is non-numeric — "
                    "cannot map to external UserId; falling back to EXTERNAL_USER_ID=%d. "
                    "Use a numeric participant ID if the external research DB is needed. "
                    "(Warning fires once per session.)",
                    participant_id, session_id, _EXTERNAL_USER_ID,
                )

    # Use separate clients so that verify=False (required for the external
    # self-signed-cert server) does NOT affect the localhost questionnaire
    # connection.  Two AsyncClient instances share no state between them.
    async with (
        httpx.AsyncClient(timeout=_TIMEOUT) as qs_client,
        httpx.AsyncClient(timeout=_TIMEOUT, verify=False) as ext_client,
    ):
        qs_ok, ext_ok = await asyncio.gather(
            _send_to_questionnaire(qs_client, session_id, unix_ms, pulse),
            _send_to_external(ext_client, session_id, pulse, user_id),
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

