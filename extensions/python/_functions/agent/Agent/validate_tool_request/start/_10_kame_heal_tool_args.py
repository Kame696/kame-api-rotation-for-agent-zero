"""KAME Shield — Tool Validation Heal.

Extension hook on Agent.validate_tool_request/start.
Heals null/empty tool_args BEFORE the framework's validator runs,
preventing ValueError crashes from LLM-generated malformed JSON.

This approach preserves the @extensible decorator (unlike monkey-patching).
"""

from helpers.extension import Extension


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
        except Exception:
            pass  # Never crash the agent loop
