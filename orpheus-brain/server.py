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
import time
import logging
from typing import Optional, Union, List, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Import content and voice modules
from content import (
    NPCS,
    FALLBACK,
    GUARD_LINES,
    EVIDENCE_KEYWORDS,
    EVIDENCE_REACTION,
    PROFANITY,
    TUNING,
)
import voice

# =====================================================================
# Logging Configuration (Sanitized - Never log API keys or secrets)
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("orpheus.server")

# Load environment variables
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()

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
        "gate_answer": "",         # Episodic memory recorded at context="gate"
        "milo_fled": False,        # True if Milo panicked and ran into the fog
        "ending": "",              # Triggered ending ('', 'A', 'A2', 'B')
        "dialogue_history": [],    # Prior conversational turns
        "turn_count": 0,
        "core_turn_count": 0,      # Number of turns elapsed in the core room
    }

session_state = create_initial_state()

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
    logger.info("======================================================")
    yield
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
    npc_id: str = Field(..., description="Target NPC identifier: 'vance', 'orpheus', 'milo'")
    player_text: str = Field(default="", description="Player message typed in chat")
    evidence: Optional[Union[str, List[str]]] = Field(
        default="", description="Evidence IDs held by player, comma-separated or array"
    )
    context: Optional[str] = Field(
        default="core", description="Scene context: 'gate', 'archive', 'voice_beat', 'core', 'fog', 'fork'"
    )

class TalkResponse(BaseModel):
    accepted: bool = Field(True, description="False if input was empty or rejected")
    player_line: str = Field("", description="Verbatim player line for UI display")
    player_seconds: float = Field(0.0, description="Display duration for player line")
    dialogue: str = Field("", description="NPC spoken dialogue response")
    npc_seconds: float = Field(0.0, description="Display duration for NPC dialogue")
    interrupt_speaker: str = Field("", description="Secondary NPC interrupting (e.g. 'milo', 'orpheus')")
    interrupt_line: str = Field("", description="Interruption dialogue text")
    interrupt_seconds: float = Field(0.0, description="Display duration for interruption")
    guard: str = Field("", description="Flagged if input was junk/profane ('junk', 'profanity')")
    action: str = Field("speak_only", description="Game action for Unreal engine")
    ending: str = Field("", description="Triggered ending identifier ('A', 'A2', 'B', or empty)")
    fog_density: float = Field(0.5, description="Environmental fog density (0.0 to 1.0)")
    vance_resolve: int = Field(100, description="Current resolve of Dr. Silas Vance")
    milo_trust: int = Field(50, description="Current trust of Milo Ardent")
    orpheus_suspicion: int = Field(0, description="Current suspicion of ORPHEUS")
    last_tag: str = Field("", description="Category tag of last reply for telemetry")
    ai_ok: bool = Field(True, description="True if generated by AI, False if fallback used")

class EventRequest(BaseModel):
    event: str = Field(..., description="Event name, e.g. 'voice_beat'")
    context: Optional[str] = Field(default="", description="Scene context")

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

def check_junk_or_profanity(text: str) -> Optional[str]:
    """Detect profane tokens or obvious junk/gibberish."""
    clean = text.lower().strip()
    if not clean:
        return None
    
    # Check profanity tokens
    tokens = re.findall(r"\b\w+\b", clean)
    for token in tokens:
        if token in PROFANITY:
            return "profanity"
            
    # Check repeated characters / keyboard smash (e.g. 'aaaaaaa', 'asdfghjkl')
    if re.search(r"(.)\1{4,}", clean):
        return "junk"
    if len(clean) > 8 and len(set(clean)) <= 3:
        return "junk"
        
    return None

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
    override_line: Optional[str] = None
) -> tuple[str, bool]:
    """
    Invoke configured LLM provider (Groq) to generate in-character reply.
    Incorporates current game state and narrative context into the prompt.
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
    
    # Dynamic narrative state context injection
    state_context = (
        f"CURRENT SCENE CONTEXT: {context.upper()}\n"
        f"GAME STATE TELEMETRY:\n"
        f"- Vance Resolve: {session_state['vance_resolve']}/100\n"
        f"- Milo Trust: {session_state['milo_trust']}/100\n"
        f"- ORPHEUS Suspicion: {session_state['orpheus_suspicion']}/100\n"
        f"- Evidence Discovered: {', '.join(session_state['evidence_seen']) if session_state['evidence_seen'] else 'None'}\n"
    )

    if session_state["gate_answer"]:
        state_context += f"- Remembered Gate Intent from Player: '{session_state['gate_answer']}'\n"

    # Specific character behavioral directives
    if npc_id == "vance":
        if session_state["vance_resolve"] <= TUNING["win_resolve"]:
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
        if session_state["milo_trust"] < TUNING["milo_runs_below"]:
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
        f"4. Do not repeat the player's message back to them."
    )

    # Groq provider path
    if LLM_PROVIDER == "groq" and groq_client is not None:
        try:
            start_t = time.time()
            messages = [{"role": "system", "content": system_prompt}]
            
            # Include last 2 turns of dialogue history for immediate conversational context
            recent_turns = session_state["dialogue_history"][-2:]
            for turn in recent_turns:
                messages.append({"role": "user", "content": turn.get("user", "")})
                messages.append({"role": "assistant", "content": turn.get("reply", "")})

            messages.append({"role": "user", "content": player_text})

            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                max_tokens=120,
                temperature=0.7,
                timeout=3.0,
            )
            elapsed = time.time() - start_t
            raw_reply = response.choices[0].message.content.strip()

            # Clean reply: strip accidental character prefixes (e.g. "Dr. Vance: ...")
            clean_reply = re.sub(
                r"^(Dr\.\s*Silas\s*Vance|Dr\.\s*Vance|Vance|ORPHEUS|Milo|Milo\s*Ardent):\s*",
                "",
                raw_reply,
                flags=re.IGNORECASE
            )
            clean_reply = clean_reply.strip('"').strip()

            logger.info(f"[LLM SUCCESS] {npc_id} generated reply in {elapsed:.2f}s: '{clean_reply[:50]}...'")
            return (clean_reply, True)
        except Exception as e:
            logger.error(f"[LLM ERROR] Groq API call failed: {e}. Falling back to pre-authored line.")

    # Offline / fallback line generation
    fallback_text = FALLBACK.get(npc_id, "Protocols compromised.")
    if npc_id == "vance" and session_state["vance_resolve"] <= TUNING["win_resolve"]:
        fallback_text = (
            "You're right... God forgive me, Liora is gone, and I've turned this valley into an open tomb. "
            "I will overload the Core. It ends now."
        )
    elif npc_id == "milo" and session_state.get("milo_fled", False):
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
    npc_id = req.npc_id.strip().lower()
    player_text = req.player_text.strip()
    context = req.context.strip().lower() if req.context else "core"

    # Contract Rule 1: Empty input -> accepted is false, do nothing
    if not player_text:
        logger.info("[TALK] Empty player input received. Returning accepted=False.")
        return TalkResponse(
            accepted=False,
            player_line="",
            player_seconds=0.0,
            dialogue="",
            npc_seconds=0.0,
            action="speak_only",
            ending=session_state["ending"],
            fog_density=session_state["fog_density"],
            vance_resolve=session_state["vance_resolve"],
            milo_trust=session_state["milo_trust"],
            orpheus_suspicion=session_state["orpheus_suspicion"],
            last_tag="empty_input",
            ai_ok=True,
        )

    # Contract Rule 2: Unknown NPC check
    if npc_id not in NPCS:
        logger.warning(f"[TALK] Request for unrecognized NPC: '{npc_id}'")
        return TalkResponse(
            accepted=True,
            player_line=player_text,
            player_seconds=calculate_duration(player_text, is_player=True),
            dialogue="[Radio Static] Frequency unassigned. No transmission received.",
            npc_seconds=2.0,
            action="speak_only",
            ending=session_state["ending"],
            fog_density=session_state["fog_density"],
            vance_resolve=session_state["vance_resolve"],
            milo_trust=session_state["milo_trust"],
            orpheus_suspicion=session_state["orpheus_suspicion"],
            last_tag="unknown_npc",
            ai_ok=False,
        )

    player_sec = calculate_duration(player_text, is_player=True)

    # Contract Rule 3: Guard checks (profanity/junk)
    guard_issue = check_junk_or_profanity(player_text)
    if guard_issue:
        logger.info(f"[GUARD TRIGGERED] Input flagged as '{guard_issue}'. Delivering guard response.")
        guard_reply = GUARD_LINES.get(npc_id, "Speak sense.")
        npc_sec = calculate_duration(guard_reply, is_player=False)
        return TalkResponse(
            accepted=True,
            player_line=player_text,
            player_seconds=player_sec,
            dialogue=guard_reply,
            npc_seconds=npc_sec,
            guard=guard_issue,
            action="speak_only",
            ending=session_state["ending"],
            fog_density=session_state["fog_density"],
            vance_resolve=session_state["vance_resolve"],
            milo_trust=session_state["milo_trust"],
            orpheus_suspicion=session_state["orpheus_suspicion"],
            last_tag="guard_line",
            ai_ok=True,
        )

    # -----------------------------------------------------------------
    # Process Evidence Registration
    # -----------------------------------------------------------------
    held_items = parse_evidence_field(req.evidence)
    for item in held_items:
        if item in EVIDENCE_KEYWORDS and item not in session_state["evidence_seen"]:
            session_state["evidence_seen"].append(item)

    # Check if player cited any held evidence item by keyword
    cited_evidence = None
    for item in held_items:
        keywords = EVIDENCE_KEYWORDS.get(item, [])
        for kw in keywords:
            if kw in player_text.lower():
                cited_evidence = item
                break
        if cited_evidence:
            break

    # -----------------------------------------------------------------
    # Episodic Memory: Gate Interaction
    # -----------------------------------------------------------------
    action = "speak_only"
    last_tag = "ai_reply"
    override_dialogue = None

    if context == "gate" and npc_id == "orpheus":
        # Record valid gate answer into session memory
        session_state["gate_answer"] = player_text
        action = "open_gate"
        last_tag = "gate_answered"
        logger.info(f"[EPISODIC MEMORY] Stored gate intent: '{player_text}'")

    # -----------------------------------------------------------------
    # Narrative Context: Voice Beat (Milo Trust Check)
    # -----------------------------------------------------------------
    elif context == "voice_beat" or (npc_id == "milo" and "voice_beat" in context):
        reassure_keywords = ["trap", "fake", "synthesis", "not elara", "stay", "listen", "trick", "vent", "audio", "don't go"]
        panic_keywords = ["go", "run", "follow", "elara is out there", "leave me", "hurry"]

        is_reassurance = any(kw in player_text.lower() for kw in reassure_keywords)
        is_panic = any(kw in player_text.lower() for kw in panic_keywords)

        if is_reassurance:
            session_state["milo_trust"] = min(100, session_state["milo_trust"] + 15)
            action = "stay"
            last_tag = "milo_stay"
            override_dialogue = "You're right... that isn't Elara. It's an acoustic synthesis trap. I'm staying beside you, Cael."
        elif is_panic:
            session_state["milo_trust"] = max(0, session_state["milo_trust"] - 25)
            last_tag = "milo_doubt"

        if session_state["milo_trust"] < TUNING["milo_runs_below"]:
            session_state["milo_fled"] = True
            action = "follow_voice"
            last_tag = "milo_fled"
            override_dialogue = "I have to find her! I can't just leave Elara out there in the fog!"
            logger.warning("[NARRATIVE] Milo's trust collapsed. Milo fled into the fog.")

    # -----------------------------------------------------------------
    # Persuasion & Evidence Scoring on Dr. Silas Vance
    # -----------------------------------------------------------------
    elif npc_id == "vance":
        if cited_evidence:
            if cited_evidence not in session_state["evidence_reacted"]:
                # First time this held evidence is revealed: apply state penalty
                session_state["evidence_reacted"].append(cited_evidence)
                drop = TUNING.get("evidence_resolve_drop", 35)
                session_state["vance_resolve"] = max(0, session_state["vance_resolve"] - drop)
                last_tag = "evidence_reaction"
                override_dialogue = EVIDENCE_REACTION.get(cited_evidence)
                logger.info(
                    f"[EVIDENCE PRESENTED] {cited_evidence} cited. "
                    f"Resolve decreased by {drop} -> {session_state['vance_resolve']}"
                )
            else:
                # Evidence already reacted to: do not re-apply penalty
                last_tag = "repeated_evidence"
                logger.info(f"[EVIDENCE REPEATED] {cited_evidence} already evaluated. No additional penalty.")
        else:
            # Good argument without new evidence gives small persuasion nudge if above threshold
            argument_keywords = ["liora", "grief", "accident", "stop", "let us go", "daughter", "memorial", "truth"]
            if any(kw in player_text.lower() for kw in argument_keywords):
                arg_drop = TUNING.get("argument_resolve_drop", 5)
                # Keep resolve above win_resolve unless required evidence was presented
                min_bound = TUNING["win_resolve"] + 1 if len(session_state["evidence_seen"]) < TUNING["win_min_evidence"] else 0
                session_state["vance_resolve"] = max(min_bound, session_state["vance_resolve"] - arg_drop)
                logger.info(f"[ARGUMENT] Vance resolve nudged down to {session_state['vance_resolve']}")

    # -----------------------------------------------------------------
    # Deterministic Action & Ending Resolver
    # -----------------------------------------------------------------
    if context == "core":
        session_state["core_turn_count"] += 1

        # Check Defeat / Seize Control condition
        defeat_keywords = ["seize control", "i surrender", "you win", "shut down cael", "take over"]
        if any(kw in player_text.lower() for kw in defeat_keywords):
            session_state["ending"] = "B"
            action = "seize_control"
            logger.info("[ENDING TRIGGERED] Ending B: Vance/ORPHEUS seizes control.")

        # Check Victory / Overload condition
        elif (
            session_state["vance_resolve"] <= TUNING["win_resolve"]
            and len(session_state["evidence_seen"]) >= TUNING["win_min_evidence"]
        ):
            action = "destroy_core"
            if session_state.get("milo_fled", False):
                session_state["ending"] = "A2"
                logger.info("[ENDING TRIGGERED] Ending A2: Core destroyed, but Milo was lost in the fog.")
            else:
                session_state["ending"] = "A"
                logger.info("[ENDING TRIGGERED] Ending A: Core destroyed, Vance yields, Milo survives.")

    elif context == "archive":
        # In the archive, inspecting/holding evidence unlocks the vault door
        if len(session_state["evidence_seen"]) > 0:
            action = "reveal_vault"

    # -----------------------------------------------------------------
    # Core Interruption Engine (Episodic Memory & Companion Pleading)
    # -----------------------------------------------------------------
    interrupt_speaker = ""
    interrupt_line = ""
    interrupt_seconds = 0.0

    if context == "core":
        # Turn 1 or on evidence citation: ORPHEUS quotes the gate answer back
        if session_state["core_turn_count"] == 1 or last_tag == "evidence_reaction":
            interrupt_speaker = "orpheus"
            if session_state["gate_answer"]:
                interrupt_line = (
                    f"Telemetry playback: At Sector 4 perimeter, intruder stated intent: '{session_state['gate_answer']}'. "
                    f"Yet archival logs confirm all external hope ceased forty days ago."
                )
            else:
                interrupt_line = (
                    "Telemetry playback: Perimeter intrusion unverified. No stated intent logged at gate."
                )
            interrupt_seconds = calculate_duration(interrupt_line, is_player=False)
        
        # Subsequent turns: Milo pleads (if Milo has not fled)
        elif session_state["core_turn_count"] >= 2 and not session_state.get("milo_fled", False):
            interrupt_speaker = "milo"
            interrupt_line = "Dr. Vance, please! Listen to Cael... look at what you're doing to this place!"
            interrupt_seconds = calculate_duration(interrupt_line, is_player=False)

    # -----------------------------------------------------------------
    # Generate Dialogue Text
    # -----------------------------------------------------------------
    reply_text, ai_ok = generate_ai_dialogue(npc_id, player_text, context, override_line=override_dialogue)
    npc_sec = calculate_duration(reply_text, is_player=False)

    # Append turn to history
    session_state["turn_count"] += 1
    session_state["dialogue_history"].append({
        "npc": npc_id,
        "user": player_text,
        "reply": reply_text,
        "context": context,
    })

    # Trigger audio playback
    voice.speak(npc_id, reply_text)

    return TalkResponse(
        accepted=True,
        player_line=player_text,
        player_seconds=player_sec,
        dialogue=reply_text,
        npc_seconds=npc_sec,
        interrupt_speaker=interrupt_speaker,
        interrupt_line=interrupt_line,
        interrupt_seconds=interrupt_seconds,
        guard="",
        action=action,
        ending=session_state["ending"],
        fog_density=session_state["fog_density"],
        vance_resolve=session_state["vance_resolve"],
        milo_trust=session_state["milo_trust"],
        orpheus_suspicion=session_state["orpheus_suspicion"],
        last_tag=last_tag,
        ai_ok=ai_ok,
    )

@app.post("/event")
def handle_event(req: EventRequest):
    """Handle narrative milestones such as the fake Elara voice beat."""
    logger.info(f"[EVENT] Received game event: {req.event} in context: {req.context}")
    if req.event == "voice_beat":
        elara_line = "Cael? Milo? Can anyone hear me? I'm trapped out past the relay..."
        voice.speak("elara", elara_line)
        return {
            "status": "triggered",
            "action": "play_elara_voice",
            "speaker": "Elara?",
            "dialogue": elara_line,
            "seconds": calculate_duration(elara_line),
        }
    return {
        "status": "unhandled_event",
        "action": "speak_only",
    }

@app.post("/say")
def handle_say(req: SayRequest):
    """Play spoken line through local voice engine."""
    res = voice.speak(req.who, req.text)
    return res

@app.post("/stop_audio")
def handle_stop_audio():
    """Halt any active audio playback."""
    return voice.stop()

@app.post("/reset")
def handle_reset():
    """Reinitialize game state for five consecutive full test runs."""
    global session_state
    session_state = create_initial_state()
    logger.info("[RESET] Session state reset to initial values.")
    return {"status": "reset", "state": session_state}

@app.get("/state")
def get_state():
    """Inspect current in-memory game state and telemetry."""
    return session_state
