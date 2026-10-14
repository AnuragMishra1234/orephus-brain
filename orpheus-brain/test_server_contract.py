"""
ORPHEUS AI Brain - Phase 2.1 Contract Verification Test
Tests FastAPI startup, /docs, /talk contract schema, guard triggers,
character routing, fallback handling, and endpoint integrity.
"""

from fastapi.testclient import TestClient
from server import app

client = TestClient(app)

def test_all():
    print("--- 1. Testing Health & Root Endpoint ---")
    r = client.get("/")
    assert r.status_code == 200, f"Root endpoint failed: {r.status_code}"
    root_data = r.json()
    assert root_data.get("status") == "online"
    print(f"[PASS] GET / -> 200 OK: {root_data}")

    print("\n--- 2. Testing Documentation Endpoint (/docs) ---")
    r = client.get("/docs")
    assert r.status_code == 200, f"/docs failed: {r.status_code}"
    print("[PASS] GET /docs -> 200 OK")

    print("\n--- 3. Testing Empty Input Handling ---")
    r = client.post("/talk", json={"npc_id": "vance", "player_text": ""})
    assert r.status_code == 200
    empty_res = r.json()
    assert empty_res["accepted"] is False, "Empty input must return accepted=False"
    assert empty_res["dialogue"] == "", "Empty input must yield empty dialogue"
    print(f"[PASS] Empty input handled: accepted={empty_res['accepted']}")

    print("\n--- 4. Testing Unknown NPC Handling ---")
    r = client.post("/talk", json={"npc_id": "unknown_ai", "player_text": "Is anyone here?"})
    assert r.status_code == 200
    unknown_res = r.json()
    assert unknown_res["accepted"] is True
    assert unknown_res["ai_ok"] is False
    assert unknown_res["last_tag"] == "unknown_npc"
    print(f"[PASS] Unknown NPC safely intercepted: last_tag={unknown_res['last_tag']}")

    print("\n--- 5. Testing Junk / Guard Handling ---")
    r = client.post("/talk", json={"npc_id": "vance", "player_text": "asdfghjkkkkkkkkkkkkkk"})
    assert r.status_code == 200
    junk_res = r.json()
    assert junk_res["guard"] == "junk", f"Expected guard=junk, got {junk_res['guard']}"
    assert len(junk_res["dialogue"]) > 0
    print(f"[PASS] Junk guard triggered: guard={junk_res['guard']}, reply='{junk_res['dialogue']}'")

    print("\n--- 6. Testing Character Dialogue & Schema Integrity ---")
    required_keys = [
        "accepted", "player_line", "player_seconds", "dialogue", "npc_seconds",
        "interrupt_speaker", "interrupt_line", "interrupt_seconds", "guard",
        "action", "ending", "fog_density", "vance_resolve", "milo_trust",
        "orpheus_suspicion", "last_tag", "ai_ok"
    ]

    for npc in ["vance", "orpheus", "milo"]:
        r = client.post("/talk", json={
            "npc_id": npc,
            "player_text": "What is this facility?",
            "context": "core"
        })
        assert r.status_code == 200
        turn_data = r.json()
        
        # Verify complete schema matches PDF
        for k in required_keys:
            assert k in turn_data, f"Missing required key '{k}' in response for {npc}"

        assert turn_data["accepted"] is True
        assert len(turn_data["dialogue"]) > 0
        assert turn_data["player_seconds"] > 0
        assert turn_data["npc_seconds"] > 0
        assert turn_data["action"] == "speak_only"
        print(f"[PASS] Character turn for '{npc}': reply='{turn_data['dialogue'][:45]}...' | ai_ok={turn_data['ai_ok']}")

    print("\n--- 7. Testing Game State & Reset Endpoints ---")
    r = client.get("/state")
    assert r.status_code == 200
    state_data = r.json()
    assert "vance_resolve" in state_data
    assert "milo_trust" in state_data
    assert "orpheus_suspicion" in state_data
    print(f"[PASS] GET /state -> 200 OK: vance_resolve={state_data['vance_resolve']}")

    r = client.post("/reset")
    assert r.status_code == 200
    assert r.json()["status"] == "reset"
    print("[PASS] POST /reset -> 200 OK")

    print("\n--- 8. Testing Narrative Events & Audio Endpoints ---")
    r = client.post("/event", json={"event": "voice_beat", "context": "voice_beat"})
    assert r.status_code == 200
    event_data = r.json()
    assert event_data["action"] == "play_elara_voice"
    print(f"[PASS] POST /event -> 200 OK: action={event_data['action']}")

    r = client.post("/say", json={"who": "vance", "text": "Where is Elara?"})
    assert r.status_code == 200
    print(f"[PASS] POST /say -> 200 OK: {r.json()}")

    r = client.post("/stop_audio")
    assert r.status_code == 200
    print(f"[PASS] POST /stop_audio -> 200 OK: {r.json()}")

    print("\n====================================================")
    print(" ALL PHASE 2.1 CONTRACT & INTEGRATION TESTS PASSED! ")
    print("====================================================")

if __name__ == "__main__":
    test_all()
