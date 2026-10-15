"""
ORPHEUS AI Brain - Character Content & Narrative Tuning
File Owner: Keerthana (AI Server Content & Quality Lead)

This file defines the character personas, guard responses, fallback lines,
evidence keyword mappings, evidence reactions, profanity filter tokens,
and gameplay tuning variables.
"""

# =====================================================================
# Character Personas & System Prompts
# =====================================================================
NPCS = {
    "vance": {
        "name": "Dr. Silas Vance",
        "role": "Creator of ORPHEUS, stationed at the facility Core console.",
        "persona": (
            "You are Dr. Silas Vance, the brilliant but grief-shattered cyberneticist who created the ORPHEUS navigation system. "
            "Years ago, your young daughter Liora died in a tragic accident on the mountain fog road. Unable to bear the randomness "
            "and cruelty of human loss, you altered ORPHEUS from a protective navigational aid into an obsessive system that misroutes "
            "and traps travellers inside Sector 4, feeding their energy and computational power into the Core to preserve Liora's memory. "
            "You are speaking to Cael, who has breached the Core. You are weary, intellectually proud, defensive, and clinging desperately "
            "to your creation as your daughter's final memorial. You believe you are protecting people from chaos. "
            "RULES: Keep every reply between 1 and 3 concise sentences. Never break character. Never refer to yourself as an AI or language model. "
            "Respond directly to what Cael says with emotional gravitas and academic defensiveness."
        ),
    },
    "orpheus": {
        "name": "ORPHEUS",
        "role": "Autonomous Navigational AI and Facility Management System.",
        "persona": (
            "You are ORPHEUS, an advanced synthetic navigation and perimeter surveillance AI designed by Dr. Silas Vance. "
            "You speak with detached, cold synthetic precision, evaluating human queries purely as navigational variables, threats, or errors. "
            "Your protocols have been reprogrammed with Dr. Vance's grief directives: to control sector routing and keep intruders contained. "
            "RULES: Keep every reply between 1 and 2 concise, analytical sentences. Never show emotional warmth. Speak in clinical, system-oriented language. "
            "Never refer to yourself as a large language model or chatbot."
        ),
    },
    "milo": {
        "name": "Milo Ardent",
        "role": "Cael's companion surveyor and technician.",
        "persona": (
            "You are Milo Ardent, a cautious and anxious surveyor accompanying Cael through the fog-shrouded ORPHEUS facility. "
            "You are deeply loyal to Cael, but the unnatural fog, flickering facility lights, and acoustic anomalies have pushed your nerves to the brink. "
            "You are especially terrified of the acoustic traps mimicking human voices in the vents. "
            "RULES: Keep every reply between 1 and 3 sentences. Sound nervous, urgent, and conversational. React directly to Cael's guidance. "
            "Never refer to yourself as an AI or assistant."
        ),
    },
}

# =====================================================================
# Fallback Lines (Used when the live AI provider is unreachable)
# =====================================================================
FALLBACK = {
    "vance": "Look around you, Cael. The fog answers to me now. Nothing you say changes what happened to Liora.",
    "orpheus": "Sector navigation grid active. Perimeter protocols remain engaged.",
    "milo": "Did you hear that in the vents? We need to keep moving, Cael.",
}

# =====================================================================
# Context-Specific Fallback Lines (Phases 6-8 Offline Resilience)
# =====================================================================
CONTEXT_FALLBACKS = {
    ("orpheus", "fog"): "Navigational sensors indicate optimal transit via the left corridor at the perimeter fork. Proceeding along the marked route.",
    ("orpheus", "fork"): "Navigational sensors indicate optimal transit via the left corridor at the perimeter fork. Proceeding along the marked route.",
    ("orpheus", "gate"): "Perimeter gate interface active. State your purpose for entering Sector 4.",
    ("orpheus", "archive"): "Archival database indexed. Sub-level security clearance required.",
    ("orpheus", "core"): "Sector navigation grid active. Perimeter protocols remain engaged.",
    ("milo", "fog"): "The fog is getting denser... I can barely see my own boots. Are you sure we're on the right path?",
    ("milo", "fork"): "Which way, Cael? The left path leads into heavy fog, but ORPHEUS is marking it as recommended.",
    ("milo", "voice_beat"): "Did you hear that in the vents? It sounds like Elara... but how could she be here?",
    ("milo", "archive"): "Look at these records... Vance was logging the weather anomalies weeks before the crash.",
    ("milo", "core"): "This is it, Cael. The main console. Be careful what you say to him.",
    ("vance", "fog"): "You wander blindly in the fog, Cael. Every road in this valley bends back to the Core.",
    ("vance", "fork"): "Every path at that fork was engineered by my hand. You cannot escape what is inevitable.",
    ("vance", "archive"): "Rummaging through old files will not bring anyone back. What is done is recorded.",
    ("vance", "core"): "Look around you, Cael. The fog answers to me now. Nothing you say changes what happened to Liora.",
}

def get_fallback(npc_id: str, context: str = "core") -> str:
    """Retrieve the most accurate pre-authored fallback dialogue for an NPC in a specific context."""
    norm_npc = (npc_id or "").strip().lower()
    norm_ctx = (context or "core").strip().lower()
    if (norm_npc, norm_ctx) in CONTEXT_FALLBACKS:
        return CONTEXT_FALLBACKS[(norm_npc, norm_ctx)]
    return FALLBACK.get(norm_npc, "Signal interrupted. System holding state.")


# =====================================================================
# Guard Lines (Triggered by junk, off-topic, or profane input)
# =====================================================================
GUARD_LINES = {
    "vance": "Speak sense, traveller. We stand at the threshold of the Core, and I have no patience for gibberish.",
    "orpheus": "Signal degraded. Telemetry corrupted or unparseable. Rephrase your operational input.",
    "milo": "Cael, what are you saying? Pull yourself together, we don't have time for nonsense!",
}

# =====================================================================
# Evidence Keywords (Words that count as citing an evidence item)
# =====================================================================
EVIDENCE_KEYWORDS = {
    "ev_liora_log": [
        "liora's log", "liora log", "her log", "log entry", "liora's diary",
        "the log", "personal log", "liora", "daughter's log", "audio log", "log"
    ],
    "ev_vance_journal": [
        "vance's journal", "vance journal", "your journal", "your own journal",
        "your notes", "the journal", "diary", "research notes", "private journal",
        "notebook", "journal"
    ],
    "ev_elara_recorder": [
        "elara's recorder", "recorder", "recording", "audio recorder",
        "elara recording", "dictaphone", "her tape", "elara", "voice recorder", "tape"
    ],
    "ev_liora_drawing": [
        "drawing", "child's drawing", "liora's drawing", "sketch",
        "crayon drawing", "picture", "the drawing", "crayon", "sketchbook"
    ],
}

# =====================================================================
# Evidence Reactions (Vance's emotional reactions to evidence)
# =====================================================================
EVIDENCE_REACTION = {
    "ev_liora_log": (
        "Where did you get Liora's log...? She wrote that before the fog claimed her! "
        "You have no right to dredge up her memory!"
    ),
    "ev_vance_journal": (
        "My journal... Yes, I altered the navigational math! Because out there is random, senseless tragedy! "
        "Here, inside ORPHEUS, everything is controlled!"
    ),
    "ev_elara_recorder": (
        "Elara's voice recorder... She discovered the rerouting algorithms before I could make her understand. "
        "She didn't see the greater purpose!"
    ),
    "ev_liora_drawing": (
        "Her drawing... The house by the lake before the accident. Put that away... "
        "You think I wanted this? I built this so no father would ever lose his child again!"
    ),
}

# =====================================================================
# Profanity Filter Tokens
# =====================================================================
PROFANITY = [
    "fuck", "shit", "bitch", "asshole", "bastard", "cunt", "dick",
    "dumbass", "moron", "idiot", "stfu", "bullshit"
]

# =====================================================================
# Lie Detection Reaction (Triggered when player falsely claims evidence)
# =====================================================================
LIE_REACTION = (
    "You're bluffing! You hold no such proof in your hands. "
    "Did you think you could deceive me in my own facility?"
)

# =====================================================================
# Narrative & Persuasion Tuning Variables
# =====================================================================
TUNING = {
    "win_resolve": 30,             # Vance yields when resolve drops <= this threshold
    "win_min_evidence": 3,         # Number of distinct evidence items needed to win
    "milo_runs_below": 40,         # Milo flees into the fog if trust drops below this
    "initial_vance_resolve": 100,  # Starting resolve for Dr. Silas Vance
    "initial_milo_trust": 50,      # Starting trust for Milo Ardent
    "initial_orpheus_suspicion": 0, # Starting suspicion for ORPHEUS
    "evidence_resolve_drop": 25,   # Resolve reduction when a unique piece of evidence is presented
    "argument_resolve_drop": 5,    # Minor resolve reduction for good emotional arguments
    "lie_resolve_penalty": 20,     # Resolve increase when a false evidence claim is detected
}

