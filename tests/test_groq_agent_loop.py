"""
Offline test of scripts/groq_agent.py's manual tool-calling loop, using a
fake Groq-shaped client (no network, no API key needed). This can't verify
Groq's own model quality, but it does prove the loop itself is correct:
tool_calls get executed against the REAL tool functions, results get threaded
back with matching tool_call_id, and a final plain-text turn ends the loop.
Run this suite anywhere; run scripts/groq_agent.py itself wherever you have
outbound network access and a GROQ_API_KEY.
"""
import json
from types import SimpleNamespace

from scripts.groq_agent import GroqAgent
from tools import inventory


class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = json.dumps(arguments)


class FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = FakeFunction(name, arguments)

    def model_dump(self):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class ScriptedFakeClient:
    """Returns a scripted sequence of responses, one per call to .create(...)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = self._responses.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_tool_call_then_final_answer_round_trips_correctly():
    inventory.add_product(name="Tata Salt 1kg", brand="Tata", unit="packet", gst_rate=0,
                           mrp=22, cost_price=18, hsn_code="2501", opening_quantity=10)

    tool_call = FakeToolCall("call_1", "get_stock", {"product_query": "tata salt"})
    responses = [
        FakeMessage(content=None, tool_calls=[tool_call]),
        FakeMessage(content="You have 10 packets of Tata Salt left.", tool_calls=None),
    ]
    client = ScriptedFakeClient(responses)
    agent = GroqAgent(client=client)

    reply = agent.send("how much salt is left?")

    assert reply == "You have 10 packets of Tata Salt left."
    assert len(agent.trace) == 1
    assert agent.trace[0]["tool"] == "get_stock"
    assert agent.trace[0]["result"]["quantity"] == 10.0

    # message threading: user -> assistant(tool_calls) -> tool(result) -> assistant(final)
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    tool_msg = agent.messages[3]
    assert tool_msg["tool_call_id"] == "call_1"
    assert json.loads(tool_msg["content"])["quantity"] == 10.0


def test_reset_clears_history_but_not_preferences():
    from tools.preferences import set_preference, get_all_preferences
    set_preference("default_payment_mode", "upi")

    client = ScriptedFakeClient([FakeMessage(content="ok", tool_calls=None)])
    agent = GroqAgent(client=client)
    agent.send("hello")
    assert len(agent.messages) > 1

    agent.reset()
    assert len(agent.messages) == 1  # just the fresh system message
    assert "default_payment_mode: upi" in agent.messages[0]["content"]
    assert get_all_preferences()["default_payment_mode"] == "upi"


def test_unknown_tool_name_does_not_crash_the_loop():
    from scripts.groq_agent import _execute_tool
    result = _execute_tool("not_a_real_tool", {})
    assert result["error_type"] == "UnknownTool"


def test_oversell_error_surfaces_as_structured_result_not_exception():
    inventory.add_product(name="Loose Sugar", unit="kg", gst_rate=0, mrp=45, cost_price=40,
                           hsn_code="1701", opening_quantity=5, is_loose=True)
    from tools import billing
    bill_id = billing.start_bill("chatX")["bill_id"]

    from scripts.groq_agent import _execute_tool
    result = _execute_tool("add_bill_item", {"bill_id": bill_id, "product_query": "sugar", "qty": 999})
    assert result["error_type"] == "OversellError"


def test_start_bill_is_chat_bound_not_left_for_the_model_to_guess():
    """
    Regression test for a real bug found during live Groq testing: start_bill
    used to require the model to supply a `chat_id` string it was never told
    anywhere in the conversation, so it invented a literal placeholder
    (observed: '<chat_id>'), producing bill IDs that got mixed up across
    turns. Fixed by binding chat_id server-side per GroqAgent (mirrors one
    ClaudeSDKClient per Telegram chat in the real submission) and dropping
    it from the tool's schema entirely.
    """
    from scripts.groq_agent import _execute_tool

    agent1 = GroqAgent(client=ScriptedFakeClient([]), chat_id="chat-1")
    agent2 = GroqAgent(client=ScriptedFakeClient([]), chat_id="chat-2")

    start_bill_schema = agent1.specs_by_name["start_bill"].schema
    assert start_bill_schema["properties"] == {}
    assert start_bill_schema["required"] == []

    result1 = _execute_tool("start_bill", {}, agent1.specs_by_name)
    result2 = _execute_tool("start_bill", {}, agent2.specs_by_name)
    assert result1["bill_id"] != result2["bill_id"]  # different chats, independent drafts

    # calling it again for the same chat reuses the open draft, doesn't
    # create a second one
    result1_again = _execute_tool("start_bill", {}, agent1.specs_by_name)
    assert result1_again["bill_id"] == result1["bill_id"]
