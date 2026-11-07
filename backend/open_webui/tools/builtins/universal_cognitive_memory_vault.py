'''"""Open Web Vibes builtin: Universal Cognitive Memory Vault

Generated from the WebUI tool export `universal_cognitive_memory_vault` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: Universal Cognitive Memory Vault
Exported functions: knowledge_guidance, list_paths, read_path, recall, remember, update_memories

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
from typing import Any, Callable, Awaitable, Optional

class Tools:
    """Durable-memory facade over Open WebUI's native built-in Memory functions."""

    @staticmethod
    async def _emit(emitter: Optional[Callable[[dict[str, Any]], Awaitable[None]]], description: str, done: bool) -> None:
        if not callable(emitter):
            return
        try:
            await emitter({'type': 'status', 'data': {'description': description, 'done': done}})
        except Exception:
            pass

    @staticmethod
    def _memory_tools():
        from open_webui.tools.builtin import add_memory, list_memory_paths, read_memory_path, search_memories, update_memory
        return (add_memory, search_memories, list_memory_paths, read_memory_path, update_memory)

    async def remember(self, content: str, type: str='user', path: str='', __request__: Any=None, __user__: dict=None, __event_emitter__: Optional[Callable[[dict[str, Any]], Awaitable[None]]]=None) -> str:
        """
        Save durable information using Open WebUI's native Memory system.
        Use user for stable facts/preferences/instructions about the user and context for durable project context.
        Use path to group related memories. Do not put long documents or chapters here.
        """
        if __request__ is None:
            return '[MEMORY ERROR] Open WebUI request context is unavailable.'
        if not (content or '').strip():
            return '[MEMORY ERROR] Content is empty.'
        if type not in {'user', 'context'}:
            type = 'user'
        await self._emit(__event_emitter__, 'Saving durable memory…', False)
        try:
            add_memory, _, _, _, _ = self._memory_tools()
            result = await add_memory(content=content.strip(), type=type, path=path.strip() or None, __request__=__request__, __user__=__user__ or {})
            await self._emit(__event_emitter__, 'Memory saved.', True)
            return result
        except Exception as exc:
            await self._emit(__event_emitter__, 'Memory save failed.', True)
            return f'[MEMORY ERROR] {exc}'

    async def recall(self, query: str='', count: int=8, type: str='all', path: str='', memory_id: str='', __request__: Any=None, __user__: dict=None) -> str:
        """
        Search Open WebUI's native persistent Memory by meaning, content, path, or exact memory ID.
        """
        if __request__ is None:
            return '[MEMORY ERROR] Open WebUI request context is unavailable.'
        try:
            _, search_memories, _, _, _ = self._memory_tools()
            return await search_memories(query=query or '', count=max(1, min(int(count), 50)), type=type if type in {'user', 'context', 'all'} else 'all', path=path.strip() or None, memory_id=memory_id.strip() or None, __request__=__request__, __user__=__user__ or {})
        except Exception as exc:
            return f'[MEMORY ERROR] {exc}'

    async def list_paths(self, query: str='', count: int=100, type: str='all', __request__: Any=None, __user__: dict=None) -> str:
        """List native Open WebUI Memory path groups."""
        if __request__ is None:
            return '[MEMORY ERROR] Open WebUI request context is unavailable.'
        try:
            _, _, list_memory_paths, _, _ = self._memory_tools()
            return await list_memory_paths(query=query or '', count=max(1, min(int(count), 500)), type=type if type in {'user', 'context', 'all'} else 'all', __request__=__request__, __user__=__user__ or {})
        except Exception as exc:
            return f'[MEMORY ERROR] {exc}'

    async def read_path(self, path: str, count: int=50, type: str='all', include_children: bool=True, __request__: Any=None, __user__: dict=None) -> str:
        """Read native Memory entries at a path, optionally including child paths."""
        if __request__ is None:
            return '[MEMORY ERROR] Open WebUI request context is unavailable.'
        if not path.strip():
            return '[MEMORY ERROR] A memory path is required.'
        try:
            _, _, _, read_memory_path, _ = self._memory_tools()
            return await read_memory_path(path=path.strip(), count=max(1, min(int(count), 100)), type=type if type in {'user', 'context', 'all'} else 'all', include_children=bool(include_children), __request__=__request__, __user__=__user__ or {})
        except Exception as exc:
            return f'[MEMORY ERROR] {exc}'

    async def update_memories(self, operations: list[dict], __request__: Any=None, __user__: dict=None) -> str:
        """
        Apply native Memory add/replace/move/remove operations in one request.
        Prefer replace/move/remove when changing an existing memory to avoid duplicates.
        """
        if __request__ is None:
            return '[MEMORY ERROR] Open WebUI request context is unavailable.'
        if not isinstance(operations, list) or not operations:
            return '[MEMORY ERROR] operations must be a non-empty array.'
        try:
            _, _, _, _, update_memory = self._memory_tools()
            return await update_memory(operations=operations, __request__=__request__, __user__=__user__ or {})
        except Exception as exc:
            return f'[MEMORY ERROR] {exc}'

    async def knowledge_guidance(self) -> str:
        """
        Explain the intended division of labor with native Knowledge Bases.
        This function does not duplicate kb_exec; Open WebUI injects kb_exec natively when enabled.
        """
        return "Memory: durable facts, preferences, standing instructions, goals, and project context. Knowledge Base: long-form source material and documents. For attached Knowledge Bases, use Open WebUI's native query_knowledge_files for semantic retrieval. When ENABLE_KB_EXEC=True, Open WebUI also exposes native kb_exec for exact filesystem-style work: ls, tree, cat, head, tail, grep, find, wc, stat, sed, and pipelines. Do not create a second disk-backed JSON RAG database or ingest chapters into this tool."
_builtin_tools = Tools()

async def remember(self, content: str, type: str='user', path: str='', __request__: Any=None, __user__: dict=None, __event_emitter__: Optional[Callable[[dict[str, Any]], Awaitable[None]]]=None) -> str:
    """
        Save durable information using Open WebUI's native Memory system.
        Use user for stable facts/preferences/instructions about the user and context for durable project context.
        Use path to group related memories. Do not put long documents or chapters here.
        """
    return await _builtin_tools.remember(content=content, type=type, path=path, __request__=__request__, __user__=__user__, __event_emitter__=__event_emitter__)

async def recall(self, query: str='', count: int=8, type: str='all', path: str='', memory_id: str='', __request__: Any=None, __user__: dict=None) -> str:
    """
        Search Open WebUI's native persistent Memory by meaning, content, path, or exact memory ID.
        """
    return await _builtin_tools.recall(query=query, count=count, type=type, path=path, memory_id=memory_id, __request__=__request__, __user__=__user__)

async def list_paths(self, query: str='', count: int=100, type: str='all', __request__: Any=None, __user__: dict=None) -> str:
    """List native Open WebUI Memory path groups."""
    return await _builtin_tools.list_paths(query=query, count=count, type=type, __request__=__request__, __user__=__user__)

async def read_path(self, path: str, count: int=50, type: str='all', include_children: bool=True, __request__: Any=None, __user__: dict=None) -> str:
    """Read native Memory entries at a path, optionally including child paths."""
    return await _builtin_tools.read_path(path=path, count=count, type=type, include_children=include_children, __request__=__request__, __user__=__user__)

async def update_memories(self, operations: list[dict], __request__: Any=None, __user__: dict=None) -> str:
    """
        Apply native Memory add/replace/move/remove operations in one request.
        Prefer replace/move/remove when changing an existing memory to avoid duplicates.
        """
    return await _builtin_tools.update_memories(operations=operations, __request__=__request__, __user__=__user__)

async def knowledge_guidance(self) -> str:
    """
        Explain the intended division of labor with native Knowledge Bases.
        This function does not duplicate kb_exec; Open WebUI injects kb_exec natively when enabled.
        """
    return await _builtin_tools.knowledge_guidance()
