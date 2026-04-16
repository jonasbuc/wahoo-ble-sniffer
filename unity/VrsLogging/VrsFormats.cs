using System;
using System.Buffers.Binary;
using UnityEngine;

namespace VrsLogging
{
    /// <summary>
    /// Static helpers that define VRSF binary record layouts and provide
    /// methods for writing them into pre-allocated Span&lt;byte&gt; buffers.
    ///
    /// VRSF Chunk Header Layout (40 bytes, all fields little-endian)
    /// ──────────────────────────────────────────────────────────────
    /// Offset  Size  Type     Field
    ///   0      4    char[4]  Magic = "VRSF"
    ///   4      1    uint8    Version  (currently 1)
    ///   5      1    uint8    StreamId (1=headpose, 2=bike, 3=hr, 4=events)
    ///   6      2    uint16   Flags    (reserved, write 0)
    ///   8      8    uint64   SessionId
    ///  16      4    uint32   ChunkSeq (monotonically increasing per stream)
    ///  20      4    uint32   RecordCount
    ///  24      4    uint32   PayloadBytes
    ///  28      4    uint32   HeaderCRC32 (computed with this field = 0)
    ///  32      4    uint32   PayloadCRC32
    ///  36      4    uint32   Reserved
    ///
    /// CRC order: always compute PayloadCRC32 first, then zero both CRC
    /// fields and compute HeaderCRC32 over the 40-byte header.
    ///
    /// Record layouts
    /// ──────────────
    /// Stream 1 – headpose (36 bytes):
    ///   [0-3]  seq      uint32
    ///   [4-7]  unity_t  float32
    ///   [8-19] pos      float32 × 3  (px, py, pz)
    ///   [20-35] rot     float32 × 4  (qx, qy, qz, qw)
    ///
    /// Stream 2 – bike (20 bytes):
    ///   [0-3]  seq        uint32
    ///   [4-7]  unity_t    float32
    ///   [8-11] speed      float32  (km/h)
    ///   [12-15] steering  float32  (−1…+1)
    ///   [16]   brakeFront uint8
    ///   [17]   brakeRear  uint8
    ///   [18-19] padding   uint16 = 0
    ///
    /// Stream 3 – hr (12 bytes):
    ///   [0-3]  seq      uint32
    ///   [4-7]  unity_t  float32
    ///   [8-11] hr_bpm   float32
    ///
    /// Stream 4 – events (variable):
    ///   [0-3]   seq       uint32
    ///   [4-7]   unity_t   float32
    ///   [8-11]  json_len  uint32
    ///   [12…]   json      UTF-8 bytes × json_len
    /// </summary>
    public static class VrsFormats
    {
        /// <summary>Total size in bytes of a VRSF chunk header.</summary>
        public const int HeaderSize = 40;

        /// <summary>Current file format version written into every chunk header.</summary>
        public const byte Version = 1;

        // Stream ID constants — used in WriteChunkHeader and by the Python collector.
        public const byte StreamHeadpose = 1;
        public const byte StreamBike     = 2;
        public const byte StreamHr       = 3;
        public const byte StreamEvents   = 4;

        // Fixed record sizes for the three fixed-size streams.
        public const int HeadposeRecordSize = 36;
        public const int BikeRecordSize     = 20;
        public const int HrRecordSize       = 12;

        /// <summary>
        /// Fill the first 40 bytes of <paramref name="dst"/> with a VRSF chunk header.
        /// Both CRC fields are written as 0; the caller must back-fill them after
        /// computing PayloadCRC32 (offset 32) and HeaderCRC32 (offset 28).
        ///
        /// Field offsets (see class-level layout diagram):
        ///   0-3  : "VRSF" magic
        ///   4    : Version
        ///   5    : streamId
        ///   6-7  : flags (uint16 LE)
        ///   8-15 : sessionId (uint64 LE)
        ///  16-19 : chunkSeq (uint32 LE)
        ///  20-23 : recordCount (uint32 LE)
        ///  24-27 : payloadBytes (uint32 LE)
        ///  28-31 : HeaderCRC32 = 0 (placeholder)
        ///  32-35 : PayloadCRC32 = 0 (placeholder)
        ///  36-39 : Reserved = 0
        /// </summary>
        public static void WriteChunkHeader(Span<byte> dst, byte streamId, ulong sessionId, uint chunkSeq, uint recordCount, uint payloadBytes, ushort flags = 0)
        {
            if (dst.Length < HeaderSize) throw new ArgumentException("dst too small");
            // Magic bytes identifying this as a VRSF file.
            dst[0] = (byte)'V'; dst[1] = (byte)'R'; dst[2] = (byte)'S'; dst[3] = (byte)'F';
            dst[4] = Version;
            dst[5] = streamId;
            BinaryPrimitives.WriteUInt16LittleEndian(dst.Slice(6, 2), flags);
            BinaryPrimitives.WriteUInt64LittleEndian(dst.Slice(8, 8), sessionId);
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(16, 4), chunkSeq);
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(20, 4), recordCount);
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(24, 4), payloadBytes);
            // CRC fields: zero for now; filled in by WriteChunk() after CRC calculation.
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(28, 4), 0u);  // HeaderCRC32 placeholder
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(32, 4), 0u);  // PayloadCRC32 placeholder
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(36, 4), 0u);  // Reserved
        }

        /// <summary>
        /// Write a headpose record (36 bytes) at the start of <paramref name="dst"/>.
        /// Encodes: seq, unity_t, pos (px py pz), rot (qx qy qz qw), all as float32 LE.
        /// </summary>
        public static void WriteHeadposeRecord(Span<byte> dst, uint seq, float unityT, Vector3 pos, Quaternion rot)
        {
            if (dst.Length < HeadposeRecordSize) throw new ArgumentException("dst too small");
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(0,4), seq);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(4,4), unityT);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(8,4),  pos.x);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(12,4), pos.y);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(16,4), pos.z);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(20,4), rot.x);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(24,4), rot.y);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(28,4), rot.z);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(32,4), rot.w);
        }

        /// <summary>
        /// Write a bike record (20 bytes).  Bytes 18-19 are padding (written as 0).
        /// </summary>
        public static void WriteBikeRecord(Span<byte> dst, uint seq, float unityT, float speed, float steering, byte brakeFront, byte brakeRear)
        {
            if (dst.Length < BikeRecordSize) throw new ArgumentException("dst too small");
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(0,4), seq);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(4,4), unityT);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(8,4), speed);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(12,4), steering);
            dst[16] = brakeFront;
            dst[17] = brakeRear;
            BinaryPrimitives.WriteUInt16LittleEndian(dst.Slice(18,2), 0);  // 2-byte alignment padding
        }

        /// <summary>Write an HR record (12 bytes): seq, unity_t, hr_bpm.</summary>
        public static void WriteHrRecord(Span<byte> dst, uint seq, float unityT, float hrBpm)
        {
            if (dst.Length < HrRecordSize) throw new ArgumentException("dst too small");
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(0,4), seq);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(4,4), unityT);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(8,4), hrBpm);
        }

        /// <summary>
        /// Write a variable-length event record and return its total byte size.
        /// Layout: [seq u32][unity_t f32][json_len u32][json UTF-8 bytes]
        /// </summary>
        public static int WriteEventRecord(Span<byte> dst, uint seq, float unityT, string json)
        {
            var utf8 = System.Text.Encoding.UTF8.GetBytes(json);
            int needed = 4 + 4 + 4 + utf8.Length; // seq + unity_t + json_len + json bytes
            if (dst.Length < needed) throw new ArgumentException("dst too small for event");
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(0,4), seq);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(4,4), unityT);
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(8,4), (uint)utf8.Length);
            utf8.CopyTo(dst.Slice(12, utf8.Length));
            return needed;
        }
    }
}
