# What this experiment shows

The deterministic source — whose key is printed in the output and which
`predict.py` reconstructs byte-for-byte — scored 7.87 bits/byte and passed
all 19 IID tests. The real laptop jitter scored 2.57 bits and failed 7 of
the 19. The completely predictable source did not just pass; it beat the
genuine one by more than five bits.

The tests are not broken. They measure statistical structure: bias,
repetition, short-range correlation, compressibility. A good cryptographic
PRG (BLAKE2b) is built to have none of those, so it always scores near the
maximum. The battery has no notion of "the adversary knows the key."
Predictability and statistical structure are different things; 90B only
sees the second.

The lesson is asymmetric. The predictors CAN catch a source with too little
entropy (the jitter case: 2.57, IID rejected). They CANNOT catch a source
with zero real entropy dressed up by a strong PRG (the BLAKE case: 7.87,
clean pass). Low entropy is visible; cryptographic predictability is invisible.

Threat-model consequence: swapping a real-but-mediocre noise source for a
backdoored PRG keyed by something the attacker knows makes the 90B / AIS 31
scores go UP. An evaluator running only the black-box battery would read the
improvement as the RNG getting better, at the exact moment it became fully
predictable. This is the operational form of AIS 31 §4.6.2 — statistical
tests can falsify a stochastic model but never confirm one; a passing battery
is necessary, never sufficient.

Side note: ~2.5 bits/sample with failed IID is normal for raw tight-loop
timing jitter on bare metal. This is why Linux never trusts a single jitter
measurement — security comes from accumulating many samples and conditioning
through BLAKE2s + ChaCha20, not from any one sample being high-entropy.
