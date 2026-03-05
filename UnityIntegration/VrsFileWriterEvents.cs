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
    public class VrsFileWriterEvents : IDisposable
    {
        readonly string _path;
        readonly byte _streamId;
        readonly ulong _sessionId;
        readonly ConcurrentQueue<byte[]> _queue = new ConcurrentQueue<byte[]>();
        readonly Thread _thread;
        readonly int _chunkIntervalMs;
        readonly int _flushIntervalMs;
        volatile bool _running;

        FileStream _fs;
        uint _chunkSeq = 0;
        long _totalBytes = 0;
        long _totalEvents = 0;
        int _lastChunkCount = 0;

        public long TotalBytesWritten => _totalBytes;
        public long TotalEventsWritten => _totalEvents;
        public int LastChunkEventCount => _lastChunkCount;

        public VrsFileWriterEvents(string path, byte streamId, ulong sessionId, int chunkIntervalMs = 500, int flushIntervalMs = 1000)
        {
            _path = path;
            _streamId = streamId;
            _sessionId = sessionId;
            _chunkIntervalMs = chunkIntervalMs;
            _flushIntervalMs = flushIntervalMs;

            Directory.CreateDirectory(Path.GetDirectoryName(path));
            _fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete, 4096, FileOptions.SequentialScan);

            _running = true;
            _thread = new Thread(Run) { IsBackground = true, Name = "VrsWriterEvents" };
            _thread.Start();
        }

        public void Enqueue(byte[] eventBytes)
        {
            _queue.Enqueue(eventBytes);
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
                        if (drained >= 4096) break;
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
                    Debug.LogError($"VrsFileWriterEvents exception: {ex}");
                    Thread.Sleep(200);
                }
            }

            var leftover = new List<byte[]>();
            while (_queue.TryDequeue(out var r)) leftover.Add(r);
            if (leftover.Count > 0) WriteChunk(leftover);
            try { _fs.Flush(true); _fs.Dispose(); } catch { }
        }

        void WriteChunk(List<byte[]> records)
        {
            uint recordCount = (uint)records.Count;
            uint payloadBytes = 0;
            foreach (var r in records) payloadBytes += (uint)r.Length;
            int totalSize = VrsFormats.HeaderSize + (int)payloadBytes;
            var buffer = ArrayPool<byte>.Shared.Rent(totalSize);
            try
            {
                var span = new Span<byte>(buffer, 0, totalSize);
                VrsFormats.WriteChunkHeader(span.Slice(0, VrsFormats.HeaderSize), _streamId, _sessionId, _chunkSeq++, recordCount, payloadBytes);
                var payloadSpan = span.Slice(VrsFormats.HeaderSize, (int)payloadBytes);
                int offset = 0;
                foreach (var r in records)
                {
                    r.CopyTo(payloadSpan.Slice(offset, r.Length));
                    offset += r.Length;
                }

                uint payloadCrc = VrsCrc32.Compute(payloadSpan);
                System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(span.Slice(32,4), payloadCrc);

                var headerCopy = new byte[VrsFormats.HeaderSize];
                span.Slice(0, VrsFormats.HeaderSize).CopyTo(headerCopy);
                for (int i = 28; i < 36; i++) headerCopy[i] = 0;
                uint headerCrc = VrsCrc32.Compute(headerCopy);
                System.Buffers.Binary.BinaryPrimitives.WriteUInt32LittleEndian(span.Slice(28,4), headerCrc);

                _fs.Write(span);
                _totalBytes += totalSize;
                _totalEvents += recordCount;
                _lastChunkCount = (int)recordCount;
            }
            finally
            {
                ArrayPool<byte>.Shared.Return(buffer);
                foreach (var r in records) { try { ArrayPool<byte>.Shared.Return(r); } catch { } }
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
