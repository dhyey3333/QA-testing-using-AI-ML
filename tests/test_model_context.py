"""A prompt longer than the model's context: retried in compact form, and JSON mode stays on.

Found on the public benchmark: a product grid made a 4,119-token prompt for a local Ollama with a
4,096-token context. The 400 was also mistaken for "this server rejects JSON mode", which switched
JSON mode off for every later call in the run.
"""

import json

import httpx

from nightshift.model import HttpModel, ModelConfig
from nightshift.observe import Element, Observation
from nightshift.prompts import Context, JudgeContext
from nightshift.spec import Spec

SPEC = Spec(name="grid", url="https://shop.test/", steps=("add Blue Top to the cart",), expect=("the cart shows Blue Top",))
OFF_SCREEN = tuple(Element(i, "a", f"Add to cart — product number {i} with a long name", in_view=False) for i in range(2, 80))
OBS = Observation(url="https://shop.test/", title="", text="Rs. 500 Blue Top\n" * 400,
                  elements=(Element(1, "button", "Search", in_view=True), *OFF_SCREEN))


def _model(limit_chars: int):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"][0]["text"]
        seen.append({"chars": len(prompt), "json_mode": "response_format" in body})
        if len(prompt) > limit_chars:
            return httpx.Response(400, json={"error": {"message": '{"error":{"code":400,"message":"request (4119 tokens) '
                                                                 'exceeds the available context size (4096 tokens)",'
                                                                 '"type":"exceed_context_size_error"}}'}})
        reply = {"action": "click", "id": 1} if "ELEMENTS" in prompt else {"checks": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}], "usage": {}})

    model = HttpModel(ModelConfig(base_url="http://model.test/v1", model="m"))
    model._client = httpx.Client(transport=httpx.MockTransport(handler))
    return model, seen


def test_an_oversized_agent_prompt_is_resent_compact_and_json_mode_stays_on():
    model, seen = _model(limit_chars=3_000)
    assert model.decide(Context(SPEC, OBS, (), None)) == {"action": "click", "id": 1}
    assert len(seen) == 2 and seen[1]["chars"] < seen[0]["chars"] <= 10_000 and seen[1]["chars"] <= 3_000
    # The next call goes out in JSON mode again: an overflow is not a JSON-mode problem.
    model.decide(Context(SPEC, OBS, (), None))
    assert all(call["json_mode"] for call in seen)


def test_an_oversized_judge_prompt_is_resent_compact():
    model, seen = _model(limit_chars=5_000)
    assert model.judge(JudgeContext(SPEC, OBS, None)) == {"checks": []}
    assert len(seen) == 2 and seen[0]["chars"] > 5_000 >= seen[1]["chars"]
