# CELL 0b — START HERE (phone-friendly form: no console input box)
#@title ▶ START HERE — paste your link and (optionally) type I AGREE { display-mode: "form" }
VIDEO_URL = ""  #@param {type:"string"}
TYPE_I_AGREE_TO_USE_GEMINI = ""  #@param {type:"string"}
# Gemini consent text (read before typing I AGREE): your TRANSCRIPT is sent to Google's Gemini API. On Google's UNPAID tier
# the content may be used to improve Google products and read by human reviewers, so never use private/confidential videos.
# Leave the second box empty to run 100% local (no Gemini). Use keys from AI Studio projects with NO billing to stay at ₦0.
import os
if VIDEO_URL.strip():
    os.environ["ZIROQ_URL"] = VIDEO_URL.strip()
os.environ["ZIROQ_GEMINI_CONSENT"] = TYPE_I_AGREE_TO_USE_GEMINI.strip()
print("Link saved." if VIDEO_URL.strip() else "No link in the form: Cell 4 will ask for it.",
      "| Gemini:", "consent received" if TYPE_I_AGREE_TO_USE_GEMINI.strip() == "I AGREE" else "OFF (local-only)")
