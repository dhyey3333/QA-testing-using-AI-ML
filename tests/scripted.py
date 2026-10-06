"""A model that follows a script, so tests exercise the real loop, browser and judge
plumbing in seconds, with no GPU, separately from how smart a real model is."""

from __future__ import annotations

from nightshift.observe import Element, Observation


class ScriptedModel:
    """Plays a fixed script, finding each element by its label the way a person would.

    Moves: ("click", "label"), ("type", "label", "text"), ("select", "label", "option"), ("raw", {...reply...}).
    A label may be prefixed with a tag: "button:Log in". When the script runs out
    the agent says pass. The judge quotes `evidence`; with `holds` left as None it
    answers honestly (true only if every quote is on the page), and a bool makes it lie.
    """

    name = "scripted"

    def __init__(self, script=(), evidence=(), holds: bool | None = None, forbid_agent: bool = False,
                 visual: dict | None = None):
        self.visual = visual  # the answer to a visual check; None: this model can't look at pictures
        self.looks = 0
        self.script = list(script)
        self.evidence = list(evidence)
        self.holds = holds
        self.forbid_agent = forbid_agent
        self.decisions = 0
        self.judgements = 0

    def decide(self, context):
        if self.forbid_agent:
            raise AssertionError("the agent was called during a run that should have been a pure replay")
        self.decisions += 1
        if not self.script:
            return {"action": "pass", "reason": "script finished"}
        move = self.script[0]
        if move[0] == "raw":
            self.script.pop(0)
            return move[1]
        kind, target, *rest = move
        element = find(context.observation, target)
        if element is None:
            return {"action": "wait"}  # the page hasn't caught up yet
        self.script.pop(0)
        reply = {"action": kind, "id": element.id}
        if kind == "type":
            reply["text"] = rest[0]
        elif kind == "select":
            reply["value"] = rest[0]
        return reply

    def judge(self, context):
        self.judgements += 1
        text = context.observation.text
        honest = bool(self.evidence) and all(quote in text for quote in self.evidence)
        holds = honest if self.holds is None else self.holds
        evidence = self.evidence if self.holds is not None else [q for q in self.evidence if q in text]
        return {"checks": [{"expected": e, "evidence": evidence, "why": "scripted", "holds": holds}
                           for e in context.spec.expect]}

    def ask(self, system, user, max_tokens=1500):
        raise NotImplementedError

    def look(self, system, user, image, max_tokens=300):
        if self.visual is None:
            raise AttributeError("this scripted model has no visual answer")
        self.looks += 1
        return self.visual


def find(observation: Observation, target: str) -> Element | None:
    tag, _, label = target.rpartition(":")  # "button:Log in", or just "Cart ("
    for element in observation.elements:
        if label.lower() in element.label.lower() and (not tag or element.tag == tag):
            return element
    return None
