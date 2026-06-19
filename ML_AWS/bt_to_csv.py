#!/usr/bin/env python3
"""
bt_to_csv.py  —  raw_events.csv (bpftrace) -> rng_hw.csv (derived features)
Verified to reproduce the existing WSL2 rng_hw.csv byte-for-byte (all 15 columns).
Usage:  python3 bt_to_csv.py raw_events.csv rng_hw.csv
Schema in:  source,cpu,nsecs,a,b,c   (leading 'Attaching N probes...' line tolerated)
Derivations (timestamp == bpftrace nsecs):
  cycles=nsecs ; delta_cycles=diff(cycles) ; jitter=low10(delta_cycles)
  jitter16=low16(cycles) ; jiffies=cycles//1000 ; num=a ; idx=jpos=row index
  delta1=diff(jiffies) ; delta2=diff(delta1) ; delta3=diff(delta2)   (prepend 0)
  min_delta=min(|d1|,|d2|,|d3|) ; est_bits=min(floor(log2(min_delta)),11), 0 if 0
"""
import sys, numpy as np, pandas as pd

def convert(inp, outp):
    # robust: tolerate leading 'Attaching N probes...' and any stray non-numeric rows
    raw = pd.read_csv(inp, header=0, names=['source','cpu','nsecs','a','b','c'],
                      engine='python', on_bad_lines='skip')
    raw = raw[raw['source'].isin(['IRQ','DISK','INPUT'])].copy()
    raw = raw[pd.to_numeric(raw['nsecs'], errors='coerce').notna()].reset_index(drop=True)
    raw['nsecs'] = raw['nsecs'].astype(np.int64)
    raw = raw.sort_values('nsecs', kind='stable').reset_index(drop=True)  # restore chronological order across CPUs

    cyc = raw['nsecs'].astype(np.int64).to_numpy()
    n   = len(cyc)
    dc  = np.diff(cyc, prepend=cyc[0]); dc[0] = 0
    jif = cyc // 1000
    # per-source cascade: each source (IRQ/DISK/INPUT) keeps independent last_time/last_delta state
    d1 = np.zeros(n, dtype=np.int64); d2 = np.zeros(n, dtype=np.int64); d3 = np.zeros(n, dtype=np.int64)
    raw = raw.reset_index(drop=True)
    for _src, g in raw.groupby('source', sort=False):
        pos = g.index.to_numpy()
        j  = jif[pos]
        a1 = np.diff(j,  prepend=0)
        a2 = np.diff(a1, prepend=0)
        a3 = np.diff(a2, prepend=0)
        d1[pos] = a1; d2[pos] = a2; d3[pos] = a3
    md  = np.minimum(np.minimum(np.abs(d1), np.abs(d2)), np.abs(d3))
    eb  = np.where(md > 0, np.minimum(np.floor(np.log2(np.maximum(md,1))).astype(np.int64), 11), 0)

    out = pd.DataFrame({
        'idx'         : np.arange(n, dtype=np.int64),
        'source'      : raw['source'].to_numpy(),
        'cpu'         : raw['cpu'].astype(np.int64).to_numpy(),
        'jpos'        : np.arange(n, dtype=np.int64),
        'jitter'      : (dc & 1023),
        'cycles'      : cyc,
        'delta_cycles': dc,
        'jitter16'    : (cyc & 65535),
        'jiffies'     : jif,
        'num'         : raw['a'].astype(np.int64).to_numpy(),
        'delta1'      : d1,
        'delta2'      : d2,
        'delta3'      : d3,
        'min_delta'   : md,
        'est_bits'    : eb,
    })
    out.to_csv(outp, index=False)
    return out

if __name__ == '__main__':
    inp  = sys.argv[1] if len(sys.argv) > 1 else 'raw_events.csv'
    outp = sys.argv[2] if len(sys.argv) > 2 else 'rng_hw.csv'
    out = convert(inp, outp)
    print(f'wrote {outp}: {len(out)} rows, {out.shape[1]} cols')
