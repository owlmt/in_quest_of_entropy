#!/usr/bin/env python3
"""
nist_90b_tests.py - NIST SP 800-90B IID tests and min-entropy estimators.

Implements:
  * Permutation battery (§5.1) - 11 statistics, 19 instances with the
    p-lag variants of periodicity and covariance
  * Min-entropy estimators (§6.3.1, .5, .6, .7, .8, .9, .10)
    - non-binary: MCV, t-Tuple, LRS, MultiMCW, Lag, MultiMMC, LZ78Y

Binary-only estimators (Collision §6.3.2, Markov §6.3.3, Compression §6.3.4)
are intentionally omitted -- our jitter source is multi-bit per sample.

Maps to AIS 31 v3.0 Tirn:
    MultiMMC predictor (§6.3.9)  <-->  Tirn T3
    LZ78Y predictor   (§6.3.10)  <-->  Tirn T4
"""

import sys
import math
import argparse
import bz2
from collections import Counter, defaultdict
import numpy as np

ALPHA_PERM = 0.001
Z_99 = 2.576  # one-sided 99% confidence (Z_{1-0.005})


# ============================================================
# §5.1 Permutation test statistics
# ============================================================

def _excursion(s):
    mean = s.mean()
    return float(np.max(np.abs(np.cumsum(s - mean))))

def _signs_directional(s):
    return np.where(s[:-1] <= s[1:], 1, -1)

def _signs_median(s):
    med = np.median(s)
    return np.where(s < med, -1, 1)

def _num_runs(signs):
    if len(signs) == 0: return 0
    return int(np.sum(np.diff(signs) != 0)) + 1

def _longest_run(signs):
    if len(signs) == 0: return 0
    boundaries = np.concatenate(([0],
                                 np.where(np.diff(signs) != 0)[0] + 1,
                                 [len(signs)]))
    return int(np.max(np.diff(boundaries)))

def _num_dir_runs(s):      return _num_runs(_signs_directional(s))
def _len_dir_runs(s):      return _longest_run(_signs_directional(s))
def _num_inc_dec(s):
    signs = _signs_directional(s)
    return int(max(np.sum(signs > 0), np.sum(signs < 0)))
def _num_runs_median(s):   return _num_runs(_signs_median(s))
def _len_runs_median(s):   return _longest_run(_signs_median(s))

def _collisions(s):
    L = len(s)
    c = []
    i = 0
    while i < L:
        seen = {}
        j = i
        found = False
        while j < L:
            if s[j] in seen:
                c.append(j - i + 1)
                found = True
                break
            seen[s[j]] = True
            j += 1
        if not found:
            break
        i = j + 1
    return c

def _avg_collision(s):
    c = _collisions(s)
    return float(np.mean(c)) if c else 0.0

def _max_collision(s):
    c = _collisions(s)
    return float(max(c)) if c else 0.0

def _periodicity(s, p):
    if p >= len(s): return 0
    return int(np.sum(s[:-p] == s[p:]))

def _covariance(s, p):
    if p >= len(s): return 0
    return float(np.sum(s[:-p].astype(np.int64) * s[p:].astype(np.int64)))

def _compression(s):
    encoded = ' '.join(str(int(x)) for x in s).encode('ascii')
    return len(bz2.compress(encoded))


PERM_TESTS = [
    ('excursion',        _excursion),
    ('num_dir_runs',     _num_dir_runs),
    ('len_dir_runs',     _len_dir_runs),
    ('num_inc_dec',      _num_inc_dec),
    ('num_runs_median',  _num_runs_median),
    ('len_runs_median',  _len_runs_median),
    ('avg_collision',    _avg_collision),
    ('max_collision',    _max_collision),
    ('periodicity_p1',   lambda s: _periodicity(s, 1)),
    ('periodicity_p2',   lambda s: _periodicity(s, 2)),
    ('periodicity_p8',   lambda s: _periodicity(s, 8)),
    ('periodicity_p16',  lambda s: _periodicity(s, 16)),
    ('periodicity_p32',  lambda s: _periodicity(s, 32)),
    ('covariance_p1',    lambda s: _covariance(s, 1)),
    ('covariance_p2',    lambda s: _covariance(s, 2)),
    ('covariance_p8',    lambda s: _covariance(s, 8)),
    ('covariance_p16',   lambda s: _covariance(s, 16)),
    ('covariance_p32',   lambda s: _covariance(s, 32)),
    ('compression',      _compression),
]


def run_permutation_battery(samples, n_perm=10000, subset=20000, seed=0):
    """
    Run §5.1 battery. Permutation testing on 1M samples × 10k shuffles × 19
    tests is hours of wall time; subset to first `subset` samples by default.
    Decision rule (§5.1): reject IID if (C0+C1 <= 5) or (C0 >= 9995).
    """
    s = np.array(samples[:subset], dtype=np.int64)
    if len(s) < 100:
        return {}
    print(f"  permutation battery: {len(s):,} samples, {n_perm} shuffles, "
          f"{len(PERM_TESTS)} statistics")
    originals = {name: fn(s) for name, fn in PERM_TESTS}
    C0 = {name: 0 for name, _ in PERM_TESTS}
    C1 = {name: 0 for name, _ in PERM_TESTS}
    rng = np.random.default_rng(seed)

    for k in range(n_perm):
        if k and k % max(n_perm // 10, 1) == 0:
            print(f"    shuffle {k}/{n_perm}", file=sys.stderr)
        perm = rng.permutation(s)
        for name, fn in PERM_TESTS:
            T = fn(perm)
            orig = originals[name]
            if T > orig:
                C0[name] += 1
            elif T == orig:
                C1[name] += 1

    results = {}
    for name, _ in PERM_TESTS:
        c0, c1 = C0[name], C1[name]
        rejected = (c0 + c1 <= 5) or (c0 >= 9995)
        results[name] = {
            'verdict': 'REJECT' if rejected else 'pass',
            'C0': c0, 'C1': c1, 'T': originals[name],
        }
    return results


# ============================================================
# §6.3.1 Most Common Value
# ============================================================

def mcv_estimate(s):
    L = len(s)
    counts = Counter(s)
    p_hat = max(counts.values()) / L
    p_upper = min(1.0, p_hat + Z_99 * math.sqrt(p_hat * (1 - p_hat) / (L - 1)))
    return -math.log2(p_upper)


# ============================================================
# §6.3.5 t-Tuple Estimate
# ============================================================

def t_tuple_estimate(s, threshold=35, t_max=50):
    L = len(s)
    s = list(s)
    P_max = []
    t = 0
    for cur_t in range(1, min(L, t_max) + 1):
        tuples = Counter()
        for i in range(L - cur_t + 1):
            tuples[tuple(s[i:i+cur_t])] += 1
        mc = max(tuples.values())
        if mc < threshold:
            break
        t = cur_t
        P = mc / (L - cur_t + 1)
        P_max.append(P ** (1.0 / cur_t))
    if t == 0:
        return None
    p_hat_max = max(P_max)
    p_upper = min(1.0, p_hat_max + Z_99 * math.sqrt(p_hat_max * (1 - p_hat_max) / (L - 1)))
    return -math.log2(p_upper)


# ============================================================
# §6.3.6 LRS Estimate
# ============================================================

def lrs_estimate(s, threshold=35, t_max=50):
    L = len(s)
    s = list(s)
    # u: smallest t such that most-common t-tuple count < threshold
    u = None
    for cur_t in range(1, min(L, t_max) + 1):
        tuples = Counter()
        for i in range(L - cur_t + 1):
            tuples[tuple(s[i:i+cur_t])] += 1
        mc = max(tuples.values())
        if mc < threshold:
            u = cur_t
            break
    if u is None:
        return None
    # v: largest t where some tuple repeats at least twice
    v = None
    for cur_t in range(u, min(L, t_max) + 1):
        tuples = Counter()
        for i in range(L - cur_t + 1):
            tuples[tuple(s[i:i+cur_t])] += 1
        if max(tuples.values()) >= 2:
            v = cur_t
        else:
            break
    if v is None or v < u:
        return None
    Pmax = []
    for W in range(u, v + 1):
        tuples = Counter()
        for i in range(L - W + 1):
            tuples[tuple(s[i:i+W])] += 1
        numer = sum(c * (c - 1) // 2 for c in tuples.values())
        denom = (L - W + 1) * (L - W) // 2
        if denom == 0:
            continue
        P_W = numer / denom
        Pmax.append(P_W ** (1.0 / W))
    if not Pmax:
        return None
    p_hat = max(Pmax)
    p_upper = min(1.0, p_hat + Z_99 * math.sqrt(p_hat * (1 - p_hat) / (L - 1)))
    return -math.log2(p_upper)


# ============================================================
# Predictor common: P_local via the §6.3 recurrence + binary search
# ============================================================

def _predictor_p_local(N, r):
    """
    Solve 0.99 = (1 - P*x)/((r+1 - r*x)*q) * 1/x^(N+1) for P (a.k.a. P_local).

    Numerical strategy: eq(P) is monotone decreasing in P on (0,1).
    eq(0+) -> 1 and eq(1-) -> 0. Use log-space when x grows, which is
    the NIST-suggested robustness fix.
    """
    if r <= 1:
        return 1e-9
    def log_eq(P):
        # Returns log10 of the RHS, or None if infeasible.
        if P <= 0 or P >= 1:
            return None
        q = 1.0 - P
        x = 1.0
        for _ in range(10):
            x = 1.0 + q * (P ** r) * (x ** (r + 1))
        # When x > 1, x^(N+1) can overflow. Use logs throughout.
        # log eq = log(1-P*x) - log((r+1-r*x)*q) - (N+1)*log(x)
        num = 1.0 - P * x
        denom = (r + 1 - r * x) * q
        if num <= 0 or denom <= 0 or x <= 0:
            return float('-inf')  # eq -> 0
        return math.log10(num) - math.log10(denom) - (N + 1) * math.log10(x)
    target = math.log10(0.99)
    lo, hi = 1e-12, 1.0 - 1e-12
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        v = log_eq(mid)
        if v is None:
            break
        if v > target:
            lo = mid       # eq still too high -> increase P
        else:
            hi = mid       # eq too low -> decrease P
    return 0.5 * (lo + hi)


def _predictor_entropy(correct, k):
    N = len(correct)
    C = int(sum(correct))
    P_g = C / N
    if P_g == 0:
        P_gu = 1 - 0.01 ** (1.0 / N)
    else:
        P_gu = min(1.0, P_g + Z_99 * math.sqrt(P_g * (1 - P_g) / (N - 1)))
    longest = 0
    cur = 0
    for c in correct:
        if c:
            cur += 1
            if cur > longest:
                longest = cur
        else:
            cur = 0
    r = longest + 1
    P_loc = _predictor_p_local(N, r)
    best = max(P_gu, P_loc, 1.0 / k)
    return -math.log2(best), {
        'P_global': P_g,
        'P_global_upper': P_gu,
        'P_local': P_loc,
        'longest_run': longest,
        '1/k': 1.0/k,
    }


# ============================================================
# §6.3.7 MultiMCW Predictor
# ============================================================

def multimcw(s):
    windows = [63, 255, 1023, 4095]
    w1 = windows[0]
    L = len(s)
    if L <= w1:
        return None
    N = L - w1
    correct = [0] * N
    scoreboard = [0, 0, 0, 0]
    winner = 0
    s = list(s)

    for i in range(w1, L):
        frequent = [None, None, None, None]
        for j, w in enumerate(windows):
            if i > w:
                window = s[i-w:i]
                cnt = Counter(window)
                mc = max(cnt.values())
                cands = {v for v, c in cnt.items() if c == mc}
                if len(cands) == 1:
                    frequent[j] = next(iter(cands))
                else:
                    for v in reversed(window):
                        if v in cands:
                            frequent[j] = v
                            break
        prediction = frequent[winner]
        if prediction is not None and prediction == s[i]:
            correct[i - w1] = 1
        for j in range(4):
            if frequent[j] == s[i]:
                scoreboard[j] += 1
                if scoreboard[j] >= scoreboard[winner]:
                    winner = j

    k = len(set(s))
    return _predictor_entropy(correct, k)


# ============================================================
# §6.3.8 Lag Predictor
# ============================================================

def lag_predictor(s, D=128):
    L = len(s)
    if L <= 1:
        return None
    N = L - 1
    correct = [0] * N
    scoreboard = [0] * D
    winner = 0
    s = list(s)

    for i in range(1, L):
        lag = [None] * D
        for d in range(1, D + 1):
            if d < i + 1:
                lag[d-1] = s[i-d]
        prediction = lag[winner]
        if prediction is not None and prediction == s[i]:
            correct[i-1] = 1
        for d in range(D):
            if lag[d] == s[i]:
                scoreboard[d] += 1
                if scoreboard[d] >= scoreboard[winner]:
                    winner = d

    k = len(set(s))
    return _predictor_entropy(correct, k)


# ============================================================
# §6.3.9 MultiMMC Predictor (= AIS 31 Tirn T3)
# ============================================================

def multimmc(s, D=16, maxEntries=100_000):
    L = len(s)
    if L < 3:
        return None
    N = L - 2
    correct = [0] * N
    scoreboard = [0] * D
    winner = 0
    models = [defaultdict(dict) for _ in range(D)]
    entries = [0] * D
    s = list(s)

    for i in range(2, L):
        # Update models with the new observation (transition ending at i-1)
        for d in range(1, D + 1):
            if d < i:
                ctx = tuple(s[i-d-1:i-1])
                nxt = s[i-1]
                bucket = models[d-1].get(ctx)
                if bucket is not None:
                    if nxt in bucket:
                        bucket[nxt] += 1
                    elif entries[d-1] < maxEntries:
                        bucket[nxt] = 1
                        entries[d-1] += 1
                elif entries[d-1] < maxEntries:
                    models[d-1][ctx] = {nxt: 1}
                    entries[d-1] += 1
        # Predict s[i]
        subpred = [None] * D
        for d in range(1, D + 1):
            if d < i + 1:
                ctx = tuple(s[i-d:i])
                bucket = models[d-1].get(ctx)
                if bucket:
                    mc = max(bucket.values())
                    ymax = max(y for y, c in bucket.items() if c == mc)
                    subpred[d-1] = ymax
        prediction = subpred[winner]
        if prediction is not None and prediction == s[i]:
            correct[i-2] = 1
        for d in range(D):
            if subpred[d] == s[i]:
                scoreboard[d] += 1
                if scoreboard[d] >= scoreboard[winner]:
                    winner = d

    k = len(set(s))
    return _predictor_entropy(correct, k)


# ============================================================
# §6.3.10 LZ78Y Predictor (= AIS 31 Tirn T4)
# ============================================================

def lz78y(s, B=16, maxDict=65_536):
    L = len(s)
    if L <= B + 1:
        return None
    N = L - B - 1
    correct = [0] * N
    D = {}
    dict_size = 0
    s = list(s)

    for i in range(B + 1, L):
        # Update dictionary with all suffixes from length B down to 1 ending at i-2
        for j in range(B, 0, -1):
            ctx = tuple(s[i-j-1:i-1])
            nxt = s[i-1]
            if ctx not in D:
                if dict_size < maxDict:
                    D[ctx] = {}
                    dict_size += 1
                else:
                    continue
            D[ctx][nxt] = D[ctx].get(nxt, 0) + 1
        # Predict
        pred = None
        maxc = 0
        for j in range(B, 0, -1):
            prev = tuple(s[i-j:i])
            bucket = D.get(prev)
            if bucket:
                mc = max(bucket.values())
                y = max(yv for yv, cv in bucket.items() if cv == mc)
                if mc > maxc:
                    pred = y
                    maxc = mc
        if pred is not None and pred == s[i]:
            correct[i - B - 1] = 1

    k = len(set(s))
    return _predictor_entropy(correct, k)


# ============================================================
# Driver
# ============================================================

def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('input', help='Raw sample file (output of collect_entropy.py)')
    p.add_argument('--no-perm', action='store_true',
                   help='Skip the permutation battery (slow)')
    p.add_argument('--perm-subset', type=int, default=20000,
                   help='Sample count for permutation tests (default 20k)')
    p.add_argument('--perm-shuffles', type=int, default=10000,
                   help='Number of shuffles per permutation test (default 10k)')
    p.add_argument('--max-samples', type=int, default=None,
                   help='Truncate input to N samples for estimators')
    args = p.parse_args()

    with open(args.input, 'rb') as f:
        data = f.read()
    samples = list(data)
    if args.max_samples:
        samples = samples[:args.max_samples]

    L = len(samples)
    k = len(set(samples))

    print("=" * 64)
    print(f"NIST SP 800-90B analysis : {args.input}")
    print("=" * 64)
    print(f"  L (samples) : {L:,}")
    print(f"  k (alphabet): {k}")
    print(f"  log2(k)     : {math.log2(k):.3f}  (theoretical max bits/sample)")
    print()

    iid_rejected = True  # default to non-IID track unless permutation passes everything

    if not args.no_perm:
        print("[1] IID assumption -- permutation battery (§5.1)")
        print("-" * 64)
        results = run_permutation_battery(
            samples,
            n_perm=args.perm_shuffles,
            subset=args.perm_subset,
        )
        any_reject = False
        for name, info in results.items():
            mark = "FAIL" if info['verdict'] == 'REJECT' else " ok "
            print(f"  [{mark}] {name:18s}  C0={info['C0']:5d}  C1={info['C1']:5d}  T={info['T']}")
            if info['verdict'] == 'REJECT':
                any_reject = True
        iid_rejected = any_reject
        print()
        print(f"  -> IID assumption: {'REJECTED' if any_reject else 'NOT rejected'}")
        print(f"  -> Track: {'non-IID (run all estimators, take min)' if any_reject else 'IID (MCV only)'}")
        print()

    print("[2] Min-entropy estimators (§6.3)")
    print("-" * 64)
    est = {}

    h = mcv_estimate(samples)
    est['MCV (§6.3.1)'] = h
    print(f"  MCV       (§6.3.1) : {h:.4f}")

    if not iid_rejected and not args.no_perm:
        # IID track: only MCV is used
        final = h
    else:
        h = t_tuple_estimate(samples)
        if h is not None:
            est['t-Tuple (§6.3.5)'] = h
            print(f"  t-Tuple   (§6.3.5) : {h:.4f}")
        h = lrs_estimate(samples)
        if h is not None:
            est['LRS (§6.3.6)'] = h
            print(f"  LRS       (§6.3.6) : {h:.4f}")

        for name, fn in [
            ('MultiMCW (§6.3.7)',  multimcw),
            ('Lag      (§6.3.8)',  lag_predictor),
            ('MultiMMC (§6.3.9)',  multimmc),
            ('LZ78Y    (§6.3.10)', lz78y),
        ]:
            print(f"  running {name} ...", file=sys.stderr)
            r = fn(samples)
            if r is None:
                continue
            h, det = r
            est[name] = h
            print(f"  {name} : {h:.4f}  "
                  f"(P_g={det['P_global']:.4f}, P_g_up={det['P_global_upper']:.4f}, "
                  f"P_loc={det['P_local']:.4f}, run={det['longest_run']})")

        final = min(est.values())

    print()
    print("=" * 64)
    print(f"Final min-entropy estimate: {final:.4f} bits/sample")
    bits_per_byte = final  # since each sample is one byte here
    print(f"  ({final/math.log2(k)*100:.1f}% of theoretical maximum log2(k)={math.log2(k):.2f})")
    if bits_per_byte < 1.0:
        print(f"  -> ~{1.0/bits_per_byte:.1f} samples needed per bit of seed entropy")
    print("=" * 64)


if __name__ == '__main__':
    main()
