# CELL 2 — Data contracts, file safety, FFprobe, state, and timeline mapping
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

@dataclass
class Source:
    video_id: str
    title: str
    duration: float
    video_path: str
    audio_path: str = ""
    width: int = 0
    height: int = 0

@dataclass
class Word:
    word: str
    start: float
    end: float
    prob: float = 1.0

@dataclass
class Sentence:
    text: str
    start: float
    end: float

@dataclass
class Transcript:
    language: str
    words: List[Word]
    sentences: List[Sentence]

@dataclass
class Segment:
    source_start: float
    source_end: float
    purpose: str
    transcript: str
    crop_fx: float = 0.5
    role: str = "support"
    confidence: float = 1.0

    @property
    def duration(self):
        return max(0.0, self.source_end - self.source_start)

@dataclass
class ClipPlan:
    title: str
    narrative_type: str
    segments: List[Segment]
    target_duration: float = 90.0   # ceiling, not a padding target
    notes: Dict[str, Any] = field(default_factory=dict)

    def content_duration(self):
        return sum(s.duration for s in self.segments)

    @property
    def duration(self):
        return self.content_duration()

    def validate(self, source_duration=None):
        if not self.segments:
            raise ValueError("ClipPlan has no source segments")
        if self.content_duration() <= 0:
            raise ValueError("ClipPlan duration is not positive")
        if self.content_duration() > min(float(self.target_duration), CFG["max_clip_duration"]) + 0.02:
            raise ValueError("ClipPlan exceeds its maximum clip length")
        for seg in self.segments:
            if seg.source_start < 0 or seg.source_end <= seg.source_start:
                raise ValueError(f"Invalid source interval: {seg.source_start}-{seg.source_end}")
            if source_duration is not None and seg.source_end > source_duration + 0.10:
                raise ValueError(f"Segment exceeds source duration: {seg.source_end}>{source_duration}")
        return True

    def to_dict(self):
        return asdict(self)


def safe_filename(value, maxlen=72):
    value = re.sub(r"[^\w\s-]", "", str(value or ""), flags=re.UNICODE).strip()
    value = re.sub(r"\s+", "_", value)
    return (value or "clip")[:maxlen]


def atomic_write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def run(cmd, cwd=None, timeout=None, check=True, quiet=False):
    cmd = [str(x) for x in cmd]
    if not quiet:
        log("exec", " ".join(cmd)[:200])
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {cmd[0]}\n{result.stderr[-3500:]}")
    return result


def ffprobe_json(path):
    result = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], quiet=True)
    return json.loads(result.stdout)


def sha256_file(path, block=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(block)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


STATE = {"run_id": RUN_ID, "stage": "initialized", "events": []}
def save_state(stage, **details):
    STATE["stage"] = stage
    STATE["events"].append({"utc": datetime.now(timezone.utc).isoformat(), "stage": stage, **details})
    atomic_write_json(os.path.join(RUN_DIR, "state.json"), STATE)


def build_sentences(words):
    sentences, current = [], []
    for i, word in enumerate(words):
        current.append(word)
        next_gap = words[i + 1].start - word.end if i + 1 < len(words) else 99.0
        if re.search(r"[.!?…]['\")\]]*$", word.word) or next_gap > 1.0:
            sentences.append(Sentence(" ".join(x.word for x in current), current[0].start, current[-1].end))
            current = []
    if current:
        sentences.append(Sentence(" ".join(x.word for x in current), current[0].start, current[-1].end))
    return sentences


def timeline_words(plan, words):
    """Map source timestamps to the assembled output timeline; no padding is introduced."""
    mapped, offset = [], 0.0
    starts = [w.start for w in words]
    for seg_index, seg in enumerate(plan.segments):
        lo = bisect.bisect_left(starts, seg.source_start - 0.01)
        hi = bisect.bisect_right(starts, seg.source_end + 0.01)
        for word in words[lo:hi]:
            mid = (word.start + word.end) / 2
            if not (seg.source_start <= mid < seg.source_end):
                continue
            t0 = offset + max(word.start, seg.source_start) - seg.source_start
            t1 = offset + min(word.end, seg.source_end) - seg.source_start
            if t1 <= t0:
                continue
            clean = re.sub(r"[{}\\]", "", word.word).strip()
            if clean:
                mapped.append({"segment_index": seg_index, "word": clean, "start": t0, "end": t1, "confidence": word.prob})
        offset += seg.duration
    return mapped


# Small, deterministic integrity helpers.
def validate_segment_list(segments, source_duration, max_duration=90.0):
    if not segments:
        raise ValueError("No segments supplied")
    for seg in segments:
        if seg.source_start < 0 or seg.source_end <= seg.source_start or seg.source_end > source_duration + 0.1:
            raise ValueError("Invalid segment boundary")
    total = sum(s.duration for s in segments)
    if total > max_duration + 0.02:
        raise ValueError("Segment list exceeds max duration")
    return total

log("contracts", "stable source/transcript/plan contracts and timeline mapper ready")
