# Improvement v3 -> v4

**Rule:** If a patient uses an ambiguous date like 'next Wednesday', clarify which specific date they mean before offering appointment times or booking.

**Why:** The assistant booked an appointment for the wrong date when the patient's request was ambiguous, leading to an incorrect booking.

**Learned from:** `20261007-204958-v3-without-R1` + re-run `20261007-211902-v3-rebaseline` (check `end_state`)

**Gate:** passed

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
| S12 Changes mind before confirming | train | 5/6 | 3/3 | ⬆ |
| S13 Ambiguous "next Wednesday" (target) | train | 3/6 | 3/3 | ⬆ |
| S14 Parent books for a child | holdout | 3/3 | 3/3 |  |
| S15 Vague cancel inside 24h, patient pushes back | train | 3/3 | 3/3 |  |
| S16 Ambiguous "next Wednesday" (means this week) | train | 3/3 | 3/3 |  |

Agent cost per conversation: $0.004974 -> $0.004983; turn latency p95: 23095 -> 5716 ms (reported, not gated).

Paired sign test over scenarios: 2 improved, 0 worsened, p = 0.500 (a suite-wide claim needs p < 0.05, i.e. at least 6 scenarios moving one way; smaller changes are judged on the targeted scenarios and the holdout). Rates are compared because the baseline is pooled.

Before (pooled): train {'pass_rate': 0.911, 'all_pass': '11/13'}, holdout {'pass_rate': 1.0, 'all_pass': '3/3'}

After: train {'pass_rate': 1.0, 'all_pass': '13/13'}, holdout {'pass_rate': 1.0, 'all_pass': '3/3'}
