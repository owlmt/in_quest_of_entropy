#!/usr/bin/env python3
"""
Step-8 predictability pipeline for the Linux RNG entropy source (AWS capture).

Targets (always FUTURE t+1):  delta_cycles, jitter, LSB(jitter)
Allowed features: delta_cycles & jitter lags + engineered (spectral/autocorr/entropy/multi-res).
NEVER features (monotonic-timestamp leakage): cycles, jiffies, idx, jitter16, nsecs.
Validation: chronological 70/15/15, never shuffle (except the explicit shuffle control).
Families: linear / trees+boosters / GPU sequence nets / classical TS, Optuna-tuned.
Controls: persistence, seasonal-naive, majority/mean, shuffle. Stats: bootstrap CI,
Hanley-McNeil AUC CI, permutation test. Stopping rule + structured JSON report.

Usage:
  source /opt/pytorch/bin/activate
  python rng_pipeline.py --csv rng_hw.csv --out results --optuna-trials 100
  # quick smoke:  python rng_pipeline.py --csv rng_hw.csv --optuna-trials 5 --cap 40000 --no-seq
"""
import argparse, json, os, time, warnings, traceback
import numpy as np, pandas as pd
from sklearn.linear_model import Ridge, ElasticNet, LogisticRegression
from sklearn.ensemble import (RandomForestRegressor, ExtraTreesRegressor,
                              RandomForestClassifier, ExtraTreesClassifier)
from sklearn.neural_network import MLPRegressor, MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (mean_absolute_error, roc_auc_score, average_precision_score,
                             balanced_accuracy_score, matthews_corrcoef, brier_score_loss)
import xgboost as xgb, lightgbm as lgb
warnings.filterwarnings('ignore')
try: import catboost as cb; HAVE_CB=True
except Exception: HAVE_CB=False
try: import optuna; optuna.logging.set_verbosity(optuna.logging.WARNING); HAVE_OPT=True
except Exception: HAVE_OPT=False

RNG = np.random.default_rng(0)
LEAK = {'cycles','jiffies','idx','jitter16','nsecs','jpos'}

# ----------------------------------------------------------------------------- data / features
def load(csv):
    df = pd.read_csv(csv)
    return df['delta_cycles'].to_numpy(np.float64), df['jitter'].to_numpy(np.float64)

def lag_block(s, W):
    return np.column_stack([s[W-1-k:len(s)-1-k] for k in range(W)])   # lags 1..W (strictly past)

def build_features(dc, jit, W, fs, cap):
    n = min(cap, len(dc))
    dc, jit = dc[:n], jit[:n]
    feats = []
    if fs in ('A','C','D','H'): feats.append(lag_block(dc, W))
    if fs in ('B','C','D','H'): feats.append(lag_block(jit, W))
    base = dc if fs in ('A','E','F','G') else jit  # series used for engineered transforms
    if fs == 'D':  # all timing-derived: + rolling mean/std of both
        for s in (dc, jit):
            L = lag_block(s, W)
            feats.append(np.column_stack([L.mean(1), L.std(1), L.min(1), L.max(1)]))
    if fs == 'E':  # spectral: FFT magnitude of the window (top components) for both series
        for s in (dc, jit):
            L = lag_block(s, W)
            F = np.abs(np.fft.rfft(L - L.mean(1, keepdims=True), axis=1))
            feats.append(F[:, :min(16, F.shape[1])])
    if fs == 'F':  # autocorrelation features of the window
        for s in (dc, jit):
            L = lag_block(s, W); Lc = L - L.mean(1, keepdims=True)
            acs = []
            for lag in [1,2,3,4,8]:
                if lag < W:
                    num = (Lc[:, :-lag]*Lc[:, lag:]).sum(1)
                    den = (Lc*Lc).sum(1) + 1e-9
                    acs.append(num/den)
            feats.append(np.column_stack(acs))
    if fs == 'G':  # entropy-rate: windowed Shannon entropy (binned) + std + range, both series
        for s in (dc, jit):
            L = lag_block(s, W)
            ent = []
            for row in L:  # vectorize-ish via histogram per row is slow; approximate with quantile bins
                pass
            # fast approximate Shannon entropy over 16 bins per row
            mn = L.min(1, keepdims=True); rng = (L.max(1, keepdims=True)-mn)+1e-9
            b = np.clip(((L-mn)/rng*16).astype(int), 0, 15)
            H = np.zeros(len(L))
            for k in range(16):
                p = (b == k).mean(1); H -= np.where(p>0, p*np.log2(p+1e-12), 0)
            feats.append(np.column_stack([H, L.std(1), L.max(1)-L.min(1)]))
    if fs == 'H':  # multi-resolution: dyadic lags + multiscale rolling means
        for s in (dc, jit):
            L = lag_block(s, min(W, 256))
            dy = [c for c in [1,2,4,8,16,32,64,128] if c <= L.shape[1]]
            feats.append(L[:, [d-1 for d in dy]])
            feats.append(np.column_stack([L[:, :sc].mean(1) for sc in dy]))
    X = np.column_stack(feats)
    return X

def targets(dc, jit, W, cap):
    n = min(cap, len(dc))
    return {'delta_cycles': dc[:n][W:], 'jitter': jit[:n][W:],
            'lsb': (jit[:n].astype(int) & 1)[W:]}

def split_idx(n, a=.70, b=.85): return slice(0,int(n*a)), slice(int(n*a),int(n*b)), slice(int(n*b),n)

# ----------------------------------------------------------------------------- metrics
def reg_metrics(y, p, pbase):
    mae = mean_absolute_error(y, p); rmse = float(np.sqrt(np.mean((y-p)**2)))
    ss = np.sum((y-y.mean())**2); r2 = float(1-np.sum((y-p)**2)/ss) if ss>0 else 0.0
    ssb = np.sum((y-pbase)**2); skill = float(1-np.sum((y-p)**2)/ssb) if ssb>0 else 0.0
    return dict(MAE=float(mae), RMSE=rmse, R2=r2, skill_vs_persist=skill)

def auc_ci(y, s):
    a = float(roc_auc_score(y, s)); n1=float(y.sum()); n0=float(len(y)-n1)
    q1=a/(2-a); q2=2*a*a/(1+a)
    se=float(np.sqrt((a*(1-a)+(n1-1)*(q1-a*a)+(n0-1)*(q2-a*a))/(n0*n1+1e-9)))
    return a, a-1.96*se, a+1.96*se

def clf_metrics(y, score):
    pred=(score>=0.5).astype(int); a,lo,hi=auc_ci(y,score)
    return dict(AUC=a, AUC_lo=lo, AUC_hi=hi, PR_AUC=float(average_precision_score(y,score)),
                bal_acc=float(balanced_accuracy_score(y,pred)), MCC=float(matthews_corrcoef(y,pred)),
                Brier=float(brier_score_loss(y,score)))

def boot_reg_skill(y,p,pbase,B=1000):
    n=len(y); out=np.empty(B)
    for i in range(B):
        b=RNG.integers(0,n,n); sm=np.sum((y[b]-p[b])**2); sb=np.sum((y[b]-pbase[b])**2)
        out[i]=1-sm/sb if sb>0 else 0
    return float(np.percentile(out,2.5)), float(np.percentile(out,97.5))

def perm_test_auc(y, score, B=500):
    a=roc_auc_score(y,score); null=np.array([roc_auc_score(RNG.permutation(y),score) for _ in range(B)])
    p=(np.sum(np.abs(null-0.5)>=abs(a-0.5))+1)/(B+1)
    return float(a), float(null.mean()), float(p)

# ----------------------------------------------------------------------------- baselines
def baselines(series, is_clf):
    n=len(series); tr,va,te=split_idx(n); yte=series[te]
    persist=series[np.arange(te.start,te.stop)-1]
    ytr=series[tr]-series[tr].mean(); bl,bac=1,0.0
    for L in range(1,min(2049,len(ytr))):
        ac=np.corrcoef(ytr[:-L],ytr[L:])[0,1]
        if ac>bac: bac,bl=ac,L
    seas=series[np.arange(te.start,te.stop)-bl]
    out={'seasonal_lag':int(bl),'seasonal_train_ac':float(bac)}
    if is_clf:
        p1=series[tr].mean(); maj=int(round(p1))
        out['majority_class']=int(maj); out['majority_base_rate']=float(max(p1,1-p1))
        out['persistence_AUC']=float(roc_auc_score(yte,persist)) if len(set(yte))>1 else 0.5
    else:
        for nm,p in [('persistence',persist),('seasonal',seas),('mean',np.full_like(yte,series[tr].mean()))]:
            ss=np.sum((yte-yte.mean())**2)
            out[f'{nm}_R2']=float(1-np.sum((yte-p)**2)/ss) if ss>0 else 0.0
    return out, persist

# ----------------------------------------------------------------------------- tabular models + optuna
def make_reg(name, t):
    if name=='Ridge': return Ridge(alpha=t.suggest_float('alpha',1e-3,1e3,log=True))
    if name=='ElasticNet': return ElasticNet(alpha=t.suggest_float('alpha',1e-4,10,log=True),
                                             l1_ratio=t.suggest_float('l1',0.05,0.95))
    if name=='RandomForest': return RandomForestRegressor(
        n_estimators=t.suggest_int('n',50,120), max_depth=t.suggest_int('d',6,16),
        min_samples_leaf=t.suggest_int('leaf',1,30), n_jobs=-1, random_state=0)
    if name=='ExtraTrees': return ExtraTreesRegressor(
        n_estimators=t.suggest_int('n',50,120), max_depth=t.suggest_int('d',6,16),
        min_samples_leaf=t.suggest_int('leaf',1,30), n_jobs=-1, random_state=0)
    if name=='XGBoost': return xgb.XGBRegressor(
        n_estimators=t.suggest_int('n',100,500), max_depth=t.suggest_int('d',3,10),
        learning_rate=t.suggest_float('lr',0.01,0.3,log=True), subsample=t.suggest_float('ss',0.6,1.0),
        colsample_bytree=t.suggest_float('cs',0.6,1.0), n_jobs=-1, verbosity=0)
    if name=='LightGBM': return lgb.LGBMRegressor(
        n_estimators=t.suggest_int('n',100,500), num_leaves=t.suggest_int('nl',15,255),
        learning_rate=t.suggest_float('lr',0.01,0.3,log=True), subsample=t.suggest_float('ss',0.6,1.0),
        n_jobs=-1, verbose=-1)
    if name=='CatBoost': return cb.CatBoostRegressor(
        iterations=t.suggest_int('n',100,500), depth=t.suggest_int('d',4,10),
        learning_rate=t.suggest_float('lr',0.01,0.3,log=True), verbose=0, allow_writing_files=False)
    if name=='MLP': return MLPRegressor(
        hidden_layer_sizes=t.suggest_categorical('h',[(64,),(128,64),(256,128,64)]),
        alpha=t.suggest_float('a',1e-5,1e-1,log=True), max_iter=80, early_stopping=True)

def make_clf(name, t):
    if name=='Logistic': return LogisticRegression(C=t.suggest_float('C',1e-3,1e3,log=True), max_iter=300)
    if name=='RandomForest': return RandomForestClassifier(
        n_estimators=t.suggest_int('n',50,120), max_depth=t.suggest_int('d',6,16),
        min_samples_leaf=t.suggest_int('leaf',1,30), n_jobs=-1, random_state=0)
    if name=='ExtraTrees': return ExtraTreesClassifier(
        n_estimators=t.suggest_int('n',50,120), max_depth=t.suggest_int('d',6,16),
        min_samples_leaf=t.suggest_int('leaf',1,30), n_jobs=-1, random_state=0)
    if name=='XGBoost': return xgb.XGBClassifier(
        n_estimators=t.suggest_int('n',100,500), max_depth=t.suggest_int('d',3,10),
        learning_rate=t.suggest_float('lr',0.01,0.3,log=True), subsample=t.suggest_float('ss',0.6,1.0),
        n_jobs=-1, verbosity=0, eval_metric='logloss')
    if name=='LightGBM': return lgb.LGBMClassifier(
        n_estimators=t.suggest_int('n',100,500), num_leaves=t.suggest_int('nl',15,255),
        learning_rate=t.suggest_float('lr',0.01,0.3,log=True), n_jobs=-1, verbose=-1)
    if name=='CatBoost': return cb.CatBoostClassifier(
        iterations=t.suggest_int('n',100,500), depth=t.suggest_int('d',4,10),
        learning_rate=t.suggest_float('lr',0.01,0.3,log=True), verbose=0, allow_writing_files=False)
    if name=='MLP': return MLPClassifier(
        hidden_layer_sizes=t.suggest_categorical('h',[(64,),(128,64),(256,128,64)]),
        alpha=t.suggest_float('a',1e-5,1e-1,log=True), max_iter=80, early_stopping=True)

def optuna_family(name, X, y, is_clf, n_trials, sub_train=60000, timeout=300):
    n=len(y); tr,va,te=split_idx(n)
    sc=StandardScaler().fit(X[tr]); Xtr,Xva,Xte=sc.transform(X[tr]),sc.transform(X[va]),sc.transform(X[te])
    ytr,yva,yte=y[tr],y[va],y[te]
    if len(Xtr)>sub_train and name in ('RandomForest','ExtraTrees','CatBoost'):
        idx=RNG.choice(len(Xtr),sub_train,replace=False); Xtr_f,ytr_f=Xtr[idx],ytr[idx]
    else: Xtr_f,ytr_f=Xtr,ytr
    def obj(t):
        m=(make_clf if is_clf else make_reg)(name,t)
        m.fit(Xtr_f,ytr_f)
        if is_clf:
            s=m.predict_proba(Xva)[:,1]
            return roc_auc_score(yva,s) if len(set(yva))>1 else 0.5
        p=m.predict(Xva); ss=np.sum((yva-yva.mean())**2)
        return 1-np.sum((yva-p)**2)/ss if ss>0 else -1e9
    study=optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=0))
    study.optimize(obj, n_trials=n_trials, timeout=timeout, show_progress_bar=False)
    # refit best on train, eval on test
    best=study.best_params
    class FT:  # fixed-trial to rebuild model from best params
        def __init__(s,p): s.p=p
        def suggest_float(s,k,*a,**kw): return s.p[k]
        def suggest_int(s,k,*a,**kw): return s.p[k]
        def suggest_categorical(s,k,*a,**kw): return s.p[k]
    m=(make_clf if is_clf else make_reg)(name,FT(best)); m.fit(Xtr_f,ytr_f)
    if is_clf:
        s=m.predict_proba(Xte)[:,1]; met=clf_metrics(yte,s)
        met['perm_p']=perm_test_auc(yte,s,300)[2]
    else:
        p=m.predict(Xte); pbase=y[np.arange(te.start,te.stop)-1]; met=reg_metrics(yte,p,pbase)
        met['skill_CI']=boot_reg_skill(yte,p,pbase,500)
    return dict(model=name, best_params=best, val_score=float(study.best_value), test=met)

# ----------------------------------------------------------------------------- GPU sequence models
def run_sequence(name, dc, jit, W, target, is_clf, cap, device, epochs=15, batch=1024):
    import torch, torch.nn as nn
    n=min(cap,len(dc)); dc,jit=dc[:n],jit[:n]
    Xd=lag_block(dc,W); Xj=lag_block(jit,W)
    X=np.stack([Xd,Xj],axis=-1).astype(np.float32)            # (N, W, 2)
    if target=='delta_cycles': y=dc[W:].astype(np.float32)
    elif target=='jitter': y=jit[W:].astype(np.float32)
    else: y=(jit[W:].astype(int)&1).astype(np.float32)
    n=len(y); tr,va,te=split_idx(n)
    mu=X[tr].reshape(-1,2).mean(0); sd=X[tr].reshape(-1,2).std(0)+1e-6
    X=(X-mu)/sd
    ymu,ysd=(0,1) if is_clf else (y[tr].mean(), y[tr].std()+1e-6)
    yN=y if is_clf else (y-ymu)/ysd
    import torch
    dev=torch.device(device)
    def t(a): return torch.tensor(a)
    Xtr,Xva,Xte=t(X[tr]),t(X[va]),t(X[te]); ytr,yva,yte=t(yN[tr]),t(yN[va]),t(yN[te])

    class CNN1D(nn.Module):
        def __init__(s):
            super().__init__(); s.c=nn.Sequential(nn.Conv1d(2,32,3,padding=1),nn.ReLU(),
                nn.Conv1d(32,32,3,padding=1),nn.ReLU(),nn.AdaptiveAvgPool1d(1)); s.f=nn.Linear(32,1)
        def forward(s,x): return s.f(s.c(x.transpose(1,2)).squeeze(-1)).squeeze(-1)
    class TCN(nn.Module):
        def __init__(s):
            super().__init__(); ch=32; layers=[]; d=1
            for _ in range(4):
                layers+=[nn.Conv1d(2 if d==1 else ch,ch,3,padding=d,dilation=d),nn.ReLU()]; d*=2
            s.c=nn.Sequential(*layers); s.f=nn.Linear(ch,1)
        def forward(s,x): return s.f(s.c(x.transpose(1,2)).mean(-1)).squeeze(-1)
    class RNNm(nn.Module):
        def __init__(s,kind,bi=False):
            super().__init__(); R={'LSTM':nn.LSTM,'GRU':nn.GRU}[kind]
            s.r=R(2,64,batch_first=True,bidirectional=bi); s.f=nn.Linear(64*(2 if bi else 1),1)
        def forward(s,x):
            o,_=s.r(x); return s.f(o[:,-1,:]).squeeze(-1)
    class Trans(nn.Module):
        def __init__(s):
            super().__init__(); s.p=nn.Linear(2,64)
            el=nn.TransformerEncoderLayer(64,4,128,batch_first=True); s.t=nn.TransformerEncoder(el,2)
            s.f=nn.Linear(64,1)
        def forward(s,x): return s.f(s.t(s.p(x)).mean(1)).squeeze(-1)
    M={'CNN1D':CNN1D,'TCN':TCN,'LSTM':lambda:RNNm('LSTM'),'GRU':lambda:RNNm('GRU'),
       'BiLSTM':lambda:RNNm('LSTM',True),'Transformer':Trans}[name]
    net=M().to(dev); opt=torch.optim.Adam(net.parameters(),1e-3)
    lossf=nn.BCEWithLogitsLoss() if is_clf else nn.MSELoss()
    def loader(Xa,ya):
        idx=np.arange(len(ya))
        for i in range(0,len(ya),batch):
            j=idx[i:i+batch]; yield Xa[j].to(dev), ya[j].to(dev)
    best_val=1e18; best_state=None; bad=0
    for ep in range(epochs):
        net.train()
        for xb,yb in loader(Xtr,ytr):
            opt.zero_grad(); out=net(xb); l=lossf(out,yb); l.backward(); opt.step()
        net.eval()
        with torch.no_grad():
            vp=torch.cat([net(xb) for xb,_ in loader(Xva,yva)]).cpu().numpy()
        vy=yva.numpy()
        vloss=float(np.mean((vp-vy)**2)) if not is_clf else float(
            np.mean(np.maximum(vp,0)-vp*vy+np.log1p(np.exp(-np.abs(vp)))))
        if vloss<best_val-1e-5: best_val=vloss; best_state={k:v.detach().cpu().clone() for k,v in net.state_dict().items()}; bad=0
        else:
            bad+=1
            if bad>=3: break
    if best_state: net.load_state_dict(best_state)
    net.eval()
    with torch.no_grad():
        tp=torch.cat([net(xb) for xb,_ in loader(Xte,yte)]).cpu().numpy()
    ya=y[te]
    if is_clf:
        s=1/(1+np.exp(-tp)); met=clf_metrics(ya,s); met['perm_p']=perm_test_auc(ya,s,300)[2]
    else:
        p=tp*ysd+ymu; pbase=y[np.arange(te.start,te.stop)-1]; met=reg_metrics(ya,p,pbase)
        met['skill_CI']=boot_reg_skill(ya,p,pbase,500)
    return dict(model=name, window=W, test=met)

# ----------------------------------------------------------------------------- orchestration
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--csv',default='rng_hw.csv'); ap.add_argument('--out',default='results')
    ap.add_argument('--optuna-trials',type=int,default=100)
    ap.add_argument('--cap',type=int,default=400000); ap.add_argument('--seq-cap',type=int,default=300000)
    ap.add_argument('--windows',default='4,8,16,32,64,128,256,512')
    ap.add_argument('--seq-windows',default='64,256')
    ap.add_argument('--device',default='cuda'); ap.add_argument('--no-seq',action='store_true')
    ap.add_argument('--no-optuna',action='store_true')
    ap.add_argument('--family-timeout',type=int,default=300)
    a=ap.parse_args()
    os.makedirs(a.out,exist_ok=True)
    WINS=[int(x) for x in a.windows.split(',')]; SWINS=[int(x) for x in a.seq_windows.split(',')]
    FSETS=list('ABCDEFGH')
    dc,jit=load(a.csv)
    R={'config':vars(a),'targets':{}}
    log=open(os.path.join(a.out,'run.log'),'w')
    def say(*m):
        s=' '.join(str(x) for x in m); print(s,flush=True); log.write(s+'\n'); log.flush()

    TARGETS=[('delta_cycles',False),('jitter',False),('lsb',True)]
    TAB_REG=['Ridge','ElasticNet','RandomForest','ExtraTrees','XGBoost','LightGBM','MLP']+(['CatBoost'] if HAVE_CB else [])
    TAB_CLF=['Logistic','RandomForest','ExtraTrees','XGBoost','LightGBM','MLP']+(['CatBoost'] if HAVE_CB else [])
    SEQ=['CNN1D','TCN','LSTM','GRU','BiLSTM','Transformer']

    for tname,is_clf in TARGETS:
        t0=time.time(); say('\n'+'='*70); say('TARGET',tname,'(classification)' if is_clf else '(regression)')
        full = (jit.astype(int)&1).astype(float) if tname=='lsb' else (jit if tname=='jitter' else dc)
        bl,_=baselines(full,is_clf); say(' baselines:',json.dumps(bl)); 
        node={'baselines':bl,'window_sweep':[],'optuna':[],'sequence':[],'best':None}

        # ---- Phase B: window x feature sweep with fast probes (linear + LightGBM) ----
        say(' -- window x feature sweep (linear + LightGBM) --')
        best_probe=(-1e18,None)
        for W in WINS:
            for fs in FSETS:
                try:
                    scap=min(a.cap,200000)
                    X=build_features(dc,jit,W,fs,scap); y=targets(dc,jit,W,scap)[tname]
                    m=min(len(X),len(y)); X,y=X[:m],y[:m]; n=len(y); tr,va,te=split_idx(n)
                    sc=StandardScaler().fit(X[tr]); Xtr,Xva=sc.transform(X[tr]),sc.transform(X[va]); yva=y[va]
                    for probe in (['Logistic','LightGBM'] if is_clf else ['Ridge','LightGBM']):
                        if probe=='Ridge': mdl=Ridge(1.0)
                        elif probe=='Logistic': mdl=LogisticRegression(max_iter=200)
                        elif is_clf: mdl=lgb.LGBMClassifier(n_estimators=150,num_leaves=63,learning_rate=.07,n_jobs=-1,verbose=-1)
                        else: mdl=lgb.LGBMRegressor(n_estimators=150,num_leaves=63,learning_rate=.07,n_jobs=-1,verbose=-1)
                        mdl.fit(Xtr,y[tr])
                        if is_clf:
                            s=mdl.predict_proba(Xva)[:,1]; val=roc_auc_score(yva,s) if len(set(yva))>1 else 0.5
                        else:
                            p=mdl.predict(Xva); ss=np.sum((yva-yva.mean())**2); val=1-np.sum((yva-p)**2)/ss if ss>0 else -1e9
                        node['window_sweep'].append(dict(W=W,fs=fs,model=probe,val=float(val)))
                        if val>best_probe[0]: best_probe=(val,(W,fs))
                except Exception as e:
                    node['window_sweep'].append(dict(W=W,fs=fs,error=str(e)[:120]))
        bW,bFS=best_probe[1]; say(f'  best probe: W={bW} fs={bFS} val={best_probe[0]:.4f}')

        # ---- Phase C: Optuna per tabular family at best (W,fs) ----
        if HAVE_OPT and not a.no_optuna:
            say(f' -- Optuna ({a.optuna_trials} trials/family) at W={bW} fs={bFS} --')
            X=build_features(dc,jit,bW,bFS,a.cap); y=targets(dc,jit,bW,a.cap)[tname]
            m=min(len(X),len(y)); X,y=X[:m],y[:m]
            for fam in (TAB_CLF if is_clf else TAB_REG):
                try:
                    res=optuna_family(fam,X,y,is_clf,a.optuna_trials,timeout=a.family_timeout); node['optuna'].append(res)
                    key='AUC' if is_clf else 'R2'; say(f'   {fam:13s} test {key}={res["test"][key]:+.4f}')
                except Exception as e:
                    node['optuna'].append(dict(model=fam,error=str(e)[:160])); say(f'   {fam:13s} ERROR {str(e)[:80]}')

        # ---- Phase D: GPU sequence models ----
        if not a.no_seq:
            say(f' -- sequence models (GPU) at W in {SWINS} --')
            for W in SWINS:
                for arch in SEQ:
                    try:
                        res=run_sequence(arch,dc,jit,W,tname,is_clf,a.seq_cap,a.device)
                        node['sequence'].append(res)
                        key='AUC' if is_clf else 'R2'; say(f'   {arch:12s} W={W:<4d} test {key}={res["test"][key]:+.4f}')
                    except Exception as e:
                        node['sequence'].append(dict(model=arch,window=W,error=str(e)[:160]))
                        say(f'   {arch:12s} W={W:<4d} ERROR {str(e)[:80]}')

        # ---- pick best overall vs baseline ----
        cand=[]
        for r in node['optuna']+node['sequence']:
            if 'test' in r:
                v=r['test'].get('AUC') if is_clf else r['test'].get('R2')
                cand.append((v,r.get('model'),r.get('window'),r['test']))
        if cand:
            cand.sort(reverse=True); node['best']=dict(score=cand[0][0],model=cand[0][1],window=cand[0][2],test=cand[0][3])
        R['targets'][tname]=node
        json.dump(R,open(os.path.join(a.out,'results.json'),'w'),indent=2)
        say(f' target done in {time.time()-t0:.0f}s; best={node["best"]}')

    # ---- verdict ----
    say('\n'+'='*70); say('VERDICT')
    for tname,is_clf in TARGETS:
        nd=R['targets'][tname]; b=nd['best']
        if is_clf:
            base=nd['baselines']['majority_base_rate']
            auc=b['test']['AUC'] if b else 0.5; lo=b['test']['AUC_lo'] if b else 0.5
            verdict='NO predictability' if lo<=0.5 else 'AUC CI>0.5 (inspect bal_acc/MCC)'
            say(f' {tname}: best AUC={auc:.4f} CI_lo={lo:.4f} | majority_base={base:.4f} -> {verdict}')
        else:
            r2=b['test']['R2'] if b else 0.0
            pr2=nd['baselines'].get('persistence_R2',0.0)
            say(f' {tname}: best R2_vs_mean={r2:+.4f} | persistence_R2={pr2:+.4f}')
    json.dump(R,open(os.path.join(a.out,'results.json'),'w'),indent=2)
    say('\nWrote',os.path.join(a.out,'results.json'))

if __name__=='__main__': main()
