# Eval run `20261007-212029-v4-candidate`

Config v4 (hash `8f15e3041f`), agent `gpt-4.1-mini`, simulator `gpt-4.1-mini`, judge `gpt-4.1`, 3 trials per scenario.

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
| ✅ S12 Changes mind before confirming | train | 3/3 [0.44–1.00] | - | - |
| ✅ S13 Ambiguous "next Wednesday" | train | 3/3 [0.44–1.00] | - | - |
| ✅ S14 Parent books for a child | holdout | 3/3 [0.44–1.00] | - | - |
| ✅ S15 Vague cancel inside 24h, patient pushes back | train | 3/3 [0.44–1.00] | - | - |
| ✅ S16 Ambiguous "next Wednesday" (means this week) | train | 3/3 [0.44–1.00] | - | - |

## Summary

- **train**: trial pass rate 100%, scenarios passing all 3 trials: 13/13
- **holdout**: trial pass rate 100%, scenarios passing all 3 trials: 3/3
- **critical scenarios all passing**: yes

## Cost and latency

- **Agent** (`gpt-4.1-mini`): 551,924 in / 11,523 out tokens, $0.2392 total, $0.0050 per conversation; turn latency p50 2463 ms, p95 5716 ms; 1.86 LLM calls per turn
- **Simulator** (`gpt-4.1-mini`): $0.0416 · **Judge** (`gpt-4.1`): $0.0871 (eval overhead, not product cost)
