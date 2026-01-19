using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using InTheHand.Bluetooth;
using Microsoft.Data.Sqlite;

namespace WahooBleLogger;

/// <summary>
/// Logs live BLE data from Wahoo TICKR and KICKR devices to SQLite
/// </summary>
class Program
{
    // GATT Service and Characteristic UUIDs
    private static readonly Guid HEART_RATE_SERVICE = BluetoothUuid.FromShortId(0x180D);
    private static readonly Guid HEART_RATE_MEASUREMENT = BluetoothUuid.FromShortId(0x2A37);
    
    private static readonly Guid CYCLING_POWER_SERVICE = BluetoothUuid.FromShortId(0x1818);
    private static readonly Guid CYCLING_POWER_MEASUREMENT = BluetoothUuid.FromShortId(0x2A63);
    
    private static readonly Guid FITNESS_MACHINE_SERVICE = BluetoothUuid.FromShortId(0x1826);
    private static readonly Guid INDOOR_BIKE_DATA = BluetoothUuid.FromShortId(0x2AD2);

    private const string DB_NAME = "training.db";
    private static bool _running = true;

    static async Task Main(string[] args)
    {
        Console.WriteLine("Wahoo BLE Logger - C# Edition");
        Console.WriteLine("==============================\n");

        // Parse command line arguments
        string? tickrAddress = GetArgument(args, "--tickr-address");
        string? kickrAddress = GetArgument(args, "--kickr-address");
        bool showAll = args.Contains("--show-all-devices");
        bool debug = args.Contains("--debug");

        // Initialize database
        InitializeDatabase();

        // Setup cancellation
        Console.CancelKeyPress += (s, e) =>
        {
            e.Cancel = true;
            _running = false;
            Console.WriteLine("\nShutting down...");
        };

        try
        {
            // Find devices
            BluetoothDevice? tickr = null;
            BluetoothDevice? kickr = null;

            if (!string.IsNullOrEmpty(tickrAddress))
            {
                Console.WriteLine($"Using specified TICKR address: {tickrAddress}");
                tickr = await FindDeviceByAddress(tickrAddress);
            }
            else
            {
                tickr = await ScanForDevice("TICKR", showAll);
            }

            if (!string.IsNullOrEmpty(kickrAddress))
            {
                Console.WriteLine($"Using specified KICKR address: {kickrAddress}");
                kickr = await FindDeviceByAddress(kickrAddress);
            }
            else
            {
                kickr = await ScanForDevice("KICKR", showAll);
            }

            if (tickr == null && kickr == null)
            {
                Console.WriteLine("ERROR: No Wahoo devices found. Exiting.");
                return;
            }

            Console.WriteLine("\nStarting data collection. Press Ctrl+C to stop.\n");

            // Start device handlers
            var tasks = new List<Task>();
            
            if (tickr != null)
            {
                tasks.Add(HandleHeartRateDevice(tickr, debug));
            }

            if (kickr != null)
            {
                tasks.Add(HandleTrainerDevice(kickr, debug));
            }

            await Task.WhenAll(tasks);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"ERROR: {ex.Message}");
            if (debug)
            {
                Console.WriteLine($"Stack trace: {ex.StackTrace}");
            }
        }
    }

    static void InitializeDatabase()
    {
        using var connection = new SqliteConnection($"Data Source={DB_NAME}");
        connection.Open();

        // Enable WAL mode
        var walCmd = connection.CreateCommand();
        walCmd.CommandText = "PRAGMA journal_mode=WAL";
        walCmd.ExecuteNonQuery();

        // Create table
        var createCmd = connection.CreateCommand();
        createCmd.CommandText = @"
            CREATE TABLE IF NOT EXISTS metrics (
                ts REAL NOT NULL,
                hr_bpm INTEGER,
                rr_ms INTEGER,
                power_w INTEGER,
                cadence_rpm REAL,
                speed_kph REAL
            )";
        createCmd.ExecuteNonQuery();

        // Create index
        var indexCmd = connection.CreateCommand();
        indexCmd.CommandText = "CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts)";
        indexCmd.ExecuteNonQuery();

        Console.WriteLine($"Database initialized: {DB_NAME}\n");
    }

    static async Task<BluetoothDevice?> ScanForDevice(string nameContains, bool showAll)
    {
        Console.WriteLine($"Scanning for device containing '{nameContains}'...");

        var devices = new List<BluetoothDevice>();
        
        try
        {
            var scanResult = await Bluetooth.ScanForDevicesAsync();
            
            foreach (var device in scanResult)
            {
                devices.Add(device);
                
                if (showAll && !string.IsNullOrEmpty(device.Name))
                {
                    Console.WriteLine($"  - {device.Name} ({device.Id})");
                }

                if (!string.IsNullOrEmpty(device.Name) && 
                    device.Name.Contains(nameContains, StringComparison.OrdinalIgnoreCase))
                {
                    Console.WriteLine($"Found {device.Name} at {device.Id}");
                    return device;
                }
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Scan error: {ex.Message}");
        }

        Console.WriteLine($"WARNING: No device found containing '{nameContains}'");
        return null;
    }

    static async Task<BluetoothDevice?> FindDeviceByAddress(string address)
    {
        // This is platform-specific and may not work on all systems
        // InTheHand.BluetoothLE doesn't have direct address lookup
        // So we scan and try to match
        var scanResult = await Bluetooth.ScanForDevicesAsync();
        foreach (var device in scanResult)
        {
            if (device.Id.ToString().Contains(address, StringComparison.OrdinalIgnoreCase))
            {
                return device;
            }
        }
        return null;
    }

    static async Task HandleHeartRateDevice(BluetoothDevice device, bool debug)
    {
        while (_running)
        {
            try
            {
                Console.WriteLine($"Connecting to {device.Name} ({device.Id})...");
                
                var gatt = device.Gatt;
                await gatt.ConnectAsync();

                if (!gatt.IsConnected)
                {
                    Console.WriteLine($"Failed to connect to {device.Name}");
                    await Task.Delay(5000);
                    continue;
                }

                Console.WriteLine($"Connected to {device.Name}");

                // Get Heart Rate Service
                var service = await gatt.GetPrimaryServiceAsync(HEART_RATE_SERVICE);
                if (service == null)
                {
                    Console.WriteLine("Heart Rate Service not found!");
                    await Task.Delay(5000);
                    continue;
                }

                // Get Heart Rate Measurement characteristic
                var characteristic = await service.GetCharacteristicAsync(HEART_RATE_MEASUREMENT);
                if (characteristic == null)
                {
                    Console.WriteLine("Heart Rate Measurement characteristic not found!");
                    await Task.Delay(5000);
                    continue;
                }

                Console.WriteLine($"Subscribed to notifications from {device.Name}");

                // Subscribe to notifications
                characteristic.CharacteristicValueChanged += (sender, args) =>
                {
                    var data = args.Value;
                    if (data == null) return;
                    
                    var parsed = ParseHeartRate(data);
                    
                    if (parsed.HasValue)
                    {
                        var (bpm, rrMs) = parsed.Value;
                        LogMetric(hrBpm: bpm, rrMs: rrMs);
                        
                        var msg = $"[{device.Name}] HR: {bpm} bpm";
                        if (rrMs.HasValue)
                            msg += $", RR: {rrMs} ms";
                        Console.WriteLine(msg);
                    }
                };

                await characteristic.StartNotificationsAsync();

                // Keep connection alive
                while (_running && gatt.IsConnected)
                {
                    await Task.Delay(1000);
                }

                await characteristic.StopNotificationsAsync();
                gatt.Disconnect();

                if (_running)
                {
                    Console.WriteLine($"{device.Name} disconnected. Reconnecting in 5s...");
                    await Task.Delay(5000);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Error with {device.Name}: {ex.Message}");
                if (_running)
                {
                    await Task.Delay(5000);
                }
            }
        }
    }

    static async Task HandleTrainerDevice(BluetoothDevice device, bool debug)
    {
        while (_running)
        {
            try
            {
                Console.WriteLine($"Connecting to {device.Name} ({device.Id})...");
                
                var gatt = device.Gatt;
                await gatt.ConnectAsync();

                if (!gatt.IsConnected)
                {
                    Console.WriteLine($"Failed to connect to {device.Name}");
                    await Task.Delay(5000);
                    continue;
                }

                Console.WriteLine($"Connected to {device.Name}");

                if (debug)
                {
                    Console.WriteLine($"Available services for {device.Name}:");
                    var services = await gatt.GetPrimaryServicesAsync();
                    foreach (var svc in services)
                    {
                        Console.WriteLine($"  Service: {svc.Uuid}");
                    }
                }

                // Try Cycling Power Service first
                GattCharacteristic? characteristic = null;
                var cpService = await gatt.GetPrimaryServiceAsync(CYCLING_POWER_SERVICE);
                
                if (cpService != null)
                {
                    characteristic = await cpService.GetCharacteristicAsync(CYCLING_POWER_MEASUREMENT);
                    if (characteristic != null)
                    {
                        Console.WriteLine("Using Cycling Power Service");
                    }
                }

                // Fallback to FTMS if no Cycling Power
                if (characteristic == null)
                {
                    var ftmsService = await gatt.GetPrimaryServiceAsync(FITNESS_MACHINE_SERVICE);
                    if (ftmsService != null)
                    {
                        characteristic = await ftmsService.GetCharacteristicAsync(INDOOR_BIKE_DATA);
                        if (characteristic != null)
                        {
                            Console.WriteLine("Using Fitness Machine Service (FTMS)");
                        }
                    }
                }

                if (characteristic == null)
                {
                    Console.WriteLine($"No supported characteristics found on {device.Name}");
                    await Task.Delay(5000);
                    continue;
                }

                Console.WriteLine($"Subscribed to notifications from {device.Name}");

                // Subscribe to notifications
                characteristic.CharacteristicValueChanged += (sender, args) =>
                {
                    var data = args.Value;
                    if (data == null) return;
                    
                    // Determine which parser to use based on service
                    var parsed = characteristic.Service.Uuid == CYCLING_POWER_SERVICE
                        ? ParseCyclingPower(data)
                        : ParseIndoorBikeData(data, debug);
                    
                    if (parsed.HasValue)
                    {
                        var (power, cadence, speed) = parsed.Value;
                        LogMetric(powerW: power, cadenceRpm: cadence, speedKph: speed);
                        
                        var parts = new List<string>();
                        if (power.HasValue) parts.Add($"Power: {power} W");
                        if (cadence.HasValue) parts.Add($"Cadence: {cadence:F1} rpm");
                        if (speed.HasValue) parts.Add($"Speed: {speed:F1} km/h");
                        
                        if (parts.Any())
                            Console.WriteLine($"[{device.Name}] {string.Join(", ", parts)}");
                    }
                };

                await characteristic.StartNotificationsAsync();

                // Keep connection alive
                while (_running && gatt.IsConnected)
                {
                    await Task.Delay(1000);
                }

                await characteristic.StopNotificationsAsync();
                gatt.Disconnect();

                if (_running)
                {
                    Console.WriteLine($"{device.Name} disconnected. Reconnecting in 5s...");
                    await Task.Delay(5000);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine($"Error with {device.Name}: {ex.Message}");
                if (_running)
                {
                    await Task.Delay(5000);
                }
            }
        }
    }

    static (int bpm, int? rrMs)? ParseHeartRate(byte[] data)
    {
        if (data.Length < 2)
            return null;

        byte flags = data[0];
        bool is16Bit = (flags & 0x01) != 0;
        
        int bpm = is16Bit ? BitConverter.ToUInt16(data, 1) : data[1];
        int offset = is16Bit ? 3 : 2;

        int? rrMs = null;
        bool hasRR = (flags & 0x10) != 0;
        
        if (hasRR && data.Length >= offset + 2)
        {
            ushort rr1024 = BitConverter.ToUInt16(data, offset);
            rrMs = (int)((rr1024 / 1024.0) * 1000);
        }

        return (bpm, rrMs);
    }

    static (int? power, double? cadence, double? speed)? ParseCyclingPower(byte[] data)
    {
        if (data.Length < 4)
            return null;

        try
        {
            ushort flags = BitConverter.ToUInt16(data, 0);
            int offset = 2;

            // Instantaneous Power (always present, sint16)
            short power = BitConverter.ToInt16(data, offset);
            offset += 2;

            // We could parse more fields based on flags, but power is the main one
            return (power, null, null);
        }
        catch
        {
            return null;
        }
    }

    static (int? power, double? cadence, double? speed)? ParseIndoorBikeData(byte[] data, bool debug)
    {
        if (data.Length < 2)
            return null;

        try
        {
            ushort flags = BitConverter.ToUInt16(data, 0);
            int offset = 2;

            if (debug)
                Console.WriteLine($"FTMS flags: 0x{flags:X4}, raw: {BitConverter.ToString(data)}");

            double? speed = null;
            double? cadence = null;
            int? power = null;

            bool hasAvgSpeed = (flags & 0x02) != 0;
            bool hasCadence = (flags & 0x04) != 0;
            bool hasAvgCadence = (flags & 0x08) != 0;
            bool hasDistance = (flags & 0x10) != 0;
            bool hasResistance = (flags & 0x20) != 0;
            bool hasPower = (flags & 0x40) != 0;

            // Instantaneous Speed (uint16, 0.01 km/h)
            if (data.Length >= offset + 2)
            {
                speed = BitConverter.ToUInt16(data, offset) * 0.01;
                offset += 2;
            }

            if (hasAvgSpeed && data.Length >= offset + 2)
                offset += 2;

            if (hasCadence && data.Length >= offset + 2)
            {
                cadence = BitConverter.ToUInt16(data, offset) * 0.5;
                offset += 2;
            }

            if (hasAvgCadence && data.Length >= offset + 2)
                offset += 2;

            if (hasDistance && data.Length >= offset + 3)
                offset += 3;

            if (hasResistance && data.Length >= offset + 2)
                offset += 2;

            if (hasPower && data.Length >= offset + 2)
            {
                power = BitConverter.ToInt16(data, offset);
                offset += 2;
            }

            return (power, cadence, speed);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"FTMS parsing error: {ex.Message}");
            return null;
        }
    }

    static void LogMetric(int? hrBpm = null, int? rrMs = null, int? powerW = null, 
                         double? cadenceRpm = null, double? speedKph = null)
    {
        try
        {
            using var connection = new SqliteConnection($"Data Source={DB_NAME}");
            connection.Open();

            var cmd = connection.CreateCommand();
            cmd.CommandText = @"
                INSERT INTO metrics (ts, hr_bpm, rr_ms, power_w, cadence_rpm, speed_kph)
                VALUES (@ts, @hr_bpm, @rr_ms, @power_w, @cadence_rpm, @speed_kph)
            ";
            
            cmd.Parameters.AddWithValue("@ts", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0);
            cmd.Parameters.AddWithValue("@hr_bpm", hrBpm.HasValue ? hrBpm.Value : DBNull.Value);
            cmd.Parameters.AddWithValue("@rr_ms", rrMs.HasValue ? rrMs.Value : DBNull.Value);
            cmd.Parameters.AddWithValue("@power_w", powerW.HasValue ? powerW.Value : DBNull.Value);
            cmd.Parameters.AddWithValue("@cadence_rpm", cadenceRpm.HasValue ? cadenceRpm.Value : DBNull.Value);
            cmd.Parameters.AddWithValue("@speed_kph", speedKph.HasValue ? speedKph.Value : DBNull.Value);
            
            cmd.ExecuteNonQuery();
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Database error: {ex.Message}");
        }
    }

    static string? GetArgument(string[] args, string name)
    {
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == name)
                return args[i + 1];
        }
        return null;
    }
}
