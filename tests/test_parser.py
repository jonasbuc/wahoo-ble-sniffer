import struct
import time


def test_pack_unpack_dfffi():
    ts = time.time()
    power = 123.0
    cadence = 78.0
    speed = 25.5
    hr = 142

    b = struct.pack("dfffi", ts, power, cadence, speed, hr)
    assert len(b) >= 24

    uts, upower, ucadence, uspeed, uhr = struct.unpack("dfffi", b[:24])
    assert abs(uts - ts) < 1e-6
    assert abs(upower - power) < 1e-3
    assert abs(ucadence - cadence) < 1e-3
    assert abs(uspeed - speed) < 1e-3
    assert int(uhr) == hr
