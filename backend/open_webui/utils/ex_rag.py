"""Per-chat retrieval memory, the thing that survives a compaction.

Why this exists, stated plainly: context compaction is lossy by definition. A model
paraphrase, and even the verbatim digest in ex_export, both reduce the transcript to
something smaller than it was. Whatever they drop is gone from the prompt, and the agent
then re-derives work it already did, or contradicts a decision it cannot see. Journalling
helps only if something looks a fact up, and a small quant often does not think to.

This module keeps the original text outside the prompt, in a vector index, and puts the
few most relevant passages back on the next turn. Two properties matter more than the
retrieval quality:

1. The index is keyed by chat_id and survives independently of the chat record. If the
   chat is wiped, summarized, or compacted, the passages are still on disk and still
   searchable. Retention is the point, not a side effect.
2. Indexing is idempotent. Record ids are derived from chat_id, message id, and chunk
   index, so re-indexing the same conversation updates in place rather than duplicating.
   Without that, every turn would append a second copy of history and the index would
   grow quadratically while returning the same message five times.

The split here is deliberate. Everything above ``class ChatMemory`` is a pure function
that imports nothing from open_webui and does no IO, so it can be tested without a
running instance. The store is the only part that touches chromadb, numpy, or the
network-adjacent embedding model, and it is wrapped so a missing dependency degrades to
"no retrieval" rather than a broken request path.

Honest limits, so nobody has to discover them:
- The default embedder is all-MiniLM-L6-v2, 384 dimensions, trained mostly on English.
  It handles prose and tool output well and code less well. It is chosen because it is
  already cached locally, so retrieval works with the network unplugged.
- Retrieval is by cosine similarity on chunk embeddings, nothing more. There is no
  reranker, no BM25, no query expansion. It is a small honest win, not a search engine.
- Chunks are bounded by characters, and the token estimate is an approximation. The
  local model uses a Qwen tokenizer; ``tiktoken`` is used when present as a better guess
  than dividing by four, and it is still a guess.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

# Chunking defaults. 1200 characters is roughly a long paragraph to a screenful, small
# enough that one chunk is usually about one idea, large enough that a retrieved chunk
# carries context around the passage that matched.
DEFAULT_CHUNK_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 200
DEFAULT_TOP_K = 6
DEFAULT_MAX_CONTEXT_CHARS = 4000

# Below this cosine similarity a match is noise. MiniLM produces a wide spread, and a
# 0.25 floor cuts the obvious non-matches without needing a reranker to decide.
DEFAULT_MIN_SCORE = 0.25

COLLECTION_NAME = "ex_chat_memory"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384

CONTEXT_HEADER = "[ex memory: earlier passages retrieved from this chat]"


def _drop_client_cache() -> None:
    """Release chromadb's process-wide client cache for our path.

    chromadb keeps a SharedSystemClient cache separate from the per-request object. A write
    made outside the serving process (a CLI, a test, a second worker) lands in sqlite but not
    in that cached system, so the next query in a long-running server returns stale or empty
    results until the process restarts. Dropping the cache before building a handle makes
    every request see what is actually on disk. The attribute moved between versions, so it
    is looked up rather than assumed, and a failure is ignored: this is a freshness fix, not
    a correctness requirement.
    """
    try:
        from chromadb.api.shared_system_client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


def estimate_tokens(text: str) -> int:
    """Rough token count. Better than len/4 when tiktoken is importable, still a guess.

    The point is budgeting, not accounting: the number has to be stable and cheap, and it
    has to not require loading the model's own tokenizer.
    """
    if not text:
        return 0
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text) // 4)


def split_chunks(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split text into overlapping chunks on paragraph then sentence then character.

    Overlap exists so a fact sitting on a chunk boundary is still retrievable from at
    least one side. Splitting on paragraph breaks first keeps chunks semantically whole
    when the author already separated ideas, which is most of the time in chat logs.
    """
    text = (text or "").strip()
    if not text:
        return []
    chunk_chars = max(200, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))

    if len(text) <= chunk_chars:
        return [text]

    # Paragraph first, then sentence, then a hard cut. The separator is kept with the
    # piece so a chunk never starts mid-word when avoidable.
    units = [u for u in re.split(r"(\n\s*\n)", text) if u]
    if max((len(u) for u in units), default=0) > chunk_chars:
        units = []
        for para in re.split(r"\n\s*\n", text):
            if len(para) <= chunk_chars:
                units.append(para)
                continue
            for sentence in re.split(r"(?<=[.!?])\s+", para):
                if len(sentence) <= chunk_chars:
                    units.append(sentence)
                else:
                    units.extend(sentence[i : i + chunk_chars] for i in range(0, len(sentence), chunk_chars))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + len(unit) <= chunk_chars:
            current += unit
        else:
            chunks.append(current.strip())
            # Carry a tail of the previous chunk forward as overlap.
            tail = current[-overlap_chars:] if overlap_chars else ""
            current = (tail + unit) if tail and len(tail) + len(unit) <= chunk_chars else unit
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c]


def message_text(message: dict) -> str:
    """Flatten one message to indexable text.

    Tool call arguments and outputs are included because for an agent those are usually
    the most factual thing in the turn: the command that ran and what it printed. Tool
    results are stored as structured items, so they are walked rather than stringified
    into JSON noise that embeds badly.
    """
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                parts.append(str(item.get("text", "")))
            elif kind == "function_call":
                parts.append(f"tool call {item.get('name', '')}: {item.get('arguments', '')}")
            elif kind == "function_call_output":
                parts.append(f"tool output: {item.get('output', '')}")
    return "\n\n".join(p for p in parts if p).strip()


def record_id(chat_id: str, message_id: str, index: int) -> str:
    """Deterministic chunk id, so re-indexing updates instead of duplicating."""
    raw = f"{chat_id}|{message_id}|{index}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def build_records(
    chat_id: str,
    messages: Iterable[dict],
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    min_chars: int = 40,
) -> list[dict]:
    """Turn stored messages into index records. Pure, deterministic, no IO.

    System messages are skipped: they are re-sent on every request anyway, so indexing
    them only competes with real history for retrieval slots.
    """
    records: list[dict] = []
    for position, message in enumerate(messages or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "system":
            continue
        text = message_text(message)
        if len(text) < int(min_chars):
            continue
        message_id = str(message.get("id") or f"pos{position}")
        for index, chunk in enumerate(
            split_chunks(text, chunk_chars=chunk_chars, overlap_chars=overlap_chars)
        ):
            records.append(
                {
                    "id": record_id(chat_id, message_id, index),
                    "text": chunk,
                    "metadata": {
                        "chat_id": str(chat_id),
                        "message_id": message_id,
                        "role": str(role or "unknown"),
                        "index": index,
                        "position": position,
                    },
                }
            )
    return records


def score_to_keep(hits: list[dict], *, min_score: float = DEFAULT_MIN_SCORE) -> list[dict]:
    """Drop weak hits and de-duplicate near-identical text from both sides of an overlap."""
    kept: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        if float(hit.get("score") or 0.0) < float(min_score):
            continue
        text = (hit.get("text") or "").strip()
        if not text:
            continue
        fingerprint = text[:160]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        kept.append(hit)
    return kept


def format_context(
    hits: list[dict],
    *,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    header: str = CONTEXT_HEADER,
) -> str | None:
    """Render hits into one block for the prompt, or None when there is nothing to add.

    Newest-first would be wrong here: similarity order is what put them in the list, so
    the block keeps similarity order and stops at the character budget. Returning None
    rather than an empty header matters, because an empty block at the message head
    invalidates the cached prompt prefix and buys nothing.
    """
    if not hits:
        return None
    budget = max(200, int(max_chars))
    lines: list[str] = []
    used = len(header) + 1
    for hit in hits:
        text = re.sub(r"\s+", " ", (hit.get("text") or "").strip())
        if not text:
            continue
        role = hit.get("role") or "unknown"
        line = f"- ({role}) {text}"
        if used + len(line) + 1 > budget:
            remaining = budget - used - 1
            if remaining < 120:
                break
            line = line[:remaining]
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return None
    return header + "\n" + "\n".join(lines)


def should_retrieve(settings: dict | None) -> bool:
    """Gate. Retrieval is opt-in per user, and only when EX and RAG are both on."""
    settings = settings or {}
    return bool(settings.get("enabled")) and bool(settings.get("ragMemory"))


def build_query(messages: Iterable[dict], *, max_chars: int = 800) -> str:
    """The retrieval query: the most recent user turn, trimmed.

    Deliberately narrow. Embedding the whole recent window blurs the query toward the
    average of everything said, which retrieves more history-like history instead of the
    passage that answers the current question.
    """
    query = ""
    for message in reversed(list(messages or [])):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        text = message_text(message)
        if text:
            query = text
            break
    return query[: int(max_chars)].strip()


class ChatMemory:
    """Vector store over chat history. Degrades to a no-op when dependencies are absent.

    One chroma collection holds every chat, filtered by a ``chat_id`` metadata field. A
    collection per chat would isolate better but ends up as thousands of tiny sqlite
    sidecars and makes a cross-chat search impossible, which is the case that matters
    when the chat itself has been wiped.
    """

    def __init__(self, data_dir: str, *, embed_model: str = EMBED_MODEL) -> None:
        self.data_dir = str(data_dir)
        self.embed_model = embed_model
        self._client = None
        self._collection = None
        self._encoder = None
        self._unavailable_reason: str | None = None

    # ---- lazy deps, so an import failure is a downgrade not a crash -------------------

    def _ensure(self, *, refresh: bool = False) -> bool:
        """Build the collection handle, or reuse it.

        ``refresh`` re-opens it. That is required on every read, not just the first one:
        because the handle is cached on the instance, a plain early return meant the client
        cache was dropped exactly once at startup and never again, so a passage written by
        another process stayed invisible for the lifetime of the server. Measured before the
        fix: the status route counted 3 while the search route returned 0 hits.
        """
        if self._unavailable_reason:
            return False
        if self._collection is not None and not refresh:
            return True
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            _drop_client_cache()
            path = f"{self.data_dir}/ex_memory"
            self._client = chromadb.PersistentClient(
                path=path,
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
                embedding_function=None,
            )
            return True
        except Exception as exc:  # pragma: no cover - depends on the install
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            return False

    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        """Embed locally and offline. Cached model, CPU, no network call per turn."""
        if not texts:
            return []
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._encoder = SentenceTransformer(self.embed_model, device="cpu")
            except Exception as exc:  # pragma: no cover - depends on the install
                self._unavailable_reason = f"{type(exc).__name__}: {exc}"
                return None
        try:
            vectors = self._encoder.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
            return [list(map(float, v)) for v in vectors]
        except Exception as exc:  # pragma: no cover
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            return None

    # ---- public surface -------------------------------------------------------------

    def status(self) -> dict:
        return {
            "available": self._ensure() and self._unavailable_reason is None,
            "reason": self._unavailable_reason,
            "collection": COLLECTION_NAME,
            "embed_model": self.embed_model,
            "dimensions": EMBED_DIM,
            "path": f"{self.data_dir}/ex_memory",
        }

    def index_messages(
        self,
        chat_id: str,
        messages: Iterable[dict],
        *,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
        batch_size: int = 64,
    ) -> dict:
        """Index a chat, idempotently. Returns counts, never raises into the request path."""
        if not chat_id:
            return {"indexed": 0, "skipped": 0, "error": "no chat_id"}
        if not self._ensure(refresh=True):
            return {"indexed": 0, "skipped": 0, "error": self._unavailable_reason}

        records = build_records(
            chat_id,
            messages,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
        )
        if not records:
            return {"indexed": 0, "skipped": 0, "error": None}

        try:
            existing = set(self._collection.get(where={"chat_id": str(chat_id)}, include=[])["ids"])
        except Exception:
            existing = set()

        fresh = [r for r in records if r["id"] not in existing]
        skipped = len(records) - len(fresh)
        if not fresh:
            return {"indexed": 0, "skipped": skipped, "error": None}

        indexed = 0
        for start in range(0, len(fresh), batch_size):
            batch = fresh[start : start + batch_size]
            vectors = self._embed([r["text"] for r in batch])
            if vectors is None:
                return {"indexed": indexed, "skipped": skipped, "error": self._unavailable_reason}
            try:
                self._collection.add(
                    ids=[r["id"] for r in batch],
                    documents=[r["text"] for r in batch],
                    embeddings=vectors,
                    metadatas=[r["metadata"] for r in batch],
                )
                indexed += len(batch)
            except Exception as exc:
                return {
                    "indexed": indexed,
                    "skipped": skipped,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        return {"indexed": indexed, "skipped": skipped, "error": None}

    def search(
        self,
        query: str,
        *,
        chat_id: str | None = None,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> list[dict]:
        """Nearest chunks, optionally scoped to one chat.

        ``chat_id=None`` searches everything, which is the recovery path: the chat is
        gone from the UI but its passages are still here.
        """
        query = (query or "").strip()
        if not query:
            return []
        if not self._ensure(refresh=True):
            return []
        vectors = self._embed([query])
        if not vectors:
            return []
        where = {"chat_id": str(chat_id)} if chat_id else None
        try:
            result = self._collection.query(
                query_embeddings=vectors,
                n_results=max(1, int(top_k)),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            return []

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[dict] = []
        for text, meta, distance in zip(documents, metadatas, distances):
            meta = meta or {}
            # Cosine space: distance = 1 - similarity.
            hits.append(
                {
                    "text": text or "",
                    "score": round(1.0 - float(distance), 4),
                    "role": meta.get("role"),
                    "chat_id": meta.get("chat_id"),
                    "message_id": meta.get("message_id"),
                }
            )
        return score_to_keep(hits, min_score=min_score)

    def retrieve_block(
        self,
        messages: Iterable[dict],
        *,
        chat_id: str | None = None,
        top_k: int = DEFAULT_TOP_K,
        max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> dict:
        """One call for the request path: build query, search, render, report.

        The report is returned rather than logged only, so the UI can show what was
        retrieved. Silent retrieval that sometimes helps and sometimes does not is
        impossible to debug from the outside.
        """
        query = build_query(messages)
        if not query:
            return {"block": None, "hits": [], "query": "", "error": None}
        hits = self.search(query, chat_id=chat_id, top_k=top_k, min_score=min_score)
        block = format_context(hits, max_chars=max_chars)
        return {"block": block, "hits": hits, "query": query[:200], "error": None}

    def forget_chat(self, chat_id: str) -> int:
        if not chat_id or not self._ensure(refresh=True):
            return 0
        try:
            existing = self._collection.get(where={"chat_id": str(chat_id)}, include=[])
            ids = existing.get("ids") or []
            if ids:
                self._collection.delete(ids=ids)
            return len(ids)
        except Exception:
            return 0

    def count(self, chat_id: str | None = None) -> int:
        if not self._ensure(refresh=True):
            return 0
        try:
            where = {"chat_id": str(chat_id)} if chat_id else None
            return len(self._collection.get(where=where, include=[])["ids"])
        except Exception:
            return 0


__all__ = [
    "ChatMemory",
    "build_query",
    "build_records",
    "estimate_tokens",
    "format_context",
    "message_text",
    "record_id",
    "score_to_keep",
    "should_retrieve",
    "split_chunks",
    "COLLECTION_NAME",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_TOP_K",
    "EMBED_DIM",
    "EMBED_MODEL",
]