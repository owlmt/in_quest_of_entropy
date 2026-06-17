/*
 * entropy_probe.cu
 * ------------------------------------------------------------------------
 * Instrumented harness to extract and probe the output of NVIDIA cuPQC's
 * proprietary get_entropy() function for ML-KEM.
 *
 * cuPQC's get_entropy<PK>(batch) allocates a device buffer of
 * batch * PK::entropy_size bytes and fills it with "cryptographically
 * secure randomness" (NVIDIA's words). The body lives inside the closed
 * static library libcupqc.a; the generator is never named or specified,
 * and its reseed semantics are undocumented. This harness pulls that buffer
 * back to the host so the bytes can be subjected to statistical batteries
 * and structural tests.
 *
 * Modes:
 *   dump       (Experiment 1)  raw byte stream -> file, for NIST STS / PractRand / ent
 *   collision  (Experiment 3a) cross-call exact-block collision test
 *   batchcorr  (Experiment 4)  single batched draw -> N x block_size matrix, for
 *                              block-to-block correlation analysis
 *
 * The KEM message m (Encaps, 32 B) or seed d||z (Keygen, 64 B) is exactly the
 * per-block entropy unit, so a "block" here IS one KEM operation's randomness.
 *
 * SPDX-License-Identifier: Apache-2.0   (this harness only; cuPQC is proprietary)
 * ------------------------------------------------------------------------
 */

#include <cupqc.hpp>
#include <pk.hpp>
#include <workspace.hpp>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>
#include <ctime>

using namespace cupqc;

/* ----- Descriptors -------------------------------------------------------
 * Keygen entropy = 64-byte (d || z) seed.  Encaps entropy = 32-byte message m.
 * Swap ML_KEM_512() for ML_KEM_768()/ML_KEM_1024() to probe other parameter
 * sets (entropy_size is the same 32/64, but exercising each path is cheap). */
using MLKEM512Keygen = decltype(ML_KEM_512() + Function<function::Keygen>() + Block() + BlockDim<128>());
using MLKEM512Encaps = decltype(ML_KEM_512() + Function<function::Encaps>() + Block() + BlockDim<128>());

#define CUDA_OK(call) do {                                                      \
    cudaError_t _e = (call);                                                    \
    if (_e != cudaSuccess) {                                                    \
        fprintf(stderr, "CUDA error: %s at %s:%d\n",                            \
                cudaGetErrorString(_e), __FILE__, __LINE__);                    \
        exit(2);                                                                \
    }                                                                           \
} while (0)

/* Pull one freshly allocated+filled entropy buffer (batch blocks) to host. */
template <class PK>
static void draw(size_t batch, std::vector<uint8_t>& out) {
    const size_t es = PK::entropy_size;
    uint8_t* d = get_entropy<PK>(batch);          // <-- the opaque CSPRNG call
    CUDA_OK(cudaDeviceSynchronize());
    out.resize(batch * es);
    CUDA_OK(cudaMemcpy(out.data(), d, batch * es, cudaMemcpyDeviceToHost));
    release_entropy(d);
}

/* ----- params ----------------------------------------------------------- */
enum class Mode { Dump, Collision, BatchCorr };

struct Params {
    Mode        mode       = Mode::Dump;
    bool        keygen     = false;            // false => encaps (32B m)
    std::string out        = "out";
    uint64_t    target     = 1ull << 30;       // dump: bytes (1 GiB)
    size_t      batch      = 65536;            // dump: blocks per call
    uint64_t    calls      = 1000000;          // collision: number of get_entropy calls
    size_t      perCall    = 1;                // collision: blocks per call (1 = liboqs pattern)
    size_t      n          = 100000;           // batchcorr: blocks in single batch
};

static void write_sidecar(const Params& p, size_t block_size,
                          uint64_t blocks_emitted, uint64_t collisions) {
    cudaDeviceProp prop; int dev = 0;
    cudaGetDevice(&dev); cudaGetDeviceProperties(&prop, dev);
    int driver = 0, runtime = 0;
    cudaDriverGetVersion(&driver); cudaRuntimeGetVersion(&runtime);
    time_t now = time(nullptr);

    std::string path = p.out + ".meta.json";
    FILE* f = fopen(path.c_str(), "w");
    if (!f) { perror("sidecar"); return; }
    const char* mode = p.mode == Mode::Dump ? "dump"
                     : p.mode == Mode::Collision ? "collision" : "batchcorr";
    fprintf(f,
        "{\n"
        "  \"subject\": \"cupqc_get_entropy\",\n"
        "  \"variant\": \"%s\",\n"
        "  \"mode\": \"%s\",\n"
        "  \"block_size_bytes\": %zu,\n"
        "  \"block_bits\": %zu,\n"
        "  \"blocks_emitted\": %llu,\n"
        "  \"collisions\": %llu,\n"
        "  \"dump_target_bytes\": %llu,\n"
        "  \"batch\": %zu,\n"
        "  \"calls\": %llu,\n"
        "  \"per_call\": %zu,\n"
        "  \"n_blocks\": %zu,\n"
        "  \"gpu\": \"%s\",\n"
        "  \"cuda_driver\": %d,\n"
        "  \"cuda_runtime\": %d,\n"
        "  \"timestamp_unix\": %ld,\n"
        "  \"note\": \"cuPQC SDK version is NOT programmatically available; record it manually.\"\n"
        "}\n",
        p.keygen ? "keygen" : "encaps", mode,
        block_size, block_size * 8, (unsigned long long)blocks_emitted,
        (unsigned long long)collisions, (unsigned long long)p.target,
        p.batch, (unsigned long long)p.calls, p.perCall, p.n,
        prop.name, driver, runtime, (long)now);
    fclose(f);
    fprintf(stderr, "[meta] wrote %s\n", path.c_str());
}

/* ----- modes ------------------------------------------------------------ */
template <class PK>
static int run_dump(const Params& p) {
    const size_t es = PK::entropy_size;
    std::string path = p.out + ".bin";
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) { perror("open dump"); return 2; }

    uint64_t written = 0;
    std::vector<uint8_t> buf;
    while (written < p.target) {
        draw<PK>(p.batch, buf);
        size_t w = fwrite(buf.data(), 1, buf.size(), f);
        if (w != buf.size()) { perror("fwrite"); fclose(f); return 2; }
        written += buf.size();
        if ((written & ((1ull<<26)-1)) < buf.size())
            fprintf(stderr, "\r[dump] %.2f / %.2f MiB",
                    written/1048576.0, p.target/1048576.0);
    }
    fprintf(stderr, "\n[dump] wrote %llu bytes -> %s\n",
            (unsigned long long)written, path.c_str());
    fclose(f);
    write_sidecar(p, es, written / es, 0);
    return 0;
}

template <class PK>
static int run_collision(const Params& p) {
    const size_t es = PK::entropy_size;
    std::string bpath = p.out + ".blocks.bin";
    FILE* fb = fopen(bpath.c_str(), "wb");
    if (!fb) { perror("open blocks"); return 2; }

    /* exact full-block collision via hash map: block-bytes -> first index */
    std::unordered_map<std::string, uint64_t> seen;
    seen.reserve(p.calls * p.perCall * 2 + 16);

    uint64_t idx = 0, collisions = 0;
    std::vector<uint8_t> buf;
    FILE* clog = fopen((p.out + ".collisions.txt").c_str(), "w");

    for (uint64_t c = 0; c < p.calls; ++c) {
        draw<PK>(p.perCall, buf);                 // fresh allocate/fill/release each call
        for (size_t b = 0; b < p.perCall; ++b) {
            const uint8_t* blk = buf.data() + b * es;
            fwrite(blk, 1, es, fb);
            std::string key((const char*)blk, es);
            auto it = seen.find(key);
            if (it != seen.end()) {
                ++collisions;
                fprintf(clog, "COLLISION block #%llu == block #%llu\n",
                        (unsigned long long)idx, (unsigned long long)it->second);
                fprintf(stderr, "\n[!!] COLLISION: block %llu == %llu\n",
                        (unsigned long long)idx, (unsigned long long)it->second);
            } else {
                seen.emplace(std::move(key), idx);
            }
            ++idx;
        }
        if ((c & 0xFFFF) == 0)
            fprintf(stderr, "\r[collision] %llu / %llu calls, %llu collisions",
                    (unsigned long long)c, (unsigned long long)p.calls,
                    (unsigned long long)collisions);
    }
    fprintf(stderr, "\n[collision] %llu blocks, %llu exact collisions\n",
            (unsigned long long)idx, (unsigned long long)collisions);
    fclose(fb); fclose(clog);
    write_sidecar(p, es, idx, collisions);
    return collisions == 0 ? 0 : 1;   // nonzero exit if any collision (smoking gun)
}

template <class PK>
static int run_batchcorr(const Params& p) {
    const size_t es = PK::entropy_size;
    std::vector<uint8_t> buf;
    draw<PK>(p.n, buf);                            // single batched draw
    std::string path = p.out + ".matrix.bin";
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) { perror("open matrix"); return 2; }
    fwrite(buf.data(), 1, buf.size(), f);
    fclose(f);
    fprintf(stderr, "[batchcorr] wrote %zu x %zu matrix -> %s\n", p.n, es, path.c_str());
    write_sidecar(p, es, p.n, 0);
    return 0;
}

/* ----- dispatch / CLI --------------------------------------------------- */
template <class PK>
static int run(const Params& p) {
    switch (p.mode) {
        case Mode::Dump:      return run_dump<PK>(p);
        case Mode::Collision: return run_collision<PK>(p);
        case Mode::BatchCorr: return run_batchcorr<PK>(p);
    }
    return 3;
}

static void usage(const char* a0) {
    fprintf(stderr,
      "usage: %s <dump|collision|batchcorr> [options]\n"
      "  --variant <encaps|keygen>   block = 32B m (default) or 64B seed\n"
      "  --out <basename>            output basename (default: out)\n"
      "  --target-bytes <N>          dump: total bytes (default 1073741824)\n"
      "  --batch <B>                 dump: blocks per get_entropy call (default 65536)\n"
      "  --calls <C>                 collision: number of get_entropy calls (default 1000000)\n"
      "  --per-call <B>              collision: blocks per call (default 1 = liboqs pattern)\n"
      "  --n <N>                     batchcorr: blocks in single batch (default 100000)\n",
      a0);
}

int main(int argc, char** argv) {
    if (argc < 2) { usage(argv[0]); return 1; }
    Params p;
    std::string m = argv[1];
    if      (m == "dump")      p.mode = Mode::Dump;
    else if (m == "collision") p.mode = Mode::Collision;
    else if (m == "batchcorr") p.mode = Mode::BatchCorr;
    else { usage(argv[0]); return 1; }

    for (int i = 2; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&](const char* name) -> const char* {
            if (i + 1 >= argc) { fprintf(stderr, "missing value for %s\n", name); exit(1); }
            return argv[++i];
        };
        if      (a == "--variant")      p.keygen = (std::string(next("--variant")) == "keygen");
        else if (a == "--out")          p.out    = next("--out");
        else if (a == "--target-bytes") p.target = strtoull(next("--target-bytes"), nullptr, 10);
        else if (a == "--batch")        p.batch  = strtoull(next("--batch"), nullptr, 10);
        else if (a == "--calls")        p.calls  = strtoull(next("--calls"), nullptr, 10);
        else if (a == "--per-call")     p.perCall= strtoull(next("--per-call"), nullptr, 10);
        else if (a == "--n")            p.n      = strtoull(next("--n"), nullptr, 10);
        else { fprintf(stderr, "unknown option: %s\n", a.c_str()); usage(argv[0]); return 1; }
    }

    fprintf(stderr, "[probe] subject=cupqc_get_entropy variant=%s mode=%s\n",
            p.keygen ? "keygen" : "encaps", argv[1]);
    return p.keygen ? run<MLKEM512Keygen>(p) : run<MLKEM512Encaps>(p);
}
