import hashlib
from pathlib import Path

from logpilot.llm.factory import create_openai_client
from logpilot.prompts.loader import load_prompt


def split_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    """Split troubleshooting documents into compact overlapping chunks."""
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    chunk_size = max(200, chunk_size)
    overlap = max(0, min(overlap, chunk_size // 3))
    chunks = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        if end < len(normalized):
            boundary = normalized.rfind("\n", start + chunk_size // 2, end)
            if boundary > start:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


class KnowledgeBase:
    """Persistent Chroma store for runbooks and historical incident documents."""

    def __init__(self, persist_directory: str | Path, collection_name: str = "spring_log_incidents"):
        import chromadb
        from chromadb.config import Settings

        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.client = chromadb.PersistentClient(
            path=str(self.persist_directory),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _embedding_client(api_key: str, base_url: str):
        return create_openai_client(api_key, base_url)

    def add_documents(
        self,
        documents: dict[str, str],
        api_key: str,
        base_url: str,
        embedding_model: str = "text-embedding-v4",
    ) -> dict:
        client = self._embedding_client(api_key, base_url)
        added_chunks = 0
        added_sources = []
        for source, text in documents.items():
            chunks = split_text(text)
            if not chunks:
                continue
            response = client.embeddings.create(model=embedding_model, input=chunks)
            embeddings = [item.embedding for item in response.data]
            ids = [
                hashlib.sha256(f"{source}:{index}:{chunk}".encode("utf-8")).hexdigest()
                for index, chunk in enumerate(chunks)
            ]
            self.collection.delete(where={"source": source})
            self.collection.upsert(
                ids=ids,
                documents=chunks,
                embeddings=embeddings,
                metadatas=[
                    {"source": source, "chunk_index": index}
                    for index in range(len(chunks))
                ],
            )
            added_chunks += len(chunks)
            added_sources.append(source)
        return {"sources": added_sources, "chunks": added_chunks}

    def search(
        self,
        query: str,
        api_key: str,
        base_url: str,
        embedding_model: str = "text-embedding-v4",
        limit: int = 3,
    ) -> dict:
        if not query.strip():
            return {"error": "知识库检索问题不能为空"}
        if self.collection.count() == 0:
            return {"total": 0, "results": []}
        client = self._embedding_client(api_key, base_url)
        embedding = client.embeddings.create(model=embedding_model, input=[query]).data[0].embedding
        limit = max(1, min(limit, 8))
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(limit, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        matches = []
        for document, metadata, distance in zip(
            result.get("documents", [[]])[0],
            result.get("metadatas", [[]])[0],
            result.get("distances", [[]])[0],
        ):
            matches.append(
                {
                    "source": metadata.get("source", "unknown"),
                    "chunk_index": metadata.get("chunk_index"),
                    "distance": round(float(distance), 4),
                    "content": document,
                }
            )
        return {"total": len(matches), "results": matches}

    def answer_question(
        self,
        question: str,
        api_key: str,
        base_url: str,
        chat_model: str = "qwen3.8-max",
        embedding_model: str = "text-embedding-v4",
        chat_history: list[dict] | None = None,
    ) -> dict:
        retrieval = self.search(
            query=question,
            api_key=api_key,
            base_url=base_url,
            embedding_model=embedding_model,
            limit=4,
        )
        if not retrieval.get("results"):
            return {
                "answer": "知识库中没有可用于回答该问题的资料。",
                "results": [],
            }

        context_parts = []
        for index, item in enumerate(retrieval["results"], 1):
            context_parts.append(
                f"【资料 {index}｜来源：{item['source']}｜文本块：{item['chunk_index']}】\n"
                f"{item['content']}"
            )
        context = "\n\n".join(context_parts)
        messages = [
            {
                "role": "system",
                "content": (
                    load_prompt("knowledge_qa_prompt.txt")
                ),
            }
        ]
        for turn in (chat_history or [])[-4:]:
            messages.append({"role": "user", "content": str(turn.get("question", ""))})
            messages.append({"role": "assistant", "content": str(turn.get("answer", ""))})
        messages.append(
            {
                "role": "user",
                "content": f"参考资料：\n{context}\n\n用户问题：{question}",
            }
        )
        client = self._embedding_client(api_key, base_url)
        response = client.chat.completions.create(
            model=chat_model,
            messages=messages,
            temperature=0.1,
            extra_body={"enable_thinking": False},
        )
        return {
            "answer": response.choices[0].message.content or "未生成回答。",
            "results": retrieval["results"],
        }

    def stats(self) -> dict:
        count = self.collection.count()
        if not count:
            return {"chunks": 0, "sources": []}
        data = self.collection.get(include=["metadatas"])
        sources = sorted(
            {
                metadata.get("source", "unknown")
                for metadata in data.get("metadatas", [])
                if metadata
            }
        )
        return {"chunks": count, "sources": sources}

    def clear(self) -> None:
        self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
