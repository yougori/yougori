"""Embeddings for the libraries that rank text by embedding similarity, from the words alone: this
inference serves chat models, not embedding models. Each text becomes a hashed bag of its words and
word pairs (weighted by how often they occur, then normalised), so texts that share words are near.
It plugs in wherever a LangChain Embeddings object goes."""
import hashlib
import math
import re

SIZE = 1024
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def vector(text):
    words = WORD.findall((text or "").lower())
    counts = {}
    for item in words + [a + " " + b for a, b in zip(words, words[1:])]:
        slot = int.from_bytes(hashlib.blake2b(item.encode(), digest_size=4).digest(), "big") % SIZE
        counts[slot] = counts.get(slot, 0) + 1
    values = [0.0] * SIZE
    for slot, count in counts.items():
        values[slot] = 1.0 + math.log(count)
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def embeddings():
    """A LangChain Embeddings object (langchain_core), made when a library needs one."""
    from langchain_core.embeddings import Embeddings

    class Lexical(Embeddings):
        def embed_documents(self, texts):
            return [vector(text) for text in texts]

        def embed_query(self, text):
            return vector(text)

    return Lexical()
