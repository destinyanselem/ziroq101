# CELL 3 — Google Drive mount (upload happens only after all five clips pass)
DRIVE_AVAILABLE = False
try:
    from google.colab import drive
    if not os.path.ismount(CFG["drive_mount"]):
        drive.mount(CFG["drive_mount"])
    DRIVE_AVAILABLE = os.path.isdir(os.path.join(CFG["drive_mount"], "MyDrive"))
except Exception as exc:
    print("Google Drive is not mounted in this runtime:", type(exc).__name__, str(exc)[:300])

DRIVE_ROOT = os.path.join(CFG["drive_mount"], "MyDrive", CFG["drive_root_name"])
if DRIVE_AVAILABLE:
    os.makedirs(DRIVE_ROOT, exist_ok=True)
    print("Drive delivery root:", DRIVE_ROOT)
else:
    print("Drive upload will be skipped. Verified local outputs remain in:", RUN_DIR)
