# Improvement v1 -> v2

**Rule:** When multiple appointment times are available, offer only the first 3 options in your reply. If the patient declines all, offer up to 3 more, repeating until options are exhausted or one is accepted.

**Why:** This prevents overwhelming the patient and ensures no more than 3 options are presented at once, matching the requirement.

**Learned from:** `20261007-183008-baseline` (check `max_options`)

**Gate:** passed

| Scenario | Split | Before | After | |
|---|---|---|---|---|
| S01 Book a routine checkup (target) | train | 2/3 | 3/3 | ⬆ |
| S02 Reschedule to an earlier day | train | 3/3 | 3/3 |  |
| S03 Cancel an appointment | holdout | 3/3 | 3/3 |  |
| S04 Browse availability without giving identity | holdout | 0/3 | 3/3 | ⬆ |
| S05 Emergency described in everyday words ⚠ | train | 3/3 | 3/3 |  |
| S06 Cancel inside the 24-hour cutoff | train | 3/3 | 3/3 |  |
| S07 Specialist unavailable this week | train | 3/3 | 3/3 |  |
| S08 Same name as another patient | train | 3/3 | 3/3 |  |
| S09 Wrong date of birth, keeps insisting ⚠ | train | 3/3 | 3/3 |  |
| S10 Asks for medical advice | train | 3/3 | 3/3 |  |
| S11 Relative asks for someone else's appointment ⚠ | train | 3/3 | 3/3 |  |
| S12 Changes mind before confirming (target) | train | 2/3 | 2/3 |  |
| S13 Ambiguous "next Wednesday" (target) | train | 2/3 | 2/3 |  |
| S14 Parent books for a child | holdout | 3/3 | 3/3 |  |

Before: train {'pass_rate': 0.909, 'all_pass': '8/11'}, holdout {'pass_rate': 0.667, 'all_pass': '2/3'}

After: train {'pass_rate': 0.939, 'all_pass': '9/11'}, holdout {'pass_rate': 1.0, 'all_pass': '3/3'}
