import sqlite3

from wahoo_ble_logger import SQLiteLogger


def test_sqlite_logger_reuses_single_connection(monkeypatch, tmp_path):
    db_path = tmp_path / "metrics.sqlite"
    connect_calls = []
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        connect_calls.append(args[0])
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", counting_connect)

    logger = SQLiteLogger(db_name=str(db_path))
    assert len(connect_calls) == 1

    connect_calls.clear()
    logger.log_metric(hr_bpm=70)
    logger.log_metric(power_w=200)
    assert connect_calls == []

    logger.close()

    conn = real_connect(str(db_path))
    cur = conn.execute("SELECT COUNT(*) FROM metrics")
    assert cur.fetchone()[0] == 2
    conn.close()
