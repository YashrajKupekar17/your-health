# How I used AI, and where my judgment led

Most of the design decisions, research and implementation direction were my own. I used Claude Code
primarily for code implementation and for summarizing relevant theory and literature, to understand
what needed to be implemented and why. I validated the important decisions and claims against the
actual traces and evaluation results rather than relying on the generated output.

## Where AI helped

- **Code implementation.** The agent, tools, eval harness, improvement loop and UI, with tests, built
  and reviewed one stage at a time.
- **Summarizing theory and literature.** How production scheduling agents, eval suites and improvement
  loops are built (Hippocratic AI, Zocdoc, Sierra, Intercom, Arize, tau-bench), condensed so I could
  decide what to build and why.
- **Running evals.** Launching suites and pulling the failing traces for me to read.

## Where my judgment overrode it

1. **I kept the scope to what the brief grades.** Every research round suggested more: an LLM safety
   supervisor, judge calibration, visit types and insurance, prompt-search algorithms, canary
   releases. I built only what demonstrates judgment and listed the rest as what production adds.
2. **I pushed back on streaming.** The AI argued replies should not stream, only progress labels.
   I wanted the page to feel live. The result: replies stream sentence by sentence, and each sentence
   passes the safety check before the patient sees it, which is also how a voice agent should feed
   text-to-speech.
3. **I kept voice focused.** From my voice-agent work, I added speech-to-text input rather than
   rebuilding a full voice stack, and transcribed on the server rather than in the browser, because
   Chrome's built-in speech recognition sends patient audio to Google.
4. **I picked one change from the target company's product, not ten.** Comparing against 2Care's
   scheduling page surfaced ten gaps. I implemented the one that matched their stated behaviour:
   handoffs carry the caller's own words.
5. **I asked "what are we doing wrong?" about my own loop.** That exposed two real flaws: the gate was
   deciding on single-run noise, and failing scenarios would "improve" by chance on any re-run
   (regression to the mean). Both are fixed in the loop.
6. **I only claim what the evidence supports.** The design note keeps the inconclusive suite-wide test
   (p = 0.625) and a live bug that never reproduced in simulation, instead of tuning scenarios until
   the numbers looked good.

## How checking caught mistakes

Validating results against traces and live runs caught:

- a simulated patient quitting before saying "yes", and another leaking its hidden goal, which had
  been hiding a real date bug in the agent;
- the LLM judge describing a question the agent never asked, which is why pass/fail comes from code
  and the judge is only advisory;
- a missed suicide phrase ("ending my life") in the emergency filter, found by building a labelled
  test set;
- a race condition that let two patients book the same slot, now covered by a test that fails
  without the lock.
