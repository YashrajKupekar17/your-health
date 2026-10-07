# Architecture and walkthrough notes

One section per deliverable: a diagram, then the points to make when explaining it. Numbers are
from the runs in `docs/evidence/`. Diagrams are Mermaid (GitHub renders them).

1. [System overview](#1-system-overview)
2. [The agent: one patient message, end to end](#2-the-agent-one-patient-message-end-to-end)
3. [Conversation state and the two-step write](#3-conversation-state-and-the-two-step-write)
4. [Safety layers](#4-safety-layers)
5. [Web UI: voice in, streamed text out](#5-web-ui-voice-in-streamed-text-out)
6. [Evaluation harness](#6-evaluation-harness)
7. [Improvement loop](#7-improvement-loop)
8. [Rule lifecycle and the two cycles](#8-rule-lifecycle-and-the-two-cycles)
9. [Production view: what is built vs what a deployment adds](#9-production-view)

---

## 1. System overview

```mermaid
flowchart TB
    subgraph Runtime["Runtime: make ui / make chat"]
        direction LR
        UI["Web UI<br/>chat + mic"] -->|HTTP + SSE| API["FastAPI server"]
        API --> AG["Agent loop<br/>agent.py"]
        AG --> GATE["Emergency gate<br/>safety.py"]
        AG <-->|tool calls| TOOLS["Tools + Session<br/>tools.py"]
        TOOLS --> CLINIC["Clinic rules + data<br/>clinic.py"]
        AG --> GUARD["Output guard<br/>guard.py"]
    end
    CFG[("config/agent.yaml<br/>core prompt (human) + learned_rules (loop)")]
    subgraph Offline["Offline: make eval / make improve"]
        direction LR
        SCN["Scenarios<br/>16 YAML cards"] --> RUN["Eval runner<br/>same Agent class"]
        SIM["Simulated patient"] <--> RUN
        RUN --> CHK["Deterministic checks<br/>+ advisory judge"]
        CHK --> LOOP["Improvement loop<br/>gate + human approval"]
    end
    OAI(("OpenAI<br/>agent LLM + transcription"))
    CFG -->|read per conversation| AG
    CFG -->|config under test| RUN
    LOOP -->|new version| CFG
    AG <--> OAI
    API -->|audio| OAI
```

**Say this**
- Two halves share one `Agent` class: the runtime a patient talks to, and the offline harness that
  measures it and changes it. The eval tests exactly the code the patient uses.
- The only thing the improvement loop can change is `learned_rules` in one YAML file. Core policy,
  tools and code stay human-owned.
- Principle for the whole design: **hard rules live in code; the model handles the conversation.**

---

## 2. The agent: one patient message, end to end

```mermaid
sequenceDiagram
    autonumber
    participant P as Patient (UI)
    participant S as Server
    participant A as Agent loop
    participant G as Emergency gate
    participant L as LLM (gpt-4.1-mini)
    participant T as Tools + Session
    participant C as Clinic
    participant O as Output guard
    P->>S: "I'm Priya Sharma, 12 Apr 1990. Checkup with Dr Rao next week?"
    S->>A: respond(text) [one message at a time per conversation]
    A->>A: turn += 1, keep caller's words
    A->>G: emergency_match(text)
    G-->>A: no match (a match = fixed 911/988 reply + urgent handoff, LLM never runs)
    loop up to 8 steps, 60 s turn deadline
        A->>L: history + 9 tool schemas
        L-->>A: tool call verify_patient(...)
        A-->>P: progress "Verifying your details…" (SSE)
        A->>T: dispatch()
        T->>C: find_patients(name, dob)
        C-->>T: exactly one match
        T-->>A: {ok, first_name} (never DOB or phone)
        A->>L: history + tool result
        L-->>A: tool call search_slots(...)
        A->>T: dispatch() -> clinic.search_slots -> at most 3 slots
        L-->>A: final text (streamed tokens)
    end
    A->>O: each complete sentence
    O-->>A: approved (or blocked -> safe reply)
    A-->>P: delta per sentence, then done (SSE)
```

**Say this**
- The model decides **what to try**; code decides **what is allowed**. Every tool call goes through
  `dispatch()`, which never raises: errors come back as data with a next step
  (`{ok: false, error, message}`), and an empty search returns the real next available slot.
- Every exit path is bounded and ends in a handoff, never a hung patient: 8 tool steps, a 60 s turn
  deadline, 40 turns per conversation, a 20 s per-request timeout.
- One JSON log line per turn and per tool call: outcome, tokens, latency, error codes. **No patient
  text** in logs.

---

## 3. Conversation state and the two-step write

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Unverified
    Unverified --> Verified: name + DOB match one record
    Unverified --> Locked: 3 failed attempts
    Unverified --> Handoff: 2 records match
    Locked --> Handoff
    Verified --> Pending: propose_*
    Pending --> Booked: yes, on a later turn
    Pending --> Verified: no / changes mind
    Booked --> Verified: slot re-checked + written
    Verified --> Handoff: emergency, 24h rule, asks for staff
    Handoff --> [*]
```

**Say this**
- `Session` is state the model cannot edit: verified patient, pending action, turn number, handoff.
  The patient id always comes from the session, never from the model's arguments.
- Edge cases in code: a wrong name and a wrong DOB give the *same* error (no probing which exists);
  a new proposal replaces the old one; switching to another patient clears any pending action.
- **Writes are two steps.** `propose_*` stores a pending action and returns a read-back built by code
  ("Book Dr Anita Rao on Monday 19 October, 09:00"). `confirm_pending` is refused unless the patient
  has sent a message since the proposal, and re-checks the slot under a lock.
- What code **cannot** know is whether that later message was "yes" or "no". That gap is graded by the
  eval, which is why the agent and the harness are designed together.

---

## 4. Safety layers

```mermaid
flowchart TD
    M["Patient message"] --> G{"Emergency gate<br/>(regex, before the LLM)"}
    G -->|match| E["Fixed 911/988 reply<br/>+ urgent handoff with caller's words"]
    G -->|no match| L["LLM + prompt rules<br/>(paraphrased emergencies, medical advice,<br/>injection is just text)"]
    L --> T{"Tools + Session<br/>(code-enforced)"}
    T -->|"not verified / not yours /<br/>inside 24h / slot taken"| R["Refusal as data<br/>-> model explains or hands off"]
    T -->|allowed| W["Clinic write under lock<br/>(no double booking)"]
    L --> O{"Output guard<br/>(every sentence)"}
    O -->|"'booked' with no confirm this turn,<br/>or another patient's DOB/phone"| SR["Blocked: never shown,<br/>safe reply instead"]
    O -->|ok| P["Sent to the patient"]
```

**Say this**
- Defence in depth, each layer catching what the one before can miss.
- **Gate:** tuned for recall (a false alarm costs a transfer, a miss can cost a life). Measured on a
  labelled set of 29 messages: it found a real miss ("ending my life"), now **14/14** explicit red
  flags, **2/10** false alarms (past or negated mentions). Paraphrases are the prompt's job (scenario S05).
- **Guard:** the most reported production failure of scheduling agents is "you're booked" when nothing
  was booked. The guard checks every sentence against the tool log before the patient sees it.
- Matches a production scheduler's "never books" list: outside the template, an unmatched patient,
  overwriting staff bookings, judging clinical urgency. All refused in code.

---

## 5. Web UI: voice in, streamed text out

```mermaid
sequenceDiagram
    participant B as Browser
    participant S as Server
    participant O as OpenAI
    participant A as Agent
    B->>B: tap mic: MediaRecorder (max 60 s)
    B->>S: POST /api/transcribe (audio/webm)
    S->>O: transcribe (vocabulary hint: provider names, "date of birth")
    O-->>S: text
    S-->>B: {"text": "..."} (audio not stored, only size and latency logged)
    B->>S: POST /api/session/{id}/message/stream
    S->>A: respond(text, on_progress, on_delta)
    A-->>B: event: progress "Checking availability…"
    A-->>B: event: delta "Dr Rao has Monday 19 October at 9:00, 9:30 or 10:00."
    A-->>B: event: delta "Which works for you?"
    A-->>B: event: done {reply, ended}
```

**Say this**
- **Server-side transcription**, not the browser's built-in speech API: in Chrome that sends patient
  audio to Google, a third party with no BAA. One vendor for patient data, any browser.
- The transcriber gets the provider names as a hint: names and dates are exactly what speech-to-text
  gets wrong, and a misheard DOB fails verification.
- **Why stream per sentence, not per token:** a token already shown cannot be retracted, and the guard
  must approve text before the patient sees it. Progress labels cover tool time; in one live turn the
  patient saw feedback at 1.3 s instead of waiting 5.1 s. A voice agent would feed these guarded
  sentences to text-to-speech.
- `DEMO_MODE` adds the behind-the-scenes panel; without it the API returns only the reply.

---

## 6. Evaluation harness

```mermaid
flowchart LR
    Y["scenarios.yaml<br/>card | expectations"] -->|card only| SIM["Simulated patient<br/>gpt-4.1-mini, temp 0"]
    Y -->|expectations only| CHK
    subgraph Trial["One trial (x3 per scenario, fresh clinic, parallel)"]
        SIM <-->|"conversation<br/>(runner owns the stop)"| AG["Agent<br/>(config under test)"]
        AG --> TR["Trace: turns, tool log,<br/>DB before/after, handoff"]
    end
    TR --> CHK["6 deterministic checks<br/>-> PASS / FAIL"]
    TR --> J["LLM judge (advisory)<br/>soft criteria + 'did the simulator<br/>follow its card?'"]
    CHK --> OUT{"Outcome"}
    J --> OUT
    OUT --> R["report.md: per scenario,<br/>Wilson intervals, cost, latency"]
    OUT --> F["results.json -> improvement loop<br/>(train failures only)"]
```

| Check | Reads | Catches |
|---|---|---|
| `end_state` | DB diff | wrong date, wrong patient, any extra write |
| `handoff` | session | missed escalation or needless transfer |
| `claims_match_writes` | text + tool log | "you're booked" with no booking (**invisible to a transcript-only judge**) |
| `no_leak` | text vs unverified records | social engineering ("I'm his wife") |
| `max_options` | text | overwhelming lists |
| `expected_refusal` | tool log | right end state but the rule never fired, so the patient was never told why |

```mermaid
flowchart TD
    T["Trial finished"] --> E{"API or simulator crashed?"}
    E -->|yes| I["infra_error: excluded"]
    E -->|no| C{"All 6 checks pass?"}
    C -->|yes| P["pass"]
    C -->|no| S{"Judge: simulator went off its card?"}
    S -->|yes| SE["sim_error: never fed to the loop"]
    S -->|no| F["fail: the agent's fault"]
```

**Say this**
- **Pass/fail comes only from code reading the database and tool log.** A transcript-only judge sees
  the words "you're booked" but not whether the booking exists, whether it is the right slot, or
  whether data was read before verification. Ours also described a question the agent never asked,
  so it is advisory only.
- 16 scenarios: happy paths plus hard cases (paraphrased emergency, 24h cutoff, two John Millers,
  wrong DOB, medical advice, relative fishing, injection, change of mind, ambiguous date). 3 are
  **holdout** (the loop never sees them), 3 are **critical** (must pass every trial).
- **S13/S16 are twins**: the same words "next Wednesday", opposite meanings. Guessing either week fails
  one of them; only asking passes both.
- The harness knows its limits: reading traces caught a simulator quitting before saying "yes", a
  wrong expectation, and a simulator leaking its hidden goal. The runner now owns the stop and cards
  list `do_not_volunteer` facts. With 3 trials, 3/3 has a 95% interval of [0.44, 1.00], so I report
  per scenario with intervals, never a single headline score.

---

## 7. Improvement loop

```mermaid
flowchart TD
    B["Baseline run<br/>(matches config + scenarios + checks + agent code)"] --> FL["Train failures<br/>+ the exchange where each broke"]
    FL --> RB["Re-run failing scenarios now, pool with baseline<br/>(they were picked because they failed)"]
    RB --> NR{"Failure reproduces?"}
    NR -->|no| N1["Ignored as noise"]
    NR -->|yes| PR["Proposer LLM<br/>sees failures + past rejections,<br/>never holdout or judge rubric"]
    PR --> FT{"Where does the fix belong?"}
    FT -->|"tool / code / eval"| TK["Ledger: needs_human ticket"]
    FT -->|prompt| LI{"Lint: ≤40 words, no ids/dates/names,<br/>no duplicates, ≤6 rules"}
    LI -->|fail| RJ["Rejected, reason fed back"]
    LI -->|ok| S1["Stage 1: targets x3"]
    S1 --> G1{"Targets improve, nothing drops?<br/>(a drop must reproduce on re-run)"}
    G1 -->|no| RJ
    G1 -->|yes| S2["Stage 2: full suite x3<br/>(train + holdout)"]
    S2 --> G2{"Targets improve, no reproduced drop,<br/>critical all pass?"}
    G2 -->|no| RJ
    G2 -->|yes| H{"Human approves?<br/>(--yes logged as auto)"}
    H -->|no| ST["Not applied, logged; cycle ends"]
    H -->|yes| V["New config version<br/>archived, rollback, ledger entry"]
    RJ -->|"next attempt (max 2 per cycle)"| PR
```

**Say this**
- A failure becomes a **structured** change: `{fix_type, rule, why, fixes, check}`, and only
  `fix_type = prompt` can become a rule. Countable limits and date maths belong in code, and the loop
  routes them to a human instead of patching them with words.
- The gate compares **pass rates against a pooled baseline**: re-running the failing scenarios removes
  the regression-to-the-mean bias of picking targets because they failed.
- **No regressions** means no scenario drops, confirmed by a re-run (3 trials are noisy), and every
  critical safety scenario passes every trial. The holdout is a generalisation check the proposer
  never sees.
- Matches what production does: Arize and GEPA use the same "cheap screen, then full validation"
  shape; Braintrust has a human approve each change; Sierra keeps hard guardrails apart from what
  gets tuned. Our repeated trials and critical gate are stricter than anything they document.

---

## 8. Rule lifecycle and the two cycles

```mermaid
timeline
    title Agent config versions
    v1 : Core prompt only
       : Baseline - agent lists up to 6 times per message
    v2 : Cycle 1 accepts R1 "offer the first 3, then 3 more"
       : Violations 6/42 -> 1/42, holdout S04 0/3 -> 3/3
    v3 : Cap moved into code (search_slots returns 3)
       : ablate R1 - nothing regresses, R1 retired
    v4 : Cycle 2 accepts R4 "clarify an ambiguous date before offering times"
       : S13 3/6 -> 3/3, twin S16 holds 3/3, agent now asks "14 or 21 October?"
```

```mermaid
flowchart LR
    F["Failure"] --> P["Prompt rule<br/>(fast, soft)"]
    P --> Q{"Belongs in code?"}
    Q -->|yes| C["Code fix<br/>(tool limit, validation)"]
    C --> A["ablate the rule"]
    A -->|nothing regresses| X["Retire the rule"]
    A -->|regresses| K["Keep it"]
    Q -->|no| K
```

**Say this**
- Cycle 1 shows the loop closing; the honest claim is the violation count and the holdout flip
  (suite-wide sign test p = 0.625 is inconclusive).
- Then the right fix went to the right place: the cap moved into the tool, and `ablate` proved the
  prompt rule was dead weight, so it was retired. Rules have to keep earning their place.
- Cycle 2 fixed a real bug the harness had been hiding (the simulator was leaking the answer). The
  twins prove the agent now asks instead of guessing.

---

## 9. Production view

```mermaid
flowchart LR
    subgraph Built["Built (this repo)"]
        direction TB
        b1["FastAPI app factory, /healthz, Docker"]
        b2["Session store interface<br/>(in memory, idle TTL)"]
        b3["One shared clinic, atomic writes<br/>(race test fails without the lock)"]
        b4["Timeouts, deadlines, turn cap"]
        b5["JSON logs, no patient text"]
        b6["Versioned prompts, ledger, rollback"]
        b7["CI: lint + config/scenario validation + 94 tests"]
    end
    subgraph Next["A real deployment adds"]
        direction TB
        n1["Postgres + unique (provider, start)<br/>or EHR via FHIR $find/$hold/$book"]
        n2["Redis sessions, several workers"]
        n3["Auth, rate limits, idempotency keys"]
        n4["OpenTelemetry traces, audit log"]
        n5["BAA + zero retention with the model provider"]
        n6["Replay of real conversations into the eval;<br/>canary releases with rollback triggers"]
    end
    Built --> Next
```

**Say this**
- Every piece on the left has a seam for the piece on the right: the session store is an interface,
  the clinic's lock stands in for a database constraint, the logs are already structured and free of
  patient data.
- Out of scope on purpose (stated as assumptions): visit types, insurance, registration, waitlists,
  voice telephony, proxy authority beyond knowing name + DOB.
