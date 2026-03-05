using System;
using System.Buffers.Binary;
using UnityEngine;

namespace VrsLogging
{
    public static class VrsFormats
    {
        public const int HeaderSize = 40;

        public const byte Version = 1;

        public const byte StreamHeadpose = 1;
        public const byte StreamBike = 2;
        public const byte StreamHr = 3;
        public const byte StreamEvents = 4;

        public const int HeadposeRecordSize = 36;
        public const int BikeRecordSize = 20;
        public const int HrRecordSize = 12;

        public static void WriteChunkHeader(Span<byte> dst, byte streamId, ulong sessionId, uint chunkSeq, uint recordCount, uint payloadBytes, ushort flags = 0)
        {
            if (dst.Length < HeaderSize) throw new ArgumentException("dst too small");
            // magic
            dst[0] = (byte)'V'; dst[1] = (byte)'R'; dst[2] = (byte)'S'; dst[3] = (byte)'F';
            dst[4] = Version;
            dst[5] = streamId;
            BinaryPrimitives.WriteUInt16LittleEndian(dst.Slice(6, 2), flags);
            BinaryPrimitives.WriteUInt64LittleEndian(dst.Slice(8, 8), sessionId);
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(16, 4), chunkSeq);
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(20, 4), recordCount);
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(24, 4), payloadBytes);
            // header_crc (zero for now)
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(28, 4), 0u);
            // payload_crc (zero for now)
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(32, 4), 0u);
            // reserved
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(36, 4), 0u);
        }

        public static void WriteHeadposeRecord(Span<byte> dst, uint seq, float unityT, Vector3 pos, Quaternion rot)
        {
            if (dst.Length < HeadposeRecordSize) throw new ArgumentException("dst too small");
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(0,4), seq);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(4,4), unityT);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(8,4), pos.x);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(12,4), pos.y);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(16,4), pos.z);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(20,4), rot.x);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(24,4), rot.y);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(28,4), rot.z);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(32,4), rot.w);
        }

        public static void WriteBikeRecord(Span<byte> dst, uint seq, float unityT, float speed, float steering, byte brakeFront, byte brakeRear)
        {
            if (dst.Length < BikeRecordSize) throw new ArgumentException("dst too small");
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(0,4), seq);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(4,4), unityT);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(8,4), speed);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(12,4), steering);
            dst[16] = brakeFront;
            dst[17] = brakeRear;
            BinaryPrimitives.WriteUInt16LittleEndian(dst.Slice(18,2), 0);
        }

        public static void WriteHrRecord(Span<byte> dst, uint seq, float unityT, float hrBpm)
        {
            if (dst.Length < HrRecordSize) throw new ArgumentException("dst too small");
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(0,4), seq);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(4,4), unityT);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(8,4), hrBpm);
        }

        public static int WriteEventRecord(Span<byte> dst, uint seq, float unityT, string json)
        {
            var utf8 = System.Text.Encoding.UTF8.GetBytes(json);
            int needed = 4 + 4 + 4 + utf8.Length; // seq + unity_t + json_len + json
            if (dst.Length < needed) throw new ArgumentException("dst too small for event");
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(0,4), seq);
            BinaryPrimitives.WriteSingleLittleEndian(dst.Slice(4,4), unityT);
            BinaryPrimitives.WriteUInt32LittleEndian(dst.Slice(8,4), (uint)utf8.Length);
            utf8.CopyTo(dst.Slice(12, utf8.Length));
            return needed;
        }
    }
}
