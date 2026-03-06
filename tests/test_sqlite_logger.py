import concurrent.futures
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

    with real_connect(str(db_path)) as mid_conn:
        mid_cur = mid_conn.execute("SELECT COUNT(*) FROM metrics")
        assert mid_cur.fetchone()[0] == 2

    logger.close()

    logger.log_metric(hr_bpm=80)
    assert len(connect_calls) == 1  # reconnect after close

    with real_connect(str(db_path)) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM metrics")
        assert cur.fetchone()[0] == 3
    logger.close()


def test_sqlite_logger_thread_safety(tmp_path):
    db_path = tmp_path / "threaded.sqlite"
    logger = SQLiteLogger(db_name=str(db_path))

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        for _ in range(10):
            executor.submit(logger.log_metric, hr_bpm=55)

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute("SELECT COUNT(*) FROM metrics")
        assert cur.fetchone()[0] == 10
    logger.close()
