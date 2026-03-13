using System;

namespace VrsLogging
{
    /// <summary>
    /// CRC32 implementation using a pre-computed 256-entry lookup table.
    ///
    /// Polynomial
    /// ----------
    /// Uses the reflected (little-endian) IEEE 802.3 polynomial:
    ///   0xEDB88320  (= bit-reversed representation of 0x04C11DB7)
    /// This is the same polynomial used by zlib/Python's zlib.crc32 and
    /// most common CRC32 implementations, ensuring cross-platform compatibility.
    ///
    /// Lookup table pre-computation
    /// ----------------------------
    /// For each byte value i (0–255), the table entry is the CRC of the
    /// single-byte message [i], computed by processing 8 bits one at a time:
    ///   if LSB == 1:  c = poly XOR (c >> 1)   (mix in polynomial)
    ///   if LSB == 0:  c = c >> 1               (just shift)
    /// The result is the "remainder" for that byte, which is reused in
    /// Compute() to avoid the inner 8-iteration loop on every byte.
    ///
    /// Compute() algorithm
    /// -------------------
    ///   crc  = 0xFFFFFFFF   (initial value — pre-conditioning)
    ///   for each byte b:
    ///     crc = Table[(crc XOR b) AND 0xFF]  XOR  (crc >> 8)
    ///   return ~crc          (final XOR — post-conditioning)
    /// This is equivalent to Python:  zlib.crc32(data) &amp; 0xFFFFFFFF
    /// </summary>
    public static class VrsCrc32
    {
        // 256-entry lookup table, populated once in the static constructor.
        static readonly uint[] Table;

        static VrsCrc32()
        {
            Table = new uint[256];
            const uint poly = 0xEDB88320u;  // reflected IEEE 802.3 polynomial
            for (uint i = 0; i < 256; i++)
            {
                uint c = i;
                // Process 8 bits of the byte value to get its CRC remainder.
                for (int j = 0; j < 8; j++)
                {
                    if ((c & 1) != 0) c = poly ^ (c >> 1);  // LSB set: XOR with polynomial
                    else              c >>= 1;                // LSB clear: just shift
                }
                Table[i] = c;
            }
        }

        /// <summary>
        /// Compute the CRC32 checksum of <paramref name="data"/>.
        /// The result matches <c>zlib.crc32(data) &amp; 0xFFFFFFFF</c> in Python.
        /// </summary>
        public static uint Compute(ReadOnlySpan<byte> data)
        {
            uint crc = 0xFFFFFFFFu;  // pre-conditioning: invert all bits
            for (int i = 0; i < data.Length; i++)
            {
                // The low byte of (crc XOR data[i]) selects the table entry;
                // the high 24 bits of crc shift down by 8.
                crc = Table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
            }
            return ~crc;  // post-conditioning: invert all bits (equivalent to XOR 0xFFFFFFFF)
        }
    }
}
