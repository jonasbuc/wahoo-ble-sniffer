import struct
import time


def test_pack_unpack_di():
    """Wire format is struct.pack('di', timestamp, hr) — 12 bytes."""
    ts = time.time()
    hr = 142

    b = struct.pack("di", ts, hr)
    assert len(b) == 12

    uts, uhr = struct.unpack("di", b)
    assert abs(uts - ts) < 1e-6
    assert int(uhr) == hr
