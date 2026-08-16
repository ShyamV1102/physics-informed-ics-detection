using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace ModbusPoller.Detection
{
    public class CusumResult
    {
        public double Expected { get; init; }
        public double Residual { get; init; }
        public double Z { get; init; }
        public double SHigh { get; init; }
        public double SLow { get; init; }
        public bool Alarm { get; init; }
    }
    public class CusumDetector
    {
        private readonly StreamConfig _cfg;
        private double _sHigh;
        private double _sLow;

        public CusumDetector(StreamConfig cfg) => _cfg = cfg;

        public CusumResult Update(double actualFlow, double valvePos)
        {
            double expected = _cfg.M * valvePos + _cfg.C;
            double residual = actualFlow - expected;
            double z = (residual - _cfg.Mu) / _cfg.Sigma;

            _sHigh = Math.Max(0.0, _sHigh + z - _cfg.Slack);
            _sLow = Math.Min(0.0, _sLow + z + _cfg.Slack);

            bool alarm = false;
            double reportedHigh = _sHigh;
            double reportedLow = _sLow;

            if (_sHigh > _cfg.H)
            {
                alarm = true;
                _sHigh = 0.0;
            }
            if (_sLow < -_cfg.H)
            {
                alarm = true;
                _sLow = 0.0;
            }

            return new CusumResult
            {
                Expected = expected,
                Residual = residual,
                Z = z,
                SHigh = reportedHigh,
                SLow = reportedLow,
                Alarm = alarm
            };
        }
    }
}
