from helpers.extension import Extension
from agent import LoopData
from helpers import plugins

# Dynamic import to avoid hard dependency issues.
# Note: A0 v1.10+ ships the core memory plugin under `plugins._memory`
# (underscore prefix marks it as a core plugin). We try both forms for
# backwards compatibility with older A0 versions where it was `plugins.memory`.
try:
    from plugins._memory.extensions.python.message_loop_prompts_after._50_recall_memories import (
        DATA_NAME_TASK as DATA_NAME_TASK_MEMORIES,
        DATA_NAME_ITER as DATA_NAME_ITER_MEMORIES,
    )
except ImportError:
    try:
        # Legacy A0 (pre-1.10) used `plugins.memory` without the underscore.
        from plugins.memory.extensions.python.message_loop_prompts_after._50_recall_memories import (  # type: ignore
            DATA_NAME_TASK as DATA_NAME_TASK_MEMORIES,
            DATA_NAME_ITER as DATA_NAME_ITER_MEMORIES,
        )
    except ImportError:
        # Fallback constants if the memory plugin isn't installed at all.
        DATA_NAME_TASK_MEMORIES = "memory_recall_task"
        DATA_NAME_ITER_MEMORIES = "memory_recall_iter"


class RecallWait(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent:
            return

        settings = plugins.get_plugin_config("_memory", self.agent) or {}

        task = self.agent.get_data(DATA_NAME_TASK_MEMORIES)
        last_iter = self.agent.get_data(DATA_NAME_ITER_MEMORIES) or 0

        if task and not task.done():
            # If memory recall is set to delayed mode, do not await on the iteration it was called
            if settings.get("memory_recall_delayed"):
                if last_iter == loop_data.iteration:
                    delay_text = self.agent.read_prompt("memory.recall_delay_msg.md")
                    loop_data.extras_temporary["memory_recall_delayed"] = delay_text
                    return

            # Otherwise await the task
            try:
                await task
            except Exception as e:
                print(f"[KAME Shield] Recall task exception: {e}")
