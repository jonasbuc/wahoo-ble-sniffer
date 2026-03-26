-- ═══════════════════════════════════════════════════════════════════════
-- MSSQL Schema for VRS Cycling Data
-- ═══════════════════════════════════════════════════════════════════════
--
-- Migration script: creates all tables, indexes, and convenience views
-- for the VR cycling data collector.  Run once against a fresh database.
--
-- Type mapping from SQLite:
--   SQLite INTEGER (64-bit)  →  BIGINT
--   SQLite INTEGER (uint32)  →  INT
--   SQLite REAL    (float32) →  REAL        (4-byte IEEE 754)
--   SQLite TEXT    (json)    →  NVARCHAR(MAX)
--   (no equivalent)          →  IDENTITY PK (MSSQL surrogate key)
--   (no equivalent)          →  TINYINT     (0/1 brake flags)
--
-- Naming conventions:
--   • Tables use the same names as the SQLite schema (sessions, headpose,
--     bike, hr, events) for compatibility with existing Python code.
--   • All raw timestamps are recv_ts_ns (nanoseconds since Unix epoch).
--   • The *_readable views add human-friendly columns (recv_ts_ms,
--     recv_ts_utc) identical to the SQLite create_readable_views.py output.
-- ═══════════════════════════════════════════════════════════════════════

-- ── Sessions ─────────────────────────────────────────────────────────
-- One row per Unity recording session, inserted when the collector
-- discovers a new session_*/manifest.json directory.

CREATE TABLE sessions (
    session_id        BIGINT          NOT NULL  PRIMARY KEY,
    started_unix_ms   BIGINT          NOT NULL,
    session_dir       NVARCHAR(500)   NULL,
    created_at        DATETIME2(3)    NOT NULL  DEFAULT SYSUTCDATETIME()
);


-- ── Headpose (stream 1) ─────────────────────────────────────────────
-- VR headset position + rotation quaternion, typically ~90 Hz.
-- Binary record: 36 bytes  <Iffffffff>

CREATE TABLE headpose (
    id            BIGINT IDENTITY(1,1)  NOT NULL  PRIMARY KEY,
    session_id    BIGINT                NOT NULL,
    recv_ts_ns    BIGINT                NOT NULL,
    seq           INT                   NOT NULL,
    unity_t       REAL                  NOT NULL,
    px            REAL                  NOT NULL,
    py            REAL                  NOT NULL,
    pz            REAL                  NOT NULL,
    qx            REAL                  NOT NULL,
    qy            REAL                  NOT NULL,
    qz            REAL                  NOT NULL,
    qw            REAL                  NOT NULL,

    CONSTRAINT fk_headpose_session
        FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

CREATE NONCLUSTERED INDEX idx_headpose_sid
    ON headpose (session_id, recv_ts_ns);


-- ── Bike (stream 2) ─────────────────────────────────────────────────
-- Cycling telemetry: speed, steering, brake states.  Typically ~50 Hz.
-- Binary record: 20 bytes  <IfffBB> + 2 padding

CREATE TABLE bike (
    id            BIGINT IDENTITY(1,1)  NOT NULL  PRIMARY KEY,
    session_id    BIGINT                NOT NULL,
    recv_ts_ns    BIGINT                NOT NULL,
    seq           INT                   NOT NULL,
    unity_t       REAL                  NOT NULL,
    speed         REAL                  NOT NULL,     -- km/h
    steering      REAL                  NOT NULL,     -- normalised -1..+1
    brake_front   TINYINT               NOT NULL,     -- 0 or 1
    brake_rear    TINYINT               NOT NULL,     -- 0 or 1

    CONSTRAINT fk_bike_session
        FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

CREATE NONCLUSTERED INDEX idx_bike_sid
    ON bike (session_id, recv_ts_ns);


-- ── Heart Rate (stream 3) ───────────────────────────────────────────
-- BLE heart-rate BPM, typically ~1 Hz.
-- Binary record: 12 bytes  <Iff>

CREATE TABLE hr (
    id            BIGINT IDENTITY(1,1)  NOT NULL  PRIMARY KEY,
    session_id    BIGINT                NOT NULL,
    recv_ts_ns    BIGINT                NOT NULL,
    seq           INT                   NOT NULL,
    unity_t       REAL                  NOT NULL,
    hr_bpm        REAL                  NOT NULL,     -- beats per minute (30-220)

    CONSTRAINT fk_hr_session
        FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

CREATE NONCLUSTERED INDEX idx_hr_sid
    ON hr (session_id, recv_ts_ns);


-- ── Events (stream 4) ──────────────────────────────────────────────
-- Arbitrary JSON event strings (triggers, laps, etc.).
-- Variable-length records.

CREATE TABLE events (
    id              BIGINT IDENTITY(1,1)  NOT NULL  PRIMARY KEY,
    session_id      BIGINT                NOT NULL,
    recv_ts_ns      BIGINT                NOT NULL,
    seq             INT                   NOT NULL,
    unity_t         REAL                  NOT NULL,
    json_payload    NVARCHAR(MAX)         NOT NULL,

    -- Computed columns for fast filtering without JSON parsing:
    evt_name  AS CAST(JSON_VALUE(json_payload, '$.evt') AS NVARCHAR(100)),
    evt_i     AS CAST(JSON_VALUE(json_payload, '$.i')   AS INT),

    CONSTRAINT fk_events_session
        FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

CREATE NONCLUSTERED INDEX idx_events_sid
    ON events (session_id, recv_ts_ns);


-- ═══════════════════════════════════════════════════════════════════════
-- Readable Views
-- ═══════════════════════════════════════════════════════════════════════
-- These mirror the SQLite *_readable views created by
-- create_readable_views.py — adding recv_ts_ms (milliseconds)
-- and recv_ts_utc (datetime) columns without modifying raw data.
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR ALTER VIEW sessions_readable AS
SELECT
    session_id,
    started_unix_ms,
    DATEADD(MILLISECOND, CAST(started_unix_ms % 1000 AS INT),
        DATEADD(SECOND, started_unix_ms / 1000, '1970-01-01'))  AS started_utc,
    session_dir,
    created_at
FROM sessions;
GO

CREATE OR ALTER VIEW headpose_readable AS
SELECT
    session_id,
    recv_ts_ns,
    recv_ts_ns / 1000000                                         AS recv_ts_ms,
    DATEADD(MICROSECOND, CAST((recv_ts_ns / 1000) % 1000000 AS INT),
        DATEADD(SECOND, recv_ts_ns / 1000000000, '1970-01-01')) AS recv_ts_utc,
    seq, unity_t, px, py, pz, qx, qy, qz, qw
FROM headpose;
GO

CREATE OR ALTER VIEW bike_readable AS
SELECT
    session_id,
    recv_ts_ns,
    recv_ts_ns / 1000000                                         AS recv_ts_ms,
    DATEADD(MICROSECOND, CAST((recv_ts_ns / 1000) % 1000000 AS INT),
        DATEADD(SECOND, recv_ts_ns / 1000000000, '1970-01-01')) AS recv_ts_utc,
    seq, unity_t, speed, steering, brake_front, brake_rear
FROM bike;
GO

CREATE OR ALTER VIEW hr_readable AS
SELECT
    session_id,
    recv_ts_ns,
    recv_ts_ns / 1000000                                         AS recv_ts_ms,
    DATEADD(MICROSECOND, CAST((recv_ts_ns / 1000) % 1000000 AS INT),
        DATEADD(SECOND, recv_ts_ns / 1000000000, '1970-01-01')) AS recv_ts_utc,
    seq, unity_t, hr_bpm
FROM hr;
GO

CREATE OR ALTER VIEW events_readable AS
SELECT
    session_id,
    recv_ts_ns,
    recv_ts_ns / 1000000                                         AS recv_ts_ms,
    DATEADD(MICROSECOND, CAST((recv_ts_ns / 1000) % 1000000 AS INT),
        DATEADD(SECOND, recv_ts_ns / 1000000000, '1970-01-01')) AS recv_ts_utc,
    seq, unity_t, json_payload,
    evt_name,
    evt_i
FROM events;
GO
