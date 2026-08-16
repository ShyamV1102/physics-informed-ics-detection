import pandas as pd
from pathlib import Path
import numpy as np 

RUN_GRID = False
RUN_DIAGNOSTICS = False
RUN_REJECTED = False
RUN_VERIFY = False
RUN_HELDOUT = True
RUN_OPPOINT = False

def scale_raw_to_real(rawValues, real_max):
    return (rawValues * real_max / 65535)


def getRealPressureValues():
    pressureValues = file['PressureReg']
    return scale_raw_to_real(pressureValues, 3200)

def getRealLevelValues(setPoint = ''):
    levelValues = file['LevelReg'] if setPoint == '' else file['LevelSpReg']
    return scale_raw_to_real(levelValues, 100)

def getRealProductPurgeFlowValues(flow):
    rawFlowValues = file['ProductFlowReg'] if flow == 'Product' else file['PurgeFlowReg']
    return scale_raw_to_real(rawFlowValues, 500)

def getAFlowValue():
    aFlowValue = file['F1FlowReg']
    return (aFlowValue * 0.0153)

def getProductPurgeValvePos(valve):
    rawValvePos = file['ProductValvePosReg'] if valve == 'Product' else file['PurgeValvePosReg']
    return scale_raw_to_real(rawValvePos, 100)

def run_cusum(resid, slack=0.5, h=5.0):
    mu = resid.mean()
    sigma = resid.std()
    z = (resid - mu) / sigma

    s_hi = 0.0
    s_lo = 0.0
    alarms = 0
    hi_series = []
    lo_series = []

    for val in z:
        s_hi = max(0.0, s_hi + val - slack)
        s_lo = min(0.0, s_lo + val + slack)
        if s_hi > h:
            alarms += 1
            s_hi = 0.0
        if s_lo < -h:
            alarms += 1
            s_lo = 0.0
        hi_series.append(s_hi)
        lo_series.append(s_lo)

    return alarms, mu, sigma, np.array(hi_series), np.array(lo_series)


def cusum_trace(resid, slack, h, mu=0.0, sigma=1.0):
    z = (resid - mu) / sigma
    s_hi = s_lo = 0.0
    Z, HI, LO = [], [], []
    for val in z:
        s_hi = max(0.0, s_hi + val - slack)
        s_lo = min(0.0, s_lo + val + slack)
        if s_hi > h:
            s_hi = 0.0
        if s_lo < -h:
            s_lo = 0.0
        Z.append(val); HI.append(s_hi); LO.append(s_lo)
    return Z, HI, LO


def cusum_count(resid, slack, h, mu=0.0, sigma=1.0):
    z = (resid - mu) / sigma
    s_hi = s_lo = 0.0
    alarms = 0
    for val in z:
        s_hi = max(0.0, s_hi + val - slack)
        s_lo = min(0.0, s_lo + val + slack)
        if s_hi > h:
            alarms += 1
            s_hi = 0.0
        if s_lo < -h:
            alarms += 1
            s_lo = 0.0
    return alarms

#ModbusPoller/ModbusPoller/data/
path = Path(__file__).parent / 'ModbusPoller/ModbusPoller/data/baseline_run_3.csv'
file = pd.read_csv(path, skipinitialspace=True, parse_dates=['timestamp'])
file['dt'] = file['timestamp'].diff().dt.total_seconds()
file['Pressure'] = getRealPressureValues()
file['Level'] = getRealLevelValues()
file['ProductFlow'] = getRealProductPurgeFlowValues('Product')
file['PurgeFlow'] = getRealProductPurgeFlowValues('Purge')  
file['AFlow'] = getAFlowValue()
file['ProductValvePos'] = getProductPurgeValvePos('Product')
file['PurgeValvePos'] = getProductPurgeValvePos('Purge')
file['LevelSetpoint'] = getRealLevelValues('SP')

# Poll cycles that stall (dt around 2.6s instead of 1s) produce a stretched
# time skew between the flow read and the valve read, corrupting the residual.
# The disturbance persists for a few samples after the interval recovers, so
# the exclusion flag is carried forward. recovery=5 chosen from an alarm sweep.

nominal = file['dt'].median()
bad = ~(file['dt'] < 1.5 * nominal)

recovery = 5
bad_window = bad.rolling(recovery, min_periods=1).max().astype(bool)
file['poll_ok'] = ~bad_window

clean = file[file['poll_ok']].copy()

m_product, c_product = np.polyfit(clean['ProductValvePos'], clean['ProductFlow'], 1)
m_purge, c_purge = np.polyfit(clean['PurgeValvePos'], clean['PurgeFlow'], 1)

resid_product = clean['ProductFlow'] - (m_product * clean['ProductValvePos'] + c_product)
resid_purge = clean['PurgeFlow'] - (m_purge * clean['PurgeValvePos'] + c_purge)


if RUN_GRID:
    for slack in [0.5, 0.75, 1.0]:
        for h in [4, 5, 6, 7, 8]:
            a_prod, *_ = run_cusum(resid_product, slack, h)
            a_purge, *_ = run_cusum(resid_purge, slack, h)
            print(f"slack={slack}, h={h}: product={a_prod}, purge={a_purge}")


print(f"product fit: m={m_product:.8f}, c={c_product:.8f}")
print(f"purge fit:   m={m_purge:.8f}, c={c_purge:.8f}")

locked = {
    'product': (resid_product, 0.75, 5.0),
    'purge':   (resid_purge,  0.5,  6.0),
}

for name, (resid, slack, h) in locked.items():
    alarms, mu, sigma, hi, lo = run_cusum(resid, slack, h)
    print(f"{name}: slack={slack}, h={h}, alarms={alarms}, mu={mu:.8f}, sigma={sigma:.8f}")

if RUN_VERIFY:
    export = clean[['timestamp', 'ProductFlowReg', 'ProductValvePosReg',
                    'PurgeFlowReg', 'PurgeValvePosReg']].copy()

    M_PROD, C_PROD, SIG_PROD = 2.12546546, -0.00309158, 0.07179686
    M_PURGE, C_PURGE, SIG_PURGE = 4.48571184, 0.00409840, 0.08653334

    resid_prod_locked  = clean['ProductFlow'] - (M_PROD  * clean['ProductValvePos'] + C_PROD)
    resid_purge_locked = clean['PurgeFlow']   - (M_PURGE * clean['PurgeValvePos']   + C_PURGE)

    zp, hip, lop = cusum_trace(resid_prod_locked,  0.75, 5.0, sigma=SIG_PROD)
    zq, hiq, loq = cusum_trace(resid_purge_locked, 0.5,  6.0, sigma=SIG_PURGE)

    export['py_prod_z'] = zp
    export['py_prod_shi'] = hip
    export['py_prod_slo'] = lop
    export['py_purge_z'] = zq
    export['py_purge_shi'] = hiq
    export['py_purge_slo'] = loq

    DATA = Path(__file__).parent / 'ModbusPoller/ModbusPoller/data'
    export.to_csv(DATA / 'clean_export.csv', index=False)
    cs_path = DATA / 'cs_trace.csv'
    if cs_path.exists():
        cs = pd.read_csv(cs_path, skipinitialspace=True)
        cs['timestamp'] = pd.to_datetime(cs['timestamp'])
        merged = export.merge(cs, on='timestamp')
        print(f"rows: export={len(export)}, merged={len(merged)}")
        for col in ['prod_z', 'prod_shi', 'prod_slo', 'purge_z', 'purge_shi', 'purge_slo']:
            diff = (merged[f'py_{col}'] - merged[f'cs_{col}']).abs().max()
            print(f"{col}: max abs diff = {diff}")

    clean[['timestamp']].to_csv(DATA / 'py_clean_ts.csv', index=False)

    cs_ts_path = DATA / 'cs_clean_ts.csv'
    if cs_ts_path.exists():
        py_ts = set(pd.to_datetime(clean['timestamp']))
        cs_ts = set(pd.to_datetime(pd.read_csv(cs_ts_path)['timestamp']))

        print(f"python kept: {len(py_ts)}")
        print(f"c# kept:     {len(cs_ts)}")
        print(f"in python not c#: {len(py_ts - cs_ts)}")
        print(f"in c# not python: {len(cs_ts - py_ts)}")

    print(nominal)

    
if RUN_DIAGNOSTICS:
    file['ramp'] = file['ProductValvePos'].diff().abs()
    ramp_product = file['ramp'][clean.index]
    ramp_purge = file['PurgeValvePos'].diff().abs()[clean.index]

    print('product corr:', resid_product.abs().corr(ramp_product))
    print('purge corr:', resid_purge.abs().corr(ramp_purge))

    worst = resid_product.abs().sort_values(ascending=False).head(10)
    print(pd.DataFrame({
        'resid': resid_product[worst.index],
        'ramp': ramp_product[worst.index],
        'dt': clean['dt'][worst.index]
    }))
    print('median ramp:', ramp_product.median())


if RUN_REJECTED:
    X = clean[['AFlow','ProductFlow','PurgeFlow','Level','ProductValvePos','PurgeValvePos']]
    X = np.column_stack([X, np.ones(len(X))])
    coef, *_ = np.linalg.lstsq(X, clean['Pressure'], rcond=None)
    resid_p = clean['Pressure'] - X @ coef
    print(clean['Pressure'].std(), resid_p.std())


    X3 = np.column_stack([resid_product, resid_purge, np.ones(len(clean))])
    coef3, *_ = np.linalg.lstsq(X3, clean['Pressure'], rcond=None)
    print((clean['Pressure'] - X3 @ coef3).std())


    resid_level = clean['Level'] - clean['LevelSetpoint']
    print(resid_level.std(), resid_level.mean())


if RUN_HELDOUT:
    M_PROD, C_PROD, SIG_PROD = 2.12546546, -0.00309158, 0.07179686
    M_PURGE, C_PURGE, SIG_PURGE = 4.48571184, 0.00409840, 0.08653334

    resid_prod_locked  = clean['ProductFlow'] - (M_PROD  * clean['ProductValvePos'] + C_PROD)
    resid_purge_locked = clean['PurgeFlow']   - (M_PURGE * clean['PurgeValvePos']   + C_PURGE)

    a_prod  = cusum_count(resid_prod_locked,  0.75, 5.0, mu=0.0, sigma=SIG_PROD)
    a_purge = cusum_count(resid_purge_locked, 0.5,  6.0, mu=0.0, sigma=SIG_PURGE)

    n = len(clean)
    print(f"held-out rows (clean): {n}")
    print(f"product alarms: {a_prod}  FPR: {a_prod / n:.6f}")
    print(f"purge alarms:   {a_purge}  FPR: {a_purge / n:.6f}")
    print(f"combined alarms: {a_prod + a_purge}  FPR: {(a_prod + a_purge) / n:.6f}")

if RUN_OPPOINT:
    cols = ['ProductFlow', 'PurgeFlow', 'ProductValvePos', 'PurgeValvePos',
            'Pressure', 'Level', 'LevelSetpoint']
    print("held-out operating point (clean rows only):")
    for col in cols:
        s = clean[col]
        print(f"{col:16s} mean={s.mean():10.4f}  std={s.std():8.4f}  "
              f"min={s.min():10.4f}  max={s.max():10.4f}")
    print(f"rows (clean): {len(clean)}")