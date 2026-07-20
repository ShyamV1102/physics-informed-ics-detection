import pandas as pd
from pathlib import Path
import numpy as np 

RUN_GRID = False
RUN_DIAGNOSTICS = False
RUN_REJECTED = False

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


#ModbusPoller/ModbusPoller/data/
path = Path(__file__).parent / 'ModbusPoller/ModbusPoller/data/readings.csv'
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