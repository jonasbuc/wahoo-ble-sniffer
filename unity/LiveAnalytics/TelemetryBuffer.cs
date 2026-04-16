using System;
using System.Collections.Generic;
using UnityEngine;

namespace LiveAnalytics
{
    /// <summary>
    /// Thread-safe fixed-capacity ring buffer for <see cref="TelemetryRecord"/> objects.
    /// Records are accumulated in <c>Update</c> and flushed by the network sender.
    /// When the buffer is full the oldest record is silently dropped (back-pressure).
    /// </summary>
    public class TelemetryBuffer
    {
        private readonly TelemetryRecord[] _ring;
        private int _head; // next write index
        private int _tail; // next read index
        private int _count;
        private readonly object _lock = new object();

        /// <summary>Maximum number of records this buffer can hold.</summary>
        public int Capacity => _ring.Length;

        /// <summary>Current number of unread records.</summary>
        public int Count { get { lock (_lock) return _count; } }

        /// <summary>Total records dropped since creation (buffer was full).</summary>
        public long DroppedCount { get; private set; }

        public TelemetryBuffer(int capacity = 512)
        {
            if (capacity <= 0) throw new ArgumentOutOfRangeException(nameof(capacity));
            _ring = new TelemetryRecord[capacity];
        }

        /// <summary>Enqueue a single record.  If full, the oldest record is dropped.</summary>
        public void Enqueue(TelemetryRecord record)
        {
            lock (_lock)
            {
                if (_count == _ring.Length)
                {
                    // Overwrite oldest – advance tail
                    _tail = (_tail + 1) % _ring.Length;
                    _count--;
                    DroppedCount++;
                }
                _ring[_head] = record;
                _head = (_head + 1) % _ring.Length;
                _count++;
            }
        }

        /// <summary>
        /// Drain up to <paramref name="maxCount"/> records into the provided list.
        /// Returns the number of records actually dequeued.
        /// </summary>
        public int DequeueBatch(List<TelemetryRecord> dest, int maxCount)
        {
            lock (_lock)
            {
                int n = Math.Min(maxCount, _count);
                for (int i = 0; i < n; i++)
                {
                    dest.Add(_ring[_tail]);
                    _ring[_tail] = null; // allow GC
                    _tail = (_tail + 1) % _ring.Length;
                }
                _count -= n;
                return n;
            }
        }

        /// <summary>Clear all buffered records.</summary>
        public void Clear()
        {
            lock (_lock)
            {
                Array.Clear(_ring, 0, _ring.Length);
                _head = _tail = _count = 0;
            }
        }
    }
}
