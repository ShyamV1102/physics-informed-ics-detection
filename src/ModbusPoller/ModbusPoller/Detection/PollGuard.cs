using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace ModbusPoller.Detection
{
    public class PollGuard
    {
        private readonly PollGuardConfig _cfg;
        private DateTime? _lastPoll;
        private int _blockedRemaining;

        public PollGuard(PollGuardConfig cfg) => _cfg = cfg;

        public bool ShouldUpdate(DateTime now)
        {
            double? dt = _lastPoll.HasValue
                ? (now - _lastPoll.Value).TotalSeconds
                : null;

            _lastPoll = now;

            if (dt is null)
                return true;

            bool stalled = dt.Value >= _cfg.NominalDtSeconds * _cfg.DtMultiplier;

            if (stalled)
                _blockedRemaining = _cfg.RecoverySamples;

            if (_blockedRemaining > 0)
            {
                _blockedRemaining--;
                return false;
            }

            return true;
        }
    }
}
