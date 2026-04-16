import struct
import zlib
from pathlib import Path

from bridge.collector_tail import FileTail, HEADER_SIZE


def make_header(payload: bytes, version: int = 1, stream_id: int = 1) -> bytes:
    # build 40-byte header, set payload_bytes at offset 24, header_crc at 28, payload_crc at 32
    hdr = bytearray(40)
    hdr[0:4] = b'VRSF'
    hdr[4] = version
    hdr[5] = stream_id
    # payload_bytes (u32 little endian) at offset 24
    payload_len = len(payload)
    hdr[24:28] = struct.pack('<I', payload_len)
    # header_crc and payload_crc left zero for now
    # compute header_crc over header with bytes 28..35 zeroed
    hdr_copy = bytearray(hdr)
    for i in range(28, 36):
        hdr_copy[i] = 0
    hcrc = zlib.crc32(hdr_copy) & 0xFFFFFFFF
    pcrc = zlib.crc32(payload) & 0xFFFFFFFF
    hdr[28:32] = struct.pack('<I', hcrc)
    hdr[32:36] = struct.pack('<I', pcrc)
    return bytes(hdr)


def write_vrsf_file(path: Path, payload: bytes, corrupt_header_crc=False, corrupt_payload_crc=False, bad_magic=False):
    hdr = make_header(payload)
    if corrupt_header_crc:
        # flip a bit in the header crc region
        b = bytearray(hdr)
        b[28] ^= 0xFF
        hdr = bytes(b)
    if corrupt_payload_crc:
        b = bytearray(hdr)
        b[32] ^= 0xFF
        hdr = bytes(b)
    if bad_magic:
        b = bytearray(hdr)
        b[0:4] = b'BAD!'
        hdr = bytes(b)
    with open(path, 'wb') as f:
        f.write(hdr)
        f.write(payload)


def test_tail_once_fixed_records(tmp_path):
    # create two fixed-size records of 8 bytes each
    rec1 = b'ABCDEFGH'
    rec2 = b'IJKLMNOP'
    payload = rec1 + rec2
    p = tmp_path / 't.vrsf'
    write_vrsf_file(p, payload)
    ft = FileTail(str(p), stream_id=1, session_id=1, rec_size=8, variable=False)
    recv_ns, parsed = ft.tail_once()
    assert recv_ns is not None
    assert parsed == [rec1, rec2]


def test_tail_once_bad_magic_advances_offset(tmp_path):
    payload = b'XXXXXXXX'
    p = tmp_path / 't2.vrsf'
    write_vrsf_file(p, payload, bad_magic=True)
    ft = FileTail(str(p), stream_id=1, session_id=1, rec_size=8, variable=False)
    recv_ns, parsed = ft.tail_once()
    assert recv_ns is None and parsed is None
    # offset should have advanced by 1 due to bad magic handling
    assert ft.offset == 1


def test_tail_once_header_crc_mismatch(tmp_path):
    payload = b'PAYLOAD'
    p = tmp_path / 't3.vrsf'
    write_vrsf_file(p, payload, corrupt_header_crc=True)
    ft = FileTail(str(p), stream_id=1, session_id=1, rec_size=7, variable=False)
    recv_ns, parsed = ft.tail_once()
    assert recv_ns is None and parsed is None
    # header CRC mismatch causes offset += 1
    assert ft.offset == 1


def test_tail_once_payload_crc_mismatch_skips_chunk(tmp_path):
    payload = b'PAYLOAD1234'
    p = tmp_path / 't4.vrsf'
    write_vrsf_file(p, payload, corrupt_payload_crc=True)
    ft = FileTail(str(p), stream_id=1, session_id=1, rec_size=11, variable=False)
    recv_ns, parsed = ft.tail_once()
    assert recv_ns is None and parsed is None
    # payload CRC mismatch causes offset to jump past the chunk
    assert ft.offset == HEADER_SIZE + len(payload)


def test_tail_once_variable_records(tmp_path):
    # construct two variable records: seq,u32; unity_t f32; jlen u32; json bytes

    def pack_var(seq, unity_t, js):
        jb = js.encode('utf8')
        return struct.pack('<IfI', seq, unity_t, len(jb)) + jb

    r1 = pack_var(1, 0.1, '{"evt":"a"}')
    r2 = pack_var(2, 0.2, '{"evt":"b","i":2}')
    payload = r1 + r2
    p = tmp_path / 't5.vrsf'
    write_vrsf_file(p, payload)
    ft = FileTail(str(p), stream_id=4, session_id=1, variable=True)
    recv_ns, parsed = ft.tail_once()
    assert recv_ns is not None
    assert isinstance(parsed, list)
    assert parsed[0][0] == 1 and parsed[1][0] == 2
