lazy import collections
lazy import hashlib
lazy import math

from .messages import say
from .settings import EMBED_KEEP_ALIVE, Config, Home
from .text import words
lazy from .model import Models, Ollama


class Retriever:
    RRF_K = 60
    BM25_K1, BM25_B = 1.5, 0.75

    def __init__(self, embeddings):
        self.embeddings = embeddings
        self.used = "words"
        self.query_embeddings = {}

    def rank(self, query, texts, prune=False):
        if not texts:
            return []
        fused = {}
        for ranking in (self.by_words(query, texts), self.by_meaning(query, texts, prune)):
            for position, index in enumerate(ranking):
                fused[index] = fused.get(index, 0.0) + 1.0 / (self.RRF_K + position + 1)
        found = sorted(fused, key=lambda index: (-fused[index], index))
        return found + [index for index in range(len(texts)) if index not in fused]

    def by_words(self, query, texts):
        scores = [(score, index) for index, score in enumerate(self.word_scores(query, texts))]
        return [
            index
            for _, index in sorted(
                (scored for scored in scores if scored[0] > 0), key=lambda pair: (-pair[0], pair[1])
            )
        ]

    def word_scores(self, query, texts):
        if not texts:
            return []
        terms, docs = set(words(query)), [words(text) for text in texts]
        average = sum(len(doc) for doc in docs) / len(docs) or 1
        holding = collections.Counter(*(set(doc) & terms) for doc in docs)
        return [self.relevance(terms, doc, len(docs), holding, average) for doc in docs]

    def relevance(self, terms, doc, total, holding, average):
        counts, score = collections.Counter(doc), 0.0
        for term in terms & counts.keys():
            idf = math.log(1 + (total - holding[term] + 0.5) / (holding[term] + 0.5))
            tf, length = counts[term], len(doc) / average
            score += (
                idf * tf * (self.BM25_K1 + 1) / (tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * length))
            )
        return score

    def by_meaning(self, query, texts, prune):
        similarity_of = self.similarities(query, texts, prune)
        if similarity_of is None:
            return []
        return sorted(range(len(texts)), key=lambda index: (-similarity_of[index], index))

    def similarities(self, query, texts, prune=False):
        if self.embeddings is None:
            return None
        if not texts:
            return []
        vectors = self.embeddings.vectors(texts, prune=prune)
        if vectors and query not in self.query_embeddings:
            asked = self.embeddings.vectors([query], keep=False)
            self.query_embeddings[query] = asked[0] if asked else None
        if not vectors or not self.query_embeddings.get(query):
            say.warn(f"Meaning search not used: {self.embeddings.failed}; the word search stands alone")
            self.embeddings = None
            return None
        self.used = f"words and meaning ({self.embeddings.name})"
        return [Embeddings.cosine(self.query_embeddings[query], vector) for vector in vectors]


class Embeddings:
    TEXTS_PER_REQUEST = 32

    def __init__(self, name):
        self.name, self.failed = name, ""
        self.cache = {"model": name, "vectors": Home.store().get("embeddings", name, {})}

    @staticmethod
    def required(option):
        if not Config.get("embed_model"):
            raise SystemExit(
                f"{option} needs an embedding model, which indexes what it learns: select one Ollama "
                f"has with tinker model NAME (tinker model lists them)"
            )
        Embeddings.check()
        return Embeddings(Config.get("embed_model"))

    @staticmethod
    def check():
        name = Config.get("embed_model")
        if not isinstance(name, str):
            raise SystemExit(f"embed_model is not a model name ({name!r}): select one with tinker model NAME")
        if name:
            try:
                Models.installed(name)
            except SystemExit as exc:
                raise SystemExit(f"The embedding model (tinker model): {exc}")

    @staticmethod
    def configured():
        name = Config.get("embed_model")
        return Embeddings(name) if name else None

    def vectors(self, texts, keep=True, prune=False):
        stored = self.cache["vectors"]
        keys = [self.key(text) for text in texts]
        missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in stored))
        fresh = {}
        for start in range(0, len(missing), self.TEXTS_PER_REQUEST):
            batch = missing[start : start + self.TEXTS_PER_REQUEST]
            found = self.embedded(batch)
            if found is None:
                return None
            fresh.update(self.keyed(batch, found))
        result = [stored.get(key) or fresh[key] for key in keys]
        if keep and (fresh or prune):
            self.kept(fresh, keys if prune else None)
        return result

    def keyed(self, texts, vectors):
        return {
            self.key(text): [round(number, 5) for number in vector] for text, vector in zip(texts, vectors)
        }

    def embedded(self, batch):
        Ollama.embedders.add(self.name)
        data = Ollama.get_json(
            "/api/embed",
            {"model": self.name, "input": batch, "keep_alive": EMBED_KEEP_ALIVE},
            timeout=300,
            errors=True,
        )
        found = data.get("embeddings") if isinstance(data, dict) else None
        if self.one_vector_each(found, batch):
            return found
        self.failed = f"{self.name} gave no embeddings" + (
            f" ({data['error']})" if isinstance(data, dict) and data.get("error") else ""
        )
        return None

    def one_vector_each(self, found, batch):
        return (
            isinstance(found, list)
            and len(found) == len(batch)
            and all(self.is_vector(vector) for vector in found)
        )

    @staticmethod
    def is_vector(value):
        return isinstance(value, list) and value and all(isinstance(number, (int, float)) for number in value)

    def kept(self, fresh, only=None):
        stored = self.cache["vectors"]
        stored.update(fresh)
        if only is not None:
            self.cache["vectors"] = {key: stored[key] for key in only}
        kept = self.cache["vectors"]
        Home.store().update(
            "embeddings", self.name, lambda saved: {**({} if only is not None else saved or {}), **kept}
        )

    @staticmethod
    def key(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return dot / size if size else 0.0
