# CELL 10b — PUBLISHING STUDIO: a platform-native posting pack for every clip (Gemini writes it, code validates it)
# Output per clip: YouTube Shorts, TikTok, Instagram Reels, Facebook Reels, X, Threads — each with title/caption, hashtags, tags,
# category, settings and PINNED COMMENTS, ready to copy and paste. If Gemini is unavailable a local template is used instead.

YT_CATEGORIES = ["Film & Animation", "Autos & Vehicles", "Music", "Pets & Animals", "Sports", "Travel & Events", "Gaming",
                 "People & Blogs", "Comedy", "Entertainment", "News & Politics", "Howto & Style", "Education",
                 "Science & Technology", "Nonprofits & Activism"]

# Limits checked 16 Nov 2026 against third-party platform guides (YouTube/Instagram/TikTok change these often; re-verify in each app).
PL = {"yt_title": 100, "yt_title_ideal": 70, "yt_title_front": 40, "yt_desc": 5000, "yt_desc_fold": 157, "yt_tags_total": 500, "yt_tag_each": 30,
      "yt_hashtags": (3, 5), "tt_caption": 2200, "tt_hashtags": (3, 5), "ig_caption": 2200, "ig_hashtags": (3, 5), "fb_desc": 2000,
      "fb_hashtags": (0, 3), "x_post": 270, "threads_post": 480, "comment": 480}

PACKAGING_PLAYBOOK = """PACKAGING RULES (apply all):
1. TRUTH FIRST. Every claim, number and name in titles/captions must come from the CLIP TRANSCRIPT or SOURCE INFO below. Never invent statistics, results, quotes or promises. Misleading metadata gets videos limited.
2. CURIOSITY WITH A REAL ANSWER. Open an information gap (a question, contrast, or surprising-but-true statement) that THIS clip actually closes. Specific beats vague: use the concrete noun/number/situation from the clip.
3. ONE IDEA. The title promises one outcome or insight a viewer could repeat in one sentence. Prefer plain, direct benefit or tension ("Stop X: do Y") over hype words. At most one emoji, usually none. No ALL CAPS shouting.
4. FRONT-LOAD. The first ~40 characters of a YouTube Shorts title and the first ~100 characters of every caption carry the main keyword/hook, because feeds truncate. YouTube titles <= 70 characters; never > 100.
5. SEARCH LANGUAGE. Use the exact words a viewer would type (topic, problem, speaker/book/concept names that appear in the source). Put them naturally in the first line of the description/caption.
6. HASHTAGS: few and relevant. YouTube 3-5 (the first three show above the title; if more than 15 YouTube ignores all). TikTok 3-5. Instagram at most 5. Facebook at most 3. Threads uses ONE topic tag instead. Mix 1-2 broad and 2-3 niche. No banned/irrelevant or spammy tags.
7. TAGS (YouTube tags field): 8-15 short tags, each <= 30 characters, total <= 500 characters including commas; include the core phrase, close variants, common misspellings of names, and the topic category. YouTube says tags matter little, so never let them replace a good title/description.
8. PINNED COMMENTS drive replies, which platforms read as engagement. Write a short comment (<= 300 characters) that does ONE of: asks a specific, easy-to-answer question tied to the clip's idea (not 'thoughts?'); asks the viewer to pick a side; or adds one useful extra line (a takeaway or action) and then asks. No begging for likes, no fake giveaways, no links to other platforms. Give TWO options per platform.
9. HOOK LINE for every caption: the first sentence stands alone and earns the tap on 'more'.
10. TONE: confident, warm, human. Match the clip's language. No claims of guaranteed results (money, health, success)."""

PACK_STR = {"type": "string"}
PACK_LIST = {"type": "array", "items": {"type": "string"}}
PACK_SCHEMA = {
    "type": "object",
    "properties": {
        "youtube": {"type": "object", "properties": {
            "title": PACK_STR, "title_alternatives": PACK_LIST, "description_hook": PACK_STR, "description_body": PACK_STR,
            "hashtags": PACK_LIST, "tags": PACK_LIST, "category": PACK_STR, "pinned_comments": PACK_LIST},
            "required": ["title", "description_hook", "description_body", "hashtags", "tags", "category", "pinned_comments"]},
        "tiktok": {"type": "object", "properties": {
            "caption": PACK_STR, "hashtags": PACK_LIST, "search_keywords": PACK_LIST, "on_screen_text": PACK_STR, "pinned_comments": PACK_LIST},
            "required": ["caption", "hashtags", "on_screen_text", "pinned_comments"]},
        "instagram": {"type": "object", "properties": {
            "caption": PACK_STR, "hashtags": PACK_LIST, "cover_text": PACK_STR, "alt_text": PACK_STR, "pinned_comments": PACK_LIST},
            "required": ["caption", "hashtags", "cover_text", "pinned_comments"]},
        "facebook": {"type": "object", "properties": {"description": PACK_STR, "hashtags": PACK_LIST, "pinned_comments": PACK_LIST},
                     "required": ["description", "hashtags", "pinned_comments"]},
        "x": {"type": "object", "properties": {"post": PACK_STR, "reply_comments": PACK_LIST}, "required": ["post", "reply_comments"]},
        "threads": {"type": "object", "properties": {"post": PACK_STR, "topic_tag": PACK_STR, "reply_comments": PACK_LIST},
                    "required": ["post", "topic_tag", "reply_comments"]},
    },
    "required": ["youtube", "tiktok", "instagram", "facebook", "x", "threads"],
}


def _clean_tag(t):
    t = re.sub(r"[^\w]", "", str(t).replace("#", ""), flags=re.UNICODE)
    return t


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        k = x.lower()
        if x and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _clip_text(text, limit):
    """Trim at a word boundary without leaving a dangling fragment."""
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-—")
    return cut


def _tags_line(tags, total=PL["yt_tags_total"], each=PL["yt_tag_each"]):
    out, used = [], 0
    for t in _dedupe([re.sub(r"\s+", " ", str(x).replace("#", "")).strip() for x in tags]):
        if not t or len(t) > each:
            continue
        add = len(t) + (2 if out else 0)      # ", " separator is counted conservatively
        if used + add > total:
            break
        out.append(t)
        used += add
    return ", ".join(out)


def _hash_list(tags, lo, hi):
    tags = _dedupe([_clean_tag(x) for x in tags])
    return ["#" + t for t in tags[:hi] if t]


def _numbers_in(text):
    return set(re.findall(r"\d[\d,\.]*", text or ""))


def finalize_pack(raw, ctx):
    """Deterministic clean-up: clamp lengths, fix hashtags/tags, assemble the final fields. Never invents content."""
    yt, tt, ig, fb, xx, th = (raw.get(k, {}) for k in ("youtube", "tiktok", "instagram", "facebook", "x", "threads"))
    credit = ctx.get("credit_block", "")
    source_line = ctx.get("source_line", "")

    title = _clip_text(str(yt.get("title", "")).replace("\n", " "), PL["yt_title"]).rstrip(".")
    yt_hash = _hash_list(yt.get("hashtags", []), *PL["yt_hashtags"])
    hook = _clip_text(yt.get("description_hook", ""), PL["yt_desc_fold"])
    body = str(yt.get("description_body", "")).strip()
    tail = [x for x in ([source_line] if source_line else []) + (["Asset credits:\n" + credit] if credit else [])]
    hash_line = " ".join(yt_hash)
    fixed_len = len(hook) + sum(len(x) for x in tail) + len(hash_line) + 12
    body = _clip_text(body, max(0, PL["yt_desc"] - fixed_len))
    description = "\n\n".join(p for p in [hook, body] + tail + [hash_line] if p)
    category = yt.get("category") if yt.get("category") in YT_CATEGORIES else "Education"
    langname = ctx.get("language_name", "English")

    youtube = {
        "title": title, "title_alternatives": [_clip_text(t, PL["yt_title"]) for t in (yt.get("title_alternatives") or [])][:3],
        "description": description, "hashtags": yt_hash, "tags_line": _tags_line(yt.get("tags", [])),
        "category": category, "pinned_comments": [_clip_text(c, PL["comment"]) for c in (yt.get("pinned_comments") or [])][:2],
        "settings": ["Visibility: choose when you publish", "Audience: select 'No, it's not made for kids' (keeps comments and notifications on)",
                     "Altered content: No (speech and footage are original recordings; answer Yes only if you add realistic AI-generated visuals or voices)",
                     "Paid promotion: No (unless you are sponsored)", f"Language: {langname}", "Location: leave blank (natural global discovery)",
                     "Comments: On, then pin the comment below right after publishing",
                     "Shorts remixing: your choice (allowing it can widen reach)"],
    }
    tt_hash = _hash_list(tt.get("hashtags", []), *PL["tt_hashtags"])
    tt_cap = _clip_text(str(tt.get("caption", "")), PL["tt_caption"] - len(" ".join(tt_hash)) - 2)
    tiktok = {"caption": (tt_cap + "\n\n" + " ".join(tt_hash)).strip(), "hashtags": tt_hash,
              "search_keywords": _dedupe([str(k).strip() for k in (tt.get("search_keywords") or [])])[:8],
              "on_screen_text": _clip_text(tt.get("on_screen_text", ""), 60),
              "pinned_comments": [_clip_text(c, PL["comment"]) for c in (tt.get("pinned_comments") or [])][:2]}
    ig_hash = _hash_list(ig.get("hashtags", []), *PL["ig_hashtags"])
    ig_cap = _clip_text(str(ig.get("caption", "")), PL["ig_caption"] - len(" ".join(ig_hash)) - 2)
    instagram = {"caption": (ig_cap + "\n\n" + " ".join(ig_hash)).strip(), "hashtags": ig_hash,
                 "cover_text": _clip_text(ig.get("cover_text", ""), 40), "alt_text": _clip_text(ig.get("alt_text", ""), 400),
                 "pinned_comments": [_clip_text(c, PL["comment"]) for c in (ig.get("pinned_comments") or [])][:2]}
    fb_hash = _hash_list(fb.get("hashtags", []), *PL["fb_hashtags"])
    fb_desc = _clip_text(str(fb.get("description", "")), PL["fb_desc"] - len(" ".join(fb_hash)) - 2)
    facebook = {"description": (fb_desc + "\n\n" + " ".join(fb_hash)).strip(), "hashtags": fb_hash,
                "pinned_comments": [_clip_text(c, PL["comment"]) for c in (fb.get("pinned_comments") or [])][:2]}
    x_post = _clip_text(str(xx.get("post", "")), PL["x_post"])
    x_out = {"post": x_post, "reply_comments": [_clip_text(c, 270) for c in (xx.get("reply_comments") or [])][:2]}
    topic = _clean_tag(th.get("topic_tag", ""))
    th_post = _clip_text(str(th.get("post", "")), PL["threads_post"])
    threads = {"post": th_post, "topic_tag": topic, "reply_comments": [_clip_text(c, 400) for c in (th.get("reply_comments") or [])][:2]}
    return {"youtube": youtube, "tiktok": tiktok, "instagram": instagram, "facebook": facebook, "x": x_out, "threads": threads}


def validate_pack(raw, ctx):
    """Problems that clamping cannot fix honestly. Returned to Gemini once for repair."""
    errs = []
    yt = raw.get("youtube", {})
    title = str(yt.get("title", "")).strip()
    if len(title) < 12:
        errs.append("youtube.title is too short; make it a specific, front-loaded title of about 40-70 characters")
    if len(title) > PL["yt_title"]:
        errs.append(f"youtube.title is {len(title)} characters; it must be at most {PL['yt_title_ideal']} (hard limit {PL['yt_title']})")
    spoken = ctx.get("transcript", "") + " " + ctx.get("source_title", "")
    for n in _numbers_in(title) - _numbers_in(spoken):
        errs.append(f"youtube.title contains the number '{n}' that is not in the clip; remove or replace it (no invented numbers)")
    if len(re.findall(r"[A-Z]", title)) > 0.5 * max(1, len(re.findall(r"[A-Za-z]", title))) and len(title) > 12:
        errs.append("youtube.title is mostly capital letters; use normal title case")
    for plat in ("youtube", "tiktok", "instagram", "facebook"):
        pcs = [c for c in (raw.get(plat, {}).get("pinned_comments") or []) if str(c).strip()]
        if not pcs:
            errs.append(f"{plat}.pinned_comments is empty; write two options")
    if not [t for t in (raw.get("youtube", {}).get("tags") or []) if str(t).strip()]:
        errs.append("youtube.tags is empty")
    return errs


# ------------------------------------------------------------------ local (no-Gemini) fallback
_STOP = set("a an the and or but if then than that this those these of to in on at for from by with as is are was were be been being it its it's i you he she we they them his her our your my me do does did have has had not no yes so just very can could would should will what when where who why how about into over under again more most some any all each other such only own same too up down out off there here".split())


def _top_keywords(text, n=8):
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text.lower()) if w not in _STOP]
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def local_pack(plan, idx, ctx):
    text = ctx["transcript"]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]
    hook = (sents[0] if sents else plan.title).strip()
    title = _clip_text(plan.title if len(plan.title) >= 12 else hook, PL["yt_title_ideal"]).rstrip(".")
    kws = _top_keywords(text)
    topics = [t for t in plan.notes.get("topics", [])]
    base_tags = _dedupe(topics + kws + [t for t in (ctx.get("source_tags") or [])][:6])
    tagwords = [w.replace("-", " ") for w in base_tags]
    hash_pool = ["Shorts"] + [w.title().replace(" ", "") for w in topics] + [w.title() for w in kws[:3]]
    q = "Which part of this hit home for you, and what will you do with it this week?"
    q2 = "Do you agree with this? Tell me why in one sentence."
    body = _clip_text(" ".join(sents[1:3]) if len(sents) > 1 else text, 600)
    return {
        "youtube": {"title": title, "title_alternatives": [], "description_hook": _clip_text(hook, PL["yt_desc_fold"]), "description_body": body,
                    "hashtags": hash_pool[:4], "tags": tagwords[:14], "category": "Education", "pinned_comments": [q, q2]},
        "tiktok": {"caption": _clip_text(hook, 150) + " " + _clip_text(body, 220), "hashtags": hash_pool[:4], "search_keywords": kws[:5],
                   "on_screen_text": _clip_text(title, 60), "pinned_comments": [q, q2]},
        "instagram": {"caption": _clip_text(hook, 125) + "\n\n" + _clip_text(body, 400), "hashtags": hash_pool[:4], "cover_text": _clip_text(title, 40),
                      "alt_text": _clip_text("Speaker talking to camera: " + hook, 300), "pinned_comments": [q, q2]},
        "facebook": {"description": _clip_text(hook, 125) + "\n\n" + _clip_text(body, 400), "hashtags": hash_pool[:2], "pinned_comments": [q, q2]},
        "x": {"post": _clip_text(hook, 250), "reply_comments": [q2, q]},
        "threads": {"post": _clip_text(hook + " " + q2, 420), "topic_tag": (topics[0] if topics else (kws[0] if kws else "Mindset")).title().replace(" ", ""),
                    "reply_comments": [q2, q]},
    }


def _clip_context(plan, idx):
    transcript = " ".join(s.transcript for s in plan.segments).strip()
    meta = globals().get("SOURCE_META") or {}
    credits = asset_credit_records(idx) if "asset_credit_records" in globals() else []
    credit_block = format_asset_credits(credits) if credits and "format_asset_credits" in globals() else ""
    channel = meta.get("channel") or meta.get("uploader") or ""
    source_line = ""
    if CFG.get("credit_source_in_description", True) and SOURCE.title and not re.fullmatch(r"[A-Za-z0-9_-]{11}", SOURCE.title):
        source_line = f"Source: {SOURCE.title}" + (f" — {channel}" if channel else "")
    lang = getattr(TRANSCRIPT, "language", "en") or "en"
    names = {"en": "English", "es": "Spanish", "fr": "French", "pt": "Portuguese", "de": "German", "yo": "Yoruba", "ha": "Hausa", "ig": "Igbo"}
    return {"transcript": transcript, "source_title": SOURCE.title, "channel": channel, "source_tags": meta.get("tags") or [],
            "language_name": names.get(lang, lang), "credit_block": credit_block, "source_line": source_line,
            "duration": float(plan.duration), "narrative": plan.narrative_type, "topics": plan.notes.get("topics", [])}


def _pack_prompt(plan, idx, ctx):
    return (PACKAGING_PLAYBOOK + "\n\nTASK: Write the complete posting pack for ONE short vertical video clip, for YouTube Shorts, TikTok, Instagram Reels, "
            "Facebook Reels, X and Threads. Write in the language of the transcript. Return JSON only.\n"
            "FIELD NOTES: youtube.title_alternatives = 3 different angles for A/B testing. youtube.description_hook = the first 1-2 sentences "
            "(<=150 characters, keyword-rich, no hashtags). youtube.description_body = 2-4 short sentences adding context plus a soft call to action; "
            "no hashtags (they are added by code). hashtags = words WITHOUT the # symbol. youtube.category must be one of: "
            + ", ".join(YT_CATEGORIES) + ". tiktok.on_screen_text = a <=8 word text overlay idea. threads.topic_tag = one word/phrase, no #. "
            "x.post must be <= 270 characters and contain no link. pinned_comments: exactly two options each.\n\n"
            f"SOURCE INFO: title={ctx['source_title']!r}; channel={ctx['channel']!r}; the video's own tags={ctx['source_tags'][:12]}\n"
            f"CLIP: {ctx['duration']:.0f} seconds; story shape={ctx['narrative']}; topics={ctx['topics']}\n"
            f"CLIP TRANSCRIPT (the only source of facts):\n{ctx['transcript'][:6000]}")


def build_publishing_packs(plans):
    """Returns {'youtube': {'1': {...}}, 'tiktok': ..., ...} and writes copy-paste Markdown files into REPORT_DIR/publishing_pack."""
    out = {k: {} for k in ("youtube", "tiktok", "instagram", "facebook", "x", "threads")}
    pack_dir = os.path.join(REPORT_DIR, "publishing_pack")
    os.makedirs(pack_dir, exist_ok=True)
    sources = {}
    all_md = ["# PROJECT ZIROQ — Publishing packs", "", f"Source: {SOURCE.title}", ""]
    for idx, plan in enumerate(plans, 1):
        ctx = _clip_context(plan, idx)
        raw, source = None, "local template"
        if "gemini_structured" in globals() and globals().get("GEMINI") is not None and getattr(globals().get("GEMINI"), "enabled", False):
            res = gemini_structured(f"publishing_pack_{RUN_ID}_{idx}", _pack_prompt(plan, idx, ctx), PACK_SCHEMA, tier="reasoning",
                                    validator=lambda d, c=ctx: validate_pack(d, c), max_repairs=1, temperature=0.5)
            if res.get("ok"):
                raw, source = res["data"], "gemini"
            else:
                log("publishing", f"clip {idx}: Gemini pack unavailable ({res.get('error_kind')}); using the local template")
        if raw is None:
            raw = local_pack(plan, idx, ctx)
        final = finalize_pack(raw, ctx)
        # last safety net: even the Gemini pack must pass the hard limits after clamping
        assert len(final["youtube"]["title"]) <= PL["yt_title"] and len(final["x"]["post"]) <= 280
        for k in out:
            out[k][str(idx)] = dict(final[k], generated_by=source)
        sources[idx] = source
        md = render_pack_markdown(idx, plan, final, source, ctx)
        with open(os.path.join(pack_dir, f"clip_{idx:02d}_PUBLISHING_PACK.md"), "w", encoding="utf-8") as f:
            f.write(md)
        all_md.append(md)
        all_md.append("\n\n---\n")
    with open(os.path.join(pack_dir, "ALL_CLIPS_PUBLISHING_PACK.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(all_md))
    log("publishing", "posting packs written: " + ", ".join(f"clip {i}={s}" for i, s in sources.items()))
    return out


def length_notes(duration):
    d = float(duration)
    n = [f"YouTube Shorts: OK ({d:.0f} s; Shorts can run up to 3 minutes for videos uploaded after 15 Oct 2024)." if d <= 180 else "YouTube Shorts: too long (limit 3 minutes)."]
    if d > 60:
        n.append("YouTube Shorts over 60 s: if the source video has an active Content ID claim, a Short this long can be blocked worldwide; check Studio after publishing.")
    n.append("Instagram Reels: OK for uploads up to 3 minutes; reach beyond ~90 s may be limited, so the first 3 seconds matter most." if d <= 180 else "Instagram Reels: over 3 minutes is not recommended.")
    n.append("TikTok: OK (limits vary by account; check the composer).")
    n.append("Facebook Reels / Threads: length limits not verified; check the app before posting.")
    if d > 140:
        n.append("X: longer than 140 s, the usual limit for standard accounts; post a shorter cut or skip X for this clip.")
    else:
        n.append("X: within the 140 s standard limit.")
    return n


def _bul(items):
    return "\n".join(f"* {x}" for x in items)


def render_pack_markdown(idx, plan, f, source, ctx):
    yt, tt, ig, fb, xx, th = (f[k] for k in ("youtube", "tiktok", "instagram", "facebook", "x", "threads"))
    note = "" if source == "gemini" else "> Local template (Gemini was unavailable). Good enough to post after a quick read; re-run later for a sharper version.\n\n"
    L = [f"# CLIP {idx:02d} — {plan.title}", "", f"{note}Review everything once before posting. Facts in titles come only from the clip.", "",
         "## Length check (" + f"{plan.duration:.0f} s)", _bul(length_notes(plan.duration)), "", "## YouTube Shorts", "### Title", yt["title"], "",
         "### Alternative titles to A/B test", _bul(yt["title_alternatives"]) or "* (none)", "",
         "### Description", "(The first ~150 characters show before 'Show more'; the first three hashtags appear above the title.)", "", yt["description"], "",
         '### Tags (copy and paste this exact line into the Tags field under "Show more")', yt["tags_line"], "",
         "### Category", f"* {yt['category']}", "", "### Additional settings", _bul(yt["settings"]), "",
         "### Pinned comment (pick one, post it after publishing, then pin it)"]
    L += [f"{n}. {c}" for n, c in enumerate(yt["pinned_comments"], 1)]
    L += ["", "## TikTok", "### Caption", tt["caption"], "", "### On-screen text idea", tt["on_screen_text"] or "(none)", "",
          "### Search keywords to say or show in the first seconds", ", ".join(tt["search_keywords"]) or "(none)", "",
          "### Pinned comment (pick one)"] + [f"{n}. {c}" for n, c in enumerate(tt["pinned_comments"], 1)]
    L += ["", "## Instagram Reels", "### Caption (max 5 hashtags)", ig["caption"], "", "### Cover text", ig["cover_text"], "",
          "### Alt text", ig["alt_text"] or "(none)", "", "### First comment (pin it)"] + [f"{n}. {c}" for n, c in enumerate(ig["pinned_comments"], 1)]
    L += ["", "## Facebook Reels", "### Description", fb["description"], "", "### Pinned comment (pick one)"] + [f"{n}. {c}" for n, c in enumerate(fb["pinned_comments"], 1)]
    L += ["", "## X", "### Post", xx["post"], "", "### Reply to your own post (pick one)"] + [f"{n}. {c}" for n, c in enumerate(xx["reply_comments"], 1)]
    L += ["", "## Threads", "### Post", th["post"], "", f"### Topic tag (add one tag in the app): {th['topic_tag']}", "", "### Reply to your own post (pick one)"]
    L += [f"{n}. {c}" for n, c in enumerate(th["reply_comments"], 1)]
    return "\n".join(L)


print("Publishing Studio ready: per-clip packs for YouTube, TikTok, Instagram, Facebook, X and Threads (with pinned comments).")
