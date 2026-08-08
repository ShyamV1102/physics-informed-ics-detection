using ModbusPoller.Db;
using ModbusPoller.Detection;
using NModbus;
using System.IO.Pipelines;
using System.Net.Sockets;

namespace ModbusPoller
{
    public class Helper
    {
        private readonly string _ip;
        private readonly int _port = 502;

        private readonly PollGuard _pollGuard;
        private readonly CusumDetector _productDetector;
        private readonly CusumDetector _purgeDetector;
        private readonly string _runId;
        private readonly LiveWriter? _live;
        private readonly double _productH;
        private readonly double _purgeH;
        private DateTime? _lastTs;

        public Helper(PollGuard pollGuard, CusumDetector productDetector, CusumDetector purgeDetector, string runId, string target, LiveWriter? live, double productH, double purgeH)
        {
            _pollGuard = pollGuard;
            _productDetector = productDetector;
            _purgeDetector = purgeDetector;
            _runId = runId;
            _ip = target;
            _live = live;
            _productH = productH;
            _purgeH = purgeH;
        }
        public async Task Connect()
        {
            ModbusFactory factory = new ModbusFactory();
            try
            {
                using (TcpClient tcpClient = new TcpClient(_ip, _port))
                {
                    using (IModbusMaster master = factory.CreateMaster(tcpClient))
                    {
                        Directory.CreateDirectory("/app/data");
                        string path = $"/app/data/raw_{_runId}.csv";
                        string detectionPath = $"/app/data/detection_{_runId}.csv";

                        if (File.Exists(path) || File.Exists(detectionPath))
                        {
                            Console.WriteLine($"ERROR: files for run '{_runId}' already exist. Choose a new run id or delete the old files.");
                            return;
                        }

                        File.AppendAllText(path, "timestamp, F1ValvePosReg, F1FlowReg, F2ValvePosReg, F2FlowReg, PurgeValvePosReg, PurgeFlowReg, ProductValvePosReg, ProductFlowReg, PressureReg, LevelReg, AInPurgeReg, BInPurgeReg, CInPurgeReg, ProductFlowSpReg, ASetpointReg, PressureSpReg, OverrideSpReg, LevelSpReg, PurgeManualSp, ProductManualSp, CoilReg\n");
                        File.AppendAllText(detectionPath,
                            "timestamp, productExpected, productResidual, productZ, productSHigh, productSLow, productAlarm, purgeExpected, purgeResidual, purgeZ, purgeSHigh, purgeSLow, purgeAlarm\n");

                        for (int loopCount = 0; loopCount < 3600; loopCount++)
                        {
                            if (tcpClient.Connected)
                            {

                                var inputRegisterValues = await master.ReadInputRegistersAsync(Convert.ToByte(0), ModbusRegisterMap.F1ValvePosReg, 13);
                                var holdingRegisterValues = await master.ReadHoldingRegistersAsync(Convert.ToByte(0), ModbusRegisterMap.ProductFlowSpReg, 5);
                                var manualSpRegisterValues = await master.ReadHoldingRegistersAsync(Convert.ToByte(0), ModbusRegisterMap.PurgeManualSp, 2);
                                var coilRegisterValues = await master.ReadCoilsAsync(Convert.ToByte(0), ModbusRegisterMap.ManualRegister, 1);
                                Console.WriteLine($"Progress = {(double)loopCount/3600 * 100:F1}");
                                DateTime now = DateTime.UtcNow;
                                string timestamp = now.ToString("O");
                                string row = $"{timestamp}, {inputRegisterValues[0]}, {inputRegisterValues[1]}, {inputRegisterValues[2]}, {inputRegisterValues[3]}, {inputRegisterValues[4]}, {inputRegisterValues[5]}, {inputRegisterValues[6]}, {inputRegisterValues[7]}, {inputRegisterValues[8]}, {inputRegisterValues[9]}, {inputRegisterValues[10]}, {inputRegisterValues[11]}, {inputRegisterValues[12]}, {holdingRegisterValues[0]}, {holdingRegisterValues[1]}, {holdingRegisterValues[2]}, {holdingRegisterValues[3]}, {holdingRegisterValues[4]}, {manualSpRegisterValues[0]}, {manualSpRegisterValues[1]}, {(coilRegisterValues[0] ? 1 : 0)}\n";



                                if (_pollGuard.ShouldUpdate(now))
                                {
                                    double productFlow = inputRegisterValues[7] * 500.0 / 65535.0;
                                    double productValve = inputRegisterValues[6] * 100.0 / 65535.0;
                                    double purgeFlow = inputRegisterValues[5] * 500.0 / 65535.0;
                                    double purgeValve = inputRegisterValues[4] * 100.0 / 65535.0;

                                    var productResult = _productDetector.Update(productFlow, productValve);
                                    var purgeResult = _purgeDetector.Update(purgeFlow, purgeValve);
                                    string detectionRow = FormattableString.Invariant($"{timestamp}, {productResult.Expected}, {productResult.Residual}, {productResult.Z}, {productResult.SHigh}, {productResult.SLow}, {(productResult.Alarm ? 1 : 0)}, {purgeResult.Expected}, {purgeResult.Residual}, {purgeResult.Z}, {purgeResult.SHigh}, {purgeResult.SLow}, {(purgeResult.Alarm ? 1 : 0)}\n");
                                    File.AppendAllText(detectionPath, detectionRow);
                                    if (_live != null)
                                    {
                                        double? dt = _lastTs.HasValue ? (now - _lastTs.Value).TotalSeconds : null;
                                        _lastTs = now;

                                        double? ScaleSp(ushort raw) => raw == 65535 ? null : raw * 500.0 / 65535.0;

                                        _live.Enqueue(new ProcessSample(_runId, now, "product",
                                            inputRegisterValues[6], productValve, inputRegisterValues[7], productFlow,
                                            productResult.Expected, productResult.Residual, productResult.Z,
                                            productResult.SHigh, productResult.SLow, _productH,
                                            ScaleSp(manualSpRegisterValues[1]), dt, false, productResult.Alarm));

                                        _live.Enqueue(new ProcessSample(_runId, now, "purge",
                                            inputRegisterValues[4], purgeValve, inputRegisterValues[5], purgeFlow,
                                            purgeResult.Expected, purgeResult.Residual, purgeResult.Z,
                                            purgeResult.SHigh, purgeResult.SLow, _purgeH,
                                            ScaleSp(manualSpRegisterValues[0]), dt, false, purgeResult.Alarm));
                                    }
                                    if (productResult.Alarm)
                                        Console.WriteLine($"{now:o} ALARM product sHigh={productResult.SHigh:F2} sLow={productResult.SLow:F2}");
                                    if (purgeResult.Alarm)
                                        Console.WriteLine($"{now:o} ALARM purge sHigh={purgeResult.SHigh:F2} sLow={purgeResult.SLow:F2}");
                                }
                                File.AppendAllText(path, row);
                            }
                            Thread.Sleep(1000);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine(ex.StackTrace);
                throw;
            }

        }
    }
}
