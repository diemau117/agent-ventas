"""RAG determinista sobre la base de conocimiento (spec §3-4). Sin embeddings."""
from app.rag.retriever import categories_for, knowledge_context, retrieve

__all__ = ["retrieve", "knowledge_context", "categories_for"]
