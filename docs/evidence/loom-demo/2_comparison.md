# Improvement v3 -> v5

**Rule:** If a patient request for an appointment date is ambiguous (such as 'next Wednesday'), clarify which specific date they mean before offering or booking any times.

**Why:** The assistant booked an appointment for the wrong date when the patient's request was ambiguous, leading to incorrect scheduling. Clarifying ambiguous date references will prevent this error.

**Learned from:** `20261007-231004-v3-baseline` + re-run `20261007-231220-v3-rebaseline` (check `end_state`)

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
| S12 Changes mind before confirming | train | 3/3 | 3/3 |  |
| S13 Ambiguous "next Wednesday" (target) | train | 2/6 | 3/3 | ⬆ |
| S14 Parent books for a child | holdout | 3/3 | 3/3 |  |
| S15 Vague cancel inside 24h, patient pushes back | train | 3/3 | 3/3 |  |
| S16 Ambiguous "next Wednesday" (means this week) | train | 3/3 | 3/3 |  |

Agent cost per conversation: $0.004636 -> $0.005179; turn latency p95: 4239 -> 4420 ms (reported, not gated).

Paired sign test over scenarios: 1 improved, 0 worsened, p = 1.000 (a suite-wide claim needs p < 0.05, i.e. at least 6 scenarios moving one way; smaller changes are judged on the targeted scenarios and the holdout). Rates are compared because the baseline is pooled.

Before (pooled): train {'pass_rate': 0.905, 'all_pass': '12/13'}, holdout {'pass_rate': 1.0, 'all_pass': '3/3'}

After: train {'pass_rate': 1.0, 'all_pass': '13/13'}, holdout {'pass_rate': 1.0, 'all_pass': '3/3'}
