# CELL 12 — Run summary, review checklist, and a one-tap download when Google Drive is not connected
import zipfile
print("\nPROJECT ZIROQ RUN SUMMARY")
print("Source:", SOURCE.title)
print("Source duration:", f"{SOURCE.duration/60:.1f} min")
print("Transcript:", f"{len(TRANSCRIPT.words):,} words / {len(TRANSCRIPT.sentences):,} sentences")
print("Verified videos:", len(CLIP_RESULTS))
try:
    _yt = json.load(open(os.path.join(REPORT_DIR, "youtube_metadata.json"), encoding="utf-8"))
except Exception:
    _yt = {}
for item in CLIP_RESULTS:
    t = (_yt.get(str(item["index"])) or {}).get("title", item["title"])
    rv = item.get("review") or {}
    print(f"  {item['index']}. {item['duration']:.0f}s{' (LONG)' if item.get('long_clip') else ''} | {t}"
          + (f" | AI review {rv['score']}/10 ({rv['verdict']})" if rv.get("score") is not None else ""))
    for fl in item.get("review_flags", [])[:2]:
        print("       check:", fl[:110])
print("\nDelivery location:", DELIVERY_LOCATION)
print("Posting packs (titles, descriptions, tags, hashtags, PINNED COMMENTS for every platform):")
print("   PUBLISHING_PACK/ALL_CLIPS_PUBLISHING_PACK.md  and  clip_01_PUBLISHING_PACK.md, clip_02_... ")

if not DRIVE_AVAILABLE and CFG.get("download_zip_when_no_drive", True):
    zip_path = os.path.join(RUN_DIR, "ZIROQ_package.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for item in CLIP_RESULTS:
            z.write(item["final_path"], "FINAL_VIDEOS/" + os.path.basename(item["final_path"]))
            if item.get("cover_path") and os.path.isfile(item["cover_path"]):
                z.write(item["cover_path"], "COVERS/" + os.path.basename(item["cover_path"]))
        pack_dir = os.path.join(REPORT_DIR, "publishing_pack")
        for root_dir, _, names in os.walk(pack_dir):
            for n in names:
                z.write(os.path.join(root_dir, n), "PUBLISHING_PACK/" + n)
        for n in ("youtube_metadata.json", "tiktok_metadata.json", "instagram_metadata.json", "preview_contact_sheet.jpg", "asset_license_manifest.json"):
            p = os.path.join(REPORT_DIR, n)
            if os.path.isfile(p):
                z.write(p, "REPORTS/" + n)
    print(f"\nDrive is not connected, so everything is zipped here: {zip_path} ({os.path.getsize(zip_path)/1e6:.0f} MB)")
    try:
        from google.colab import files
        files.download(zip_path)
        print("Your browser should start downloading the zip now (check your Downloads).")
    except Exception as exc:
        print("Automatic download unavailable here; open the Colab Files panel (folder icon), find the zip above, and download it.", type(exc).__name__)

print("\nHuman review before publishing:")
print("  [ ] Watch every short from beginning to end")
print("  [ ] Verify reordered excerpts do not alter context or imply false chronology")
print("  [ ] Verify captions, names, numbers, and speaker meaning")
print("  [ ] Read the title and pinned comment in the posting pack; edit anything that feels off")
print("  [ ] Confirm source usage rights and any third-party asset license manifest")
