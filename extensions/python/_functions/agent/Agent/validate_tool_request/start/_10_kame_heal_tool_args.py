"""KAME Shield — Tool Validation Heal.

Extension hook on Agent.validate_tool_request/start.
Heals null/empty tool_args BEFORE the framework's validator runs,
preventing ValueError crashes from LLM-generated malformed JSON.

v1.0.7 adds a response-tool heal: upstream Agent Zero's tools/response.py
does `self.args["text"] ... else self.args["message"]`, which raises
KeyError: 'message' when the model emits a response call with empty args
(seen with Codex-style models). KAME now guarantees the response tool
always receives a usable "text" key, salvaging common wrong-key variants
("content", "answer", "response", "answer_text") and coercing null values.

This approach preserves the @extensible decorator (unlike monkey-patching).
"""

from helpers.extension import Extension

# Wrong key names some models use instead of "text"/"message", in salvage order.
_RESPONSE_SALVAGE_KEYS = ("content", "answer", "response", "answer_text")


def heal_response_args(tool_request):
    """Ensure a response-tool request has a usable text/message argument.

    Mutates tool_request["tool_args"] in place. Assumes tool_args is a dict.
    """
    if tool_request.get("tool_name") != "response":
        return
    args = tool_request["tool_args"]
    # Coerce null values on the keys upstream reads ({"text": null} etc.)
    for key in ("text", "message"):
        if key in args and args[key] is None:
            args[key] = ""
    if "text" in args or "message" in args:
        return
    # Neither key present: salvage a wrong-key variant, else inject empty text
    # so upstream response.py can't KeyError.
    for key in _RESPONSE_SALVAGE_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            args["text"] = value
            return
    args["text"] = ""


class KameHealToolArgs(Extension):
    async def execute(self, data: dict = {}, **kwargs):
        try:
            # data["args"] = (self, tool_request)
            # data["kwargs"] = {}
            args = data.get("args", ())
            if len(args) >= 2:
                tool_request = args[1]
                if isinstance(tool_request, dict):
                    # Heal missing or null tool_args
                    if "tool_args" not in tool_request or tool_request.get("tool_args") is None:
                        tool_request["tool_args"] = {}
                    # Heal wrong type (e.g. string "{}")
                    if isinstance(tool_request.get("tool_args"), str):
                        try:
                            import json
                            tool_request["tool_args"] = json.loads(tool_request["tool_args"])
                        except (json.JSONDecodeError, TypeError):
                            tool_request["tool_args"] = {}
                    # Heal non-dict results of the above (e.g. json.loads("[]"))
                    if not isinstance(tool_request.get("tool_args"), dict):
                        tool_request["tool_args"] = {}
                    # v1.0.7: response-tool specific heal (KeyError: 'message')
                    heal_response_args(tool_request)
        except Exception:
            pass  # Never crash the agent loop
