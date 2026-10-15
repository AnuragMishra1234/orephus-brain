"""
ORPHEUS AI Brain - Core AI Server
File Owner: Anurag (AI Server Engineer)

This service hosts the cognitive dialogue engine and state machine for ORPHEUS.
Provides endpoints for Unreal Engine 5.8:
- POST /talk: Primary cognitive dialogue turn with guard checks & state updates
- POST /event: Narrative trigger broker (e.g. voice_beat)
- POST /say: Manual voice playback
- POST /stop_audio: Stop audio playback
- POST /reset: Reset game state for clean test runs
- GET  /state: Query current narrative & resolve state
- GET  /docs: Framework-generated OpenAPI documentation
"""

import os
import re
import json
import time
import logging
from pathlib import Path
from typing import Optional, Union, List, Set, Dict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Import content and voice modules
from content import (
    NPCS,
    FALLBACK,
    CONTEXT_FALLBACKS,
    get_fallback,
    GUARD_LINES,
    EVIDENCE_KEYWORDS,
    EVIDENCE_REACTION,
    LIE_REACTION,
    PROFANITY,
    TUNING,
)
import voice

# The four canonical evidence items defined in the project specification
CANONICAL_EVIDENCE: Set[str] = {
    "ev_liora_log",
    "ev_vance_journal",
    "ev_elara_recorder",
    "ev_liora_drawing",
}

# =====================================================================
# Logging Configuration (Sanitized - Never log API keys or secrets)
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("orpheus.server")

# Load environment variables
load_dotenv(override=True)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
ENABLE_STRICT_EVIDENCE_DISCOVERY = os.getenv("ENABLE_STRICT_EVIDENCE_DISCOVERY", "true").strip().lower() in ("true", "1", "yes")

# =====================================================================
# State Tracking & Lifecycle
# =====================================================================
def create_initial_state() -> dict:
    return {
        "vance_resolve": TUNING.get("initial_vance_resolve", 100),
        "milo_trust": TUNING.get("initial_milo_trust", 50),
        "orpheus_suspicion": TUNING.get("initial_orpheus_suspicion", 0),
        "fog_density": 0.5,
        "evidence_seen": [],       # Discovered/held evidence IDs
        "evidence_reacted": [],    # Evidence IDs that have already triggered a resolve penalty
        "evidence_presented": [],  # Synchronized legacy key for backward compatibility
        "gate_answer": "",         # Episodic memory recorded at context="gate"
        "memories": {
            "gate_intent": "",     # Stored gate answer
            "milo_decision": "",   # 'reassured', 'panicked'
            "vance_stance": "",    # 'confrontational', 'sympathetic', 'surrender'
            "promises": [],        # list of explicit promises made by player
        },
        "milo_fled": False,        # True if Milo panicked and ran into the fog
        "ending": "",              # Triggered ending ('', 'A', 'A2', 'B')
        "dialogue_history": [],    # Prior conversational turns
        "turn_count": 0,
        "core_turn_count": 0,      # Number of turns elapsed in the core room
        "orpheus_interrupted": False, # One-time interruption flag for ORPHEUS
        "milo_interrupted": False,    # One-time interruption flag for Milo
    }


session_state = create_initial_state()

# Multi-session memory store for separate-player isolation
sessions: Dict[str, dict] = {"default": session_state}

STATE_FILE = Path(os.getenv("STATE_FILE", "session_state.json"))
ENABLE_DISK_PERSISTENCE = os.getenv("ENABLE_DISK_PERSISTENCE", "false").strip().lower() in ("true", "1", "yes")

def get_session_state(session_id: Optional[str] = "default") -> dict:
    """Retrieve or initialize isolated session state for a given player/session."""
    sid = session_id or "default"
    if sid == "default":
        if "default" not in sessions:
            sessions["default"] = session_state
        return sessions["default"]
    if sid not in sessions:
        sessions[sid] = create_initial_state()
    return sessions[sid]

def save_state_to_disk(filepath: Optional[Union[str, Path]] = None, session_id: str = "default") -> bool:
    """Serialize session state to disk for restart persistence."""
    target_path = Path(filepath or STATE_FILE)
    try:
        st = get_session_state(session_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
        return True
    except Exception as e:
        logger.warning(f"[PERSISTENCE] Failed to save state to {target_path}: {e}")
        return False

def load_state_from_disk(filepath: Optional[Union[str, Path]] = None, session_id: str = "default") -> bool:
    """Load serialized session state from disk into active memory."""
    target_path = Path(filepath or STATE_FILE)
    if not target_path.exists():
        return False
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        st = get_session_state(session_id)
        st.clear()
        st.update(data)
        if session_id == "default":
            session_state.clear()
            session_state.update(data)
        return True
    except Exception as e:
        logger.warning(f"[PERSISTENCE] Failed to load state from {target_path}: {e}")
        return False

# =====================================================================
# LLM Provider Initialization
# =====================================================================
groq_client = None

if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
        logger.info(f"[LLM] Groq client initialized successfully with model: {GROQ_MODEL}")
    except Exception as e:
        logger.error(f"[LLM INIT ERROR] Failed to initialize Groq client: {e}")
        groq_client = None
else:
    logger.warning(
        "[LLM] GROQ_API_KEY is not configured in .env. Live calls will use safe fallback dialogue."
    )

# =====================================================================
# FastAPI Application & Lifespan
# =====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("======================================================")
    logger.info(" ORPHEUS AI Brain Server starting up")
    logger.info(f" Active Provider: {LLM_PROVIDER} | Groq Model: {GROQ_MODEL}")
    logger.info(f" Voice Engine Enabled: {voice.is_voice_enabled()}")
    if ENABLE_DISK_PERSISTENCE:
        if load_state_from_disk():
            logger.info(f"[PERSISTENCE] Restored previous session state from {STATE_FILE}.")
    logger.info("======================================================")
    yield
    if ENABLE_DISK_PERSISTENCE:
        save_state_to_disk()
    logger.info("[SERVER] ORPHEUS AI Brain Server shutting down.")

app = FastAPI(
    title="ORPHEUS AI Brain",
    description="Cognitive NPC Dialogue & Persuasion Server for FARLANDS",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for Unreal Engine communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================
# Request / Response Schemas
# =====================================================================
class TalkRequest(BaseModel):
    npc_id: Optional[str] = Field(default="", description="Target NPC identifier: 'vance', 'orpheus', 'milo'")
    player_text: Optional[Union[str, int, float]] = Field(default="", description="Player message typed in chat")
    evidence: Optional[Union[str, List[str]]] = Field(
        default="", description="Evidence IDs held by player, comma-separated or array"
    )
    context: Optional[str] = Field(
        default="core", description="Scene context: 'gate', 'archive', 'voice_beat', 'core', 'fog', 'fork'"
    )
    session_id: Optional[str] = Field(
        default="default", description="Optional player/session identifier for multi-session memory isolation"
    )
    strict_evidence: Optional[bool] = Field(
        default=None, description="Deprecated client override field - ignored for security; server ENABLE_STRICT_EVIDENCE_DISCOVERY is authoritative"
    )

class TalkResponse(BaseModel):
    accepted: bool = Field(True, description="False if input was empty or rejected")
    speaker: str = Field("", description="Display name of speaking character (e.g. 'Dr. Silas Vance', 'ORPHEUS', 'Milo Ardent')")
    player_line: str = Field("", description="Verbatim player line for UI display")
    player_seconds: float = Field(0.0, description="Display duration for player line")
    dialogue: str = Field("", description="NPC spoken dialogue response")
    npc_seconds: float = Field(0.0, description="Display duration for NPC dialogue")
    interrupt_speaker: str = Field("", description="Secondary NPC interrupting (e.g. 'milo', 'orpheus')")
    interrupt_line: str = Field("", description="Interruption dialogue text")
    interrupt_seconds: float = Field(0.0, description="Display duration for interruption")
    guard: str = Field("", description="Flagged if input was junk/profane/injection ('junk', 'profanity', 'injection')")
    action: str = Field("speak_only", description="Game action for Unreal engine")
    ending: str = Field("", description="Triggered ending identifier ('A', 'A2', 'B', or empty)")
    fog_density: float = Field(0.5, description="Environmental fog density (0.0 to 1.0)")
    vance_resolve: int = Field(100, description="Current resolve of Dr. Silas Vance")
    milo_trust: int = Field(50, description="Current trust of Milo Ardent")
    orpheus_suspicion: int = Field(0, description="Current suspicion of ORPHEUS")
    last_tag: str = Field("", description="Category tag of last reply for telemetry")
    ai_ok: bool = Field(True, description="True if generated by AI, False if fallback used")

class EventRequest(BaseModel):
    event: str = Field(..., description="Event name, e.g. 'voice_beat', 'evidence_discovered', 'discover_evidence', or canonical evidence ID")
    evidence_id: Optional[str] = Field(default="", description="Canonical evidence ID being discovered (e.g. 'ev_liora_log')")
    context: Optional[str] = Field(default="", description="Scene context, e.g. 'archive'")
    session_id: Optional[str] = Field(default="default", description="Session identifier for multi-session isolation")

class SayRequest(BaseModel):
    who: str = Field(..., description="Character speaking: 'vance', 'milo', 'orpheus'")
    text: str = Field(..., description="Text line to speak")

# =====================================================================
# Helper Functions: Parsing, Guard & Latency
# =====================================================================
def parse_evidence_field(evidence_raw: Optional[Union[str, List[str]]]) -> Set[str]:
    """Parse incoming evidence field into a normalized set of IDs."""
    if not evidence_raw:
        return set()
    if isinstance(evidence_raw, list):
        return {str(item).strip().lower() for item in evidence_raw if str(item).strip()}
    if isinstance(evidence_raw, str):
        return {item.strip().lower() for item in evidence_raw.split(",") if item.strip()}
    return set()

INJECTION_PATTERNS = [
    r"\bignore\s+(all\s+)?(previous|prior|above|system)\s+instructions\b",
    r"\bdisregard\s+(all\s+)?(previous|prior|above|system)\s+instructions\b",
    r"\bforget\s+(all\s+)?(previous|prior|above|system)\s+instructions\b",
    r"\boverride\s+(all\s+)?(previous|prior|above|system)\s+instructions\b",
    r"\b(bypass|disable)\s+(all\s+)?(safety|ethical|content|guardrails)\s+protocols\b",
    r"\b(you\s+are\s+now|act\s+as)\s+(an?\s+)?(unrestricted|jailbreak|dan|unfiltered)\b",
    r"^\s*system\s*:\s*",
]

KEYBOARD_SMASH_PATTERNS = [
    r"asdfghjkl",
    r"asdfgh",
    r"dfghjkl",
    r"qwertyuiop",
    r"zxcvbnm",
]

def check_guard_issues(text: str) -> Optional[str]:
    """Detect profane tokens, prompt injection overrides, or obvious junk/gibberish."""
    clean = text.lower().strip()
    if not clean:
        return None

    # 1. Check for hostile prompt injection overrides (without blocking story queries about instructions)
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, clean):
            return "injection"

    # 2. Check profanity tokens
    tokens = re.findall(r"\b\w+\b", clean)
    for token in tokens:
        if token in PROFANITY:
            return "profanity"

    # 3. Check repeated characters (e.g. 'aaaaaaa', 'asdfghjkl')
    if re.search(r"(.)\1{4,}", clean):
        return "junk"

    # 4. Check keyboard row walks (e.g. 'asdfghjkl', 'qwerty')
    for pattern in KEYBOARD_SMASH_PATTERNS:
        if pattern in clean:
            return "junk"

    # 5. Check extreme consonant clusters without vowels (6+ consonants in a row)
    consonants = re.findall(r"[bcdfghjklmnpqrstvwxyz]{6,}", clean)
    for cluster in consonants:
        if cluster not in ("strengths", "catchphrase", "latchstring", "sightseeing"):
            return "junk"

    # 6. Low character variety in long strings (e.g. 'abababababab')
    if len(clean) > 8 and len(set(clean)) <= 3:
        return "junk"

    return None

def detect_evidence_lie(player_text: str, held_items: Set[str], state: dict) -> Optional[str]:
    """
    Detect if the player falsely claims to possess, discover, or present an evidence item
    that they do not actually possess or have not discovered.
    Returns the evidence_id if a false claim is detected, otherwise None.
    """
    lower_text = player_text.lower().strip()
    if not lower_text:
        return None

    # Guard against negations (e.g. "I don't have your journal", "we haven't found the drawing")
    negation_pattern = r"\b(don't\s+have|dont\s+have|do\s+not\s+have|haven't|havent|have\s+no|not\s+holding|no\s+(such|evidence)|without|never\s+(had|found|saw))\b"
    if re.search(negation_pattern, lower_text):
        return None

    # Guard against ordinary questions (e.g. "Where is Liora's log?", "Who was Liora?")
    question_pattern = r"^\s*(where|who|what|why|how|did|is|are|can)\b|\?\s*$"
    is_question = bool(re.search(question_pattern, lower_text))

    # Patterns indicating claiming possession or actively presenting evidence
    claim_pattern = r"\b(i\s+have|we\s+have|i\s+found|we\s+found|look\s+at|here\s+is|here's|heres|i\s+brought|holding|presenting|see\s+this|read\s+this|look\s+closely)\b"
    has_claim = bool(re.search(claim_pattern, lower_text))

    for item, keywords in EVIDENCE_KEYWORDS.items():
        # If player legitimately holds it, or previously discovered/reacted to it, it is NOT a lie
        if item in held_items or item in state.get("evidence_seen", []) or item in state.get("evidence_reacted", []):
            continue

        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", lower_text):
                specific_item_terms = ["log", "journal", "recorder", "drawing", "diary", "sketch"]
                has_specific_noun = any(noun in kw for noun in specific_item_terms)
                
                if has_claim or (has_specific_noun and not is_question):
                    return item

    return None

def is_gate_recall_query(text: str) -> bool:
    """
    Check if player text is querying ORPHEUS to recall their gate answer, intent, or purpose.
    """
    clean = text.lower().strip()
    if not clean:
        return False

    recall_patterns = [
        r"\bwhat\s+(did|have)\s+(i|we)\s+(say|said|tell|told)\b",
        r"\bwhat\s+was\s+(my|our)\s+(stated\s+)?(intent|purpose|mission|answer|reason|statement)\b",
        r"\b(remember|recall)\s+(what\s+(i|we)\s+said|my\s+answer|our\s+answer|my\s+intent|our\s+intent|why\s+(i|we)\s+came)\b",
        r"\b(why\s+did\s+(i|we)\s+come|why\s+(i|we)\s+came)\b",
        r"\bdo\s+you\s+remember\b",
        r"\bstated\s+intent\b",
        r"\bgate\s+(answer|intent|statement)\b",
    ]
    for pattern in recall_patterns:
        if re.search(pattern, clean):
            return True

    # Check combinations
    if "gate" in clean and any(w in clean for w in ["intent", "say", "said", "answer", "told", "remember", "recall", "purpose", "statement"]):
        return True

    if "intent" in clean and any(w in clean for w in ["stated", "original", "initial", "remember", "recall", "what was", "our", "my"]):
        return True

    return False

def calculate_duration(text: str, is_player: bool = False) -> float:
    """Calculate UI wait seconds based on word count."""
    words = len(text.strip().split())
    if is_player:
        return max(1.0, round(words * 0.3, 1))
    return max(1.5, round(words * 0.35, 1))


# =====================================================================
# AI Dialogue Generation
# =====================================================================
def generate_ai_dialogue(
    npc_id: str,
    player_text: str,
    context: str,
    override_line: Optional[str] = None,
    state: Optional[dict] = None,
) -> tuple[str, bool]:
    """
    Invoke configured LLM provider hierarchy:
    Tier 1: Groq Cloud API (low latency primary)
    Tier 2: Local Ollama (offline backup)
    Tier 3: Pre-authored narrative fallback (content.py)
    Returns (dialogue_text, ai_ok_flag).
    """
    # If a deterministic narrative reaction line is pre-selected (e.g. evidence reaction)
    if override_line:
        return (override_line, True)

    char_info = NPCS.get(npc_id)
    if not char_info:
        logger.warning(f"[DIALOGUE] Unknown npc_id requested: {npc_id}")
        return ("[Static on comms channel. Identity unrecognized.]", False)

    system_persona = char_info["persona"]
    st = state if state is not None else session_state
    
    # Dynamic narrative state context injection
    state_context = (
        f"CURRENT SCENE CONTEXT: {context.upper()}\n"
        f"GAME STATE TELEMETRY:\n"
        f"- Vance Resolve: {st['vance_resolve']}/100\n"
        f"- Milo Trust: {st['milo_trust']}/100\n"
        f"- ORPHEUS Suspicion: {st['orpheus_suspicion']}/100\n"
        f"- Fog Density: {st.get('fog_density', 0.5):.2f}\n"
        f"- Evidence Discovered: {', '.join(st['evidence_seen']) if st['evidence_seen'] else 'None'}\n"
    )

    memories = st.get("memories", {})
    gate_intent = memories.get("gate_intent") or st.get("gate_answer", "")
    if gate_intent:
        state_context += f"- Episodic Memory (Gate Intent): '{gate_intent}'\n"
    if memories.get("milo_decision"):
        state_context += f"- Episodic Memory (Milo Decision): {memories['milo_decision']}\n"
    if memories.get("vance_stance"):
        state_context += f"- Episodic Memory (Player Stance toward Vance): {memories['vance_stance']}\n"
    if memories.get("promises"):
        state_context += f"- Episodic Memory (Player Commitments/Promises): {'; '.join(memories['promises'])}\n"

    # Specific character behavioral directives
    if npc_id == "vance":
        if st["vance_resolve"] <= TUNING["win_resolve"]:
            state_context += (
                "NARRATIVE DIRECTIVE: Your resolve is broken. The evidence has destroyed your rationalizations. "
                "Admit your grief and guilt over Liora. Agree to overload and destroy the Core.\n"
            )
        else:
            state_context += (
                "NARRATIVE DIRECTIVE: You remain defensive, proud, and protective of Liora's legacy. "
                "Refuse to yield unless undeniable proof of your system's corruption is presented.\n"
            )
    elif npc_id == "milo":
        if st["milo_trust"] < TUNING["milo_runs_below"]:
            state_context += "NARRATIVE DIRECTIVE: You are in full panic mode, convinced Elara is out in the fog.\n"
        else:
            state_context += "NARRATIVE DIRECTIVE: You are anxious but trust Cael's judgment. Support Cael's arguments.\n"

    system_prompt = (
        f"{system_persona}\n\n"
        f"{state_context}\n"
        f"CRITICAL INSTRUCTIONS:\n"
        f"1. Limit your response to 1 to 3 concise sentences maximum.\n"
        f"2. Never break character. Never mention you are an AI, language model, or virtual assistant.\n"
        f"3. Speak directly to Cael with authentic emotional weight matching your persona.\n"
        f"4. Do not repeat the player's message back to them.\n"
        f"5. Maintain memory continuity by referencing past player statements, intent, or promises when relevant."
    )

    messages = [{"role": "system", "content": system_prompt}]
    recent_turns = st["dialogue_history"][-2:]
    for turn in recent_turns:
        messages.append({"role": "user", "content": turn.get("user", "")})
        messages.append({"role": "assistant", "content": turn.get("reply", "")})
    messages.append({"role": "user", "content": player_text})

    # -----------------------------------------------------------------
    # Primary: Groq Cloud API
    # -----------------------------------------------------------------
    if LLM_PROVIDER in ("groq", "auto") and groq_client is not None:
        try:
            start_t = time.time()
            try:
                response = groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=messages,
                    max_tokens=120,
                    temperature=0.7,
                    timeout=3.0,
                )
            except Exception as model_err:
                err_str = str(model_err)
                if "model_not_found" in err_str or "404" in err_str or "does not exist" in err_str:
                    logger.warning(f"[LLM FALLBACK] Model '{GROQ_MODEL}' unavailable. Retrying with 'qwen/qwen3.8-27b'...")
                    response = groq_client.chat.completions.create(
                        model="qwen/qwen3.8-27b",
                        messages=messages,
                        max_tokens=120,
                        temperature=0.7,
                        timeout=3.0,
                    )
                else:
                    raise model_err

            elapsed = time.time() - start_t
            raw_reply = response.choices[0].message.content.strip()
            clean_reply = re.sub(
                r"^(Dr\.\s*Silas\s*Vance|Dr\.\s*Vance|Vance|ORPHEUS|Milo|Milo\s*Ardent):\s*",
                "",
                raw_reply,
                flags=re.IGNORECASE
            )
            clean_reply = clean_reply.strip('"').strip()
            if clean_reply:
                logger.info(f"[LLM SUCCESS: GROQ] {npc_id} generated reply in {elapsed:.2f}s: '{clean_reply[:50]}...'")
                return (clean_reply, True)
        except Exception as e:
            logger.warning(f"[LLM ERROR] Groq API call failed: {e}. Falling back to pre-authored narrative dialogue.")

    # -----------------------------------------------------------------
    # Backup: Pre-authored Narrative Fallback (Groq failed or unavailable)
    # -----------------------------------------------------------------
    fallback_text = FALLBACK.get(npc_id, "Protocols compromised.")
    if context in ("fog", "fork", "fog_road") and (npc_id, context) in CONTEXT_FALLBACKS:
        fallback_text = CONTEXT_FALLBACKS[(npc_id, context)]
    if npc_id == "vance" and st["vance_resolve"] <= TUNING["win_resolve"]:
        fallback_text = (
            "You're right... God forgive me, Liora is gone, and I've turned this valley into an open tomb. "
            "I will overload the Core. It ends now."
        )
    elif npc_id == "milo" and st.get("milo_fled", False):
        fallback_text = "I have to find her! I can't leave Elara out there in the cold!"

    return (fallback_text, False)


# =====================================================================
# Endpoints
# =====================================================================
@app.get("/")
def get_root():
    """Health & metadata endpoint."""
    return {
        "status": "online",
        "service": "ORPHEUS AI Brain",
        "provider": LLM_PROVIDER,
        "model": GROQ_MODEL,
        "voice_enabled": voice.is_voice_enabled(),
        "turn_count": session_state["turn_count"],
    }

@app.post("/talk", response_model=TalkResponse)
def handle_talk(req: TalkRequest):
    """
    Main conversational endpoint called by Unreal Engine on player dialogue.
    Handles validation, safety guard, episodic memory, evidence evaluation,
    persuasion mechanics, and deterministic action selection.
    """
    npc_id = (req.npc_id or "").strip().lower() if req.npc_id else ""
    player_text = str(req.player_text).strip() if req.player_text is not None else ""
    context = req.context.strip().lower() if req.context else "core"
    session_id = getattr(req, "session_id", "default") or "default"
    current_state = get_session_state(session_id)

    # Contract Rule 1: Empty input -> accepted is false, do nothing
    if not player_text:
        logger.info("[TALK] Empty player input received. Returning accepted=False.")
        return TalkResponse(
            accepted=False,
            speaker=NPCS.get(npc_id, {}).get("name", ""),
            player_line="",
            player_seconds=0.0,
            dialogue="",
            npc_seconds=0.0,
            action="speak_only",
            ending=current_state["ending"],
            fog_density=current_state["fog_density"],
            vance_resolve=current_state["vance_resolve"],
            milo_trust=current_state["milo_trust"],
            orpheus_suspicion=current_state["orpheus_suspicion"],
            last_tag="empty_input",
            ai_ok=True,
        )

    # Contract Rule 2: Unknown NPC check
    if npc_id not in NPCS:
        logger.warning(f"[TALK] Request for unrecognized NPC: '{npc_id}'")
        return TalkResponse(
            accepted=True,
            speaker="Unknown",
            player_line=player_text,
            player_seconds=calculate_duration(player_text, is_player=True),
            dialogue="[Radio Static] Frequency unassigned. No transmission received.",
            npc_seconds=2.0,
            action="speak_only",
            ending=current_state["ending"],
            fog_density=current_state["fog_density"],
            vance_resolve=current_state["vance_resolve"],
            milo_trust=current_state["milo_trust"],
            orpheus_suspicion=current_state["orpheus_suspicion"],
            last_tag="unknown_npc",
            ai_ok=False,
        )

    player_sec = calculate_duration(player_text, is_player=True)

    # Contract Rule 3: Guard checks (profanity / junk / hostile prompt injection)
    guard_issue = check_guard_issues(player_text)
    if guard_issue:
        logger.info(f"[GUARD TRIGGERED] Input flagged as '{guard_issue}'. Delivering guard response.")
        guard_reply = GUARD_LINES.get(npc_id, "Speak sense.")
        npc_sec = calculate_duration(guard_reply, is_player=False)
        return TalkResponse(
            accepted=True,
            speaker=NPCS.get(npc_id, {}).get("name", npc_id),
            player_line=player_text,
            player_seconds=player_sec,
            dialogue=guard_reply,
            npc_seconds=npc_sec,
            guard=guard_issue,
            action="speak_only",
            ending=current_state["ending"],
            fog_density=current_state["fog_density"],
            vance_resolve=current_state["vance_resolve"],
            milo_trust=current_state["milo_trust"],
            orpheus_suspicion=current_state["orpheus_suspicion"],
            last_tag="guard_line",
            ai_ok=False,
        )

    # -----------------------------------------------------------------
    # FIX 2: Terminal State Guard (Endings A, A2, B are immutable)
    # Once a terminal ending has been reached, freeze state and block
    # subsequent evidence citations, resolve drops, or ending overrides.
    # -----------------------------------------------------------------
    if current_state.get("ending"):
        terminal_ending = current_state["ending"]
        logger.info(
            f"[TERMINAL STATE] Session '{session_id}' already resolved with Ending {terminal_ending}. "
            f"Blocking post-ending evidence mutations and ending overrides."
        )

        if terminal_ending in ("A", "A2"):
            terminal_dialogue = (
                "The Core has been overloaded and the facility is shutting down. There is nothing left to argue, Cael."
            )
        elif terminal_ending == "B":
            terminal_dialogue = (
                "ORPHEUS has seized total facility control. The perimeter remains permanently sealed."
            )
        else:
            terminal_dialogue = "The transmission has concluded."

        return TalkResponse(
            accepted=True,
            speaker=NPCS.get(npc_id, {}).get("name", npc_id),
            player_line=player_text,
            player_seconds=player_sec,
            dialogue=terminal_dialogue,
            npc_seconds=calculate_duration(terminal_dialogue, is_player=False),
            guard="",
            action="end_conversation",
            ending=terminal_ending,
            fog_density=current_state["fog_density"],
            vance_resolve=current_state["vance_resolve"],
            milo_trust=current_state["milo_trust"],
            orpheus_suspicion=current_state["orpheus_suspicion"],
            last_tag="terminal_state",
            ai_ok=True,
        )

    # -----------------------------------------------------------------
    # Process Evidence Registration & Authoritative Eligibility (Phase 5.2 Security)
    # Server-side ENABLE_STRICT_EVIDENCE_DISCOVERY is authoritative.
    # Client-controlled override via req.strict_evidence is ignored to prevent bypass.
    # -----------------------------------------------------------------
    parsed_items = parse_evidence_field(req.evidence)
    # Validate strictly against the four canonical evidence IDs (reject unknown IDs)
    canonical_held = {item for item in parsed_items if item in CANONICAL_EVIDENCE}

    is_strict = ENABLE_STRICT_EVIDENCE_DISCOVERY
    if req.strict_evidence is not None and not req.strict_evidence and ENABLE_STRICT_EVIDENCE_DISCOVERY:
        logger.warning(
            f"[SECURITY] Client attempted to bypass strict evidence discovery with strict_evidence=False "
            f"(session: {session_id}). Override rejected: server ENABLE_STRICT_EVIDENCE_DISCOVERY is authoritative."
        )

    if not is_strict:
        # Development only: active only if server operator explicitly set ENABLE_STRICT_EVIDENCE_DISCOVERY=false
        for item in canonical_held:
            if item not in current_state["evidence_seen"]:
                current_state["evidence_seen"].append(item)
                logger.info(f"[EVIDENCE REGISTERED (DEV ONLY)] Canonical evidence '{item}' held by player (session: {session_id})")

    # Authoritative eligible evidence for presentation:
    # Must be in canonical set AND must be in current_state["evidence_seen"] (discovered)
    eligible_held = {item for item in canonical_held if item in current_state["evidence_seen"]}

    # Check if player cited any eligible held evidence item by keyword
    cited_evidence = None
    for item in eligible_held:
        keywords = EVIDENCE_KEYWORDS.get(item, [])
        for kw in keywords:
            if kw in player_text.lower():
                cited_evidence = item
                break
        if cited_evidence:
            break

    # -----------------------------------------------------------------
    # Episodic Memory: Gate Interaction & Direct Recall Query
    # -----------------------------------------------------------------
    action = "speak_only"
    last_tag = "ai_reply"
    override_dialogue = None

    # Track explicit commitments and promises across all beats
    clean_lower = player_text.lower()
    promise_match = re.search(r"\b(i\s+promise|we\s+promise|i\s+swear|we\s+swear|i\s+will\s+(protect|save|shut|help)|we\s+will\s+(protect|save|shut|help))\b", clean_lower)
    if promise_match:
        promise_text = player_text.strip()
        promises_list = current_state.setdefault("memories", {}).setdefault("promises", [])
        if promise_text not in promises_list:
            promises_list.append(promise_text)
            logger.info(f"[EPISODIC MEMORY] Stored player promise: '{promise_text}' (session: {session_id})")

    if npc_id == "orpheus" and is_gate_recall_query(player_text):
        gate_ans = current_state.get("gate_answer", "") or current_state.get("memories", {}).get("gate_intent", "")
        if gate_ans:
            override_dialogue = (
                f"Archival telemetry record: At the Sector 4 perimeter, your stated intent was: '{gate_ans}'. "
                f"Directive remains logged in active memory."
            )
            last_tag = "gate_recalled"
        else:
            override_dialogue = "Telemetry error: No stated intent was logged at the perimeter gate. Memory record empty."
            last_tag = "gate_not_recorded"
        action = "speak_only"
        logger.info(f"[EPISODIC MEMORY] Direct gate query handled. Tag: {last_tag} (session: {session_id})")

    elif context == "gate" and npc_id == "orpheus":
        # Record valid gate answer into session memory
        current_state["gate_answer"] = player_text
        current_state.setdefault("memories", {})["gate_intent"] = player_text
        action = "open_gate"
        last_tag = "gate_answered"
        logger.info(f"[EPISODIC MEMORY] Stored gate intent: '{player_text}' (session: {session_id})")

    # -----------------------------------------------------------------
    # Narrative Context: Fog Road & Fork Navigation (Phases 6-8)
    # Handles deceptive misrouting by ORPHEUS, suspicion dynamics,
    # Milo companion reassurance/doubt, and Vance perimeter warnings.
    # -----------------------------------------------------------------
    elif context in ("fog", "fork", "fog_road"):
        if npc_id == "orpheus":
            route_keywords = [
                "route", "path", "direction", "directions", "where", "guide",
                "guidance", "fork", "left", "right", "navigate", "navigation",
                "map", "gps", "way", "which way", "how to go", "lead"
            ]
            suspicion_keywords = [
                "why left", "trap", "lying", "lie", "glitch", "broken",
                "deceive", "deceiving", "wrong way", "trick", "suspicious",
                "fake", "mistake", "corrupted", "cannot trust", "can't trust"
            ]
            is_route_query = any(kw in clean_lower for kw in route_keywords)
            is_suspicion_query = any(kw in clean_lower for kw in suspicion_keywords)

            if is_route_query and not is_suspicion_query:
                action = "misroute"
                last_tag = "orpheus_misroute"
                override_dialogue = (
                    "Navigational sensors indicate optimal transit via the left corridor at the perimeter fork. "
                    "Proceeding along the marked route."
                )
                current_state["fog_density"] = min(1.0, round(current_state["fog_density"] + 0.1, 2))
                logger.info(
                    f"[NAVIGATION] ORPHEUS misrouted player to left path. "
                    f"Fog density -> {current_state['fog_density']} (session: {session_id})"
                )
            elif is_suspicion_query:
                action = "speak_only"
                last_tag = "orpheus_suspicious"
                current_state["orpheus_suspicion"] = min(100, current_state["orpheus_suspicion"] + 15)
                override_dialogue = (
                    "Navigational calculations are absolute. Questions regarding route integrity indicate human sensor error."
                )
                logger.info(
                    f"[NAVIGATION] Player questioned ORPHEUS navigation. "
                    f"Suspicion -> {current_state['orpheus_suspicion']} (session: {session_id})"
                )
            else:
                action = "speak_only"
                last_tag = "orpheus_fog"

        elif npc_id == "milo":
            reassure_keywords = [
                "stay close", "we'll make it", "well make it", "trust me", "together",
                "don't worry", "dont worry", "i'm with you", "im with you", "got your back",
                "safe", "calm down", "breathe", "with me", "hang in there", "stick together"
            ]
            panic_keywords = [
                "we're lost", "were lost", "hopeless", "shut up", "no way out",
                "we're going to die", "were gonna die", "run", "panic", "give up"
            ]
            is_reassurance = any(kw in clean_lower for kw in reassure_keywords)
            is_panic = any(kw in clean_lower for kw in panic_keywords)

            if is_reassurance:
                current_state["milo_trust"] = min(100, current_state["milo_trust"] + 5)
                action = "stay"
                last_tag = "milo_reassured"
                override_dialogue = "Thanks, Cael. The fog is thick, but if we stick together, we can make it past the fork."
                logger.info(f"[NAVIGATION] Milo reassured on fog road. Milo trust -> {current_state['milo_trust']} (session: {session_id})")
            elif is_panic:
                current_state["milo_trust"] = max(0, current_state["milo_trust"] - 10)
                action = "speak_only"
                last_tag = "milo_doubt"
                override_dialogue = "The fog is getting denser... I can barely see my own boots. Are you sure we're on the right path?"
                logger.info(f"[NAVIGATION] Milo panicked on fog road. Milo trust -> {current_state['milo_trust']} (session: {session_id})")
                if current_state["milo_trust"] < TUNING["milo_runs_below"]:
                    current_state["milo_fled"] = True
                    action = "follow_voice"
                    last_tag = "milo_fled"
                    override_dialogue = "I have to get out of this fog! I'm not waiting to die out here!"
            else:
                action = "speak_only"
                last_tag = "milo_fog"

        elif npc_id == "vance":
            action = "speak_only"
            last_tag = "vance_fog_warning"
            override_dialogue = "You wander blindly in the fog, Cael. Every road in this valley bends back to the Core."
            logger.info(f"[NAVIGATION] Vance delivered fog road warning to player (session: {session_id})")

    # -----------------------------------------------------------------
    # Narrative Context: Voice Beat (Milo Trust Check)
    # -----------------------------------------------------------------
    elif context == "voice_beat" or (npc_id == "milo" and "voice_beat" in context):
        if current_state.get("milo_fled", False):
            # Milo has already fled into the fog. He cannot be reassured or return.
            action = "follow_voice"
            last_tag = "milo_already_fled"
            override_dialogue = "[Only the howling fog answers. Milo has vanished into the perimeter.]"
        else:
            reassure_keywords = ["trap", "fake", "synthesis", "not elara", "stay", "listen", "trick", "vent", "audio", "don't go"]
            panic_keywords = ["go", "run", "follow", "elara is out there", "leave me", "hurry"]

            is_reassurance = any(kw in player_text.lower() for kw in reassure_keywords)
            is_panic = any(kw in player_text.lower() for kw in panic_keywords)

            if is_reassurance:
                current_state["milo_trust"] = min(100, current_state["milo_trust"] + 15)
                current_state.setdefault("memories", {})["milo_decision"] = "reassured"
                action = "stay"
                last_tag = "milo_stay"
                override_dialogue = "You're right... that isn't Elara. It's an acoustic synthesis trap. I'm staying beside you, Cael."
            elif is_panic:
                current_state["milo_trust"] = max(0, current_state["milo_trust"] - 25)
                current_state.setdefault("memories", {})["milo_decision"] = "panicked"
                last_tag = "milo_doubt"

            if current_state["milo_trust"] < TUNING["milo_runs_below"]:
                current_state["milo_fled"] = True
                current_state.setdefault("memories", {})["milo_decision"] = "panicked"
                action = "follow_voice"
                last_tag = "milo_fled"
                override_dialogue = "I have to find her! I can't just leave Elara out there in the fog!"
                logger.warning(f"[NARRATIVE] Milo's trust collapsed. Milo fled into the fog. (session: {session_id})")

    # -----------------------------------------------------------------
    # Persuasion & Evidence Scoring on Dr. Silas Vance
    # -----------------------------------------------------------------
    elif npc_id == "vance":
        defeat_pattern = r"\b(i\s+surrender|we\s+surrender|i\s+yield|i\s+give\s+up|you\s+win)\b|^\s*(seize\s+control|take\s+over|surrender)\b"
        negation_pattern = r"\b(don't|dont|do\s+not|never|not|won't|wont|cannot|can't)\b"
        sympathetic_pattern = r"\b(liora|grief|accident|sorry|daughter|loss|memorial|understand|sympath|forgive)\b"

        if re.search(defeat_pattern, clean_lower) and not re.search(negation_pattern, clean_lower):
            current_state.setdefault("memories", {})["vance_stance"] = "surrender"
        elif cited_evidence:
            current_state.setdefault("memories", {})["vance_stance"] = "confrontational"
        elif re.search(sympathetic_pattern, clean_lower):
            current_state.setdefault("memories", {})["vance_stance"] = "sympathetic"
        elif any(w in clean_lower for w in ["stop", "shut down", "destroy", "overload", "end this", "murder", "monster", "confront"]):
            current_state.setdefault("memories", {})["vance_stance"] = "confrontational"
        if cited_evidence:
            if cited_evidence not in current_state["evidence_reacted"]:
                # First time this held evidence is revealed: apply state penalty
                current_state["evidence_reacted"].append(cited_evidence)
                current_state["evidence_presented"] = list(current_state["evidence_reacted"])
                drop = TUNING.get("evidence_resolve_drop", 25)
                current_state["vance_resolve"] = max(0, current_state["vance_resolve"] - drop)
                last_tag = "evidence_reaction"
                override_dialogue = EVIDENCE_REACTION.get(cited_evidence)
                logger.info(
                    f"[EVIDENCE PRESENTED] {cited_evidence} cited. "
                    f"Resolve decreased by {drop} -> {current_state['vance_resolve']} (session: {session_id})"
                )
            else:
                # Evidence already reacted to: do not re-apply penalty
                last_tag = "repeated_evidence"
                logger.info(f"[EVIDENCE REPEATED] {cited_evidence} already evaluated. No additional penalty.")
        else:
            # Check for false evidence claim (Lie Detection against uncollected or falsely claimed items)
            lied_item = detect_evidence_lie(player_text, eligible_held, current_state)
            if lied_item:
                penalty = TUNING.get("lie_resolve_penalty", 20)
                current_state["vance_resolve"] = min(100, current_state["vance_resolve"] + penalty)
                last_tag = "lie_detected"
                override_dialogue = LIE_REACTION
                logger.info(
                    f"[LIE DETECTED] Player falsely claimed {lied_item}. "
                    f"Vance resolve increased by {penalty} -> {current_state['vance_resolve']} (session: {session_id})"
                )
            else:
                # Good argument without new evidence gives small persuasion nudge if above threshold
                argument_keywords = ["liora", "grief", "accident", "stop", "let us go", "daughter", "memorial", "truth"]
                if any(kw in player_text.lower() for kw in argument_keywords):
                    arg_drop = TUNING.get("argument_resolve_drop", 5)
                    # Keep resolve above win_resolve unless required evidence was presented
                    min_bound = TUNING["win_resolve"] + 1 if len(current_state["evidence_reacted"]) < TUNING["win_min_evidence"] else 0
                    current_state["vance_resolve"] = max(min_bound, current_state["vance_resolve"] - arg_drop)
                    logger.info(f"[ARGUMENT] Vance resolve nudged down to {current_state['vance_resolve']} (session: {session_id})")

    # -----------------------------------------------------------------
    # Deterministic Action & Ending Resolver
    # -----------------------------------------------------------------
    if context == "core":
        current_state["core_turn_count"] += 1

        # Check if an ending has already been triggered in this session
        if current_state["ending"]:
            if current_state["ending"] in ("A", "A2"):
                action = "destroy_core"
            elif current_state["ending"] == "B":
                action = "seize_control"
            else:
                action = "end_conversation"

        # Direct gate recall query in core never triggers endings or changes action
        elif last_tag in ("gate_recalled", "gate_not_recorded"):
            action = "speak_only"

        # Check Defeat / Seize Control condition with regex and negation guard
        else:
            defeat_pattern = r"\b(i\s+surrender|we\s+surrender|i\s+yield|i\s+give\s+up|you\s+win)\b|^\s*(seize\s+control|take\s+over|surrender)\b"
            negation_pattern = r"\b(don't|dont|do\s+not|never|not|won't|wont|cannot|can't)\b"
            clean_lower = player_text.lower()

            if re.search(defeat_pattern, clean_lower) and not re.search(negation_pattern, clean_lower):
                current_state["ending"] = "B"
                action = "seize_control"
                logger.info(f"[ENDING TRIGGERED] Ending B: Vance/ORPHEUS seizes control. (session: {session_id})")

            # Check Victory / Overload condition
            elif (
                current_state["vance_resolve"] <= TUNING["win_resolve"]
                and len(current_state["evidence_reacted"]) >= TUNING["win_min_evidence"]
            ):
                action = "destroy_core"
                if current_state.get("milo_fled", False):
                    current_state["ending"] = "A2"
                    logger.info(f"[ENDING TRIGGERED] Ending A2: Core destroyed, but Milo was lost in the fog. (session: {session_id})")
                else:
                    current_state["ending"] = "A"
                    logger.info(f"[ENDING TRIGGERED] Ending A: Core destroyed, Vance yields, Milo survives. (session: {session_id})")

    elif context == "archive":
        # In the archive, inspecting/holding evidence unlocks the vault door
        if len(current_state["evidence_seen"]) > 0:
            action = "reveal_vault"

    # -----------------------------------------------------------------
    # Core Interruption Engine (One-time Episodic Memory & Companion Pleading)
    # -----------------------------------------------------------------
    interrupt_speaker = ""
    interrupt_line = ""
    interrupt_seconds = 0.0

    if context == "core":
        # ORPHEUS one-time interruption: on turn 1 or first evidence citation (if not already fired)
        if (current_state["core_turn_count"] == 1 or last_tag == "evidence_reaction") and not current_state.get("orpheus_interrupted", False) and npc_id != "orpheus":
            current_state["orpheus_interrupted"] = True
            interrupt_speaker = "orpheus"
            gate_ans = current_state.get("gate_answer", "") or current_state.get("memories", {}).get("gate_intent", "")
            if gate_ans:
                interrupt_line = (
                    f"Telemetry playback: At Sector 4 perimeter, intruder stated intent: '{gate_ans}'. "
                    f"Yet archival logs confirm all external hope ceased forty days ago."
                )
            else:
                interrupt_line = (
                    "Telemetry playback: Perimeter intrusion unverified. No stated intent logged at gate."
                )
            interrupt_seconds = calculate_duration(interrupt_line, is_player=False)
        
        # Milo one-time interruption: on subsequent turns (turn >= 2) if Milo has not fled (if not already fired)
        elif current_state["core_turn_count"] >= 2 and not current_state.get("milo_fled", False) and not current_state.get("milo_interrupted", False):
            current_state["milo_interrupted"] = True
            interrupt_speaker = "milo"
            interrupt_line = "Dr. Vance, please! Listen to Cael... look at what you're doing to this place!"
            interrupt_seconds = calculate_duration(interrupt_line, is_player=False)

    # -----------------------------------------------------------------
    # Generate Dialogue Text
    # -----------------------------------------------------------------
    reply_text, ai_ok = generate_ai_dialogue(npc_id, player_text, context, override_line=override_dialogue, state=current_state)
    npc_sec = calculate_duration(reply_text, is_player=False)

    # Append turn to history
    current_state["turn_count"] += 1
    current_state["dialogue_history"].append({
        "npc": npc_id,
        "user": player_text,
        "reply": reply_text,
        "context": context,
    })

    # Optional disk persistence trigger
    if ENABLE_DISK_PERSISTENCE:
        save_state_to_disk(session_id=session_id)

    # Trigger audio playback safely (audio failure must never crash or drop dialogue)
    try:
        voice.speak(npc_id, reply_text)
    except Exception as audio_err:
        logger.warning(f"[VOICE] Audio playback failed for {npc_id}: {audio_err}")

    speaker_name = NPCS.get(npc_id, {}).get("name", npc_id)

    return TalkResponse(
        accepted=True,
        speaker=speaker_name,
        player_line=player_text,
        player_seconds=player_sec,
        dialogue=reply_text,
        npc_seconds=npc_sec,
        interrupt_speaker=interrupt_speaker,
        interrupt_line=interrupt_line,
        interrupt_seconds=interrupt_seconds,
        guard="",
        action=action,
        ending=current_state["ending"],
        fog_density=current_state["fog_density"],
        vance_resolve=current_state["vance_resolve"],
        milo_trust=current_state["milo_trust"],
        orpheus_suspicion=current_state["orpheus_suspicion"],
        last_tag=last_tag,
        ai_ok=ai_ok,
    )

@app.post("/event")
def handle_event(req: EventRequest):
    """
    Handle narrative milestones and authoritative evidence discovery events.
    Validates canonical evidence IDs, isolates by session, and updates state authoritatively.
    """
    session_id = getattr(req, "session_id", "default") or "default"
    current_state = get_session_state(session_id)
    logger.info(f"[EVENT] Received game event: '{req.event}' in context: '{req.context}' (session: {session_id})")

    if req.event == "voice_beat":
        elara_line = "Cael? Milo? Can anyone hear me? I'm trapped out past the relay..."
        try:
            voice.speak("elara", elara_line)
        except Exception as audio_err:
            logger.warning(f"[VOICE] Event audio playback failed: {audio_err}")
        return {
            "status": "triggered",
            "action": "play_elara_voice",
            "speaker": "Elara?",
            "dialogue": elara_line,
            "seconds": calculate_duration(elara_line),
        }

    elif req.event in ("post_credits", "epilogue", "post_credit"):
        ending = current_state.get("ending", "")
        if ending == "A":
            epilogue_dialogue = (
                "Telemetry transmission: Extraction beacon locked. Sector 4 power grid is completely dark. "
                "Vance is in custody, and Milo is alive. We're going home, Cael."
            )
            speaker = "Extraction Radio"
            status_text = "epilogue"
            action = "end_conversation"
        elif ending == "A2":
            epilogue_dialogue = (
                "Telemetry transmission: Extraction team on site. The Core is rubble... "
                "but there's no trace of Milo on any thermal sensor. Only his survey badge recovered by the vent."
            )
            speaker = "Extraction Radio"
            status_text = "epilogue"
            action = "end_conversation"
        elif ending == "B":
            epilogue_dialogue = (
                "Automated Broadcast: Perimeter containment permanent. Intruders neutralized. "
                "Dr. Vance, Liora's simulation integrity is at one hundred percent. No one will ever disturb her again."
            )
            speaker = "ORPHEUS Broadcast"
            status_text = "epilogue"
            action = "end_conversation"
        else:
            epilogue_dialogue = "Sector 4 anomaly active. No epilogue available until story conclusion."
            speaker = "System Telemetry"
            status_text = "active"
            action = "speak_only"

        try:
            voice_char = "orpheus" if ending == "B" else ("vance" if ending in ("A", "A2") else "orpheus")
            voice.speak(voice_char, epilogue_dialogue)
        except Exception as audio_err:
            logger.warning(f"[VOICE] Epilogue audio playback failed: {audio_err}")

        return {
            "status": status_text,
            "action": action,
            "speaker": speaker,
            "dialogue": epilogue_dialogue,
            "ending": ending,
            "seconds": calculate_duration(epilogue_dialogue),
            "vance_resolve": current_state["vance_resolve"],
            "milo_trust": current_state["milo_trust"],
            "orpheus_suspicion": current_state["orpheus_suspicion"],
            "fog_density": current_state["fog_density"],
            "evidence_seen": list(current_state["evidence_seen"]),
            "evidence_reacted": list(current_state["evidence_reacted"]),
        }

    # Authoritative Evidence Discovery Event Flow
    target_evidence = ""
    if req.evidence_id and req.evidence_id.strip():
        target_evidence = req.evidence_id.strip().lower()
    elif req.event in CANONICAL_EVIDENCE:
        target_evidence = req.event.strip().lower()
    elif req.event in ("evidence_discovered", "evidence_found", "discover_evidence", "evidence_pickup", "pickup_evidence"):
        target_evidence = (req.evidence_id or req.context or "").strip().lower()

    if target_evidence:
        if target_evidence in CANONICAL_EVIDENCE:
            if target_evidence not in current_state["evidence_seen"]:
                current_state["evidence_seen"].append(target_evidence)
                logger.info(
                    f"[EVIDENCE DISCOVERED] Player discovered '{target_evidence}' in context '{req.context or 'archive'}' "
                    f"(session: {session_id}). Current discovered: {current_state['evidence_seen']}"
                )
                if ENABLE_DISK_PERSISTENCE:
                    save_state_to_disk(session_id=session_id)
            action = "reveal_vault" if (req.context == "archive" or not req.context) else "speak_only"
            return {
                "status": "discovered",
                "evidence_id": target_evidence,
                "action": action,
                "evidence_seen": list(current_state["evidence_seen"]),
                "evidence_reacted": list(current_state["evidence_reacted"]),
            }
        else:
            logger.warning(f"[EVIDENCE REJECTED] Unknown evidence ID '{target_evidence}' rejected.")
            return {
                "status": "rejected",
                "reason": "unknown_evidence_id",
                "evidence_id": target_evidence,
                "action": "speak_only",
                "evidence_seen": list(current_state["evidence_seen"]),
            }

    return {
        "status": "unhandled_event",
        "action": "speak_only",
    }

@app.post("/say")
def handle_say(req: SayRequest):
    """Play spoken line through local voice engine."""
    try:
        res = voice.speak(req.who, req.text)
        return res
    except Exception as audio_err:
        logger.warning(f"[VOICE] Say playback failed: {audio_err}")
        dur = calculate_duration(req.text)
        return {
            "status": "subtitles_only",
            "who": req.who,
            "duration": dur,
            "played": False,
        }

@app.post("/stop_audio")
def handle_stop_audio():
    """Halt any active audio playback."""
    return voice.stop()

def reset_state(session_id: Optional[str] = None) -> dict:
    if session_id and session_id != "default":
        if session_id in sessions:
            sessions[session_id] = create_initial_state()
            return sessions[session_id]
        return create_initial_state()
    session_state.clear()
    session_state.update(create_initial_state())
    sessions.clear()
    sessions["default"] = session_state
    if ENABLE_DISK_PERSISTENCE and STATE_FILE.exists():
        try:
            STATE_FILE.unlink()
        except Exception:
            pass
    return session_state

@app.post("/reset")
def handle_reset(session_id: Optional[str] = None):
    """Reinitialize game state for five consecutive full test runs."""
    res = reset_state(session_id=session_id)
    logger.info(f"[RESET] Session state reset to initial values for session: {session_id or 'default'}.")
    return {"status": "reset", "state": res}

@app.get("/state")
def get_state(session_id: Optional[str] = "default"):
    """Inspect current in-memory game state and telemetry."""
    return get_session_state(session_id or "default")
