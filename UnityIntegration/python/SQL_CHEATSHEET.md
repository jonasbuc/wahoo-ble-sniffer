# SQLite cheatsheet — common inspection queries

Useful queries to inspect the collector DB. Run from repo root (macOS / zsh):

```bash
. .venv/bin/activate
sqlite3 -header -column collector_out/vrs.sqlite "<QUERY>"
```

Examples

- List tables:

```sql
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
```

- Show schema for a table/view:

```sql
PRAGMA table_info('headpose');
```

- Count rows per table:

```sql
SELECT 'headpose', COUNT(*) FROM headpose;
SELECT 'bike', COUNT(*) FROM bike;
```

- Query readable view (shows recv_ts_iso if you created views):

```sql
SELECT session_id, recv_ts_iso, seq, unity_t FROM headpose_readable ORDER BY recv_ts_ns LIMIT 20;
```

- Join sessions to headpose to get session metadata + first headpose rows:

```sql
SELECT s.session_id, s.started_unix_iso, h.recv_ts_iso, h.seq, h.px, h.py, h.pz
FROM sessions_readable s
JOIN headpose_readable h ON s.session_id = h.session_id
ORDER BY s.session_id, h.recv_ts_ns
LIMIT 50;
```

- Parse JSON fields from events (if using events_readable):

```sql
SELECT session_id, recv_ts_iso, evt_name, evt_i, json FROM events_readable LIMIT 50;
```

Notes
- Use the `_readable` views created by `create_readable_views.py` to get
  ISO timestamps and parsed JSON columns if you prefer human-friendly output.
