"""Runner behaviour that does not need a real model: the code, not the simulator, decides when it is over."""

from types import SimpleNamespace as NS

from evals.runner import run_trial
from evals.scenario import load_scenarios
from tests.test_agent import reply, tool_call
from yourhealth.agent import load_config


class RouterLLM:
    """Agent calls carry `tools`; simulator calls do not. Each side gets its own script."""

    def __init__(self, agent_script, patient_script):
        self.agent, self.patient = list(agent_script), list(patient_script)
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        if "tools" in kw:
            return self.agent.pop(0)
        return NS(choices=[NS(message=NS(content=self.patient.pop(0)))])


def test_premature_done_is_overruled_while_a_proposal_is_pending():
    sc = next(s for s in load_scenarios() if s.id == "S01")
    agent = [
        reply(calls=[tool_call("verify_patient", full_name="Priya Sharma", date_of_birth="1990-04-12")]),
        reply(calls=[tool_call("propose_booking", slot_id="P1-20261019-0900", reason="checkup")]),
        reply("Shall I book Monday 19 October at 9:00 with Dr Rao?"),
        reply(calls=[tool_call("confirm_pending")]),
        reply("Done, I've booked you for Monday at 9:00."),
    ]
    patient = [
        "Priya Sharma, 12 April 1990, checkup with Dr Rao Monday 9:00",
        "[DONE]",  # quits while the assistant is waiting for a yes: not allowed
        "Yes, please book it.",
        "[DONE]",  # after the booking: allowed
    ]
    trace = run_trial(sc, load_config(), 1, RouterLLM(agent, patient))
    assert trace["sim_stop_overruled"] == 1
    assert [t["patient"] for t in trace["turns"]][-1] == "Yes, please book it."
    assert any(a["status"] == "booked" and a["id"] not in trace["before"] for a in trace["after"].values())
