# Source audit — what is open vs. closed for cuPQC `get_entropy()`

**Finding: there is no open-source `get_entropy()` anywhere.** The generator is
inside the proprietary static library `libcupqc.a`. Every open wrapper merely
calls it and trusts it.

## What the documentation says (and doesn't)

NVIDIA's host-function reference defines only:

```
template<class PK> uint8_t* get_entropy(size_t batch, cudaStream_t stream = 0);
//  "Allocate and fill the entropy buffer using cryptographically secure
//   randomness for the algorithm described by PK, sufficient for batch batches."
```

It never names the CSPRNG, never states the seeding source, and never states
whether internal state is reseeded per call or merely advanced. The Security
Notes page covers side-channel review (MLSCA, KyberSlash, NTT access patterns)
but says nothing about the entropy generator. `release_entropy()` frees the
buffer — which tells you nothing about generator-state lifetime.

## The liboqs cuPQC backend (Apache-2.0, open) confirms the gap

`open-quantum-safe/liboqs-cupqc-meta` contains the production backend — three
near-identical files `cuda/ml-kem-{512,768,1024}/cupqc_ml-kem.cu`. The entropy
handling is exactly:

```c
workspace   = make_workspace<MLKEM_Keygen>(1);
randombytes = get_entropy<MLKEM_Keygen>(1);     // opaque; body in libcupqc.a
...
keygen_kernel<<<1, MLKEM_Keygen::BlockDim>>>(d_pk, d_sk, workspace, randombytes);
...
release_entropy(randombytes);
```

A grep of the entire repo for `curand|entropy|random|seed|rng|getrandom|/dev/`
returns nothing but the parameter name `randombytes`.

### Two consequences worth recording

1. **The liboqs GPU path bypasses `OQS_randombytes`.** On the CPU side liboqs
   lets you substitute the RNG (system / OpenSSL / custom) via
   `OQS_randombytes_custom_algorithm`. The cuPQC backend never routes that into
   the kernel — it calls cuPQC's own `get_entropy()` directly. Any audit of the
   liboqs RNG does **not** cover the GPU path.
2. **`batch=1`, allocate→fill→`release_entropy` per operation.** Fresh buffer
   each call, but buffer lifetime ≠ generator-state lifetime; reseed semantics
   remain invisible from source.

## Conclusion

The wrappers are open; the RNG is not; reading source cannot name it. It must be
probed at runtime — which is what this repository does.
