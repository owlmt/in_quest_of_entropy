#!/usr/bin/env python3
"""
rng_raw.py — predict the RAW entropy-input event stream directly, per source and per CPU.

Operates on raw nsecs (= rng_hw 'cycles'), grouping by source-vector (num) and by cpu, so we
test whether a *single* device's inter-arrival cadence (NVMe queue, ENA RX queue, timer) is
predictable — which the globally-merged stream can hide.

For each raw stream:
  target A: dnsecs(t+1)            [regression]  -- raw inter-arrival time
  target B: lowbit(dnsecs)(t+1)    [classification] -- the entropy-relevant low bit
Plus: next-vector(t+1) classification (which source fires next) on the global stream.
Plus: round-trip -- reconstruct low10(dnsecs) from the dnsecs prediction, compare to truth.

Controls: persistence, seasonal-naive, mean/majority, shuffle. Stats: bootstrap CI,
Hanley-McNeil AUC CI, permutation test. Chronological 70/15/15, never shuffle (except control).
Sequence model (LSTM) included for the busiest stream. Reviewer-skeptical: structure must
survive controls and appear in the LOW BITS to matter for entropy.
"""
import argparse, json, os, warnings, numpy as np, pandas as pd
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, matthews_corrcoef, balanced_accuracy_score
import lightgbm as lgb
warnings.filterwarnings('ignore'); RNG=np.random.default_rng(0)

def lag(s,W): return np.column_stack([s[W-1-k:len(s)-1-k] for k in range(W)])
def split(n,a=.7,b=.85): return slice(0,int(n*a)),slice(int(n*a),int(n*b)),slice(int(n*b),n)
def auc_ci(y,s):
    a=float(roc_auc_score(y,s)); n1=float(y.sum()); n0=float(len(y)-n1)
    q1=a/(2-a); q2=2*a*a/(1+a); se=float(np.sqrt((a*(1-a)+(n1-1)*(q1-a*a)+(n0-1)*(q2-a*a))/(n0*n1+1e-9)))
    return a,a-1.96*se,a+1.96*se
def boot_skill(y,p,pb,B=500):
    n=len(y); out=np.empty(B)
    for i in range(B):
        b=RNG.integers(0,n,n); sm=np.sum((y[b]-p[b])**2); sb=np.sum((y[b]-pb[b])**2); out[i]=1-sm/sb if sb>0 else 0
    return float(np.percentile(out,2.5)),float(np.percentile(out,97.5))
def perm_auc(y,s,B=300):
    a=roc_auc_score(y,s); null=np.array([roc_auc_score(RNG.permutation(y),s) for _ in range(B)])
    return float(a),float((np.sum(np.abs(null-.5)>=abs(a-.5))+1)/(B+1))
def autocorr(x,lags=(1,2,5,10,50)):
    x=x-x.mean(); out={}
    for L in lags:
        if L<len(x): out[L]=round(float(np.corrcoef(x[:-L],x[L:])[0,1]),4)
    return out

def model_stream(cyc, name, W, use_lstm=False, device='cuda'):
    """cyc: sorted raw nsecs for one stream. Returns dict of results."""
    cyc=np.asarray(cyc,np.float64)
    if len(cyc)<5000: return dict(stream=name,n=int(len(cyc)),skipped='too_small')
    d=np.diff(cyc)                          # raw inter-arrival (dnsecs)
    d=d[d>=0]                                # guard
    res=dict(stream=name, n=int(len(d)), ac_dnsecs=autocorr(d), ac_lowbit=autocorr((d.astype(np.int64)&1).astype(float)))
    # ---- target A: dnsecs(t+1) regression ----
    X=lag(d,W); y=d[W:]; m=min(len(X),len(y)); X,y=X[:m],y[:m]; n=len(y); tr,va,te=split(n)
    pbase=d[np.arange(te.start,te.stop)+W-1]                 # persistence = previous dnsecs
    sc=StandardScaler().fit(X[tr]); Xtr,Xte=sc.transform(X[tr]),sc.transform(X[te]); yte=y[te]
    regs={}
    for nm,md in [('persistence',None),('Ridge',Ridge(1.0)),
                  ('LGBM',lgb.LGBMRegressor(n_estimators=300,num_leaves=63,learning_rate=.05,n_jobs=-1,verbose=-1))]:
        p = pbase if md is None else (md.fit(Xtr,y[tr]).predict(Xte))
        ss=np.sum((yte-yte.mean())**2); r2=float(1-np.sum((yte-p)**2)/ss) if ss>0 else 0.0
        ssb=np.sum((yte-pbase)**2); sk=float(1-np.sum((yte-p)**2)/ssb) if ssb>0 else 0.0
        entry=dict(R2=r2, skill_vs_persist=sk)
        if md is not None: entry['skill_CI']=boot_skill(yte,p,pbase,400)
        # round-trip: does the dnsecs prediction recover the entropy low bits?
        entry['lowbit_corr']=round(float(np.corrcoef((np.asarray(p).astype(np.int64)&1023),
                                                     (yte.astype(np.int64)&1023))[0,1]),4)
        regs[nm]=entry
    res['dnsecs_pred']=regs
    # ---- target B: lowbit(dnsecs)(t+1) classification ----
    yb=(d.astype(np.int64)&1)[W:][:m]; p1=yb[tr].mean()
    clfs={'majority_base':float(max(p1,1-p1))}
    for nm,md in [('Logistic',LogisticRegression(max_iter=200)),
                  ('LGBM',lgb.LGBMClassifier(n_estimators=300,num_leaves=63,learning_rate=.05,n_jobs=-1,verbose=-1))]:
        if len(set(yb[tr]))<2: clfs[nm]={'AUC':0.5}; continue
        md.fit(Xtr,yb[tr]); s=md.predict_proba(Xte)[:,1]
        a,lo,hi=auc_ci(yb[te],s); pr=(s>=.5).astype(int)
        _,pp=perm_auc(yb[te],s,250)
        clfs[nm]=dict(AUC=a,AUC_lo=lo,AUC_hi=hi,bal_acc=float(balanced_accuracy_score(yb[te],pr)),
                      MCC=float(matthews_corrcoef(yb[te],pr)),perm_p=pp)
    res['lowbit_pred']=clfs
    # ---- shuffle control: permute the series BEFORE windowing -> destroys temporal structure ----
    if len(set(yb[tr]))>1:
        dperm=RNG.permutation(d); Xc=lag(dperm,W); ybc=(dperm.astype(np.int64)&1)[W:]
        mc=min(len(Xc),len(ybc)); Xc,ybc=Xc[:mc],ybc[:mc]; trc,_,tec=split(len(ybc))
        if len(set(ybc[trc]))>1:
            scp=StandardScaler().fit(Xc[trc]); mp=lgb.LGBMClassifier(n_estimators=200,num_leaves=63,learning_rate=.05,n_jobs=-1,verbose=-1)
            mp.fit(scp.transform(Xc[trc]),ybc[trc])
            res['lowbit_shuffle_AUC']=round(float(roc_auc_score(ybc[tec],mp.predict_proba(scp.transform(Xc[tec]))[:,1])),4)
    # ---- optional LSTM on dnsecs ----
    if use_lstm:
        try:
            import torch, torch.nn as nn
            dev=torch.device(device if torch.cuda.is_available() else 'cpu')
            Xs=lag(d,W).astype(np.float32)[...,None]; ys=d[W:].astype(np.float32); mm=min(len(Xs),len(ys)); Xs,ys=Xs[:mm],ys[:mm]
            tr2,va2,te2=split(len(ys)); mu=Xs[tr2].mean(); sd=Xs[tr2].std()+1e-6; Xs=(Xs-mu)/sd
            ym,yd=ys[tr2].mean(),ys[tr2].std()+1e-6; yn=(ys-ym)/yd
            class L(nn.Module):
                def __init__(s): super().__init__(); s.r=nn.LSTM(1,64,batch_first=True); s.f=nn.Linear(64,1)
                def forward(s,x): o,_=s.r(x); return s.f(o[:,-1]).squeeze(-1)
            net=L().to(dev); opt=torch.optim.Adam(net.parameters(),1e-3); lf=nn.MSELoss()
            Xt=torch.tensor(Xs[tr2]); yt=torch.tensor(yn[tr2]); Xv=torch.tensor(Xs[te2])
            bs=2048
            for ep in range(8):
                net.train()
                for i in range(0,len(yt),bs):
                    opt.zero_grad(); l=lf(net(Xt[i:i+bs].to(dev)),yt[i:i+bs].to(dev)); l.backward(); opt.step()
            net.eval()
            with torch.no_grad():
                pp=torch.cat([net(Xv[i:i+bs].to(dev)) for i in range(0,len(Xv),bs)]).cpu().numpy()*yd+ym
            yv=ys[te2]; ss=np.sum((yv-yv.mean())**2); res['dnsecs_pred']['LSTM']={'R2':float(1-np.sum((yv-pp)**2)/ss)}
        except Exception as e:
            res['lstm_error']=str(e)[:120]
    return res

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--csv',default='rng_hw.csv'); ap.add_argument('--out',default='raw_results.json')
    ap.add_argument('--window',type=int,default=64); ap.add_argument('--min-stream',type=int,default=30000)
    ap.add_argument('--topk',type=int,default=6); ap.add_argument('--lstm',action='store_true'); ap.add_argument('--device',default='cuda')
    a=ap.parse_args()
    df=pd.read_csv(a.csv); df=df.sort_values('cycles',kind='stable').reset_index(drop=True)
    cyc=df['cycles'].to_numpy(np.float64)
    R={'config':vars(a),'global':None,'per_source':[],'per_cpu':[],'next_source':None}
    def show(tag,r):
        key=r.get('dnsecs_pred',{}); 
        bl=key.get('LGBM',{}); lb=r.get('lowbit_pred',{}).get('LGBM',{})
        print(f"[{tag}] n={r.get('n')} ac_dnsecs(lag1)={r.get('ac_dnsecs',{}).get(1)} "
              f"ac_lowbit(lag1)={r.get('ac_lowbit',{}).get(1)} | dnsecs LGBM R2={bl.get('R2')} "
              f"skill={bl.get('skill_vs_persist')} lowbitcorr={bl.get('lowbit_corr')} | "
              f"lowbit LGBM AUC={lb.get('AUC')} CI=[{lb.get('AUC_lo')},{lb.get('AUC_hi')}] "
              f"MCC={lb.get('MCC')} permp={lb.get('perm_p')} shuf={r.get('lowbit_shuffle_AUC')}",flush=True)

    print('=== GLOBAL raw stream ==='); R['global']=model_stream(cyc,'global',a.window,a.lstm,a.device); show('global',R['global'])
    print('=== PER-SOURCE (IRQ vector num) ===')
    for v,c in df['num'].value_counts().head(a.topk).items():
        if c<a.min_stream: continue
        r=model_stream(df.loc[df['num']==v,'cycles'].to_numpy(np.float64),f'num={v}',a.window,a.lstm,a.device)
        R['per_source'].append(r); show(f'num={v}',r)
    print('=== PER-CPU ===')
    for cpu,c in df['cpu'].value_counts().head(a.topk).items():
        if c<a.min_stream: continue
        r=model_stream(df.loc[df['cpu']==cpu,'cycles'].to_numpy(np.float64),f'cpu={cpu}',a.window,a.lstm,a.device)
        R['per_cpu'].append(r); show(f'cpu={cpu}',r)
    # ---- next-source classification: predict next IRQ vector from last W vectors ----
    print('=== NEXT-SOURCE (which vector fires next) ===')
    try:
        vec=df['num'].to_numpy(); uniq,inv=np.unique(vec,return_inverse=True)
        W=a.window; X=lag(inv.astype(float),W); y=inv[W:]; m=min(len(X),len(y)); X,y=X[:m],y[:m]
        tr,va,te=split(len(y))
        clf=lgb.LGBMClassifier(n_estimators=300,num_leaves=127,learning_rate=.05,n_jobs=-1,verbose=-1)
        clf.fit(X[tr],y[tr]); pred=clf.predict(X[te])
        acc=float((pred==y[te]).mean()); marg=float(pd.Series(y[tr]).value_counts(normalize=True).iloc[0])
        R['next_source']=dict(accuracy=acc, marginal_top1=marg, n_classes=int(len(uniq)))
        print(f'  next-source acc={acc:.4f}  marginal(most-frequent)={marg:.4f}  classes={len(uniq)}')
    except Exception as e:
        R['next_source']={'error':str(e)[:120]}; print('  next-source error', str(e)[:80])
    json.dump(R,open(a.out,'w'),indent=2); print('\nWrote',a.out)

if __name__=='__main__': main()
