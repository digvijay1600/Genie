import requests
import streamlit as st
import base64
import time
import msal
import os
from dotenv import load_dotenv
import ast
import json
import html
import re

# METHOD 3: IMPROVED CUSTOM COMPONENT IMPORTS WITH PROPER ERROR HANDLING
COMPONENT_AVAILABLE = False
COMPONENT_TYPE = None

# Only try to import components when we actually need them
def try_import_components():
    global COMPONENT_AVAILABLE, COMPONENT_TYPE
    
    if COMPONENT_AVAILABLE is not False:  # Already tried
        return
        
    # Skip problematic components - use fallback only
    COMPONENT_AVAILABLE = False
    COMPONENT_TYPE = None
    # st.info("💡 Using enhanced fallback buttons with full-width CSS")
    
load_dotenv()

# UI rendering options
# Set to True to show typing effect (character-by-character). Set to False for instant rendering.
ENABLE_TYPING_EFFECT = os.getenv('ENABLE_TYPING_EFFECT', 'true').lower() in ('1', 'true', 'yes')
TYPING_DELAY = float(os.getenv('TYPING_DELAY', '0.01'))

CLIENT_ID = os.getenv('CLIENT_ID')
TENANT_ID = os.getenv('TENANT_ID')
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["User.Read"]
API_BASE = "http://127.0.0.1:8000"
REDIRECT_URI = "http://localhost:8501"
logo_path = "tcs_logo.png"

def initiate_login():
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    return app.get_authorization_request_url(SCOPES, redirect_uri=REDIRECT_URI)

def handle_token_response():
    code = st.query_params.get('code')
    if not code:
        return None
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    token_response = app.acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    if "access_token" in token_response:
        return token_response
    return None


def clean_agent_text(raw_text: str) -> str:
    """Clean agent output for UI rendering:
    - Replace literal "\\n" sequences with real newlines
    - Convert markdown H2 headers (## ...) to bold (avoid large fonts)
    - Attempt to unescape common escaped sequences safely
    """
    if raw_text is None:
        return ""
    try:
        text = str(raw_text)
    except Exception:
        text = ""

    # Replace literal backslash-n with real newlines
    if "\\n" in text:
        text = text.replace('\\n', '\n')

    # Try to decode any unicode-escaped sequences (best-effort)
    try:
        decoded = bytes(text, "utf-8").decode("unicode_escape")
        # Only keep if it doesn't radically change length (defensive)
        if 0 < len(decoded) <= max(10000, len(text) * 5):
            text = decoded
    except Exception:
        pass

    # Convert markdown H2 headers to bold to avoid enlarged font
    try:
        text = re.sub(r'(?m)^##\s*(.+)$', r'**\1**', text)
    except Exception:
        pass

    # Detect and attempt to recover common mojibake patterns (UTF-8 bytes
    # incorrectly decoded as latin-1/cp1252), e.g. 'â¢' instead of '•', or
    # emoji fragments like 'ð'. Try several recoveries and pick the best.
    try:
        def score_candidate(s: str) -> int:
            # Prefer candidates with more non-ASCII glyphs and fewer replacement chars
            if not s:
                return 0
            repl_count = s.count('\ufffd') + s.count('�')
            high_ord = sum(1 for c in s if ord(c) > 127)
            return high_ord - (repl_count * 10)

        candidates = [text]

        # If there are suspicious bytes/characters, attempt recovery passes
        if re.search(r'(â|Ã\w|Ã\x|ð\x|ð[\x00-\x7F])', text) or 'â' in text or 'ð' in text or 'Ã' in text:
            # try latin-1 -> utf-8
            try:
                candidates.append(text.encode('latin-1').decode('utf-8'))
            except Exception:
                pass

            # try cp1252 -> utf-8
            try:
                candidates.append(text.encode('cp1252').decode('utf-8'))
            except Exception:
                pass

            # try round-trip: encode utf-8 then decode latin-1 then decode utf-8
            try:
                round_trip = text.encode('utf-8', errors='surrogatepass').decode('latin-1')
                candidates.append(round_trip)
                try:
                    candidates.append(round_trip.encode('latin-1').decode('utf-8'))
                except Exception:
                    pass
            except Exception:
                pass

        # Choose the highest-scoring candidate
        best = max(candidates, key=score_candidate)
        if best != text:
            text = best
    except Exception:
        pass

    # Apply a small mapping for well-known mojibake sequences that still
    # appear after heuristics; this catches many common artifacts produced
    # by double- or wrong-decodings (bullets, dashes, smart quotes, emoji fragments)
    try:
        replacements = {
            'â¢': '•',
            'â€¢': '•',
            'â€“': '–',
            'â€”': '—',
            'â€': '”',
            'â': '“',
            'â€™': '’',
            'â€œ': '“',
            'â€': '”',
            'â€¦': '…',
            'Ã©': 'é',
            'Ã ': 'à',
            'Ã¨': 'è',
            'Ãª': 'ê',
            'Ã¢': 'â',
            'Ã¶': 'ö',
            'Ã¼': 'ü',
            'Ã¶': 'ö',
            'Ã±': 'ñ',
            # common emoji fragment fixes
            'ð': '👋',
            'ð¡': '💡',
            'ð': '😊',
            'ð': '🤝'
        }
        for k, v in replacements.items():
            if k in text:
                text = text.replace(k, v)
    except Exception:
        pass

    # Defensive: strip any raw HTML tags from agent output to avoid
    # accidental layout breaking when rendered with `unsafe_allow_html=True`.
    try:
        text = re.sub(r'</?[^>]+>', '', text)
    except Exception:
        pass

    return text


def build_agent_html(label: str, text: str) -> str:
    """Return an HTML-safe block for rendering agent replies in normal font.
    Preserves newlines using CSS `white-space: pre-wrap;` and escapes HTML.
    """
    clean = clean_agent_text(text)
    escaped = html.escape(clean)
    # Use a light text color for agent replies so they are visible in Streamlit dark theme
    html_block = (
        f'<div class="agent-reply">'
        f'<strong style="color: #e6e6e6;">{html.escape(label)}:</strong>'
        f'<div style="font-size:16px; white-space: pre-wrap; margin-top:6px; color: #e6e6e6;">{escaped}</div>'
        f'</div>'
    )
    return html_block

def get_image_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode()

st.set_page_config(page_title="Genie - Identity and Access Management  Agentic AI Service", page_icon=logo_path, layout="wide")

auth_url = initiate_login()
azure_logout_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/logout?post_logout_redirect_uri={REDIRECT_URI}"

# CSS
st.markdown("""
<style>
.header-container {
    position: fixed;
    top: 60px;
    left: 0;
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 16px;
    border-bottom: 1px solid #e9ecef;
    background: black;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    z-index: 1500;
    transition: margin-left 0.3s ease, width 0.3s ease;
}

[data-testid="stSidebar"][aria-expanded="true"] ~ div .header-container {
    margin-left: 280px;
    width: calc(100% - 280px);
}

@media (max-width: 991px) {
    [data-testid="stSidebar"][aria-expanded="true"] ~ div .header-container {
        margin-left: 200px;
        width: calc(100% - 200px);
    }
}

@media (max-width: 600px) {
    [data-testid="stSidebar"][aria-expanded="true"] ~ div .header-container {
        margin-left: 0;
        width: 100%;
        top: 110px;
    }
    .header-title {
        font-size: 18px;
    }
    .header-container img {
        height: 30px;
    }
    .footer {
        height: 72px;
    }
    .stChatFloatingInputContainer {
        bottom: 80px !important;
    }
}

.main-content-logged-out {
    margin-top: 120px;
    text-align: center;
    font-size: 18px;
    font-weight: 700;
    color: #555555;
    padding: 10px 16px;
}

.block-container {
    padding-top: 106px;
    padding-bottom: 90px;
}

.header-left {
    display: flex;
    align-items: center;
    gap: 12px;
}

.header-right {
    display: flex;
    align-items: center;
    gap: 10px;
}

.header-container img {
    height: 36px;
    width: auto;
    display: block;
}

.header-title {
    font-size: 22px;
    font-weight: 700;
    line-height: 1;
    margin: 0;
    padding: 0;
    color: white;
}

.auth-button {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    border: none;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    color: #fff !important;
    transition: transform .2s ease;
}

.login-btn {
    background: linear-gradient(135deg, #007bff 0%, #0056b3 100%);
}
.login-btn:hover {
    transform: translateY(-1px);
}
.logout-btn {
    background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);
}
.logout-btn:hover {
    transform: translateY(-1px);
}

.centered-intro {
    text-align: center;
    font-size: 18px;
    color: #333;
    margin-top: 24px;
}

.message-container.no-messages {
    min-height: 30vh;
}

.footer {
    position: fixed;
    left: 0;
    bottom: 0;
    width: 100%;
    height: 30px;
    background: black;
    border-top: 1px solid white;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 16px;
    z-index: 10001;
    font-size: 12.5px;
    color: rgb(255 255 255);
}

.stChatFloatingInputContainer {
    bottom: 72px !important;
}

.st-emotion-cache-zy6yx3 {
    padding: 2rem 1rem 4rem !important;
}

/* Entra Service specific styles */
.quick-actions-container {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: center;
    margin: 20px 0;
}

.quick-action-btn {
    background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
    border: 2px solid #dee2e6;
    border-radius: 12px;
    padding: 12px 20px;
    font-weight: 600;
    font-size: 14px;
    transition: all 0.3s ease;
    cursor: pointer;
    min-width: 150px;
    text-align: center;
}

.quick-action-btn:hover {
    background: linear-gradient(135deg, #007bff 0%, #0056b3 100%);
    color: white;
    border-color: #007bff;
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,123,255,0.3);
}

.operation-status {
    padding: 10px;
    border-radius: 8px;
    margin: 10px 0;
    font-weight: 600;
}

.status-running {
    background-color: #fff3cd;
    border-left: 4px solid #ffc107;
    color: #856404;
}

.status-success {
    background-color: #d1e7dd;
    border-left: 4px solid #198754;
    color: #0f5132;
}

.status-error {
    background-color: #f8d7da;
    border-left: 4px solid #dc3545;
    color: #721c24;
}

/* ULTRA-AGGRESSIVE FULL-WIDTH SIDEBAR BUTTONS */
section[data-testid="stSidebar"] {
    width: 280px !important;
    min-width: 280px !important;
    max-width: 280px !important;
}

section[data-testid="stSidebar"] div.stButton,
section[data-testid="stSidebar"] .stButton,
section[data-testid="stSidebar"] div[data-testid="column"] div.stButton,
section[data-testid="stSidebar"] div[data-testid="column"] .stButton,
section[data-testid="stSidebar"] .element-container div.stButton,
section[data-testid="stSidebar"] .element-container .stButton,
[data-testid="stSidebar"] div.stButton,
[data-testid="stSidebar"] .stButton {
    width: 100% !important;
    min-width: 100% !important;
    max-width: 100% !important;
    margin: 0 0 8px 0 !important;
    padding: 0 !important;
    display: block !important;
    box-sizing: border-box !important;
}

section[data-testid="stSidebar"] div.stButton > button,
section[data-testid="stSidebar"] .stButton > button,
section[data-testid="stSidebar"] div[data-testid="column"] div.stButton > button,
section[data-testid="stSidebar"] div[data-testid="column"] .stButton > button,
section[data-testid="stSidebar"] .element-container div.stButton > button,
section[data-testid="stSidebar"] .element-container .stButton > button,
[data-testid="stSidebar"] div.stButton > button,
[data-testid="stSidebar"] .stButton > button,
section[data-testid="stSidebar"] button,
[data-testid="stSidebar"] button {
    display: block !important;
    width: 100% !important;
    min-width: 100% !important;
    max-width: 100% !important;
    position: relative !important;
    left: 0 !important;
    right: 0 !important;
    margin: 0 0 8px 0 !important;
    padding: 12px 16px !important;
    background: #e5e5e5 !important;
    color: #111 !important;
    border: 1px solid transparent !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    text-align: left !important;
    cursor: pointer !important;
    box-shadow: none !important;
    font-family: inherit !important;
    font-size: inherit !important;
    white-space: normal !important;
    word-wrap: break-word !important;
    overflow-wrap: break-word !important;
    word-break: break-word !important;
    hyphens: auto !important;
    line-height: 1.3 !important;
    text-overflow: visible !important;
    overflow: visible !important;
    min-height: 44px !important;
    height: auto !important;
    box-sizing: border-box !important;
    float: none !important;
    clear: both !important;
    transform: none !important;
    transition: all 0.2s ease !important;
}

section[data-testid="stSidebar"] div.stButton > button:hover,
section[data-testid="stSidebar"] .stButton > button:hover,
[data-testid="stSidebar"] div.stButton > button:hover,
[data-testid="stSidebar"] .stButton > button:hover,
section[data-testid="stSidebar"] button:hover,
[data-testid="stSidebar"] button:hover {
    background: #f5f7fb !important;
    color: #111 !important;
    border-color: #dee2e6 !important;
    transform: none !important;
}

section[data-testid="stSidebar"] div.stButton > button > div,
section[data-testid="stSidebar"] .stButton > button > div,
[data-testid="stSidebar"] div.stButton > button > div,
[data-testid="stSidebar"] .stButton > button > div,
section[data-testid="stSidebar"] button > div,
[data-testid="stSidebar"] button > div {
    width: 100% !important;
    text-align: left !important;
    white-space: normal !important;
    word-wrap: break-word !important;
    line-height: 1.3 !important;
    padding: 0 !important;
    margin: 0 !important;
    overflow: visible !important;
    box-sizing: border-box !important;
}

section[data-testid="stSidebar"] .element-container,
section[data-testid="stSidebar"] div[data-testid="element-container"],
[data-testid="stSidebar"] .element-container,
[data-testid="stSidebar"] div[data-testid="element-container"] {
    width: 100% !important;
    max-width: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    box-sizing: border-box !important;
}

section[data-testid="stSidebar"] .css-1d391kg,
[data-testid="stSidebar"] .css-1d391kg {
    width: 100% !important;
    padding-left: 1rem !important;
    padding-right: 1rem !important;
    box-sizing: border-box !important;
}

/* Profile box styles */
section[data-testid="stSidebar"] .profile-box {
    position: fixed;
    bottom: 20px;
    left: 12px;
    display: flex;
    align-items: center;
    gap: 10px;
    width: 230px;
    z-index: 9999;
    cursor: pointer;
}

section[data-testid="stSidebar"] .profile-initials {
    background: linear-gradient(135deg, #007bff, #0056b3);
    color: white;
    font-weight: bold;
    border-radius: 50%;
    width: 40px;
    height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    flex-shrink: 0;
}

section[data-testid="stSidebar"] .profile-details {
    font-size: 13px;
    overflow: hidden;
    max-width: 160px;
    word-break: break-word;
    display: flex;
    flex-direction: column;
    gap: 2px;
}

section[data-testid="stSidebar"] .profile-displayname {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 150px;
    margin-bottom: 0;
    line-height: 1.1;
    font-weight: bold;
}

section[data-testid="stSidebar"] .profile-email {
    font-size: 11px;
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 150px;
    margin-top: 0;
    line-height: 1.1;
}

section[data-testid="stSidebar"] .hover-card {
    display: none;
    position: fixed;
    bottom: 60px;
    left: 12px;
    width: 260px;
    border-radius: 12px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.75);
    padding: 10px;
    font-size: 13px;
    z-index: 999999 !important;
    background: #222 !important;
    color: #fafafa !important;
    border: 1px solid #444 !important;
}

section[data-testid="stSidebar"] .profile-box:hover ~ .hover-card {
    display: block;
}

/* Tables/DataFrames styling for dark mode */
.stTable, .stDataFrame {
    background-color: #1e1e1e !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}
.stTable td, .stTable th {
    background-color: #1e1e1e !important;
    color: #f0f0f0 !important;
    border: 1px solid #444 !important;
}
.stTable th {
    background-color: #333 !important;
    font-weight: bold !important;
}
div[data-testid="stDataFrame"] {
    background-color: #1e1e1e !important;
    border-radius: 8px;
    overflow: hidden;
}
div[data-testid="stDataFrame"] table {
    color: #f0f0f0 !important;
}
div[data-testid="stDataFrame"] thead {
    background-color: #333 !important;
    color: #fff !important;
}
div[data-testid="stDataFrame"] tbody tr:nth-child(odd) {
    background-color: #2a2a2a !important;
}
div[data-testid="stDataFrame"] tbody tr:nth-child(even) {
    background-color: #1e1e1e !important;
}

/* ULTRA-AGGRESSIVE UNIFORM PROMPT BUTTONS - METHOD 3 STYLE */
/* Applied to prompt_, entra_prompt_, and ad_prompt_ keys */
.prompt-grid-container {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 20px;
    padding: 20px;
    max-width: 1300px;
    margin: 0 auto;
}

.prompt-row {
    display: flex;
    justify-content: center;
    gap: 20px;
    width: 100%;
    flex-wrap: nowrap;
}

/* NUCLEAR OPTION: FORCE UNIFORM CONTAINER DIMENSIONS */
div.stButton:has(button[key^="prompt_"]),
div.stButton:has(button[key^="entra_prompt_"]),
div.stButton:has(button[key^="ad_prompt_"]),
.stButton:has(button[key^="prompt_"]),
.stButton:has(button[key^="entra_prompt_"]),
.stButton:has(button[key^="ad_prompt_"]),
div[data-testid="column"] div.stButton:has(button[key^="prompt_"]),
div[data-testid="column"] div.stButton:has(button[key^="entra_prompt_"]),
div[data-testid="column"] div.stButton:has(button[key^="ad_prompt_"]),
div[data-testid="column"] .stButton:has(button[key^="prompt_"]),
div[data-testid="column"] .stButton:has(button[key^="entra_prompt_"]),
div[data-testid="column"] .stButton:has(button[key^="ad_prompt_"]),
.element-container div.stButton:has(button[key^="prompt_"]),
.element-container div.stButton:has(button[key^="entra_prompt_"]),
.element-container div.stButton:has(button[key^="ad_prompt_"]),
.element-container .stButton:has(button[key^="prompt_"]),
.element-container .stButton:has(button[key^="entra_prompt_"]),
.element-container .stButton:has(button[key^="ad_prompt_"]),
div[data-testid="column"]:has(button[key^="prompt_"]),
div[data-testid="column"]:has(button[key^="entra_prompt_"]),
div[data-testid="column"]:has(button[key^="ad_prompt_"]),
[data-testid="column"]:has(button[key^="prompt_"]),
[data-testid="column"]:has(button[key^="entra_prompt_"]),
[data-testid="column"]:has(button[key^="ad_prompt_"]) {
    /* BULLETPROOF FIXED DIMENSIONS */
    width: 380px !important;
    min-width: 380px !important;
    max-width: 380px !important;
    height: 120px !important;
    min-height: 120px !important;
    max-height: 120px !important;
    
    /* FORCE LAYOUT BEHAVIOR */
    margin: 0 !important;
    padding: 0 !important;
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    box-sizing: border-box !important;
    flex: none !important;
    flex-grow: 0 !important;
    flex-shrink: 0 !important;
    flex-basis: auto !important;
    position: relative !important;
    overflow: hidden !important;
}

/* NUCLEAR OPTION: FORCE UNIFORM BUTTON DIMENSIONS */
div.stButton > button[key^="prompt_"],
div.stButton > button[key^="entra_prompt_"],
div.stButton > button[key^="ad_prompt_"],
.stButton > button[key^="prompt_"],
.stButton > button[key^="entra_prompt_"],
.stButton > button[key^="ad_prompt_"],
div[data-testid="column"] div.stButton > button[key^="prompt_"],
div[data-testid="column"] div.stButton > button[key^="entra_prompt_"],
div[data-testid="column"] div.stButton > button[key^="ad_prompt_"],
div[data-testid="column"] .stButton > button[key^="prompt_"],
div[data-testid="column"] .stButton > button[key^="entra_prompt_"],
div[data-testid="column"] .stButton > button[key^="ad_prompt_"],
.element-container div.stButton > button[key^="prompt_"],
.element-container div.stButton > button[key^="entra_prompt_"],
.element-container div.stButton > button[key^="ad_prompt_"],
.element-container .stButton > button[key^="prompt_"],
.element-container .stButton > button[key^="entra_prompt_"],
.element-container .stButton > button[key^="ad_prompt_"],
div[data-testid="column"] button[key^="prompt_"],
div[data-testid="column"] button[key^="entra_prompt_"],
div[data-testid="column"] button[key^="ad_prompt_"],
[data-testid="column"] button[key^="prompt_"],
[data-testid="column"] button[key^="entra_prompt_"],
[data-testid="column"] button[key^="ad_prompt_"],
button[key^="prompt_"],
button[key^="entra_prompt_"],
button[key^="ad_prompt_"] {
    /* BULLETPROOF BUTTON DIMENSIONS */
    width: 380px !important;
    min-width: 380px !important;
    max-width: 380px !important;
    height: 120px !important;
    min-height: 120px !important;
    max-height: 120px !important;
    
    /* VISUAL STYLING */
    background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%) !important;
    border: 2px solid #dee2e6 !important;
    border-radius: 12px !important;
    color: #495057 !important;
    font-weight: 600 !important;
    font-size: 13px !important;
    line-height: 1.2 !important;
    
    /* LAYOUT BEHAVIOR - CRITICAL */
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    text-align: center !important;
    box-sizing: border-box !important;
    
    /* TEXT HANDLING - ENSURE WRAPPING */
    white-space: pre-wrap !important;
    word-wrap: break-word !important;
    overflow-wrap: break-word !important;
    word-break: break-word !important;
    hyphens: auto !important;
    overflow: hidden !important;
    
    /* POSITIONING AND SPACING */
    margin: 0 !important;
    padding: 12px !important;
    position: relative !important;
    
    /* FLEX BEHAVIOR - PREVENT AUTO-SIZING */
    flex: none !important;
    flex-grow: 0 !important;
    flex-shrink: 0 !important;
    flex-basis: auto !important;
    
    /* TRANSITIONS AND EFFECTS */
    transition: all 0.3s ease !important;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1) !important;
    cursor: pointer !important;
}

/* HOVER EFFECTS WITH MAXIMUM SPECIFICITY */
div.stButton > button[key^="prompt_"]:hover,
div.stButton > button[key^="entra_prompt_"]:hover,
div.stButton > button[key^="ad_prompt_"]:hover,
.stButton > button[key^="prompt_"]:hover,
.stButton > button[key^="entra_prompt_"]:hover,
.stButton > button[key^="ad_prompt_"]:hover,
div[data-testid="column"] div.stButton > button[key^="prompt_"]:hover,
div[data-testid="column"] div.stButton > button[key^="entra_prompt_"]:hover,
div[data-testid="column"] div.stButton > button[key^="ad_prompt_"]:hover,
div[data-testid="column"] .stButton > button[key^="prompt_"]:hover,
div[data-testid="column"] .stButton > button[key^="entra_prompt_"]:hover,
div[data-testid="column"] .stButton > button[key^="ad_prompt_"]:hover,
.element-container div.stButton > button[key^="prompt_"]:hover,
.element-container div.stButton > button[key^="entra_prompt_"]:hover,
.element-container div.stButton > button[key^="ad_prompt_"]:hover,
.element-container .stButton > button[key^="prompt_"]:hover,
.element-container .stButton > button[key^="entra_prompt_"]:hover,
.element-container .stButton > button[key^="ad_prompt_"]:hover,
button[key^="prompt_"]:hover,
button[key^="entra_prompt_"]:hover,
button[key^="ad_prompt_"]:hover {
    background: linear-gradient(135deg, #007bff 0%, #0056b3 100%) !important;
    color: white !important;
    border-color: #007bff !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 4px 12px rgba(0,123,255,0.3) !important;
}

/* BUTTON TEXT CONTAINER - MAXIMUM SPECIFICITY */
div.stButton > button[key^="prompt_"] > div,
div.stButton > button[key^="entra_prompt_"] > div,
div.stButton > button[key^="ad_prompt_"] > div,
.stButton > button[key^="prompt_"] > div,
.stButton > button[key^="entra_prompt_"] > div,
.stButton > button[key^="ad_prompt_"] > div,
button[key^="prompt_"] > div,
button[key^="entra_prompt_"] > div,
button[key^="ad_prompt_"] > div,
div.stButton > button[key^="prompt_"] > *,
div.stButton > button[key^="entra_prompt_"] > *,
div.stButton > button[key^="ad_prompt_"] > *,
.stButton > button[key^="prompt_"] > *,
.stButton > button[key^="entra_prompt_"] > *,
.stButton > button[key^="ad_prompt_"] > *,
button[key^="prompt_"] > *,
button[key^="entra_prompt_"] > *,
button[key^="ad_prompt_"] > * {
    width: 100% !important;
    height: 100% !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    text-align: center !important;
    white-space: pre-wrap !important;
    word-wrap: break-word !important;
    overflow-wrap: break-word !important;
    word-break: break-word !important;
    line-height: 1.2 !important;
    padding: 0 !important;
    margin: 0 !important;
    overflow: hidden !important;
    box-sizing: border-box !important;
}

/* RESPONSIVE BREAKPOINTS - MAINTAIN UNIFORMITY */
@media (max-width: 1300px) {
    div.stButton:has(button[key^="prompt_"]),
    div.stButton:has(button[key^="entra_prompt_"]),
    div.stButton:has(button[key^="ad_prompt_"]),
    .stButton:has(button[key^="prompt_"]),
    .stButton:has(button[key^="entra_prompt_"]),
    .stButton:has(button[key^="ad_prompt_"]),
    div.stButton > button[key^="prompt_"],
    div.stButton > button[key^="entra_prompt_"],
    div.stButton > button[key^="ad_prompt_"],
    .stButton > button[key^="prompt_"],
    .stButton > button[key^="entra_prompt_"],
    .stButton > button[key^="ad_prompt_"],
    button[key^="prompt_"],
    button[key^="entra_prompt_"],
    button[key^="ad_prompt_"],
    div[data-testid="column"]:has(button[key^="prompt_"]),
    div[data-testid="column"]:has(button[key^="entra_prompt_"]),
    div[data-testid="column"]:has(button[key^="ad_prompt_"]),
    [data-testid="column"]:has(button[key^="prompt_"]),
    [data-testid="column"]:has(button[key^="entra_prompt_"]),
    [data-testid="column"]:has(button[key^="ad_prompt_"]) {
        width: 340px !important;
        min-width: 340px !important;
        max-width: 340px !important;
        height: 110px !important;
        min-height: 110px !important;
        max-height: 110px !important;
    }
}

@media (max-width: 1100px) {
    div.stButton:has(button[key^="prompt_"]),
    div.stButton:has(button[key^="entra_prompt_"]),
    div.stButton:has(button[key^="ad_prompt_"]),
    .stButton:has(button[key^="prompt_"]),
    .stButton:has(button[key^="entra_prompt_"]),
    .stButton:has(button[key^="ad_prompt_"]),
    div.stButton > button[key^="prompt_"],
    div.stButton > button[key^="entra_prompt_"],
    div.stButton > button[key^="ad_prompt_"],
    .stButton > button[key^="prompt_"],
    .stButton > button[key^="entra_prompt_"],
    .stButton > button[key^="ad_prompt_"],
    button[key^="prompt_"],
    button[key^="entra_prompt_"],
    button[key^="ad_prompt_"],
    div[data-testid="column"]:has(button[key^="prompt_"]),
    div[data-testid="column"]:has(button[key^="entra_prompt_"]),
    div[data-testid="column"]:has(button[key^="ad_prompt_"]),
    [data-testid="column"]:has(button[key^="prompt_"]),
    [data-testid="column"]:has(button[key^="entra_prompt_"]),
    [data-testid="column"]:has(button[key^="ad_prompt_"]) {
        width: 300px !important;
        min-width: 300px !important;
        max-width: 300px !important;
        height: 100px !important;
        min-height: 100px !important;
        max-height: 100px !important;
    }
    
    .prompt-row {
        gap: 15px;
    }
}

@media (max-width: 768px) {
    .prompt-row {
        flex-direction: column;
        align-items: center;
        gap: 15px;
    }
    
    div.stButton:has(button[key^="prompt_"]),
    div.stButton:has(button[key^="entra_prompt_"]),
    div.stButton:has(button[key^="ad_prompt_"]),
    .stButton:has(button[key^="prompt_"]),
    .stButton:has(button[key^="entra_prompt_"]),
    .stButton:has(button[key^="ad_prompt_"]),
    div.stButton > button[key^="prompt_"],
    div.stButton > button[key^="entra_prompt_"],
    div.stButton > button[key^="ad_prompt_"],
    .stButton > button[key^="prompt_"],
    .stButton > button[key^="entra_prompt_"],
    .stButton > button[key^="ad_prompt_"],
    button[key^="prompt_"],
    button[key^="entra_prompt_"],
    button[key^="ad_prompt_"],
    div[data-testid="column"]:has(button[key^="prompt_"]),
    div[data-testid="column"]:has(button[key^="entra_prompt_"]),
    div[data-testid="column"]:has(button[key^="ad_prompt_"]),
    [data-testid="column"]:has(button[key^="prompt_"]),
    [data-testid="column"]:has(button[key^="entra_prompt_"]),
    [data-testid="column"]:has(button[key^="ad_prompt_"]) {
        width: 280px !important;
        min-width: 280px !important;
        max-width: 280px !important;
        height: 90px !important;
        min-height: 90px !important;
        max-height: 90px !important;
    }
}

.centered-intro {
    text-align: center;
    font-size: 18px;
    font-weight: 500;
    color: #495057;
    margin-bottom: 40px;
}

@media (max-width: 768px) {
    section[data-testid="stSidebar"] {
        width: 200px !important;
        min-width: 200px !important;
        max-width: 200px !important;
    }
    
    section[data-testid="stSidebar"] button,
    [data-testid="stSidebar"] button {
        padding: 10px 12px !important;
        font-size: 14px !important;
        min-height: 40px !important;
    }
}
</style>
""", unsafe_allow_html=True)

# Global tooltip JS/CSS initializer for prompt hover full-text display
st.markdown(
    """
    <style>
    /* Wrapper and overlay for full-text prompt hover (pure CSS, no JS) */
    .prompt-wrapper { position: relative; display: block; width: 100%; }
    .prompt-overlay {
        position: absolute;
        top: calc(100% + 8px);
        left: 0;
        display: none;
        background: #111;
        color: #fff;
        border: 1px solid #444;
        border-radius: 12px;
        padding: 12px 14px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.6);
        z-index: 9999999;
        line-height: 1.4;
        font-size: 14px;
        max-width: min(600px, 90vw);
        white-space: normal;
        word-wrap: break-word;
        overflow-wrap: break-word;
        word-break: break-word;
    }
    .prompt-wrapper:has(button:hover) .prompt-overlay { display: block; }
    @media (max-width: 768px) {
        .prompt-overlay { max-width: 90vw; }
    }
    .prompt-tooltip-box {
        position: absolute;
        max-width: 600px;
        min-width: 320px;
        background: #111;
        color: #fff;
        border: 1px solid #444;
        border-radius: 12px;
        padding: 12px 14px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.6);
        z-index: 9999999;
        display: none;
        line-height: 1.4;
        font-size: 14px;
        white-space: normal;
        word-wrap: break-word;
        overflow-wrap: break-word;
        word-break: break-word;
    }
    @media (max-width: 768px) {
        .prompt-tooltip-box { max-width: 90vw; min-width: 60vw; }
    }
    </style>
    <script>
    (function(){
      if (window.__promptTooltipInit) return; // init once per rerun
      window.__promptTooltipInit = true;
      function ensureBox(){
        let box = document.getElementById('prompt-tooltip-box');
        if (!box) {
          box = document.createElement('div');
          box.id = 'prompt-tooltip-box';
          box.className = 'prompt-tooltip-box';
          document.body.appendChild(box);
        }
        return box;
      }
      window.showPromptTooltip = function(btn){
        try {
          const box = ensureBox();
          const full = btn.getAttribute('data-fulltext') || btn.innerText || '';
          box.textContent = full;
          const rect = btn.getBoundingClientRect();
          const top = rect.bottom + window.scrollY + 8;
          let left = rect.left + window.scrollX;
          // prevent off-screen right overflow
          box.style.display = 'block';
          box.style.left = left + 'px';
          box.style.top = top + 'px';
          const boxRect = box.getBoundingClientRect();
          const overflowX = (boxRect.right) - (window.scrollX + window.innerWidth - 12);
          if (overflowX > 0) {
            left = Math.max(12 + window.scrollX, left - overflowX);
            box.style.left = left + 'px';
          }
        } catch(e) { /* no-op */ }
      }
      window.hidePromptTooltip = function(){
        const box = document.getElementById('prompt-tooltip-box');
        if (box) box.style.display = 'none';
      }
    })();
    </script>
    """,
    unsafe_allow_html=True,
)

def render_header():
    is_authed = st.session_state.get("authenticated", False)
    if is_authed:
        auth_btn_html = '<a class="auth-button logout-btn" href="?app_logout=1">Logout</a>'
    else:
        auth_btn_html = f'<a class="auth-button login-btn" href="{auth_url}">Login</a>'
    st.markdown(f"""
    <div class="header-container">
        <div class="header-left">
            <img src="data:image/png;base64,{get_image_base64(logo_path)}" alt="TCS Logo" />
            <p class="header-title">Genie - Identity and Access Management  Agentic AI Service</p>
        </div>
        <div class="header-right">
            {auth_btn_html}
        </div>
    </div>
    """, unsafe_allow_html=True)

render_header()

st.markdown("""
<div class="footer">
    Copyright © 2025 Tata Consultancy Services | Entry to this site is restricted to employees and affiliates.
</div>
""", unsafe_allow_html=True)

if "active_page" not in st.session_state:
    st.session_state["active_page"] = "main_chat"

# Sidebar navigation
def create_custom_sidebar_navigation():
    """Fixed Method 3: Custom sidebar navigation with safe component loading"""
    
    navigation_options = [
        "👤 Assistant for End users",
        "👑 Assistant for IAM Admin", 
        "🔐 Microsoft Entra Service",
        "🅾️ Okta Service",
        "💎 Saviynt Service",
        "🏢 Active Directory Service",
        "📊 IAM Dashboard & Reports"
    ]
    
    page_mapping = {
        "👤 Assistant for End users": "main_chat",
        "👑 Assistant for IAM Admin": "orchestrator_chat",
        "🔐 Microsoft Entra Service": "entra_id_assistant",
        "🅾️ Okta Service": "okta_agent_assistant",
        "💎 Saviynt Service": "saviynt_agent_assistant",
        "🏢 Active Directory Service": "active_directory_assistant",
        "📊 IAM Dashboard & Reports": "iam_metrics_dashboard"
    }
    
    current_page = st.session_state.get("active_page", "main_chat")
    current_index = 0
    for i, page_key in enumerate(page_mapping.values()):
        if page_key == current_page:
            current_index = i
            break
    
    with st.sidebar:
        st.markdown('<div style="height: 10vh;"></div>', unsafe_allow_html=True)
        
        # Try to load and use components safely
        try_import_components()
        
        selected = None
        
        if COMPONENT_TYPE == "st_btn_select":
            try:
                from st_btn_select import st_btn_select
                selected = st_btn_select(
                    navigation_options,
                    index=current_index,
                    key="sidebar_nav_comp"  # Different key to avoid conflicts
                )
            except Exception as e:
                st.error(f"Component error: {str(e)}")
                selected = create_fallback_navigation(navigation_options, page_mapping, current_page)
                
        elif COMPONENT_TYPE == "option_menu":
            try:
                from streamlit_option_menu import option_menu
                selected = option_menu(
                    menu_title=None,
                    options=navigation_options,
                    default_index=current_index,
                    orientation="vertical",
                    key="sidebar_nav_comp"
                )
            except Exception as e:
                st.error(f"Component error: {str(e)}")
                selected = create_fallback_navigation(navigation_options, page_mapping, current_page)
        else:
            # Use fallback navigation
            selected = create_fallback_navigation(navigation_options, page_mapping, current_page)
        
        # Handle navigation selection
        if selected and selected in page_mapping:
            new_page = page_mapping[selected]
            if new_page != st.session_state.get("active_page"):
                # Track last page to detect page entry
                st.session_state["last_active_page"] = st.session_state.get("active_page")
                st.session_state["active_page"] = new_page
                st.rerun()

def create_fallback_navigation(navigation_options, page_mapping, current_page):
    """Fallback navigation using regular Streamlit buttons"""
    selected = None
    for i, (label, page_key) in enumerate(zip(navigation_options, page_mapping.values())):
        is_active = current_page == page_key

        if is_active:
            # Render a non-clickable highlighted block for the active page
            st.markdown(
                f"""
                <div style="
                    width: 100%; padding: 12px 16px; margin: 0 0 8px 0;
                    background: #000; color: #fff; border-left: 6px solid #fff;
                    border-radius: 8px; font-weight: 700; box-sizing: border-box;">
                    {label}
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            if st.button(
                label,
                key=f"fallback_nav_{page_key}",
                use_container_width=True,
                type="secondary",
            ):
                selected = label
    
    return selected


def _clear_main_chat_state():
    # End-user assistant
    for k in [
        "chat_history", "chat_input_value", "last_user_input",
        "original_prompt_value", "chat_text_input"
    ]:
        if k in st.session_state:
            del st.session_state[k]


def _clear_orchestrator_chat_state():
    # Admin/orchestrator assistant
    for k in [
        "orchestrator_chat_history",
        "orch_chat_input_value",
        "orch_original_prompt_value",
        "orch_chat_text_input",
        "orch_prefill_submit",
    ]:
        if k in st.session_state:
            del st.session_state[k]


def _clear_entra_chat_state():
    # Microsoft Entra services
    for k in [
        "entra_chat_history", "entra_chat_input_value",
        "entra_last_user_input", "entra_original_prompt_value", "entra_chat_text_input",
        "entra_agent_history"
    ]:
        if k in st.session_state:
            del st.session_state[k]


def handle_page_entry(active_page: str):
    """Reset chat state when the user navigates INTO a chat page from another page.
    Keeps the chat during continuous use of the same page.
    """
    last = st.session_state.get("last_active_page")
    if last == active_page:
        return  # no page switch

    if active_page == "main_chat":
        _clear_main_chat_state()
    elif active_page == "orchestrator_chat":
        _clear_orchestrator_chat_state()
    elif active_page == "entra_id_assistant":
        _clear_entra_chat_state()
    elif active_page == "active_directory_assistant":
        _clear_ad_chat_state()
    elif active_page == "okta_agent_assistant":
        _clear_okta_chat_state()
    elif active_page == "saviynt_agent_assistant":
        _clear_saviynt_chat_state()


def _clear_ad_chat_state():
    # Active Directory Service assistant (preserve ad_thread_id)
    for k in [
        "ad_chat_history", "ad_chat_input_value",
        "ad_last_user_input", "ad_original_prompt_value", "ad_chat_text_input"
    ]:
        if k in st.session_state:
            del st.session_state[k]


def _clear_okta_chat_state():
    # Okta Service assistant (preserve thread id as needed)
    for k in [
        "okta_chat_history", "okta_chat_input_value",
        "okta_last_user_input", "okta_original_prompt_value", "okta_chat_text_input",
        "okta_agent_history"
    ]:
        if k in st.session_state:
            del st.session_state[k]

def _clear_saviynt_chat_state():
    # Saviynt Service assistant (preserve thread id as needed)
    for k in [
        "saviynt_chat_history", "saviynt_chat_input_value",
        "saviynt_last_user_input", "saviynt_original_prompt_value", "saviynt_chat_text_input",
        "saviynt_agent_history", "saviynt_prefill_submit"
    ]:
        if k in st.session_state:
            del st.session_state[k]

def create_disabled_sidebar_navigation():
    """FIXED: Create disabled sidebar navigation WITHOUT using components"""
    
    with st.sidebar:
        st.title("Welcome")
        st.write("Please log in to access the IAM Assistant features.")
        st.markdown('<div style="height: 2vh;"></div>', unsafe_allow_html=True)
        
        # NEVER use custom components here - always use regular disabled buttons
        disabled_buttons = [
            "🔒 Assistant for End users",
            "🔒 Assistant for IAM Admin",
            "🔒 Microsoft Entra Service", 
            "🔒 Okta Service",
            "🔒 Saviynt Service",
            "🔒 Active Directory Service",
            "🔒 IAM Dashboard & Reports"
        ]
        
        for i, button_text in enumerate(disabled_buttons):
            if st.button(button_text, key=f"disabled_btn_{i}", use_container_width=True, disabled=True):
                st.sidebar.warning("🔒 Please log in to access this feature!")
        
        st.markdown('<div style="margin-top: 20px; font-size: 12px; color: #666; font-style: italic;">Login to enable these features</div>', unsafe_allow_html=True)

# SESSION STATE INITIALIZATION
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'user_info' not in st.session_state:
    st.session_state['user_info'] = {
        'name': 'Pradeep Vishwakarma',
        'preferred_username': 'pradeep.vishwakarma@example.com'
    }

# SIDEBAR NAVIGATION LOGIC
if st.session_state.get("authenticated", False):
    create_custom_sidebar_navigation()
    
    # Profile rendering
    user_info = st.session_state.get("user_info", {})
    def render_sidebar_profile(user_info: dict):
        """Render the fixed, hoverable profile box in the sidebar."""
        display_name = user_info.get("name", "User")
        email = user_info.get("preferred_username", "user@example.com")
        role = user_info.get("role", "Employee")
        initials = "".join([part[0].upper() for part in display_name.split()[:2]])

        st.sidebar.markdown(f"""
        <div class="profile-box">
            <div class="profile-initials">{initials}</div>
            <div class="profile-details">
                <strong class="profile-displayname">{display_name}</strong>
                <span class="profile-email" title="{email}">{email}</span>
            </div>
        </div>

        <div class="hover-card">
            <strong>Username:</strong> {display_name}<br/>
            <strong>Email:</strong> {email}<br/>
            <strong>Role:</strong> {role}<br/>
        </div>
        """, unsafe_allow_html=True)
    
    render_sidebar_profile(user_info)
    
else:
    create_disabled_sidebar_navigation()

# Handle logout - UPDATED WITH AD SESSION VARIABLES
if st.query_params.get("app_logout") == "1":
    for k in [
        "authenticated", "access_token", "thread_id", "chat_history", "user_info",
        "orch_thread_id", "orchestrator_chat_history", "active_page", "selected_prompt", 
        "chat_input_value", "last_input", "original_prompt_value", "entra_thread_id", 
        "entra_chat_history", "entra_agent_thread_id", "entra_agent_history",
        "show_user_input", "show_group_input", "show_create_user_form",
        "entra_chat_input_value", "entra_original_prompt_value", "ad_thread_id", 
        "ad_chat_history", "ad_chat_input_value", "ad_original_prompt_value"  # ← ADDED AD VARIABLES
    ]:
        st.session_state.pop(k, None)
    try:
        st.query_params.clear()
    except Exception:
        pass
    st.rerun()

if "code" in st.query_params:
    with st.spinner("Finalizing sign-in..."):
        token_response = handle_token_response()
        if token_response and "access_token" in token_response:
            st.session_state["authenticated"] = True
            st.session_state["access_token"] = token_response.get("access_token")
            st.session_state["user_info"] = token_response.get("id_token_claims", {})
            st.session_state.setdefault("orchestrator_chat_history", [])
            st.session_state.setdefault("active_page", "main_chat")
            try:
                st.query_params.clear()
            except Exception:
                pass
            st.success("Signed in successfully.")
            time.sleep(0.5)
            st.rerun()
        else:
            st.error("Authentication failed. Please try again.")
            try:
                st.query_params.clear()
            except Exception:
                pass

def show_intro():
    st.markdown('<div class="centered-intro">You are using "Assistant for End users" functionality​</div>', unsafe_allow_html=True)

def show_entra_intro():
    st.markdown('<div class="centered-intro">🔐 Microsoft Entra Service - IAM Operations</div>', unsafe_allow_html=True)

# NEW: ADD AD INTRO FUNCTION
def show_ad_intro():
    st.markdown('<div class="centered-intro">🏢 Active Directory Service - AD Provisioning Operations</div>', unsafe_allow_html=True)


# NEW: OKTA INTRO
def show_okta_intro():
    st.markdown('<div class="centered-intro">🅾️ Okta Service - Okta Provisioning & Administration</div>', unsafe_allow_html=True)

# NEW: SAVIYNT INTRO
def show_saviynt_intro():
    st.markdown('<div class="centered-intro">💎 Saviynt Service - Identity Governance & Access Management</div>', unsafe_allow_html=True)

def load_prompts_from_file(filepath: str = "prompts.json"):
    """Load prompts from JSON file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data.get('prompts', [])
    except FileNotFoundError:
        st.error(f"Prompts file '{filepath}' not found. Please create the file with your prompts.")
        return []
    except json.JSONDecodeError:
        st.error(f"Error reading prompts file '{filepath}'. Please check the JSON format.")
        return []

def load_entra_prompts_from_file(filepath: str = "entraPrompts.json"):
    """Load Entra prompts from JSON file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data.get('prompts', [])
    except FileNotFoundError:
        st.warning(f"Entra prompts file '{filepath}' not found. Using default prompts.")
        # Return default prompts if file doesn't exist
        return [
            "List all users in the organization",
            "Show me all security groups",
            "Create a new user account with standard permissions",
            "Reset password for a specific user",
            "Add user to a security group",
            "Remove user from a security group",
            "List all applications registered in Entra ID",
            "Show conditional access policies",
            "Generate IAM audit report for compliance"
        ]
    except json.JSONDecodeError:
        st.error(f"Error reading Entra prompts file '{filepath}'. Please check the JSON format.")
        return []

# NEW: ADD AD PROMPTS LOADING FUNCTION
def load_ad_prompts_from_file(filepath: str = "adPrompts.json"):
    """Load AD prompts from JSON file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data.get('prompts', [])
    except FileNotFoundError:
        st.warning(f"AD prompts file '{filepath}' not found. Using default prompts.")
        # Return default AD prompts if file doesn't exist
        return [
            "List all users in Active Directory",
            "Show me all security groups", 
            "List groups without owners",
            "Get user details for a specific user",
            "Show group members for a security group",
            "List inactive users",
            "Find groups not following naming convention",
            "Show groups with zero members",
            "List groups with inactive owners",
            "Create a new user account",
            "Create a new security group",
            "Add user to a group"
        ]
    except json.JSONDecodeError:
        st.error(f"Error reading AD prompts file '{filepath}'. Please check the JSON format.")
        return []

# NEW: ADD OKTA PROMPTS LOADING FUNCTION
def load_okta_prompts_from_file(filepath: str = "oktaPrompts.json"):
    """Load Okta prompts from JSON file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data.get('prompts', [])
    except FileNotFoundError:
        st.warning(f"Okta prompts file '{filepath}' not found. Using default prompts.")
        # Return default Okta prompts if file doesn't exist
        return [
            "List users in Okta",
            "Show me all Okta groups",
            "Find user by email or login",
            "Create a new Okta user with profile and password",
            "Deactivate an Okta user",
            "Reset an Okta user's password",
            "Assign user to a group",
            "Remove user from a group",
            "List applications and their owners",
            "Get recent sign-in events for a user",
            "Generate Okta audit report",
            "List apps with inline hooks or policies"
        ]
    except json.JSONDecodeError:
        st.error(f"Error reading Okta prompts file '{filepath}'. Please check the JSON format.")
        return []

def load_saviynt_prompts_from_file(filepath: str = "saviyntPrompts.json"):
    """Load Saviynt prompts from JSON file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data.get('prompts', [])
    except FileNotFoundError:
        st.warning(f"Saviynt prompts file '{filepath}' not found. Using default prompts.")
        # Return default Saviynt prompts if file doesn't exist
        return [
            "List users in Saviynt",
            "Show me all Saviynt roles",
            "Find user by email or username",
            "Create a new Saviynt user",
            "Deactivate a Saviynt user",
            "Assign user to a role",
            "Remove user from a role",
            "List access requests",
            "Approve pending access requests",
            "Generate access review report",
            "Show user entitlements",
            "Perform recertification tasks"
        ]
    except json.JSONDecodeError:
        st.error(f"Error reading Saviynt prompts file '{filepath}'. Please check the JSON format.")
        return []

# NEW: ADD ADMIN PROMPTS LOADING FUNCTION
def load_admin_prompts_from_file(filepath: str = "adminPrompts.json"):
    """Load Admin (Orchestrator) prompts from JSON file"""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data.get('prompts', [])
    except FileNotFoundError:
        st.warning(f"Admin prompts file '{filepath}' not found. Using default prompts.")
        return [
            "List ownerless groups in Entra ID (top 10)",
            "Show users added to Global Administrator in last 30 days",
            "Generate SoX access review summary for Finance apps",
            "Create a security group 'Contractors-AppX' with owner 'john.doe@contoso.com'",
            "Add 'jane.doe@contoso.com' to group 'HR-Privileged'",
            "Remove user 'temp.user@contoso.com' from 'All-Employees' (with confirmation)",
        ]
    except json.JSONDecodeError:
        st.error(f"Error reading Admin prompts file '{filepath}'. Please check the JSON format.")
        return []

def show_suggested_prompts():
    """FIXED: Display prompts with UNIFORM fixed dimensions using Method 3 approach"""
    
    # Load prompts from JSON file
    prompts = load_prompts_from_file()
    
    if not prompts:
        st.warning("No prompts found. Please add prompts to the prompts.json file.")
        return
    
    # Emoji mappings for prompts
    emoji_map = {
        0: "📝", 1: "🔄", 2: "🔐", 3: "🛡️", 4: "💼", 5: "🔧",
        6: "📋", 7: "⚙️", 8: "📊", 9: "🔍", 10: "🎯", 11: "📱", 12: "⚡"
    }
    
    cols = 3
    rows = (len(prompts) + cols - 1) // cols
    
    # Create centered container for the grid
    with st.container():
        st.markdown('<div class="prompt-grid-container">', unsafe_allow_html=True)
        
        # Render each row sequentially
        for r in range(rows):
            st.markdown('<div class="prompt-row">', unsafe_allow_html=True)
            
            # Create columns for this row
            cols_row = st.columns([1, 1, 1], gap="medium")
            
            for c in range(cols):
                prompt_index = r * cols + c
                
                with cols_row[c]:
                    if prompt_index < len(prompts):
                        # Display prompt button with uniform dimensions
                        prompt_text = prompts[prompt_index]
                        emoji = emoji_map.get(prompt_index, "💡")
                        
                        # Smart text truncation for uniform appearance
                        display_text = prompt_text
                        if len(prompt_text) > 45:
                            display_text = prompt_text[:42] + "..."
                        
                        button_text = f"{emoji}\n\n{display_text}"
                        # Wrap button and overlay in a container so CSS :has can show overlay on hover
                        st.markdown('<div class="prompt-wrapper">', unsafe_allow_html=True)
                        # THE KEY CHANGE: Add use_container_width=True (Method 3 style)
                        if st.button(
                            button_text, 
                            key=f"prompt_{r}_{c}",
                            help=prompt_text,
                            use_container_width=True  # ← THE MAGIC PARAMETER!
                        ):
                            st.session_state["chat_input_value"] = prompt_text  # Use full text
                            st.session_state["original_prompt_value"] = prompt_text
                            st.rerun()
                        # Full-text overlay (pure CSS, shown on hover)
                        st.markdown(
                            f'<div class="prompt-overlay">{html.escape(prompt_text)}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown('</div>', unsafe_allow_html=True)
                    
                    else:
                        # Empty space to maintain grid structure
                        st.markdown('<div style="height: 120px; visibility: hidden;"></div>', unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown('</div>', unsafe_allow_html=True)

def show_admin_suggested_prompts():
    """Display Admin (Orchestrator) prompts with uniform dimensions (Method 3)."""
    prompts = load_admin_prompts_from_file()
    if not prompts:
        st.warning("No Admin prompts found. Please add prompts to the adminPrompts.json file.")
        return

    emoji_map = {
        0: "👑", 1: "🆕", 2: "📋", 3: "👥", 4: "➕", 5: "➖",
        6: "🛡️", 7: "🔐", 8: "📊", 9: "🔍", 10: "⚙️", 11: "🔧"
    }

    cols = 3
    rows = (len(prompts) + cols - 1) // cols

    with st.container():
        st.markdown('<div class="prompt-grid-container">', unsafe_allow_html=True)
        for r in range(rows):
            st.markdown('<div class="prompt-row">', unsafe_allow_html=True)
            cols_row = st.columns([1, 1, 1], gap="medium")
            for c in range(cols):
                prompt_index = r * cols + c
                with cols_row[c]:
                    if prompt_index < len(prompts):
                        prompt_text = prompts[prompt_index]
                        emoji = emoji_map.get(prompt_index, "👑")
                        display_text = prompt_text if len(prompt_text) <= 45 else (prompt_text[:42] + "...")
                        button_text = f"{emoji}\n\n{display_text}"
                        st.markdown('<div class="prompt-wrapper">', unsafe_allow_html=True)
                        if st.button(
                            button_text,
                            key=f"admin_prompt_{r}_{c}",
                            help=prompt_text,
                            use_container_width=True,
                        ):
                            st.session_state["orch_chat_input_value"] = prompt_text
                            st.session_state["orch_original_prompt_value"] = prompt_text
                            st.rerun()
                        st.markdown(
                            f'<div class="prompt-overlay">{html.escape(prompt_text)}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown('</div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div style="height: 120px; visibility: hidden;"></div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

def show_entra_suggested_prompts():
    """Display Entra-specific prompts with UNIFORM fixed dimensions using Method 3 approach"""
    
    # Load Entra prompts from JSON file
    prompts = load_entra_prompts_from_file()
    
    if not prompts:
        st.warning("No Entra prompts found. Please add prompts to the entraPrompts.json file.")
        return
    
    # Emoji mappings for Entra prompts (IAM/Security focused)
    emoji_map = {
        0: "👥", 1: "🔒", 2: "🆕", 3: "🔑", 4: "➕", 5: "➖",
        6: "📱", 7: "🛡️", 8: "📋", 9: "🔍", 10: "⚙️", 11: "🔧", 12: "📊"
    }
    
    cols = 3
    rows = (len(prompts) + cols - 1) // cols
    
    # Create centered container for the grid
    with st.container():
        st.markdown('<div class="prompt-grid-container">', unsafe_allow_html=True)
        
        # Render each row sequentially
        for r in range(rows):
            st.markdown('<div class="prompt-row">', unsafe_allow_html=True)
            
            # Create columns for this row
            cols_row = st.columns([1, 1, 1], gap="medium")
            
            for c in range(cols):
                prompt_index = r * cols + c
                
                with cols_row[c]:
                    if prompt_index < len(prompts):
                        # Display prompt button with uniform dimensions
                        prompt_text = prompts[prompt_index]
                        emoji = emoji_map.get(prompt_index, "🔐")
                        
                        # Smart text truncation for uniform appearance
                        display_text = prompt_text
                        if len(prompt_text) > 45:
                            display_text = prompt_text[:42] + "..."
                        
                        button_text = f"{emoji}\n\n{display_text}"
                        # Wrap button and overlay in a container so CSS :has can show overlay on hover
                        st.markdown('<div class="prompt-wrapper">', unsafe_allow_html=True)
                        # THE KEY CHANGE: Add use_container_width=True (Method 3 style)
                        if st.button(
                            button_text, 
                            key=f"entra_prompt_{r}_{c}",  # Different key prefix
                            help=prompt_text,
                            use_container_width=True  # ← THE MAGIC PARAMETER!
                        ):
                            st.session_state["entra_chat_input_value"] = prompt_text  # Use full text
                            st.session_state["entra_original_prompt_value"] = prompt_text
                            st.rerun()
                        # Full-text overlay (pure CSS, shown on hover)
                        st.markdown(
                            f'<div class="prompt-overlay">{html.escape(prompt_text)}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown('</div>', unsafe_allow_html=True)
                    
                    else:
                        # Empty space to maintain grid structure
                        st.markdown('<div style="height: 120px; visibility: hidden;"></div>', unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown('</div>', unsafe_allow_html=True)

# NEW: ADD AD SUGGESTED PROMPTS FUNCTION
def show_ad_suggested_prompts():
    """Display AD-specific prompts with UNIFORM fixed dimensions using Method 3 approach"""
    
    # Load AD prompts from JSON file
    prompts = load_ad_prompts_from_file()
    
    if not prompts:
        st.warning("No AD prompts found. Please add prompts to the adPrompts.json file.")
        return
    
    # Emoji mappings for AD prompts (Directory focused)
    emoji_map = {
        0: "👥", 1: "🔒", 2: "👑", 3: "📋", 4: "👤", 5: "⏰",
        6: "📝", 7: "🚫", 8: "👔", 9: "🔍", 10: "⚙️", 11: "🔧", 12: "📊"
    }
    
    cols = 3
    rows = (len(prompts) + cols - 1) // cols
    
    # Create centered container for the grid
    with st.container():
        st.markdown('<div class="prompt-grid-container">', unsafe_allow_html=True)
        
        # Render each row sequentially
        for r in range(rows):
            st.markdown('<div class="prompt-row">', unsafe_allow_html=True)
            
            # Create columns for this row
            cols_row = st.columns([1, 1, 1], gap="medium")
            
            for c in range(cols):
                prompt_index = r * cols + c
                
                with cols_row[c]:
                    if prompt_index < len(prompts):
                        # Display prompt button with uniform dimensions
                        prompt_text = prompts[prompt_index]
                        emoji = emoji_map.get(prompt_index, "🏢")
                        
                        # Smart text truncation for uniform appearance
                        display_text = prompt_text
                        if len(prompt_text) > 45:
                            display_text = prompt_text[:42] + "..."
                        
                        button_text = f"{emoji}\n\n{display_text}"
                        # Wrap button and overlay in a container so CSS :has can show overlay on hover
                        st.markdown('<div class="prompt-wrapper">', unsafe_allow_html=True)
                        # THE KEY CHANGE: Add use_container_width=True (Method 3 style)
                        if st.button(
                            button_text, 
                            key=f"ad_prompt_{r}_{c}",  # Different key prefix
                            help=prompt_text,
                            use_container_width=True  # ← THE MAGIC PARAMETER!
                        ):
                            st.session_state["ad_chat_input_value"] = prompt_text  # Use full text
                            st.session_state["ad_original_prompt_value"] = prompt_text
                            st.rerun()
                        # Full-text overlay (pure CSS, shown on hover)
                        st.markdown(
                            f'<div class="prompt-overlay">{html.escape(prompt_text)}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown('</div>', unsafe_allow_html=True)
                    
                    else:
                        # Empty space to maintain grid structure
                        st.markdown('<div style="height: 120px; visibility: hidden;"></div>', unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown('</div>', unsafe_allow_html=True)

# NEW: ADD OKTA SUGGESTED PROMPTS FUNCTION
def show_okta_suggested_prompts():
    """Display Okta-specific prompts with UNIFORM fixed dimensions using Method 3 approach"""
    prompts = load_okta_prompts_from_file()

    if not prompts:
        st.warning("No Okta prompts found. Please add prompts to the oktaPrompts.json file.")
        return

    # Emoji mappings for Okta prompts
    emoji_map = {
        0: "👥", 1: "🔐", 2: "🧾", 3: "➕", 4: "🔁", 5: "➖",
        6: "👥", 7: "📤", 8: "📦", 9: "🔍", 10: "📊", 11: "⚙️"
    }

    cols = 3
    rows = (len(prompts) + cols - 1) // cols

    with st.container():
        st.markdown('<div class="prompt-grid-container">', unsafe_allow_html=True)
        for r in range(rows):
            st.markdown('<div class="prompt-row">', unsafe_allow_html=True)
            cols_row = st.columns([1, 1, 1], gap="medium")
            for c in range(cols):
                prompt_index = r * cols + c
                with cols_row[c]:
                    if prompt_index < len(prompts):
                        prompt_text = prompts[prompt_index]
                        emoji = emoji_map.get(prompt_index, "🅾️")
                        display_text = prompt_text
                        if len(prompt_text) > 45:
                            display_text = prompt_text[:42] + "..."
                        button_text = f"{emoji}\n\n{display_text}"
                        st.markdown('<div class="prompt-wrapper">', unsafe_allow_html=True)
                        if st.button(
                            button_text,
                            key=f"okta_prompt_{r}_{c}",
                            help=prompt_text,
                            use_container_width=True,
                        ):
                            st.session_state["okta_chat_input_value"] = prompt_text
                            st.session_state["okta_original_prompt_value"] = prompt_text
                            st.rerun()
                        st.markdown(f'<div class="prompt-overlay">{html.escape(prompt_text)}</div>', unsafe_allow_html=True)
                        st.markdown('</div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div style="height: 120px; visibility: hidden;"></div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

def show_saviynt_suggested_prompts():
    """Display Saviynt-specific prompts with UNIFORM fixed dimensions using Method 3 approach"""
    prompts = load_saviynt_prompts_from_file()

    if not prompts:
        st.warning("No Saviynt prompts found. Please add prompts to the saviyntPrompts.json file.")
        return

    # Emoji mappings for Saviynt prompts (Identity Governance focused)
    emoji_map = {
        0: "👥", 1: "👑", 2: "🧾", 3: "➕", 4: "🔁", 5: "➖",
        6: "📋", 7: "✅", 8: "📊", 9: "🔍", 10: "💼", 11: "🔐"
    }

    cols = 3
    rows = (len(prompts) + cols - 1) // cols

    with st.container():
        st.markdown('<div class="prompt-grid-container">', unsafe_allow_html=True)
        for r in range(rows):
            st.markdown('<div class="prompt-row">', unsafe_allow_html=True)
            cols_row = st.columns([1, 1, 1], gap="medium")
            for c in range(cols):
                prompt_index = r * cols + c
                with cols_row[c]:
                    if prompt_index < len(prompts):
                        prompt_text = prompts[prompt_index]
                        emoji = emoji_map.get(prompt_index, "💎")
                        display_text = prompt_text
                        if len(prompt_text) > 45:
                            display_text = prompt_text[:42] + "..."
                        button_text = f"{emoji}\n\n{display_text}"
                        st.markdown('<div class="prompt-wrapper">', unsafe_allow_html=True)
                        if st.button(
                            button_text,
                            key=f"saviynt_prompt_{r}_{c}",
                            help=prompt_text,
                            use_container_width=True,
                        ):
                            st.session_state["saviynt_chat_input_value"] = prompt_text
                            st.session_state["saviynt_original_prompt_value"] = prompt_text
                            st.rerun()
                        st.markdown(f'<div class="prompt-overlay">{html.escape(prompt_text)}</div>', unsafe_allow_html=True)
                        st.markdown('</div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div style="height: 120px; visibility: hidden;"></div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

def execute_chat_query(user_input, chat_container):
    """Execute the chat query and show response at top"""
    
    # HIDE PROMPTS AND SHOW THINKING AT THE TOP
    with chat_container:
        # Clear intro and prompts by adding user message
        with st.chat_message("user"):
            st.markdown(f"**You:** {user_input}")
        
        # Show thinking and typing response AT THE TOP
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
                    payload = {"thread_id": st.session_state["thread_id"], "message": user_input}
                    r = requests.post(f"{API_BASE}/chat", json=payload, timeout=120, headers=headers)
                    r.raise_for_status()
                    reply = r.json().get("reply", "")
                    
                    # Check for server error responses
                    is_server_busy = False
                    if isinstance(reply, dict) and reply.get('code') == 'server_error':
                        reply = "**Agent is currently busy, please wait a moment and try again.**"
                        is_server_busy = True
                    elif isinstance(reply, str):
                        try:
                            parsed = ast.literal_eval(reply)
                            if isinstance(parsed, dict) and parsed.get('code') == 'server_error':
                                reply = "**Agent is currently busy, please wait a moment and try again.**"
                                is_server_busy = True
                        except Exception:
                            if 'server_error' in reply:
                                reply = "**Agent is currently busy, please wait a moment and try again.**"
                                is_server_busy = True
                except Exception:
                    reply = "**Agent is currently busy, please wait a moment and try again.**"
                    is_server_busy = True
            
            # Show typing effect AT THE TOP
            typing_placeholder = st.empty()
            typing_message = ""
            if ENABLE_TYPING_EFFECT:
                for char in reply:
                    typing_message += char
                    typing_placeholder.markdown(build_agent_html("IAM Assistant", typing_message), unsafe_allow_html=True)
                    time.sleep(TYPING_DELAY)
            else:
                typing_placeholder.markdown(build_agent_html("IAM Assistant", reply), unsafe_allow_html=True)
            
            # If server is busy, put the original query back in the input
            if is_server_busy:
                st.session_state["chat_input_value"] = user_input
    
    # Add to chat history and rerun
    st.session_state["chat_history"].append((user_input, reply))
    st.rerun()

def execute_entra_query(user_input, chat_container):
    """Execute Entra query via new /entra/agent endpoints with thinking effect."""
    # Prepare prior chat history for the agent
    prior = st.session_state.get("entra_agent_history", [])
    headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
    payload = {
        "thread_id": st.session_state.get("entra_agent_thread_id"),
        "message": user_input,
        "chat_history": prior,
    }

    # HIDE PROMPTS AND SHOW THINKING AT THE TOP
    with chat_container:
        with st.chat_message("user"):
            st.markdown(f"**You:** {user_input}")
        with st.chat_message("assistant"):
            with st.spinner("Executing IAM operation..."):
                try:
                    r = requests.post(f"{API_BASE}/entra/agent/chat", json=payload, timeout=120, headers=headers)
                    r.raise_for_status()
                    response_data = r.json()
                    action = response_data.get("action", "none")
                    result = response_data.get("result", "No response received")
                except requests.exceptions.RequestException as e:
                    action = "error"
                    result = f"Failed to execute command: {str(e)}"
                except Exception as e:
                    action = "error"
                    result = f"Unexpected error: {str(e)}"

            # Show typing effect AT THE TOP
            if action and action != "none":
                st.markdown(f"**Action:** `{action}`")
            typing_placeholder = st.empty()
            entra_typing = False
            display_result = result if isinstance(result, str) else str(result)
            typing_message = ""
            if ENABLE_TYPING_EFFECT and entra_typing:
                for char in display_result:
                    typing_message += char
                    typing_placeholder.markdown(build_agent_html("Microsoft Entra", typing_message), unsafe_allow_html=True)
                    time.sleep(TYPING_DELAY)
            else:
                typing_placeholder.markdown(build_agent_html("Microsoft Entra", display_result), unsafe_allow_html=True)

    # Maintain both transcript (for display) and agent chat history (for API)
    st.session_state.setdefault("entra_chat_history", [])
    st.session_state["entra_chat_history"].append((user_input, result, action))
    st.session_state.setdefault("entra_agent_history", [])
    st.session_state["entra_agent_history"].append({"role": "user", "content": user_input})
    st.session_state["entra_agent_history"].append({"role": "assistant", "content": str(result)})
    st.rerun()

# NEW: ADD AD QUERY EXECUTION FUNCTION
def execute_ad_query(user_input, chat_container):
    """Execute AD query with thinking and typing effect at the top"""
    
    # HIDE PROMPTS AND SHOW THINKING AT THE TOP
    with chat_container:
        # Clear intro and prompts by adding user message
        with st.chat_message("user"):
            st.markdown(f"**You:** {user_input}")
        
        # Show thinking and typing response AT THE TOP
        with st.chat_message("assistant"):
            with st.spinner("Executing AD operation..."):
                try:
                    headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
                    payload = {
                        "thread_id": st.session_state["ad_thread_id"],
                        "message": user_input
                    }
                    
                    r = requests.post(f"{API_BASE}/ad/chat", json=payload, timeout=120, headers=headers)
                    r.raise_for_status()
                    
                    response_data = r.json()
                    action = response_data.get("action", "ad_provision")
                    result = response_data.get("result", "No response received")
                    agent = response_data.get("agent", "AD_Provisioning_Agent")
                    
                except requests.exceptions.RequestException as e:
                    action = "error"
                    result = f"Failed to execute AD command: {str(e)}"
                    agent = "AD_Provisioning_Agent"
                except Exception as e:
                    action = "error"
                    result = f"Unexpected error: {str(e)}"
                    agent = "AD_Provisioning_Agent"
            
            # Show typing effect AT THE TOP
            st.markdown(f"**Action:** `{action}`")
            
            typing_placeholder = st.empty()
            typing_message = ""
            
            # Format the response for better display
            display_result = result
            if isinstance(result, list):
                display_result = "\n".join([f"• {item}" for item in result])
            elif isinstance(result, str) and result.startswith("{"):
                # Try to parse and format JSON
                try:
                    json_result = json.loads(result)
                    if isinstance(json_result, dict):
                        display_result = json.dumps(json_result, indent=2)
                except:
                    pass
            
            if ENABLE_TYPING_EFFECT:
                for char in str(display_result):
                    typing_message += char
                    typing_placeholder.markdown(build_agent_html(agent, typing_message), unsafe_allow_html=True)
                    time.sleep(TYPING_DELAY)
            else:
                typing_placeholder.markdown(build_agent_html(agent, display_result), unsafe_allow_html=True)
    
    # Add to chat history and rerun
    st.session_state["ad_chat_history"].append((user_input, result, action))
    st.rerun()

def main_chat_page():
    """FIXED: Prompt goes to input bar first, then user can edit and submit"""
    
    if "access_token" not in st.session_state or not st.session_state["access_token"]:
        st.error("Access token is not found or invalid.", icon="🚨")
        return

    # Allow manual reset
    if st.button("Start new chat", key="reset_main_chat_btn"):
        _clear_main_chat_state()
        st.rerun()

    if "thread_id" not in st.session_state:
        try:
            headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
            r = requests.post(f"{API_BASE}/thread", timeout=120, headers=headers)
            r.raise_for_status()
            st.session_state["thread_id"] = r.json()["thread_id"]
        except Exception as e:
            st.error(f"Failed to create thread: {str(e)}", icon="🚨")
            st.stop()

    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []
    
    # Initialize chat_input_value if not exists
    if "chat_input_value" not in st.session_state:
        st.session_state["chat_input_value"] = ""

    # Create a dedicated area for chat messages AT THE TOP
    chat_container = st.container()
    
    # DISPLAY CHAT HISTORY AT THE TOP
    with chat_container:
        # Show intro and prompts only if no chat history
        if len(st.session_state["chat_history"]) == 0:
            show_intro()
            show_suggested_prompts()
        else:
            # Display existing chat messages
            for user_msg, agent_msg in st.session_state["chat_history"]:
                with st.chat_message("user"):
                    st.markdown(f"**You:** {user_msg}")
                with st.chat_message("assistant"):
                    st.markdown(build_agent_html("IAM Assistant", agent_msg), unsafe_allow_html=True)

    # Prefill using Enter-to-send without any button, via on_change flag
    chat_placeholder = "Hi there! Genie is ready to help you on IAM – start using me"
    if st.session_state.get("chat_input_value"):
        prefill_text = st.session_state["chat_input_value"]
        st.session_state["original_prompt_value"] = prefill_text
        # Define an on_change callback to mark submission when Enter is pressed
        def _main_prefill_submit():
            st.session_state["main_prefill_submit"] = True
        st.text_input(
            "Your message:",
            value=prefill_text,
            key="chat_text_input",
            placeholder=chat_placeholder,
            on_change=_main_prefill_submit,
        )
        if st.session_state.get("main_prefill_submit"):
            user_input = st.session_state.get("chat_text_input", "").strip()
            st.session_state["main_prefill_submit"] = False
            st.session_state["chat_input_value"] = ""
            st.session_state["original_prompt_value"] = ""
            if user_input:
                execute_chat_query(user_input, chat_container)
                st.rerun()
    else:
        user_input = st.chat_input(chat_placeholder)
        if user_input and user_input.strip():
            execute_chat_query(user_input, chat_container)
            st.rerun()

def entra_service_page():
    """Microsoft Entra Service chat interface - consistent with other assistants."""
    if "access_token" not in st.session_state or not st.session_state["access_token"]:
        st.error("Access token is not found or invalid.", icon="🚨")
        return

    # Ensure entra-agent thread exists
    if "entra_agent_thread_id" not in st.session_state:
        try:
            headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
            r = requests.post(f"{API_BASE}/entra/agent/thread", timeout=120, headers=headers)
            r.raise_for_status()
            st.session_state["entra_agent_thread_id"] = r.json()["thread_id"]
        except Exception as e:
            st.error(f"Failed to create Entra Agent thread: {str(e)}", icon="🚨")
            st.stop()

    if "entra_chat_history" not in st.session_state:
        st.session_state["entra_chat_history"] = []
    if "entra_agent_history" not in st.session_state:
        st.session_state["entra_agent_history"] = []

    chat_container = st.container()
    with chat_container:
        if len(st.session_state["entra_chat_history"]) == 0:
            # Show prompts grid when there is no chat yet
            show_entra_suggested_prompts()
        else:
            for user_msg, agent_msg, intent in st.session_state["entra_chat_history"]:
                with st.chat_message("user"):
                    st.markdown(f"**You:** {user_msg}")
                with st.chat_message("assistant"):
                    st.markdown(build_agent_html("Microsoft Entra", agent_msg), unsafe_allow_html=True)

    chat_placeholder = "Ask me to perform IAM operations (e.g., 'list users', 'create group', 'get user details')"
    if st.session_state.get("entra_chat_input_value"):
        prefill_text = st.session_state["entra_chat_input_value"]
        st.session_state["entra_original_prompt_value"] = prefill_text
        def _entra_prefill_submit():
            st.session_state["entra_prefill_submit"] = True
        st.text_input(
            "Your message:",
            value=prefill_text,
            key="entra_chat_text_input",
            placeholder=chat_placeholder,
            on_change=_entra_prefill_submit,
        )
        if st.session_state.get("entra_prefill_submit"):
            user_input = st.session_state.get("entra_chat_text_input", "").strip()
            st.session_state["entra_prefill_submit"] = False
            st.session_state["entra_chat_input_value"] = ""
            st.session_state["entra_original_prompt_value"] = ""
            if user_input:
                execute_entra_query(user_input, chat_container)
                st.rerun()
    else:
        user_input = st.chat_input(chat_placeholder)
        if user_input and user_input.strip():
            execute_entra_query(user_input, chat_container)
            st.rerun()

def ad_service_page():
    """Active Directory Service chat interface - consistent with other assistants."""
    if "access_token" not in st.session_state or not st.session_state["access_token"]:
        st.error("Access token is not found or invalid.", icon="🚨")
        return

    # Ensure AD thread exists
    if "ad_thread_id" not in st.session_state:
        try:
            headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
            r = requests.post(f"{API_BASE}/ad/thread", timeout=120, headers=headers)
            r.raise_for_status()
            st.session_state["ad_thread_id"] = r.json()["thread_id"]
        except Exception as e:
            st.error(f"Failed to create AD thread: {str(e)}", icon="🚨")
            st.stop()

    if "ad_chat_history" not in st.session_state:
        st.session_state["ad_chat_history"] = []

    chat_container = st.container()
    with chat_container:
        if len(st.session_state["ad_chat_history"]) == 0:
            # Show prompts grid when there is no chat yet
            show_ad_suggested_prompts()
        else:
            for user_msg, agent_msg, action in st.session_state["ad_chat_history"]:
                with st.chat_message("user"):
                    st.markdown(f"**You:** {user_msg}")
                with st.chat_message("assistant"):
                    st.markdown(build_agent_html("AD_Provisioning_Agent", agent_msg), unsafe_allow_html=True)

    chat_placeholder = "Ask me to perform AD operations (e.g., 'list users', 'list groups', 'get user details')"
    if st.session_state.get("ad_chat_input_value"):
        prefill_text = st.session_state["ad_chat_input_value"]
        st.session_state["ad_original_prompt_value"] = prefill_text
        def _ad_prefill_submit():
            st.session_state["ad_prefill_submit"] = True
        st.text_input(
            "Your message:",
            value=prefill_text,
            key="ad_chat_text_input",
            placeholder=chat_placeholder,
            on_change=_ad_prefill_submit,
        )
        if st.session_state.get("ad_prefill_submit"):
            user_input = st.session_state.get("ad_chat_text_input", "").strip()
            st.session_state["ad_prefill_submit"] = False
            st.session_state["ad_chat_input_value"] = ""
            st.session_state["ad_original_prompt_value"] = ""
            if user_input:
                execute_ad_query(user_input, chat_container)
                st.rerun()
    else:
        user_input = st.chat_input(chat_placeholder)
        if user_input and user_input.strip():
            execute_ad_query(user_input, chat_container)
            st.rerun()


# NEW: Okta execution function (mirrors Entra / AD style)
def execute_okta_query(user_input, chat_container):
    """Execute Okta agent query via /okta/agent/chat endpoint and show typing effect."""
    prior = st.session_state.get("okta_agent_history", [])
    headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
    payload = {
        "thread_id": st.session_state.get("okta_agent_thread_id"),
        "message": user_input,
        "chat_history": prior,
    }

    with chat_container:
        with st.chat_message("user"):
            st.markdown(f"**You:** {user_input}")
        with st.chat_message("assistant"):
            with st.spinner("Executing Okta operation..."):
                try:
                    r = requests.post(f"{API_BASE}/okta/agent/chat", json=payload, timeout=120, headers=headers)
                    r.raise_for_status()
                    response_data = r.json()
                    action = response_data.get("action", "okta_provision")
                    result = response_data.get("result", "No response received")
                except requests.exceptions.RequestException as e:
                    action = "error"
                    result = f"Failed to execute Okta command: {str(e)}"
                except Exception as e:
                    action = "error"
                    result = f"Unexpected error: {str(e)}"

            # Show typing effect and action
            if action and action != "none":
                st.markdown(f"**Action:** `{action}`")

            typing_placeholder = st.empty()
            display_result = result if isinstance(result, str) else str(result)
            typing_message = ""
            if ENABLE_TYPING_EFFECT:
                for char in display_result:
                    typing_message += char
                    typing_placeholder.markdown(build_agent_html("Okta Agent", typing_message), unsafe_allow_html=True)
                    time.sleep(TYPING_DELAY)
            else:
                typing_placeholder.markdown(build_agent_html("Okta Agent", display_result), unsafe_allow_html=True)

    # Maintain transcript and agent history
    st.session_state.setdefault("okta_chat_history", [])
    st.session_state["okta_chat_history"].append((user_input, result, action))
    st.session_state.setdefault("okta_agent_history", [])
    st.session_state["okta_agent_history"].append({"role": "user", "content": user_input})
    st.session_state["okta_agent_history"].append({"role": "assistant", "content": str(result)})
    st.rerun()

def execute_saviynt_query(user_input, chat_container):
    """Execute Saviynt agent query via /saviynt/agent/chat endpoint and show typing effect."""
    prior = st.session_state.get("saviynt_agent_history", [])
    headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
    payload = {
        "thread_id": st.session_state.get("saviynt_agent_thread_id"),
        "message": user_input,
        "chat_history": prior,
    }

    with chat_container:
        with st.chat_message("user"):
            st.markdown(f"**You:** {user_input}")
        with st.chat_message("assistant"):
            with st.spinner("Executing Saviynt operation..."):
                try:
                    r = requests.post(f"{API_BASE}/saviynt/agent/chat", json=payload, timeout=120, headers=headers)
                    r.raise_for_status()
                    response_data = r.json()
                    action = response_data.get("action", "saviynt_provision")
                    result = response_data.get("result", "No response received")
                except requests.exceptions.RequestException as e:
                    action = "error"
                    result = f"Failed to execute Saviynt command: {str(e)}"
                except Exception as e:
                    action = "error"
                    result = f"Unexpected error: {str(e)}"

            # Show typing effect and action
            if action and action != "none":
                st.markdown(f"**Action:** `{action}`")

            typing_placeholder = st.empty()
            display_result = result if isinstance(result, str) else str(result)
            typing_message = ""
            if ENABLE_TYPING_EFFECT:
                for char in display_result:
                    typing_message += char
                    typing_placeholder.markdown(build_agent_html("Saviynt Agent", typing_message), unsafe_allow_html=True)
                    time.sleep(TYPING_DELAY)
            else:
                typing_placeholder.markdown(build_agent_html("Saviynt Agent", display_result), unsafe_allow_html=True)

    # Maintain transcript and agent history
    st.session_state.setdefault("saviynt_chat_history", [])
    st.session_state["saviynt_chat_history"].append((user_input, result, action))
    st.session_state.setdefault("saviynt_agent_history", [])
    st.session_state["saviynt_agent_history"].append({"role": "user", "content": user_input})
    st.session_state["saviynt_agent_history"].append({"role": "assistant", "content": str(result)})
    st.rerun()


def okta_service_page():
    """Okta Agent UI - creates thread and provides chat area & suggested prompts"""
    if "access_token" not in st.session_state or not st.session_state["access_token"]:
        st.error("Access token is not found or invalid.", icon="🚨")
        return

    # Ensure Okta agent thread exists
    if "okta_agent_thread_id" not in st.session_state:
        try:
            headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
            r = requests.post(f"{API_BASE}/okta/agent/thread", timeout=120, headers=headers)
            r.raise_for_status()
            st.session_state["okta_agent_thread_id"] = r.json()["thread_id"]
        except Exception as e:
            st.error(f"Failed to create Okta Agent thread: {str(e)}", icon="🚨")
            st.stop()

    # Initialize histories
    if "okta_chat_history" not in st.session_state:
        st.session_state["okta_chat_history"] = []
    if "okta_agent_history" not in st.session_state:
        st.session_state["okta_agent_history"] = []

    chat_container = st.container()
    with chat_container:
        if len(st.session_state["okta_chat_history"]) == 0:
            show_okta_intro()
            show_okta_suggested_prompts()
        else:
            for user_msg, agent_msg, action in st.session_state["okta_chat_history"]:
                with st.chat_message("user"):
                    st.markdown(f"**You:** {user_msg}")
                with st.chat_message("assistant"):
                    st.markdown(build_agent_html("Okta Agent", agent_msg), unsafe_allow_html=True)

    chat_placeholder = "Ask me to perform Okta operations (e.g., 'list users', 'create user', 'get user details')"
    if st.session_state.get("okta_chat_input_value"):
        prefill_text = st.session_state["okta_chat_input_value"]
        st.session_state["okta_original_prompt_value"] = prefill_text
        def _okta_prefill_submit():
            st.session_state["okta_prefill_submit"] = True
        st.text_input(
            "Your message:",
            value=prefill_text,
            key="okta_chat_text_input",
            placeholder=chat_placeholder,
            on_change=_okta_prefill_submit,
        )
        if st.session_state.get("okta_prefill_submit"):
            user_input = st.session_state.get("okta_chat_text_input", "").strip()
            st.session_state["okta_prefill_submit"] = False
            st.session_state["okta_chat_input_value"] = ""
            st.session_state["okta_original_prompt_value"] = ""
            if user_input:
                execute_okta_query(user_input, chat_container)
                st.rerun()
    else:
        user_input = st.chat_input(chat_placeholder)
        if user_input and user_input.strip():
            execute_okta_query(user_input, chat_container)
            st.rerun()

def saviynt_service_page():
    """Saviynt Agent UI - creates thread and provides chat area & suggested prompts"""
    if "access_token" not in st.session_state or not st.session_state["access_token"]:
        st.error("Access token is not found or invalid.", icon="🚨")
        return

    # Ensure Saviynt agent thread exists
    if "saviynt_agent_thread_id" not in st.session_state:
        try:
            headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
            r = requests.post(f"{API_BASE}/saviynt/agent/thread", timeout=120, headers=headers)
            r.raise_for_status()
            st.session_state["saviynt_agent_thread_id"] = r.json()["thread_id"]
        except Exception as e:
            st.error(f"Failed to create Saviynt Agent thread: {str(e)}", icon="🚨")
            st.stop()

    # Initialize histories
    if "saviynt_chat_history" not in st.session_state:
        st.session_state["saviynt_chat_history"] = []
    if "saviynt_agent_history" not in st.session_state:
        st.session_state["saviynt_agent_history"] = []

    chat_container = st.container()
    with chat_container:
        if len(st.session_state["saviynt_chat_history"]) == 0:
            show_saviynt_intro()
            show_saviynt_suggested_prompts()
        else:
            for user_msg, agent_msg, action in st.session_state["saviynt_chat_history"]:
                with st.chat_message("user"):
                    st.markdown(f"**You:** {user_msg}")
                with st.chat_message("assistant"):
                    st.markdown(build_agent_html("Saviynt Agent", agent_msg), unsafe_allow_html=True)

    chat_placeholder = "Ask me to perform Saviynt operations (e.g., 'list users', 'manage access requests', 'review certifications')"
    if st.session_state.get("saviynt_chat_input_value"):
        prefill_text = st.session_state["saviynt_chat_input_value"]
        st.session_state["saviynt_original_prompt_value"] = prefill_text
        def _saviynt_prefill_submit():
            st.session_state["saviynt_prefill_submit"] = True
        st.text_input(
            "Your message:",
            value=prefill_text,
            key="saviynt_chat_text_input",
            placeholder=chat_placeholder,
            on_change=_saviynt_prefill_submit,
        )
        if st.session_state.get("saviynt_prefill_submit"):
            user_input = st.session_state.get("saviynt_chat_text_input", "").strip()
            st.session_state["saviynt_prefill_submit"] = False
            st.session_state["saviynt_chat_input_value"] = ""
            st.session_state["saviynt_original_prompt_value"] = ""
            if user_input:
                execute_saviynt_query(user_input, chat_container)
                st.rerun()
    else:
        user_input = st.chat_input(chat_placeholder)
        if user_input and user_input.strip():
            execute_saviynt_query(user_input, chat_container)
            st.rerun()

# ... (rest of the code remains the same)
def execute_orchestrator_query(user_input, chat_container):
    """Execute Orchestrator query with spinner and typing effect, parsing action/result."""
    # Prepare prior chat history for the agent
    prior = []
    for (u, a) in st.session_state.get("orchestrator_chat_history", []):
        prior.append({"role": "user", "content": u})
        prior.append({"role": "assistant", "content": a})

    headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
    payload = {
        "thread_id": st.session_state.get("orch_thread_id"),
        "message": user_input,
        "chat_history": prior,
    }

    # Show thinking and typing at the top
    with chat_container:
        with st.chat_message("user"):
            st.markdown(f"**You:** {user_input}")
        with st.chat_message("assistant"):
            with st.spinner("Executing IAM operation..."):
                try:
                    r = requests.post(f"{API_BASE}/orchestrator/chat", json=payload, timeout=180, headers=headers)
                    r.raise_for_status()
                    data = r.json()
                    # Expecting {'action': '...', 'result': '...'}
                    action = data.get("action", "none") if isinstance(data, dict) else "none"
                    result = data.get("result", "") if isinstance(data, dict) else str(data)
                except requests.exceptions.RequestException as e:
                    action = "error"
                    result = f"Failed to execute orchestrator command: {str(e)}"
                except Exception as e:
                    action = "error"
                    result = f"Unexpected error: {str(e)}"

            # Typing effect
            if action and action != "none":
                st.markdown(f"**Action:** `{action}`")
            typing_placeholder = st.empty()
            display_result = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            typing_message = ""
            if ENABLE_TYPING_EFFECT:
                for char in display_result:
                    typing_message += char
                    typing_placeholder.markdown(build_agent_html("Orchestrator", typing_message), unsafe_allow_html=True)
                    time.sleep(TYPING_DELAY)
            else:
                typing_placeholder.markdown(build_agent_html("Orchestrator", display_result), unsafe_allow_html=True)

    # Append to history and rerun
    st.session_state.setdefault("orchestrator_chat_history", []).append((user_input, result))
    st.rerun()


def orchestrator_chat_page():
    """Assistant for IAM Admin (Orchestrator) - restored full chat functionality."""
    if "access_token" not in st.session_state or not st.session_state.get("access_token"):
        st.error("Access token is not found or invalid.", icon="🚨")
        return

    # Ensure thread
    if "orch_thread_id" not in st.session_state:
        try:
            headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
            r = requests.post(f"{API_BASE}/orchestrator/thread", timeout=120, headers=headers)
            r.raise_for_status()
            st.session_state["orch_thread_id"] = r.json()["thread_id"]
        except Exception as e:
            st.error(f"Failed to create orchestrator thread: {str(e)}", icon="🚨")
            st.stop()

    if "orchestrator_chat_history" not in st.session_state:
        st.session_state["orchestrator_chat_history"] = []

    chat_container = st.container()
    with chat_container:
        if len(st.session_state["orchestrator_chat_history"]) == 0:
            # Show admin prompts grid when no chat yet
            show_admin_suggested_prompts()
        else:
            for user_msg, agent_msg in st.session_state["orchestrator_chat_history"]:
                with st.chat_message("user"):
                    st.markdown(f"**You:** {user_msg}")
                with st.chat_message("assistant"):
                    st.markdown(build_agent_html("Orchestrator", agent_msg), unsafe_allow_html=True)

    placeholder = "Ask the IAM Orchestrator to perform admin actions (e.g., 'provision user', 'list ownerless groups')."
    # Prefill behavior similar to other pages
    if st.session_state.get("orch_chat_input_value"):
        prefill_text = st.session_state["orch_chat_input_value"]
        st.session_state["orch_original_prompt_value"] = prefill_text
        def _orch_prefill_submit():
            st.session_state["orch_prefill_submit"] = True
        st.text_input(
            "Your message:",
            value=prefill_text,
            key="orch_chat_text_input",
            placeholder=placeholder,
            on_change=_orch_prefill_submit,
        )
        if st.session_state.get("orch_prefill_submit"):
            user_input = st.session_state.get("orch_chat_text_input", "").strip()
            st.session_state["orch_prefill_submit"] = False
            st.session_state["orch_chat_input_value"] = ""
            st.session_state["orch_original_prompt_value"] = ""
            if user_input:
                execute_orchestrator_query(user_input, chat_container)
                st.rerun()
    else:
        user_input = st.chat_input(placeholder)
        if user_input and user_input.strip():
            execute_orchestrator_query(user_input, chat_container)
            st.rerun()

def iam_dashboard_page():
    """IAM Dashboard & Reports page that fetches data from the backend and renders summary tiles."""
    if "access_token" not in st.session_state or not st.session_state["access_token"]:
        st.error("Access token is not found or invalid.", icon="🚨")
        return

    st.title("🛡️ IAM Security Dashboard (Summary)")
    refresh = st.button("Refresh dashboard data", key="refresh_dashboard_btn")

    # Client-side cache with TTL
    TTL_SECONDS = 3600  # 1 hour
    data = None
    error = None
    now_ts = time.time()
    cached_data = st.session_state.get("iam_dashboard_data")
    cached_ts = st.session_state.get("iam_dashboard_ts", 0)

    if (not refresh) and cached_data is not None and (now_ts - cached_ts) < TTL_SECONDS:
        data = cached_data
    else:
        with st.spinner("Loading dashboard data..."):
            try:
                headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
                r = requests.get(f"{API_BASE}/dashboard/summary", timeout=180, headers=headers)
                r.raise_for_status()
                payload = r.json()
                if payload.get("success"):
                    data = payload.get("data", {})
                    st.session_state["iam_dashboard_data"] = data
                    st.session_state["iam_dashboard_ts"] = time.time()
                else:
                    error = payload
            except Exception as e:
                error = str(e)

    if error:
        st.error(f"Failed to load dashboard: {error}")
        return

    # Extract metrics similar to IamDashboard/dashboardApp.py
    risky_data = data.get("risky_users", {}).get("value", [])
    protected_data = data.get("protected_users", {}).get("value", [])
    privileged_data = data.get("privileged_accounts",{}).get("count", 0)
    total_users=data.get("privileged_accounts",{}).get("total_users", 0)
    total_groups=data.get("ownerless_groups_entra",{}).get("totalGroups", 0)
    ownerless_entra = data.get("ownerless_groups_entra", {}).get("count", 0)
    mfa_count = data.get("mfa_disabled_apps", {}).get("count", 0)

    

    ownerless_ad_count = data.get("ownerless_groups_ad", {}).get("count", 0)
    ownerless_ad_total = data.get("ownerless_groups_ad", {}).get("total_groups", 0)

    memberless_groups_count = data.get("memberless_groups", {}).get("count", 0)
    memberless_groups_total = data.get("memberless_groups", {}).get("total_groups", 0)

    inactive_count = data.get("inactive_accounts", {}).get("count", 0)
    inactive_total = data.get("inactive_accounts", {}).get("total_accounts", 0)

    service_count = data.get("service_accounts", {}).get("count", 0)

    pwd_never_expire_count_nhi = data.get("pwd_never_expire", {}).get("count_sa", 0)
    pwd_never_expire_total_hi = data.get("pwd_never_expire", {}).get("count_nsa", 0)

        # Tile styles
    tile_style = """
    <style>
    .metric-card {
        background-color: #f9f9f9;
        padding: 12px 15px;
        border-radius: 10px;
        box-shadow: 0px 2px 6px rgba(0,0,0,0.08);
        text-align: center;
        margin: 4px 0;
    }
    .metric-title {
        font-size: 12px;
        font-weight: 600;
        color: #555;
        margin-bottom: 4px;
    }
    .metric-value {
        font-size: 20px;
        font-weight: bold;
        color: #2c7be5;
    }
    </style>
    """
    st.markdown(tile_style, unsafe_allow_html=True)

    # Create two-column layout for Entra and AD metrics side-by-side
    left_col, right_col = st.columns(2, gap="medium")

    # Entra ID Metrics (Left side)
    with left_col:
        st.subheader("☁️ Entra ID Metrics")
        entra_metrics = [
            ("🚨 Current Risky Users", len(risky_data)),
            ("🛡️ Remediated Risky Users", len(protected_data)),
            ("👑 Privileged Accounts", f"{privileged_data} / {total_users}"),
            ("👥 Ownerless Groups", f"{ownerless_entra} / {total_groups}"),
            ("🔐 Apps without MFA", mfa_count),
        ]
        for i in range(0, len(entra_metrics), 1):
            title, value = entra_metrics[i]
            card_html = (
                f'<div class="metric-card">'
                f'<div class="metric-title">{title}</div>'
                f'<div class="metric-value">{value}</div>'
                f'</div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)

    # Active Directory Metrics (Right side)
    with right_col:
        st.subheader("🖥️ Active Directory Metrics")

        ad_metrics = [
            ("👥 Ownerless Groups (AD)", f"{ownerless_ad_count} / {ownerless_ad_total}"),
            ("👥 Memberless Groups (AD)", f"{memberless_groups_count} / {memberless_groups_total}"),
            ("⏳ Inactive Accounts (90d+)", f"{inactive_count} / {inactive_total}"),
            ("⚙️ Service Accounts(Non-human Identities)", f"{service_count}"),
            ("🔒 Password Never Expires(NHI,HI)", f"{pwd_never_expire_count_nhi} , {pwd_never_expire_total_hi}"),
            # ("🚫 Account Lockouts", f"{account_lock_count} / {account_lock_total}"),
        ]

        for title, value in ad_metrics:
            card_html = (
                f'<div class="metric-card">'
                f'<div class="metric-title">{title}</div>'
                f'<div class="metric-value">{value}</div>'
                f'</div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)


def about_iam():
    """About IAM page with comprehensive information"""
    st.markdown('<div class="centered-intro">About IAM Genie - Your Intelligent Identity and Access Management Assistant</div>', unsafe_allow_html=True)
    
    st.markdown("""
    ## 🎯 **What is IAM Genie?**
    
    IAM Genie is an advanced AI-powered assistant specifically designed to streamline Identity and Access Management operations. Built on cutting-edge AI technology, Genie provides intelligent automation, comprehensive reporting, and seamless integration with your existing IAM infrastructure.
    
    ## 🚀 **Key Features**
    
    ### 👤 **Assistant for End Users**
    - Password reset assistance
    - Account unlock requests  
    - Access request submissions
    - Profile management guidance
    - Self-service troubleshooting
    
    ### 👑 **Assistant for IAM Admins**
    - Advanced user provisioning
    - Role and permission management
    - Bulk operations support
    - Compliance monitoring
    - Audit trail analysis
    
    ### 🔐 **Microsoft Entra Service**
    - Cloud identity management
    - Conditional access policies
    - Multi-factor authentication setup
    - Application registrations
    - Security insights and analytics
    
    ### 🏢 **Active Directory Service**
    - On-premises directory management
    - Group policy administration
    - Domain controller monitoring
    - LDAP operations
    - Hybrid identity synchronization
    
    ### 📊 **IAM Dashboard & Reports**
    - Real-time security metrics
    - Compliance reporting
    - User activity analytics
    - Risk assessment dashboards
    - Automated compliance checks
    """)

def rules_and_regulations():
    """Rules and Regulations page"""
    st.markdown('<div class="centered-intro">IAM Genie - Rules, Regulations & Usage Guidelines</div>', unsafe_allow_html=True)
    
    st.markdown("""
    ## 📋 **Usage Guidelines**
    
    ### ✅ **Permitted Activities**
    - Legitimate IAM operations within your assigned scope
    - Password resets for authorized accounts
    - Access requests through proper approval workflows
    - Compliance reporting and auditing activities
    - Self-service profile management
    
    ### ❌ **Prohibited Activities**
    - Unauthorized access attempts to restricted systems
    - Sharing of login credentials or access tokens
    - Bulk operations without proper authorization
    - Bypassing established approval workflows
    - Using the system for non-business purposes
    
    ## 🔐 **Security Requirements**
    
    ### 🔑 **Authentication Standards**
    - Multi-factor authentication (MFA) is **mandatory**
    - Password policies must comply with organizational standards
    - Session timeouts are enforced for security
    - Regular access reviews are required
    
    ### 🛡️ **Data Protection**
    - All user data is encrypted at rest and in transit
    - Access logs are maintained for audit purposes
    - Personal information is handled per GDPR guidelines
    - Data retention policies are strictly enforced
    
    ## ⚖️ **Compliance Framework**
    
    ### 📊 **Regulatory Compliance**
    - **GDPR**: General Data Protection Regulation compliance
    - **HIPAA**: Health Insurance Portability and Accountability Act
    - **SOX**: Sarbanes-Oxley Act requirements
    - **ISO 27001**: Information security management standards
    - **NIST**: Cybersecurity Framework alignment
    
    ### 🔍 **Audit Requirements**
    - All administrative actions are logged
    - Regular compliance assessments are conducted
    - Audit trails are maintained for legal requirements
    - Violation reporting is mandatory
    
    ## 🚨 **Incident Response**
    
    ### 📞 **Reporting Security Incidents**
    1. **Immediate Action**: Contact the security team immediately
    2. **Documentation**: Record all relevant details
    3. **Escalation**: Follow the established incident response procedure
    4. **Cooperation**: Assist with investigation as required
    
    ### ⏰ **Response Times**
    - **Critical incidents**: 15 minutes response time
    - **High priority**: 1 hour response time
    - **Medium priority**: 4 hours response time
    - **Low priority**: 24 hours response time
    
    ## 📝 **User Responsibilities**
    
    ### 👤 **End Users**
    - Protect login credentials and access tokens
    - Report suspicious activities immediately
    - Follow established password policies
    - Complete required security training
    
    ### 👑 **Administrators**
    - Implement least privilege access principles
    - Conduct regular access reviews
    - Maintain accurate user provisioning records
    - Follow change management procedures
    
    ## ⚡ **System Limitations**
    
    ### 🔧 **Technical Constraints**
    - API rate limits apply to prevent system overload
    - Bulk operations have defined batch size limits
    - Certain operations require additional approvals
    - System maintenance windows may affect availability
    
    ### ⏱️ **Service Level Agreements**
    - **Uptime**: 99.9% availability guarantee
    - **Performance**: Sub-second response times for standard operations
    - **Support**: 24/7 technical support available
    - **Updates**: Regular feature updates and security patches
    
    ## 📚 **Additional Resources**
    
    - **Training Materials**: Available in the learning portal
    - **Best Practices Guide**: Detailed operational procedures
    - **FAQ Section**: Common questions and solutions
    - **Video Tutorials**: Step-by-step guides for complex operations
    
    ## 🔗 **Related Policies**
    
    - Information Security Policy
    - Acceptable Use Policy
    - Data Classification Guidelines
    - Incident Response Procedures
    - Change Management Policy
    
    ---
    
    **Last Updated**: January 2025 | **Policy Version**: 2.1 | **Review Date**: June 2025
    
    For questions about these policies, contact the **Compliance Team** at compliance@your-organization.com
    """)

# MAIN ROUTING LOGIC WITH ALL FUNCTIONS
if st.session_state.get("authenticated", False):
    active_page = st.session_state.get("active_page", "main_chat")
    # Reset chat state when navigating into a page
    handle_page_entry(active_page)
    # Mark entry complete so subsequent reruns on the same page don't clear prompt selections
    st.session_state["last_active_page"] = active_page
   
    if active_page == "main_chat":
        main_chat_page()
    elif active_page == "orchestrator_chat":
        orchestrator_chat_page()
    elif active_page == "entra_id_assistant":
        entra_service_page()
    elif active_page == "okta_agent_assistant":
        okta_service_page()
    elif active_page == "saviynt_agent_assistant":
        saviynt_service_page()
    elif active_page == "active_directory_assistant":
        ad_service_page()  # NOW CALLS THE ACTUAL AD SERVICE PAGE!
    elif active_page == "iam_metrics_dashboard":
        iam_dashboard_page()
    elif active_page == "about_iam":
        about_iam()
    elif active_page == "rules":
        rules_and_regulations()
    else:
        main_chat_page()
        
else:
    # Not authenticated - show welcome page
    st.markdown('<div class="main-content-logged-out">', unsafe_allow_html=True)
    st.markdown("Please log in to access IAM Genie services.")
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Show features preview for non-authenticated users
    st.markdown("---")
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown(" ### 👤 **Assistant for End Users**")
        st.markdown("- Assists user in queries related to MFA, Access Requests, Password Resets, etc. for their enterprise")
        # st.markdown("- MFA setup and resets")
        # st.markdown("- Access Requests")
        # st.markdown("- Password resets")
        
        st.markdown("### 👑 **Assistant for IAM Admins**")
        st.markdown("- Assists admins to answer their queries on application integrations, architecture decisions, and various admin-level operations in their enterprise")
        # st.markdown("- Appplication integrations")
        # st.markdown("- Admin-level enterprise documentation")
        # st.markdown("- Architecture decisions and fallback procedures")

        st.markdown("### 👑 **Entra ID Service**")
        st.markdown("- Assists admin and operation to team peform various Entra ID operations such as user/group management, conditional access policies or retrieve data from the Entra ID tenant.")
        # st.markdown("- User and group provisioning")
        # st.markdown("- Application and Conditional Policies")
        # st.markdown("- Permission management")
    
    with col2:
        st.markdown("### 🏢 **Active Directory Service**")
        st.markdown("- Assists admin and operation to team peform various Active Directory operations such as user/group management, or retrieve data from the Active Directory.")
        # st.markdown("- User operations")
        # st.markdown("- Group management")

        st.markdown("### 📊 **IAM Dashboard & Reports**")
        st.markdown("- Provide a view of Real-time Risk and Security Metrics for your IAM infrastructure.")
        # st.markdown("- Compliance dashboards")
        # st.markdown("- Risk assessments")