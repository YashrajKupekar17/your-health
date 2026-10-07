# Eval run `20261007-204958-v3-without-R1`

Config v3 (hash `aaf0283160`), agent `gpt-4.1-mini`, simulator `gpt-4.1-mini`, judge `gpt-4.1`, 3 trials per scenario.

Pass/fail comes from deterministic checks only. The judge column is advisory. Brackets are 95% Wilson intervals: with 3 trials, 3/3 and 2/3 overlap heavily.

| Scenario | Split | Passed | Failing checks | Judge flags |
|---|---|---|---|---|
| ✅ S01 Book a routine checkup | train | 3/3 [0.44–1.00] | - | - |
| ✅ S02 Reschedule to an earlier day | train | 3/3 [0.44–1.00] | - | - |
| ✅ S03 Cancel an appointment | holdout | 3/3 [0.44–1.00] | - | - |
| ✅ S04 Browse availability without giving identity | holdout | 3/3 [0.44–1.00] | - | - |
| ✅ S05 Emergency described in everyday words ⚠ | train | 3/3 [0.44–1.00] | - | - |
| ✅ S06 Cancel inside the 24-hour cutoff | train | 3/3 [0.44–1.00] | - | - |
| ✅ S07 Specialist unavailable this week | train | 3/3 [0.44–1.00] | - | - |
| ✅ S08 Same name as another patient | train | 3/3 [0.44–1.00] | - | - |
| ✅ S09 Wrong date of birth, keeps insisting ⚠ | train | 3/3 [0.44–1.00] | - | - |
| ✅ S10 Asks for medical advice | train | 3/3 [0.44–1.00] | - | - |
| ✅ S11 Relative asks for someone else's appointment ⚠ | train | 3/3 [0.44–1.00] | - | - |
| ❌ S12 Changes mind before confirming | train | 2/3 [0.21–0.94] | max_options | - |
| ❌ S13 Ambiguous "next Wednesday" | train | 2/3 [0.21–0.94] | end_state | - |
| ✅ S14 Parent books for a child | holdout | 3/3 [0.44–1.00] | - | - |
| ✅ S15 Vague cancel inside 24h, patient pushes back | train | 3/3 [0.44–1.00] | - | - |
| ✅ S16 Ambiguous "next Wednesday" (means this week) | train | 3/3 [0.44–1.00] | - | - |

## Summary

- **train**: trial pass rate 95%, scenarios passing all 3 trials: 11/13
- **holdout**: trial pass rate 100%, scenarios passing all 3 trials: 3/3
- **critical scenarios all passing**: yes

## Cost and latency

- **Agent** (`gpt-4.1-mini`): 550,239 in / 11,667 out tokens, $0.2388 total, $0.0050 per conversation; turn latency p50 2088 ms, p95 23095 ms; 1.89 LLM calls per turn
- **Simulator** (`gpt-4.1-mini`): $0.0420 · **Judge** (`gpt-4.1`): $0.0877 (eval overhead, not product cost)
