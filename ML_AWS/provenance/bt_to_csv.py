import sys, numpy as np, pandas as pd
def convert(inp, outp):
    raw = pd.read_csv(inp, header=0, names=['source','cpu','nsecs','a','b','c'],
                      engine='python', on_bad_lines='skip')
    raw = raw[raw['source'].isin(['IRQ','DISK','INPUT'])].copy()
    raw = raw[pd.to_numeric(raw['nsecs'], errors='coerce').notna()].reset_index(drop=True)
    raw['nsecs'] = raw['nsecs'].astype(np.int64)
    raw = raw.sort_values('nsecs', kind='stable').reset_index(drop=True)
    cyc = raw['nsecs'].to_numpy(np.int64); n = len(cyc)
    dc  = np.diff(cyc, prepend=cyc[0]); dc[0] = 0
    jif = cyc // 1000
    d1=np.zeros(n,np.int64); d2=np.zeros(n,np.int64); d3=np.zeros(n,np.int64)
    for _s,g in raw.groupby('source', sort=False):
        pos=g.index.to_numpy(); j=jif[pos]
        a1=np.diff(j,prepend=0); a2=np.diff(a1,prepend=0); a3=np.diff(a2,prepend=0)
        d1[pos]=a1; d2[pos]=a2; d3[pos]=a3
    md=np.minimum(np.minimum(np.abs(d1),np.abs(d2)),np.abs(d3))
    eb=np.where(md>0, np.minimum(np.floor(np.log2(np.maximum(md,1))).astype(np.int64),11), 0)
    pd.DataFrame({'idx':np.arange(n),'source':raw['source'].to_numpy(),'cpu':raw['cpu'].to_numpy(np.int64),
        'jpos':np.arange(n),'jitter':dc&1023,'cycles':cyc,'delta_cycles':dc,'jitter16':cyc&65535,
        'jiffies':jif,'num':raw['a'].to_numpy(np.int64),'delta1':d1,'delta2':d2,'delta3':d3,
        'min_delta':md,'est_bits':eb}).to_csv(outp,index=False)
    print(f'wrote {outp}: {n} rows')
convert(sys.argv[1] if len(sys.argv)>1 else 'raw_events.csv',
        sys.argv[2] if len(sys.argv)>2 else 'rng_hw.csv')
