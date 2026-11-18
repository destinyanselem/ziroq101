# CELL 10 — Publishing metadata, editorial research playbook, source register, contact sheet, and staged delivery


def hashtags_for_plan(plan):
    tags = {"#Shorts", "#Reels", "#LearnSomething"}
    topic_tags = {
        "money": "#Money", "business": "#Business", "entrepreneurship": "#Entrepreneurship",
        "self-improvement": "#SelfImprovement", "psychology": "#Psychology", "leadership": "#Leadership",
        "technology": "#Technology", "relationships": "#Relationships",
    }
    for topic in plan.notes.get("topics", []):
        if topic in topic_tags:
            tags.add(topic_tags[topic])
    return list(sorted(tags))[:7]


def asset_credit_records(clip_index):
    """Return attribution lines required by the exact assets used in one finished clip."""
    credits = []
    for asset_type in ("music", "broll"):
        for asset in ASSET_MANIFEST.get(asset_type, []):
            if int(asset.get("clip_index") or 0) != int(clip_index):
                continue
            if not asset.get("attribution_required"):
                continue
            label = asset.get("attribution_text") or asset.get("title") or asset_type
            link = asset.get("creator_url") or asset.get("source_url") or asset.get("license_url")
            credits.append({"asset_type": asset_type, "text": label, "url": link,
                            "creator_url": asset.get("creator_url"), "source_url": asset.get("source_url"),
                            "provider": asset.get("provider"), "license": asset.get("license")})
    return credits


def format_asset_credits(credits):
    lines = []
    for row in credits:
        links = []
        if row.get("creator_url"):
            links.append("Creator: " + row["creator_url"])
        source_url = row.get("source_url") or (row.get("url") if not row.get("creator_url") else None)
        if source_url:
            links.append("Coverr/source: " + source_url)
        elif row.get("url"):
            links.append(row["url"])
        lines.append(row["text"] + ((" — " + " | ".join(links)) if links else ""))
    return "\n".join(lines)


def create_publishing_metadata(plans):
    if "build_publishing_packs" in globals():       # CELL 10b: platform-native packs (Gemini + validators, local fallback)
        return build_publishing_packs(plans)
    return legacy_publishing_metadata(plans)


def legacy_publishing_metadata(plans):
    youtube, tiktok, instagram = {}, {}, {}
    for idx, plan in enumerate(plans, 1):
        title = plan.title.strip().rstrip(".!? ")[:92]
        hashtags = hashtags_for_plan(plan)
        transcript_excerpt = " ".join(s.transcript for s in plan.segments)
        prompt = "What part of this idea do you agree or disagree with, and why?"
        credits = asset_credit_records(idx)
        credit_block = format_asset_credits(credits)
        credit_suffix = ("\n\nAsset credits:\n" + credit_block) if credit_block else ""
        youtube[str(idx)] = {
            "title": title,
            "description": (f"{title}.\n\nA short extract from an authorized long-form source." + credit_suffix + "\n\n" + " ".join(hashtags)),
            "hashtags": hashtags, "pinned_comment": prompt,
            "asset_credits": credits,
            "music_note": "Embedded music is the cleared track listed in asset_license_manifest.json; do not replace it with unlicensed audio.",
        }
        tiktok[str(idx)] = {
            "caption": f"{title}. {prompt}" + (f"\n\n{credit_block}" if credit_block else "") + "\n" + " ".join(hashtags[:5]),
            "hashtags": hashtags[:5], "discussion_prompt": prompt, "asset_credits": credits,
        }
        instagram[str(idx)] = {
            "caption": f"{title}.\n\n{prompt}" + (f"\n\n{credit_block}" if credit_block else "") + "\n\n" + " ".join(hashtags),
            "hashtags": hashtags, "cover_text": title[:58], "asset_credits": credits,
        }
    return {"youtube": youtube, "tiktok": tiktok, "instagram": instagram}


EDITORIAL_PLAYBOOK = r"""# PROJECT ZIROQ — Editorial Playbook

## Operating principle
The selection score is a transparent editorial heuristic, not a model of actual audience retention or a promise of virality. The editor optimizes for a clear idea, source-faithful context, specificity, narrative progression, useful payoff, and distinctness across the five cuts.

## Curiosity and information gaps
Use a real question, contrast, unexpected but supportable statement, concrete detail, or unresolved problem as an opening. The following material must answer the question or deliver the promised explanation. Never invent a mystery, statistic, quotation, or payoff. This translates the information-gap account of curiosity into an editorial rule: open a gap only when the source contains a real answer.

## Narrative transportation
Look for a person, situation, stakes, conflict, turning point, and consequence. A personal example can be stronger than an abstract claim when the source contains enough context. Reordered excerpts must not imply that separate events occurred in a false chronology. Green and Brock describe transportation in terms of imagery, affect, and attentional focus; the practical edit should preserve those elements rather than merely increasing cut frequency.

## Memorability and shareability
Prefer concrete details, specific examples, clear contrasts, and a single understandable point. Avoid generic motivational filler. A useful clip should leave one sentence, question, or practical action that a viewer could accurately repeat to someone else. Shareability is a creative hypothesis, not an analytics result.

## Story shapes
Choose the structure supported by the source: hook → context → development → payoff; problem → mistake → consequence → solution; story → conflict → turning point → lesson; question → answer → explanation; or claim → evidence → qualification → conclusion. Do not force every source into one template.

## Editing and pacing
Remove silence and repetition only where meaning survives. Let a meaningful pause remain when it contributes to emotion or emphasis. Use a crop change, B-roll cutaway, diagram, statistic, or sound accent only when it clarifies or renews attention. Captions should group natural phrases and emphasize meaningful words; do not animate every word with arbitrary colour changes.

## Audio policy
Speech remains dominant. Music is ducked under speech and faded at the edges. The default fallback is locally synthesized original instrumental audio. An external track is accepted only when item-level metadata exposes a license on the explicit allowlist (CC0 or CC BY 3.0/4.0). Public Domain Mark is a rights-status mark, not a copyright licence, so it is not treated as a licence grant. CC BY-NC, CC BY-ND, CC BY-SA, unknown licenses, and unclear synchronization rights are excluded. Keep each license manifest with the master export.

## Visual truth
B-roll must illustrate the topic, not pretend that a stock actor participated in the speaker's event. If no suitable licensed asset is available, retain the speaker footage and use only extractive title/caption graphics. Do not fabricate charts, statistics, people, quotes, or archival evidence.

## Final human review
Automatic checks can measure codec, duration, frame samples, audio level, and timestamp mapping. They cannot conclusively judge whether a reordered story is fair, whether a joke lands, whether a facial crop follows the active speaker, or whether a particular example needs more context. Preview all five clips before publication.
"""

SOURCE_REGISTER = [
    {"title": "Loewenstein (1994), The Psychology of Curiosity", "url": "https://www.cmu.edu/dietrich/sds/docs/loewenstein/PsychofCuriosity.pdf", "use": "Information-gap framing; original source to consult"},
    {"title": "Green & Brock (2000), The Role of Transportation in the Persuasiveness of Public Narratives", "url": "https://doi.org/10.1037/0022-3514.79.5.701", "use": "Narrative transportation: imagery, affect, attention"},
    {"title": "YouTube audience retention help", "url": "https://support.google.com/youtube/answer/9314415", "use": "Use actual retention analytics after publication; do not treat editorial scores as audience data"},
    {"title": "YouTube Shorts guidance", "url": "https://support.google.com/youtube/answer/15424877", "use": "Platform-native publishing guidance"},
    {"title": "TikTok creator tips", "url": "https://newsroom.tiktok.com/5-tips-for-tiktok-creators", "use": "Platform creative guidance; review current guidance at publishing time"},
    {"title": "Mixkit Stock Music Free License", "url": "https://mixkit.co/license/modal/musicFree/", "use": "Manual import only; no scraping or automated catalogue downloading"},
    {"title": "Mixkit User Terms", "url": "https://mixkit.co/terms/", "use": "Restrictions include mass downloading/scraping and building a competing service"},
    {"title": "Pixabay API documentation", "url": "https://pixabay.com/api/docs/", "use": "Official video search endpoint, API request limits, 24-hour caching, and no systematic mass downloads"},
    {"title": "Pixabay Content License", "url": "https://pixabay.com/service/license-summary/", "use": "Use/adaptation permission and prohibited-use checks"},
    {"title": "Coverr API documentation", "url": "https://api.coverr.co/docs/start/", "use": "Free API tier, authentication, audio/video search, and download tracking"},
    {"title": "Coverr Content License", "url": "https://coverr.co/license/", "use": "Commercial use, attribution, premium/sponsored media, and AI-training restrictions"},
    {"title": "Internet Archive Metadata API", "url": "https://internetarchive.readthedocs.io/en/stable/metadata.html", "use": "Verify item-level licenseurl and available files"},
    {"title": "Internet Archive search guide", "url": "https://archivesupport.zendesk.com/hc/en-us/articles/360018359991-Search-A-Basic-Guide", "use": "Fielded queries and Boolean search operators"},
    {"title": "Sentence Transformers all-MiniLM-L6-v2 model card", "url": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2", "use": "Optional local embeddings; model card identifies Apache-2.0 license"},
    {"title": "Google Colab FAQ and resource limits", "url": "https://research.google.com/colaboratory/faq.html", "use": "Free GPU, VM lifetime, and usage are dynamic/not guaranteed"},
]


def create_contact_sheet(video_paths, out_path):
    thumbs = []
    for idx, path in enumerate(video_paths, 1):
        frame_path = os.path.join(RENDER_DIR, f"contact_{idx}.jpg")
        info = ffprobe_json(path)
        dur = float(info["format"]["duration"])
        run(["ffmpeg", "-y", "-ss", f"{min(dur*0.45, 20):.2f}", "-i", path, "-frames:v", "1", "-q:v", "3", frame_path], quiet=True)
        im = Image.open(frame_path).convert("RGB")
        im.thumbnail((240, 426))
        canvas = Image.new("RGB", (260, 470), "black")
        canvas.paste(im, ((260-im.width)//2, 12))
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 444), f"CLIP {idx:02d}  |  {dur:.1f}s", fill="white")
        thumbs.append(canvas)
    sheet = Image.new("RGB", (260*len(thumbs), 470), "#151515")
    for i, thumb in enumerate(thumbs):
        sheet.paste(thumb, (260*i, 0))
    sheet.save(out_path, quality=92)
    return out_path


def safe_copy_verified(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    temp = dst + ".uploading"
    shutil.copy2(src, temp)
    if os.path.getsize(temp) != os.path.getsize(src) or sha256_file(temp) != sha256_file(src):
        try: os.remove(temp)
        except OSError: pass
        raise RuntimeError(f"Upload copy integrity mismatch: {os.path.basename(src)}")
    os.replace(temp, dst)
    return dst


def write_research_assets():
    with open(os.path.join(REPORT_DIR, "editorial_playbook.md"), "w", encoding="utf-8") as f:
        f.write(EDITORIAL_PLAYBOOK)
    atomic_write_json(os.path.join(REPORT_DIR, "source_register.json"), SOURCE_REGISTER)


def write_performance_template(path):
    header = ["run_id", "clip_index", "platform", "post_url", "views", "average_view_duration_sec",
              "completion_rate", "shares", "saves", "comments", "likes", "collected_utc"]
    if not os.path.isfile(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)


def enforce_five_output_count(plans, clip_results):
    if len(plans) != CFG["required_clip_count"] or len(clip_results) != CFG["required_clip_count"]:
        raise RuntimeError(f"Delivery blocked: expected exactly {CFG['required_clip_count']} plans and completed results; got {len(plans)} and {len(clip_results)}")
    return True


def build_and_deliver_package(plans, clip_results):
    enforce_five_output_count(plans, clip_results)
    if not all(x["technical_pass"] and x["editorial_pass"] for x in clip_results):
        raise RuntimeError("Delivery gate blocked: one or more clips failed QA")

    metadata = create_publishing_metadata(plans)
    atomic_write_json(os.path.join(REPORT_DIR, "youtube_metadata.json"), metadata["youtube"])
    atomic_write_json(os.path.join(REPORT_DIR, "tiktok_metadata.json"), metadata["tiktok"])
    atomic_write_json(os.path.join(REPORT_DIR, "instagram_metadata.json"), metadata["instagram"])
    atomic_write_json(os.path.join(REPORT_DIR, "clip_report.json"), {
        "run_id": RUN_ID, "source": asdict(SOURCE), "language": TRANSCRIPT.language,
        "similarity_backend": SIMILARITY_BACKEND, "clips": clip_results,
        "disclaimer": "Editorial heuristic scores are not measured audience retention or virality predictions.",
    })
    atomic_write_json(os.path.join(REPORT_DIR, "quality_report.json"), {
        "run_id": RUN_ID,
        "summary": {
            "expected_clips": CFG["required_clip_count"],
            "verified_clips": sum(1 for item in clip_results if item["technical_pass"] and item["editorial_pass"]),
            "all_technical_checks_passed": all(item["technical_pass"] for item in clip_results),
            "all_editorial_checks_passed": all(item["editorial_pass"] for item in clip_results),
        },
        "clips": [{
            "index": item["index"], "title": item["title"], "duration": item["duration"],
            "technical_checks": item["technical_checks"], "editorial_checks": item["editorial_checks"],
            "human_review_required": item.get("human_review_required", True),
        } for item in clip_results],
        "limitations": [
            "Automated checks cannot prove narrative fairness, factual truth, or whether a reordered excerpt changes implied chronology.",
            "Human review of all five complete exports remains required before publication.",
        ],
    })
    write_research_assets()
    write_performance_template(os.path.join(REPORT_DIR, "performance_history.csv"))
    contact_sheet = create_contact_sheet([x["final_path"] for x in clip_results], os.path.join(REPORT_DIR, "preview_contact_sheet.jpg"))
    guide = ["# Publishing guide", "", f"Run ID: `{RUN_ID}`", "", "Review each MP4 and cover before upload.",
             "Use the platform-specific JSON file for the title/caption and hashtags.",
             "Verify the music and stock-footage license manifest before publishing or transferring the masters.",
             "Use the platform metadata asset_credits; free-tier Coverr API assets require a Coverr link and a creator-profile link when a creator is listed.",
             "Do not use sponsored partner items or watermarked premium previews unless separately licensed.",
             "Coverr assets and metadata must never be used for AI training, fine-tuning, model evaluation, or datasets.",
             "The source speaker's words are original excerpts; cover titles are editorial overlays.",
             "Do not describe the heuristic score as measured retention or guaranteed virality.", ""]
    with open(os.path.join(REPORT_DIR, "publishing_guide.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(guide))
    atomic_write_json(os.path.join(REPORT_DIR, "asset_license_manifest.json"), ASSET_MANIFEST)

    # All five local outputs are QA-passed before any final Drive folder is created.
    if DRIVE_AVAILABLE:
        source_folder = safe_filename(SOURCE.title, 64) + "_" + RUN_ID
        drive_folder = os.path.join(DRIVE_ROOT, source_folder)
        folders = {
            "videos": os.path.join(drive_folder, "FINAL_VIDEOS"),
            "covers": os.path.join(drive_folder, "COVERS"),
            "publishing": os.path.join(drive_folder, "PUBLISHING_PACK"),
            "research": os.path.join(drive_folder, "RESEARCH"),
            "reports": os.path.join(drive_folder, "REPORTS"),
        }
        for folder in folders.values(): os.makedirs(folder, exist_ok=True)
        delivered = []
        for item in clip_results:
            delivered_path = safe_copy_verified(item["final_path"], os.path.join(folders["videos"], os.path.basename(item["final_path"])))
            drive_info = ffprobe_json(delivered_path)
            drive_duration = float(drive_info["format"]["duration"])
            if abs(drive_duration-item["duration"]) > 0.15:
                raise RuntimeError("Drive copy duration verification failed")
            cover_src = item["cover_path"]
            safe_copy_verified(cover_src, os.path.join(folders["covers"], os.path.basename(cover_src)))
            delivered.append({"path": delivered_path, "duration": drive_duration, "sha256": sha256_file(delivered_path)})
        for name in ("youtube_metadata.json", "tiktok_metadata.json", "instagram_metadata.json", "publishing_guide.md"):
            safe_copy_verified(os.path.join(REPORT_DIR, name), os.path.join(folders["publishing"], name))
        pack_src = os.path.join(REPORT_DIR, "publishing_pack")
        if os.path.isdir(pack_src):
            for name in sorted(os.listdir(pack_src)):
                safe_copy_verified(os.path.join(pack_src, name), os.path.join(folders["publishing"], name))
        for name in ("editorial_playbook.md", "source_register.json"):
            safe_copy_verified(os.path.join(REPORT_DIR, name), os.path.join(folders["research"], name))
        for name in os.listdir(REPORT_DIR):
            src = os.path.join(REPORT_DIR, name)
            if os.path.isfile(src):
                safe_copy_verified(src, os.path.join(folders["reports"], name))
        atomic_write_json(os.path.join(drive_folder, "DELIVERY_VERIFICATION.json"), {
            "run_id": RUN_ID, "five_videos": len(delivered), "verified": len(delivered) == int(CFG["required_clip_count"]),
            "files": delivered, "contact_sheet": os.path.join(folders["reports"], os.path.basename(contact_sheet)),
        })
        print("\nDRIVE DELIVERY VERIFIED:", drive_folder)
        for item in delivered: print(" ", item["path"], f"({item['duration']:.2f}s)")
        return drive_folder
    else:
        print("\nAll five local outputs passed the delivery gate; Google Drive was not available in this runtime.")
        return RUN_DIR


write_research_assets()
log("delivery", "publishing, playbook, license, reporting, and integrity-gated delivery functions ready")
