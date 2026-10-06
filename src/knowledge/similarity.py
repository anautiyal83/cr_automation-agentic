"""UC similarity matching for few-shot selection."""
from __future__ import annotations

from pathlib import Path

from src.knowledge.few_shot_store import FewShotStore


class SimilarityMatcher:
    def __init__(self, store: FewShotStore, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.store = store
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._get_model()
        return model.encode(text).tolist()

    async def find_similar(self, uc_description: str, top_k: int = 3) -> list[dict]:
        """Find top-K most similar UC entries by description embedding."""
        descriptions = await self.store.get_all_descriptions()
        if not descriptions:
            return []

        model = self._get_model()
        query_emb = model.encode(uc_description)

        # Embed all stored descriptions
        stored_texts = [desc for _, desc in descriptions]
        stored_embs = model.encode(stored_texts)

        # Cosine similarity
        from numpy import dot
        from numpy.linalg import norm

        similarities = []
        for i, (uc_name, desc) in enumerate(descriptions):
            sim = float(dot(query_emb, stored_embs[i]) / (norm(query_emb) * norm(stored_embs[i]) + 1e-8))
            similarities.append((uc_name, sim))

        similarities.sort(key=lambda x: x[1], reverse=True)
        top = similarities[:top_k]

        results = []
        for uc_name, score in top:
            entry = await self.store.get_entry(uc_name)
            if entry:
                results.append({
                    "uc_name": uc_name,
                    "similarity": score,
                    "entry": entry,
                })
        return results

    async def get_few_shot_context(self, uc_description: str, top_k: int = 3) -> list[dict]:
        """Get formatted few-shot examples ready for agent prompts."""
        similar = await self.find_similar(uc_description, top_k)
        examples = []
        for item in similar:
            entry = item["entry"]
            example = {"uc_name": entry.uc_name, "similarity": item["similarity"]}
            # Load config contents
            for config_type, path in [
                ("validation_rules", entry.validation_rules_path),
                ("json_template", entry.json_template_path),
                ("workflow", entry.workflow_path),
            ]:
                p = Path(path)
                if p.exists():
                    example[config_type] = p.read_text(encoding="utf-8")
            examples.append(example)
        return examples
