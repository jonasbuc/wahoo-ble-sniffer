using System;

namespace VrsLogging
{
    public static class VrsCrc32
    {
        static readonly uint[] Table;

        static VrsCrc32()
        {
            Table = new uint[256];
            const uint poly = 0xEDB88320u;
            for (uint i = 0; i < 256; i++)
            {
                uint c = i;
                for (int j = 0; j < 8; j++)
                {
                    if ((c & 1) != 0) c = poly ^ (c >> 1);
                    else c >>= 1;
                }
                Table[i] = c;
            }
        }

        public static uint Compute(ReadOnlySpan<byte> data)
        {
            uint crc = 0xFFFFFFFFu;
            for (int i = 0; i < data.Length; i++)
            {
                crc = Table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
            }
            return ~crc;
        }
    }
}
