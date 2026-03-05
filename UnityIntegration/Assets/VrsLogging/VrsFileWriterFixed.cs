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
    public class VrsFileWriterFixed : IDisposable
    {
        readonly string _path;
        readonly byte _streamId;
        readonly ulong _sessionId;
        readonly int _recordSize;
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
                    while (_queue.TryDequeue(out var item))
                    {
                        batch.Add(item);
                        drained++;
                        // prevent too large batches; can be tuned
                        if (drained >= 8192) break;
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
            uint recordCount = (uint)records.Count;
            uint payloadBytes = (uint)(recordCount * _recordSize);
            int totalSize = VrsFormats.HeaderSize + (int)payloadBytes;
            var buffer = ArrayPool<byte>.Shared.Rent(totalSize);
            try
            {
                var span = new Span<byte>(buffer, 0, totalSize);
                // write header with zeros for CRCs
                VrsFormats.WriteChunkHeader(span.Slice(0, VrsFormats.HeaderSize), _streamId, _sessionId, _chunkSeq++, recordCount, payloadBytes);
                var payloadSpan = span.Slice(VrsFormats.HeaderSize, (int)payloadBytes);
                int offset = 0;
                for (int i = 0; i < records.Count; i++)
                {
                    var rec = records[i];
                    rec.AsSpan(0, _recordSize).CopyTo(payloadSpan.Slice(offset, _recordSize));
                    offset += _recordSize;
                }

                // compute payload crc
                uint payloadCrc = VrsCrc32.Compute(payloadSpan);
                // write payload crc into header
                BinaryPrimitives.WriteUInt32LittleEndian(span.Slice(32,4), payloadCrc);

                // compute header crc with crc fields zeroed
                // set header crc bytes to zero in a copy
                var headerCopy = new byte[VrsFormats.HeaderSize];
                span.Slice(0, VrsFormats.HeaderSize).CopyTo(headerCopy);
                // zero header_crc (offset 28..31) and payload_crc (32..35)
                for (int i = 28; i < 36; i++) headerCopy[i] = 0;
                uint headerCrc = VrsCrc32.Compute(headerCopy);
                BinaryPrimitives.WriteUInt32LittleEndian(span.Slice(28,4), headerCrc);

                // now write to file
                _fs.Write(span);
                _totalBytes += totalSize;
                _totalRecords += recordCount;
                _lastChunkCount = (int)recordCount;
            }
            finally
            {
                // return buffers and let writer manage lifecycle of record arrays
                ArrayPool<byte>.Shared.Return(buffer);
                foreach (var r in records)
                {
                    // return record arrays to pool if they were rented by producer (they should be)
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
