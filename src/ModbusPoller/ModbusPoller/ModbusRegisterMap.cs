using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace ModbusPoller
{
    public class ModbusRegisterMap
    {
        // Input registers (%IW100–%IW112)
        public const ushort F1ValvePosReg = 100;
        public const ushort F1FlowReg = 101;
        public const ushort F2ValvePosReg = 102;
        public const ushort F2FlowReg = 103;
        public const ushort PurgeValvePosReg = 104;
        public const ushort PurgeFlowReg = 105;
        public const ushort ProductValvePosReg = 106;
        public const ushort ProductFlowReg = 107;
        public const ushort PressureReg = 108;
        public const ushort LevelReg = 109;
        public const ushort AInPurgeReg = 110;
        public const ushort BInPurgeReg = 111;
        public const ushort CInPurgeReg = 112;

        // Holding registers (%MW0–%MW4)
        public const ushort PurgeManualSp = 12;
        public const ushort ProductManualSp = 13;
        public const ushort ProductFlowSpReg = 1024;
        public const ushort ASetpointReg = 1025;
        public const ushort PressureSpReg = 1026;
        public const ushort OverrideSpReg = 1027;
        public const ushort LevelSpReg = 1028;

        //Coil Registers
        public const ushort ManualRegister = 0;
    }
}
