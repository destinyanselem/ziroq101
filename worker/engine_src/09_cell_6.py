# CELL 6 — Whole-source content map + optional local semantic embeddings
from collections import Counter, defaultdict

EMOTION_LEXICON = {
    "hope": {"hope", "believe", "possible", "future", "dream", "opportunity", "change"},
    "tension": {"fear", "risk", "danger", "wrong", "crisis", "lose", "failure", "mistake"},
    "surprise": {"unexpected", "shocked", "surprise", "actually", "truth", "secret", "nobody"},
    "amusement": {"funny", "laugh", "joke", "ridiculous", "absurd"},
    "reflection": {"learned", "realize", "understand", "meaning", "remember", "experience"},
}
TOPIC_LEXICON = {
    "money": {"money", "wealth", "rich", "poor", "invest", "debt", "cash", "finance", "financial"},
    "business": {"business", "customer", "company", "market", "sales", "product", "profit", "startup"},
    "entrepreneurship": {"entrepreneur", "founder", "build", "company", "startup", "risk", "venture"},
    "self-improvement": {"habit", "discipline", "growth", "mindset", "goal", "success", "failure", "confidence"},
    "psychology": {"psychology", "behavior", "brain", "mind", "emotion", "decision", "belief"},
    "leadership": {"leader", "team", "manage", "culture", "trust", "leadership", "people"},
    "technology": {"technology", "software", "computer", "ai", "data", "algorithm", "internet"},
    "relationships": {"relationship", "friend", "family", "partner", "marriage", "trust"},
}
HOOK_CUES = {"why", "how", "never", "truth", "secret", "mistake", "wrong", "actually", "nobody", "everyone", "because"}
STORY_CUES = {"when", "then", "i", "my", "we", "years", "happened", "remember", "story", "experience"}
PAYOFF_CUES = {"therefore", "lesson", "learned", "result", "that's why", "which means", "so", "in the end"}
ACTION_CUES = {"start", "stop", "build", "practice", "change", "ask", "write", "measure", "focus", "save", "invest", "decide"}


def tokens(text):
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def classify_text(text):
    low = (text or "").lower()
    tok = set(tokens(low))
    topics = [topic for topic, vocab in TOPIC_LEXICON.items() if len(tok & vocab) >= 1]
    emotions = [emotion for emotion, vocab in EMOTION_LEXICON.items() if len(tok & vocab) >= 1]
    if any(c in low for c in ("because", "the reason", "this is why", "which means")):
        role = "explanation"
    elif any(c in low for c in ("i learned", "the lesson", "what matters", "in the end", "that's why", "the result")):
        role = "payoff"
    elif len(tok & STORY_CUES) >= 2 or any(c in low for c in ("when i was", "one day", "years ago", "i remember")):
        role = "story"
    elif len(tok & ACTION_CUES) >= 2 or any(c in low for c in ("you should", "you need to", "the first step")):
        role = "lesson"
    else:
        role = "claim"
    return topics, emotions, role


def build_content_map(transcript):
    records = []
    starts = [w.start for w in transcript.words]
    for index, sentence in enumerate(transcript.sentences):
        lo = bisect.bisect_left(starts, sentence.start - 0.01)
        hi = bisect.bisect_right(starts, sentence.end + 0.01)
        related_words = transcript.words[lo:hi]
        avg_conf = float(np.mean([w.prob for w in related_words])) if related_words else 0.0
        topics, emotions, role = classify_text(sentence.text)
        records.append({
            "sentence_index": index, "start": sentence.start, "end": sentence.end,
            "text": sentence.text, "topics": topics, "emotions": emotions,
            "role": role, "confidence": avg_conf,
            "word_count": len(related_words),
        })
    return records


def build_sentence_vectors(records):
    texts = [r["text"] for r in records]
    if CFG["use_semantic_embeddings"]:
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(CFG["semantic_model_id"])
            vectors = model.encode(texts, batch_size=64, show_progress_bar=True,
                                   normalize_embeddings=True, convert_to_numpy=True)
            log("semantic", f"using local sentence embeddings: {CFG['semantic_model_id']}")
            return np.asarray(vectors, dtype=np.float32), "sentence-transformers"
        except Exception as exc:
            log("semantic", f"local embedding model unavailable ({type(exc).__name__}); using TF-IDF fallback")
    from sklearn.feature_extraction.text import TfidfVectorizer
    vectorizer = TfidfVectorizer(max_features=18000, ngram_range=(1, 2), stop_words="english", min_df=1)
    matrix = vectorizer.fit_transform(texts)
    log("semantic", "using local TF-IDF similarity fallback (lexical, not a neural semantic model)")
    return matrix, "tfidf_fallback"


CONTENT_MAP = build_content_map(TRANSCRIPT)
SENTENCE_VECTORS, SIMILARITY_BACKEND = build_sentence_vectors(CONTENT_MAP)
content_map_path = os.path.join(REPORT_DIR, "content_knowledge_map.json")
atomic_write_json(content_map_path, {
    "source_id": SOURCE.video_id, "source_duration": SOURCE.duration,
    "language": TRANSCRIPT.language, "similarity_backend": SIMILARITY_BACKEND,
    "sentence_count": len(CONTENT_MAP), "sentences": CONTENT_MAP,
})

# Summarize dominant topics/emotions from the entire transcript, not a sampled opening section.
topic_counts = Counter(t for row in CONTENT_MAP for t in row["topics"])
emotion_counts = Counter(e for row in CONTENT_MAP for e in row["emotions"])
print("Whole-source topic map:", topic_counts.most_common(10) or "no lexicon matches")
print("Whole-source emotion cues:", emotion_counts.most_common(8) or "no lexicon matches")
print("Similarity backend:", SIMILARITY_BACKEND)
save_state("content_map_ready", sentences=len(CONTENT_MAP), similarity_backend=SIMILARITY_BACKEND)
