# Design Note

Diagrams: [ARCHITECTURE.md](ARCHITECTURE.md) · Run instructions: [README](../README.md) in the repo
https://github.com/YashrajKupekar17/your-health

Loom → https://www.loom.com/share/00daaab06de54819b0b0bcc23705d4a5

## Key Design Choices

**Hard rules live in code; the model handles the conversation.** This separation ensures a confused or
manipulated model cannot bypass critical constraints.

- **Identity before data:** Patient data is inaccessible until name + DOB match exactly one record.
  Wrong name/DOB return the same error; 3 failures lock the session; 2 matching records trigger staff
  handoff.
- **Two-step writes:** `propose_*` never writes state. `confirm_pending` only works on a later patient
  message and re-checks availability under a lock before committing.
- **Layered safety:** An emergency filter runs before the model (14/14 explicit red flags detected,
  2/10 false alarms). Every output sentence passes through a guard that blocks unsupported claims such
  as "you're booked" when no booking exists, or exposure of another patient's data.
- **Narrow tools:** Tools return structured errors with next steps, and searches return at most 3 real
  slots.
- **Grade what happened:** Evaluation reads database state and tool logs rather than trusting
  transcripts. This catches failures such as claiming a booking that was never created or accessing
  data before verification.
- **16 scenarios:** Includes an LLM patient simulator, 3 hidden holdouts, 3 safety scenarios that must
  pass every run, and "twin" scenarios where only asking for clarification can reliably succeed.

## Improvement Loop

1. Re-run failures alongside the baseline to distinguish real failures from noise.
2. Classify the fix as code, tool, eval, or instruction. Only instruction problems become one short
   rule; rules are linted to prevent test/patient-specific content.
3. Test on the failing scenarios, then the full suite. Keep changes only when the target improves,
   nothing regresses, and all safety scenarios continue to pass.
4. A human approves the change; versions are logged with rollback support. `ablate` periodically
   re-tests old rules and retires rules that no longer help.

## Results

| Change | Before | After |
|---|---|---|
| Offer 3 times at a time | 6/42; hidden S04: 0/3 | 1/42; S04: 3/3 |
| Move cap into code | Prompt rule | Rule removed; no regression |
| Ask when date is ambiguous | S13: 3/6 | S13: 3/3; twin S16: 3/3 |

Safety scenarios passed every run. I do not claim a suite-wide statistical win (p=0.625, then 0.5);
the claim is targeted improvements, hidden-scenario improvement, and no observed regressions.

## What I Would Change for a Real Clinic

**Test on real conversations, not only simulations.** A live chat exposed a cancellation-rule failure
that never reproduced in the simulated suite. In production, I would sample de-identified conversations
daily, have staff label failures, and convert them into regression scenarios before the next release.

## AI Usage & Voice Experience

I have worked on voice agents previously, so for this version I kept the voice layer focused on
speech-to-text transcription rather than rebuilding the complete voice stack. I am also working on an
end-to-end voice agent using a cascading STT → LLM → TTS pipeline, which informs how I approach latency,
turn-taking, and streaming decisions here.

Most of the design decisions, research, and implementation direction were my own. I used Claude Code
primarily for code implementation and for summarizing relevant theory and literature to help understand
what needed to be implemented and why. I validated the important decisions and claims against the
actual traces and evaluation results rather than relying on the generated output. More detail:
[AI_USAGE.md](AI_USAGE.md).

**Core principle:** use the model for conversation; use deterministic code for anything that must be
correct.
