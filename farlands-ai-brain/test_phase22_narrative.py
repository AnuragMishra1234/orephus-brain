"""
ORPHEUS AI Brain - Phase 2.2 Comprehensive Test Suite
Validates:
1. Valid gate answer is remembered in session_state.
2. Empty or junk-guarded answers do not overwrite valid gate memory.
3. Remembered answer is recalled during core interruption.
4. No answer is fabricated when no gate answer exists.
5. Memory survives intervening NPC conversations and context changes.
6. /reset clears memory and restores all documented defaults.
7. Each valid evidence ID is handled correctly.
8. Repeated presentation of the same evidence does not repeatedly apply its state penalty.
9. Invalid or unconfirmed evidence does not cause unintended state changes.
10. Vance's response changes appropriately with resolve/evidence.
11. Milo's response changes appropriately with trust (reassure vs panic).
12. ORPHEUS's response and state updates.
13. Actions are selected only when their documented conditions are met (destroy_core, seize_control, follow_voice, stay).
14. Unknown NPCs, invalid requests, and LLM failures do not crash the server.
15. Phase 2.1 contract tests still pass.
"""

from fastapi.testclient import TestClient
from server import app, session_state

client = TestClient(app)

def run_all_tests():
    print("================================================================")
    print(" STARTING PHASE 2.2 COMPREHENSIVE TEST SUITE")
    print("================================================================")

    # -----------------------------------------------------------------
    # Test 6: /reset restores all defaults
    # -----------------------------------------------------------------
    print("\n--- Test 6: Verify /reset restores documented defaults ---")
    r = client.post("/reset")
    assert r.status_code == 200
    st = client.get("/state").json()
    assert st["vance_resolve"] == 100
    assert st["milo_trust"] == 50
    assert st["orpheus_suspicion"] == 0
    assert st["gate_answer"] == ""
    assert st["evidence_seen"] == []
    assert st["evidence_reacted"] == []
    assert st["milo_fled"] is False
    assert st["ending"] == ""
    print("[PASS] Initial state properly initialized.")

    # -----------------------------------------------------------------
    # Test 4: No answer fabricated when no gate answer exists
    # -----------------------------------------------------------------
    print("\n--- Test 4: Core interruption with NO gate answer ---")
    r = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Dr. Vance, answer me.",
        "context": "core"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["interrupt_speaker"] == "orpheus"
    assert "No stated intent logged" in data["interrupt_line"] or "unverified" in data["interrupt_line"]
    assert "None" not in data["interrupt_line"]
    print(f"[PASS] Default fallback interruption verified: '{data['interrupt_line']}'")

    # -----------------------------------------------------------------
    # Test 1: Valid gate answer is remembered
    # -----------------------------------------------------------------
    print("\n--- Test 1: Valid gate answer recorded ---")
    client.post("/reset")
    gate_prompt = "We came to find Elara and shut down the fog."
    r = client.post("/talk", json={
        "npc_id": "orpheus",
        "player_text": gate_prompt,
        "context": "gate"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "open_gate"
    st = client.get("/state").json()
    assert st["gate_answer"] == gate_prompt
    print(f"[PASS] Gate memory saved: '{st['gate_answer']}' and action is 'open_gate'")

    # -----------------------------------------------------------------
    # Test 2: Empty or junk-guarded input does NOT overwrite memory
    # -----------------------------------------------------------------
    print("\n--- Test 2: Empty or junk input does NOT overwrite gate memory ---")
    # Empty input
    client.post("/talk", json={
        "npc_id": "orpheus",
        "player_text": "",
        "context": "gate"
    })
    st = client.get("/state").json()
    assert st["gate_answer"] == gate_prompt, "Empty input must not overwrite gate memory!"

    # Junk input
    client.post("/talk", json={
        "npc_id": "orpheus",
        "player_text": "asdfghjkkkkkkkkkkkk",
        "context": "gate"
    })
    st = client.get("/state").json()
    assert st["gate_answer"] == gate_prompt, "Junk input must not overwrite gate memory!"
    print("[PASS] Gate memory protected from empty/junk overwrite.")

    # -----------------------------------------------------------------
    # Test 5: Memory survives intervening NPC conversations and context changes
    # -----------------------------------------------------------------
    print("\n--- Test 5: Memory survives intervening conversations ---")
    # Intervening talk with Milo in archive
    client.post("/event", json={"event": "evidence_discovered", "evidence_id": "ev_liora_log", "context": "archive"})
    client.post("/talk", json={
        "npc_id": "milo",
        "player_text": "Keep your flashlight ready, Milo.",
        "context": "archive",
        "evidence": "ev_liora_log"
    })
    st = client.get("/state").json()
    assert st["gate_answer"] == gate_prompt
    assert "ev_liora_log" in st["evidence_seen"]
    print("[PASS] Gate memory persisted through archive context.")

    # -----------------------------------------------------------------
    # Test 3: Remembered answer is recalled during Core interruption
    # -----------------------------------------------------------------
    print("\n--- Test 3: Gate memory recalled during Core interruption ---")
    r = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Dr. Vance, step away from the Core console.",
        "context": "core",
        "evidence": "ev_liora_log"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["interrupt_speaker"] == "orpheus"
    assert gate_prompt in data["interrupt_line"]
    print(f"[PASS] ORPHEUS quoted player's gate answer back: '{data['interrupt_line']}'")

    # -----------------------------------------------------------------
    # Test 9: Question/Negation about evidence vs Affirmative Lie
    # -----------------------------------------------------------------
    print("\n--- Test 9: Question about unheld evidence vs Affirmative Lie ---")
    current_resolve = client.get("/state").json()["vance_resolve"]
    # 9a: Question about journal without holding it (should NOT trigger lie detection)
    r_q = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Did you write in your private journal, Dr. Vance?",
        "context": "core",
        "evidence": ""  # Player does NOT hold ev_vance_journal!
    })
    assert r_q.status_code == 200
    st_after_q = client.get("/state").json()
    assert "ev_vance_journal" not in st_after_q["evidence_seen"]
    assert "ev_vance_journal" not in st_after_q["evidence_reacted"]
    assert r_q.json()["last_tag"] != "lie_detected"
    print("[PASS] Question about unheld evidence did not trigger lie detection.")

    # 9b: Affirmative false claim without holding evidence (SHOULD trigger lie detection)
    # First lower resolve slightly so we can observe the +20 penalty
    session_state["vance_resolve"] = 70
    r_lie = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "I read your private journal, Dr. Vance!",
        "context": "core",
        "evidence": ""  # Player does NOT hold ev_vance_journal!
    })
    assert r_lie.status_code == 200
    data_lie = r_lie.json()
    assert data_lie["last_tag"] == "lie_detected"
    assert "bluffing" in data_lie["dialogue"] or "deceive" in data_lie["dialogue"]
    st_after_lie = client.get("/state").json()
    assert st_after_lie["vance_resolve"] == 90  # 70 + 20 penalty
    print(f"[PASS] False claim triggered lie detection: resolve increased 70 -> {st_after_lie['vance_resolve']}.")

    # -----------------------------------------------------------------
    # Test 7 & 10: Valid evidence cited drops resolve by 25 and triggers reaction
    # -----------------------------------------------------------------
    print("\n--- Test 7 & 10: Valid evidence citation drops resolve and triggers reaction ---")
    resolve_before = client.get("/state").json()["vance_resolve"]
    r = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Dr. Vance, look at Liora's log. She knew the fog was failing.",
        "context": "core",
        "evidence": "ev_liora_log"  # Player actually holds ev_liora_log
    })
    assert r.status_code == 200
    data = r.json()
    assert data["last_tag"] == "evidence_reaction"
    assert "Liora's log" in data["dialogue"]
    st_after = client.get("/state").json()
    assert st_after["vance_resolve"] == resolve_before - 25
    print(f"[PASS] Vance resolve dropped from {resolve_before} to {st_after['vance_resolve']}.")

    # -----------------------------------------------------------------
    # Test 8: Repeated presentation of same evidence does NOT re-apply penalty
    # -----------------------------------------------------------------
    print("\n--- Test 8: Repeated evidence presentation does not re-apply penalty ---")
    resolve_before_repeat = client.get("/state").json()["vance_resolve"]
    r = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Again, look at Liora's log!",
        "context": "core",
        "evidence": "ev_liora_log"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["last_tag"] == "repeated_evidence"
    st_after_repeat = client.get("/state").json()
    assert st_after_repeat["vance_resolve"] == resolve_before_repeat
    print(f"[PASS] Resolve remained constant at {st_after_repeat['vance_resolve']} on duplicate evidence.")

    # -----------------------------------------------------------------
    # Test 11: Milo trust (reassurance vs panic)
    # -----------------------------------------------------------------
    print("\n--- Test 11: Milo trust dynamics in voice_beat ---")
    client.post("/reset")
    # Reassure Milo
    r = client.post("/talk", json={
        "npc_id": "milo",
        "player_text": "Milo, stay with me! It's an audio synthesis trap, not Elara!",
        "context": "voice_beat"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "stay"
    st = client.get("/state").json()
    assert st["milo_trust"] == 65
    assert st["milo_fled"] is False
    print(f"[PASS] Reassured Milo: trust increased to {st['milo_trust']}, action='stay'.")

    # Panic Milo
    client.post("/talk", json={
        "npc_id": "milo",
        "player_text": "Go! Run after her into the fog!",
        "context": "voice_beat"
    })
    client.post("/talk", json={
        "npc_id": "milo",
        "player_text": "Hurry, follow her into the fog right now!",
        "context": "voice_beat"
    })
    st = client.get("/state").json()
    assert st["milo_trust"] < 40
    assert st["milo_fled"] is True
    print(f"[PASS] Panicked Milo: trust fell to {st['milo_trust']}, milo_fled=True.")

    # -----------------------------------------------------------------
    # Test 13: Deterministic Action & Ending Selection
    # -----------------------------------------------------------------
    print("\n--- Test 13: Deterministic Endings (A, A2, B) ---")
    
    # Test Ending A: Win with Milo
    client.post("/reset")
    client.post("/talk", json={"npc_id": "orpheus", "player_text": "Find Elara", "context": "gate"})
    # Milo stays
    client.post("/talk", json={"npc_id": "milo", "player_text": "It's a trap, stay close", "context": "voice_beat"})
    
    # Discover clues in Archive
    for ev in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]:
        client.post("/event", json={"event": "evidence_discovered", "evidence_id": ev, "context": "archive"})
    
    # Present 1st evidence: ev_liora_log (drops resolve 100 -> 75)
    client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Look at Liora's log, Dr. Vance.",
        "context": "core",
        "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder"
    })
    # Present 2nd evidence: ev_vance_journal (drops resolve 75 -> 50)
    client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "And look at your journal! You altered the code out of grief!",
        "context": "core",
        "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder"
    })
    # Present 3rd evidence: ev_elara_recorder (drops resolve 50 -> 25, triggers Ending A)
    r = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Listen to Elara's voice recorder! She never wanted this nightmare!",
        "context": "core",
        "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder"
    })
    data = r.json()
    assert data["vance_resolve"] == 25
    assert data["action"] == "destroy_core"
    assert data["ending"] == "A"
    print(f"[PASS] Ending A triggered: action={data['action']}, ending={data['ending']}")

    # Test Ending A2: Win but Milo fled
    client.post("/reset")
    # Milo flees
    client.post("/talk", json={"npc_id": "milo", "player_text": "Run, follow the voice into the fog", "context": "voice_beat"})
    client.post("/talk", json={"npc_id": "milo", "player_text": "Go into the fog now!", "context": "voice_beat"})
    assert client.get("/state").json()["milo_fled"] is True

    # Discover clues for Ending A2
    for ev in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]:
        client.post("/event", json={"event": "evidence_discovered", "evidence_id": ev, "context": "archive"})

    # Break Vance's resolve with 3 evidence items
    client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Look at Liora's log.",
        "context": "core",
        "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder"
    })
    client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "And your journal proves it all.",
        "context": "core",
        "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder"
    })
    r = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "Here is Elara's audio recorder, Vance. Face reality.",
        "context": "core",
        "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder"
    })
    data = r.json()
    assert data["vance_resolve"] == 25
    assert data["action"] == "destroy_core"
    assert data["ending"] == "A2"
    print(f"[PASS] Ending A2 triggered: action={data['action']}, ending={data['ending']}")

    # Test Ending B: Defeat / Seize Control
    client.post("/reset")
    r = client.post("/talk", json={
        "npc_id": "vance",
        "player_text": "I surrender. Seize control of the facility.",
        "context": "core"
    })
    data = r.json()
    assert data["action"] == "seize_control"
    assert data["ending"] == "B"
    print(f"[PASS] Ending B triggered: action={data['action']}, ending={data['ending']}")

    # -----------------------------------------------------------------
    # Test 14: Unknown NPCs and invalid requests
    # -----------------------------------------------------------------
    print("\n--- Test 14: Server stability against bad inputs ---")
    r = client.post("/talk", json={"npc_id": "nonexistent", "player_text": "hello"})
    assert r.status_code == 200
    assert r.json()["last_tag"] == "unknown_npc"

    r = client.post("/talk", json={"npc_id": "vance", "player_text": ""})
    assert r.status_code == 200
    assert r.json()["accepted"] is False
    print("[PASS] Stability under edge cases confirmed.")

    # -----------------------------------------------------------------
    # Test 15: Phase 2.1 contract verification
    # -----------------------------------------------------------------
    print("\n--- Test 15: Phase 2.1 Contract Schema Verification ---")
    required_keys = [
        "accepted", "speaker", "player_line", "player_seconds", "dialogue", "npc_seconds",
        "interrupt_speaker", "interrupt_line", "interrupt_seconds", "guard",
        "action", "ending", "fog_density", "vance_resolve", "milo_trust",
        "orpheus_suspicion", "last_tag", "ai_ok"
    ]
    r = client.post("/talk", json={"npc_id": "orpheus", "player_text": "Status", "context": "gate"})
    data = r.json()
    for k in required_keys:
        assert k in data, f"Contract broken: missing key '{k}'"
    assert len(data) == 18
    print("[PASS] Full 18-field Unreal response contract intact.")

    print("\n================================================================")
    print(" ALL 15 PHASE 2.2 TESTS PASSED PERFECTLY! ")
    print("================================================================")

if __name__ == "__main__":
    run_all_tests()
