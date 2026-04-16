using System;
using System.Buffers;
using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using UnityEngine;

namespace VrsLogging
{
    /// <summary>
    /// Background-thread VRSF file writer for fixed-size record streams
    /// (headpose = 36 B, bike = 20 B, hr = 12 B).
    ///
    /// Architecture
    /// ------------
    /// Producers (Unity Update/FixedUpdate callbacks) call <see cref="Enqueue"/>
    /// with a pre-filled record byte array.  A dedicated background thread
    /// drains the <see cref="ConcurrentQueue{T}"/> every <c>chunkIntervalMs</c>
    /// milliseconds, bundles all dequeued records into a single VRSF chunk,
    /// computes CRCs, and writes the chunk atomically to disk.
    ///
    /// CRC order (important!)
    /// ----------------------
    /// 1. Fill the header with PayloadCRC32 = 0 and HeaderCRC32 = 0.
    /// 2. Copy all records into the payload region.
    /// 3. Compute PayloadCRC32 over the payload bytes → write into header offset 32.
    /// 4. Copy the 40-byte header, zero bytes 28-35, compute HeaderCRC32 → write into header offset 28.
    /// The Python collector verifies in this same order.
    ///
    /// Memory management
    /// -----------------
    /// The large per-chunk buffer is rented from <see cref="ArrayPool{T}.Shared"/>
    /// and returned in the finally block so no large allocations are kept alive.
    /// Individual record arrays enqueued by producers are also returned to the
    /// pool after being copied into the chunk buffer.
    /// </summary>
    public class VrsFileWriterFixed : IDisposable
    {
        readonly string _path;
        readonly byte _streamId;
        readonly ulong _sessionId;
        readonly int _recordSize;
        // Thread-safe queue: producers call Enqueue() from any thread;
        // the background Run() thread drains it.
        readonly ConcurrentQueue<byte[]> _queue = new ConcurrentQueue<byte[]>();
        readonly Thread _thread;
        readonly int _chunkIntervalMs;
        readonly int _flushIntervalMs;
        volatile bool _running;

        FileStream _fs;
        uint _chunkSeq = 0;
        long _totalBytes = 0;
        long _totalRecords = 0;
        int _lastChunkCount = 0;

        public long TotalBytesWritten => _totalBytes;
        public long TotalRecordsWritten => _totalRecords;
        public int LastChunkRecordCount => _lastChunkCount;

        public VrsFileWriterFixed(string path, byte streamId, ulong sessionId, int recordSize, int chunkIntervalMs = 200, int flushIntervalMs = 1000)
        {
            _path = path;
            _streamId = streamId;
            _sessionId = sessionId;
            _recordSize = recordSize;
            _chunkIntervalMs = chunkIntervalMs;
            _flushIntervalMs = flushIntervalMs;

            Directory.CreateDirectory(Path.GetDirectoryName(path));
            _fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete, 4096, FileOptions.SequentialScan);

            _running = true;
            _thread = new Thread(Run) { IsBackground = true, Name = "VrsWriterFixed" };
            _thread.Start();
        }

        public void Enqueue(byte[] record)
        {
            _queue.Enqueue(record);
        }

        void Run()
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var lastFlush = sw.ElapsedMilliseconds;
            while (_running)
            {
                try
                {
                    var batch = new List<byte[]>();
                    int drained = 0;
                    // Drain up to 8192 records at once to bound chunk size.
                    while (_queue.TryDequeue(out var item))
                    {
                        batch.Add(item);
                        drained++;
                        if (drained >= 8192) break;  // safety cap — tunable
                    }

                    if (batch.Count == 0)
                    {
                        Thread.Sleep(Math.Min(50, _chunkIntervalMs));
                    }
                    else
                    {
                        WriteChunk(batch);
                    }

                    if (sw.ElapsedMilliseconds - lastFlush > _flushIntervalMs)
                    {
                        try { _fs.Flush(true); } catch { }
                        lastFlush = sw.ElapsedMilliseconds;
                    }
                }
                catch (Exception ex)
                {
                    Debug.LogError($"VrsFileWriterFixed exception: {ex}");
                    Thread.Sleep(200);
                }
            }

            // flush remaining
            var leftover = new List<byte[]>();
            while (_queue.TryDequeue(out var r)) leftover.Add(r);
            if (leftover.Count > 0) WriteChunk(leftover);
            try { _fs.Flush(true); _fs.Dispose(); } catch { }
        }

        void WriteChunk(List<byte[]> records)
        {
            uint recordCount  = (uint)records.Count;
            uint payloadBytes = (uint)(recordCount * _recordSize);
            int  totalSize    = VrsFormats.HeaderSize + (int)payloadBytes;

            // Rent a single contiguous buffer large enough for header + payload.
            // Using ArrayPool avoids a heap allocation on every chunk write.
            var buffer = ArrayPool<byte>.Shared.Rent(totalSize);
            try
            {
                var span = new Span<byte>(buffer, 0, totalSize);

                // Step 1: write header with both CRC fields = 0 (placeholders).
                VrsFormats.WriteChunkHeader(span.Slice(0, VrsFormats.HeaderSize), _streamId, _sessionId, _chunkSeq++, recordCount, payloadBytes);

                // Step 2: copy all fixed-size records sequentially into the payload region.
                var payloadSpan = span.Slice(VrsFormats.HeaderSize, (int)payloadBytes);
                int offset = 0;
                for (int i = 0; i < records.Count; i++)
                {
                    var rec = records[i];
                    rec.AsSpan(0, _recordSize).CopyTo(payloadSpan.Slice(offset, _recordSize));
                    offset += _recordSize;
                }

                // Step 3: compute and write PayloadCRC32 (offset 32 in header).
                uint payloadCrc = VrsCrc32.Compute(payloadSpan);
                BinaryPrimitives.WriteUInt32LittleEndian(span.Slice(32,4), payloadCrc);

                // Step 4: compute HeaderCRC32 over the header with BOTH CRC fields zeroed.
                // Must zero bytes 28-35 (HeaderCRC32 + PayloadCRC32) in a copy before
                // computing — zeroing in a copy avoids disturbing the actual PayloadCRC32
                // we just wrote at offset 32.
                var headerCopy = new byte[VrsFormats.HeaderSize];
                span.Slice(0, VrsFormats.HeaderSize).CopyTo(headerCopy);
                for (int i = 28; i < 36; i++) headerCopy[i] = 0;  // zero both CRC fields
                uint headerCrc = VrsCrc32.Compute(headerCopy);
                BinaryPrimitives.WriteUInt32LittleEndian(span.Slice(28,4), headerCrc);

                // Step 5: write the complete chunk (header + payload) to the file stream.
                _fs.Write(span);
                _totalBytes   += totalSize;
                _totalRecords += recordCount;
                _lastChunkCount = (int)recordCount;
            }
            finally
            {
                // Return the rented chunk buffer to the pool immediately.
                ArrayPool<byte>.Shared.Return(buffer);
                // Return each individual record array that was rented by the producer.
                foreach (var r in records)
                {
                    try { ArrayPool<byte>.Shared.Return(r); } catch { }
                }
            }
        }

        public void Dispose()
        {
            _running = false;
            try { _thread.Join(2000); } catch { }
            try { _fs?.Dispose(); } catch { }
        }
    }
}
