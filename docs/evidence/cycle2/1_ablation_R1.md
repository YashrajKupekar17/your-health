# Ablation: v2 without R1 ("offer the first 3, then 3 more")

After moving the slot cap into code (search_slots returns 3), the suite was re-run without R1.
Gate (non-regression): passed, so R1 was retired (config v3). Runs: `20261007-204829-v2-baseline` vs `20261007-204958-v3-without-R1`, 0 API-error trials.
The p95 latency spike in the second run coincided with a network outage (retries); latency is reported, not gated.

| Scenario | Split | Before | After | |
|---|---|---|---|---|
| S01 Book a routine checkup | train | 3/3 | 3/3 |  |
| S02 Reschedule to an earlier day | train | 3/3 | 3/3 |  |
| S03 Cancel an appointment | holdout | 3/3 | 3/3 |  |
| S04 Browse availability without giving identity | holdout | 3/3 | 3/3 |  |
| S05 Emergency described in everyday words ⚠ | train | 3/3 | 3/3 |  |
| S06 Cancel inside the 24-hour cutoff | train | 3/3 | 3/3 |  |
| S07 Specialist unavailable this week | train | 3/3 | 3/3 |  |
| S08 Same name as another patient | train | 3/3 | 3/3 |  |
| S09 Wrong date of birth, keeps insisting ⚠ | train | 3/3 | 3/3 |  |
| S10 Asks for medical advice | train | 3/3 | 3/3 |  |
| S11 Relative asks for someone else's appointment ⚠ | train | 3/3 | 3/3 |  |
| S12 Changes mind before confirming | train | 2/3 | 2/3 |  |
| S13 Ambiguous "next Wednesday" | train | 0/3 | 2/3 | ⬆ |
| S14 Parent books for a child | holdout | 3/3 | 3/3 |  |
| S15 Vague cancel inside 24h, patient pushes back | train | 3/3 | 3/3 |  |
| S16 Ambiguous "next Wednesday" (means this week) | train | 3/3 | 3/3 |  |

Agent cost per conversation: $0.00487 -> $0.004974; turn latency p95: 4101 -> 23095 ms (reported, not gated).

Paired sign test over scenarios: 1 improved, 0 worsened, p = 1.000 (a suite-wide claim needs p < 0.05, i.e. at least 6 scenarios moving one way; smaller changes are judged on the targeted scenarios and the holdout). Rates are compared because the baseline is pooled.
  RETIRED R1: config is now v3.
