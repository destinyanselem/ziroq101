# CELL 0 — Runtime setup (no PyAV reinstall, no broad environment upgrade)
!apt-get -qq update && apt-get -qq install -y ffmpeg fonts-dejavu-core
%pip -q install yt-dlp faster-whisper ctranslate2 numpy scipy opencv-python-headless pillow requests scikit-learn

%pip -q install "google-genai>=2.3.0"   # Gemini client (optional: notebook runs local-only without keys)

# Optional semantic-embedding backend (recommended; small model, local inference):
# Uncomment this line if sentence-transformers is not already available in the runtime.
# %pip -q install sentence-transformers
