-- One row per experimental run
CREATE TABLE runs (
    run_id              text PRIMARY KEY,
    label               text NOT NULL,           -- 'clean' or 'attack'
    attack_type         text,                    -- 'drift', 'replay', 'cmd_inject', etc
    attack_variant      text,                    -- '002', 'subtle', 'autosp'
    attack_onset_epoch  double precision,        -- ground truth, NULL for clean
    attack_offset_epoch double precision,
    vantage             text,                    -- 'poller' or 'plc'
    notes               text
);

-- Per-sample process telemetry, one row per stream per poll
CREATE TABLE process (
    id              bigserial PRIMARY KEY,
    run_id          text NOT NULL REFERENCES runs(run_id),
    ts_epoch        double precision NOT NULL,
    ts_time         timestamptz GENERATED ALWAYS AS (to_timestamp(ts_epoch)) STORED,
    stream          text NOT NULL,               -- 'product' or 'purge'
    valve_raw       integer,
    valve           double precision,
    flow_raw        integer,
    flow            double precision,
    flow_expected   double precision,
    residual        double precision,
    residual_sigma  double precision,
    cusum_high      double precision,
    cusum_low       double precision,
    threshold_h     double precision,
    setpoint        double precision,
    dt_seconds      double precision,
    poll_excluded   boolean DEFAULT false,
    alarm           boolean DEFAULT false
);

-- Unified alerts from both detectors
CREATE TABLE alerts (
    id            bigserial PRIMARY KEY,
    run_id        text NOT NULL REFERENCES runs(run_id),
    ts_epoch      double precision NOT NULL,
    ts_time       timestamptz GENERATED ALWAYS AS (to_timestamp(ts_epoch)) STORED,
    source        text NOT NULL,                 -- 'physics' or 'network'
    rule          text NOT NULL,                 -- 'cusum_high', 'R1', 'R2'
    stream        text,                          -- 'product', 'purge', or host
    severity      text NOT NULL DEFAULT 'alarm', -- 'alarm' or 'info'
    value         double precision,
    threshold     double precision,
    detail        text,
    src_ip        text,
    dst_ip        text,
    unit_id       integer,
    function_code integer,
    register      integer
);

CREATE INDEX ON process (run_id, ts_time);
CREATE INDEX ON process (ts_time);
CREATE INDEX ON alerts  (run_id, ts_time);
CREATE INDEX ON alerts  (ts_time);
