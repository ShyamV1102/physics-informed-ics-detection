using System.Threading.Channels;
using Npgsql;

namespace ModbusPoller.Db
{
    public record ProcessSample(
        string RunId,
        DateTime Ts,
        string Stream,
        int ValveRaw,
        double Valve,
        int FlowRaw,
        double Flow,
        double FlowExpected,
        double Residual,
        double ResidualSigma,
        double CusumHigh,
        double CusumLow,
        double ThresholdH,
        double? Setpoint,
        double? DtSeconds,
        bool PollExcluded,
        bool Alarm);

    public class LiveWriter : IAsyncDisposable
    {
        private readonly string _conn;
        private readonly Channel<ProcessSample> _channel;
        private readonly CancellationTokenSource _cts = new();
        private Task? _pump;
        private long _dropped;

        public LiveWriter(string connectionString, int capacity = 10000)
        {
            _conn = connectionString;
            _channel = Channel.CreateBounded<ProcessSample>(
                new BoundedChannelOptions(capacity)
                {
                    FullMode = BoundedChannelFullMode.DropWrite,
                    SingleReader = true,
                    SingleWriter = true
                });
        }

        /// <summary>Creates the runs row. Must be called before any Enqueue (foreign key).</summary>
        public async Task StartRunAsync(string runId, string label, string? attackType,
                                        string? attackVariant, string? notes)
        {
            await using var c = new NpgsqlConnection(_conn);
            await c.OpenAsync();

            await using (var del = new NpgsqlCommand(
                "DELETE FROM alerts WHERE run_id=@r; DELETE FROM process WHERE run_id=@r; " +
                "DELETE FROM runs WHERE run_id=@r;", c))
            {
                del.Parameters.AddWithValue("r", runId);
                await del.ExecuteNonQueryAsync();
            }

            await using (var ins = new NpgsqlCommand(
                "INSERT INTO runs (run_id, label, attack_type, attack_variant, vantage, notes) " +
                "VALUES (@r, @l, @t, @v, 'poller', @n)", c))
            {
                ins.Parameters.AddWithValue("r", runId);
                ins.Parameters.AddWithValue("l", label);
                ins.Parameters.AddWithValue("t", (object?)attackType ?? DBNull.Value);
                ins.Parameters.AddWithValue("v", (object?)attackVariant ?? DBNull.Value);
                ins.Parameters.AddWithValue("n", (object?)notes ?? DBNull.Value);
                await ins.ExecuteNonQueryAsync();
            }

            _pump = Task.Run(() => PumpAsync(_cts.Token));
            Console.WriteLine($"live writer started for run '{runId}'");
        }

        /// <summary>Marks the attack onset on the runs row. Call from a keypress or a signal file.</summary>
        public async Task MarkOnsetAsync(string runId, DateTime onsetUtc, string attackType)
        {
            await using var c = new NpgsqlConnection(_conn);
            await c.OpenAsync();
            await using var cmd = new NpgsqlCommand(
                "UPDATE runs SET attack_onset_epoch=@e, attack_type=@t WHERE run_id=@r", c);
            cmd.Parameters.AddWithValue("e", ToEpoch(onsetUtc));
            cmd.Parameters.AddWithValue("t", attackType);
            cmd.Parameters.AddWithValue("r", runId);
            await cmd.ExecuteNonQueryAsync();
        }

        public void Enqueue(ProcessSample s)
        {
            if (!_channel.Writer.TryWrite(s)) Interlocked.Increment(ref _dropped);
        }

        private static double ToEpoch(DateTime utc) =>
            (utc - DateTime.UnixEpoch).TotalSeconds;

        private async Task PumpAsync(CancellationToken ct)
        {
            var batch = new List<ProcessSample>(256);
            try
            {
                while (await _channel.Reader.WaitToReadAsync(ct))
                {
                    batch.Clear();
                    while (batch.Count < 256 && _channel.Reader.TryRead(out var s))
                        batch.Add(s);
                    if (batch.Count == 0) continue;

                    try { await FlushAsync(batch, ct); }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"live writer flush failed, dropping {batch.Count} rows: {ex.Message}");
                    }

                    await Task.Delay(250, ct);
                }
            }
            catch (OperationCanceledException) { }
        }

        private async Task FlushAsync(List<ProcessSample> batch, CancellationToken ct)
        {
            await using var c = new NpgsqlConnection(_conn);
            await c.OpenAsync(ct);

            await using (var w = await c.BeginBinaryImportAsync(
                "COPY process (run_id, ts_epoch, stream, valve_raw, valve, flow_raw, flow, " +
                "flow_expected, residual, residual_sigma, cusum_high, cusum_low, threshold_h, " +
                "setpoint, dt_seconds, poll_excluded, alarm) FROM STDIN (FORMAT BINARY)", ct))
            {
                foreach (var s in batch)
                {
                    await w.StartRowAsync(ct);
                    await w.WriteAsync(s.RunId, ct);
                    await w.WriteAsync(ToEpoch(s.Ts), ct);
                    await w.WriteAsync(s.Stream, ct);
                    await w.WriteAsync(s.ValveRaw, ct);
                    await w.WriteAsync(s.Valve, ct);
                    await w.WriteAsync(s.FlowRaw, ct);
                    await w.WriteAsync(s.Flow, ct);
                    await w.WriteAsync(s.FlowExpected, ct);
                    await w.WriteAsync(s.Residual, ct);
                    await w.WriteAsync(s.ResidualSigma, ct);
                    await w.WriteAsync(s.CusumHigh, ct);
                    await w.WriteAsync(s.CusumLow, ct);
                    await w.WriteAsync(s.ThresholdH, ct);
                    if (s.Setpoint.HasValue) await w.WriteAsync(s.Setpoint.Value, ct);
                    else await w.WriteNullAsync(ct);
                    if (s.DtSeconds.HasValue) await w.WriteAsync(s.DtSeconds.Value, ct);
                    else await w.WriteNullAsync(ct);
                    await w.WriteAsync(s.PollExcluded, ct);
                    await w.WriteAsync(s.Alarm, ct);
                }
                await w.CompleteAsync(ct);
            }

            var alarms = batch.Where(s => s.Alarm).ToList();
            if (alarms.Count == 0) return;

            await using var w2 = await c.BeginBinaryImportAsync(
                "COPY alerts (run_id, ts_epoch, source, rule, stream, severity, value, threshold, detail) " +
                "FROM STDIN (FORMAT BINARY)", ct);
            foreach (var s in alarms)
            {
                bool high = s.CusumHigh >= s.ThresholdH;
                double val = high ? s.CusumHigh : Math.Abs(s.CusumLow);
                string rule = high ? "cusum_high" : "cusum_low";
                string dir = high ? "above" : "below";
                await w2.StartRowAsync(ct);
                await w2.WriteAsync(s.RunId, ct);
                await w2.WriteAsync(ToEpoch(s.Ts), ct);
                await w2.WriteAsync("physics", ct);
                await w2.WriteAsync(rule, ct);
                await w2.WriteAsync(s.Stream, ct);
                await w2.WriteAsync("alarm", ct);
                await w2.WriteAsync(val, ct);
                await w2.WriteAsync(s.ThresholdH, ct);
                await w2.WriteAsync(
                    $"{s.Stream} flow {dir} expected: CUSUM {val:F2} over threshold {s.ThresholdH:F1}, " +
                    $"residual {s.ResidualSigma:F2} sigma", ct);
            }
            await w2.CompleteAsync(ct);
        }

        public async ValueTask DisposeAsync()
        {
            _channel.Writer.TryComplete();
            if (_pump != null)
            {
                try { await _pump.WaitAsync(TimeSpan.FromSeconds(10)); } catch { }
            }
            _cts.Cancel();
            if (_dropped > 0)
                Console.WriteLine($"live writer dropped {_dropped} rows (channel full)");
        }
    }
}