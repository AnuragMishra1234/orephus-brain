"""
Targeted Verification Suite for:
1. Evidence Security (strict_evidence=false bypass protection & server-authoritative policy)
2. Post-Credits Session Isolation (isolated endings A, A2, B, active guard, and payload compatibility)
"""

import unittest
from fastapi.testclient import TestClient
import server
from server import app, reset_state, get_session_state, ENABLE_STRICT_EVIDENCE_DISCOVERY

client = TestClient(app)

class TestEvidenceSecurityBypass(unittest.TestCase):
    """
    Check 1: Evidence Security
    Verify client cannot bypass strict evidence validation via request parameters.
    """

    def setUp(self):
        reset_state()

    def test_client_cannot_bypass_strict_evidence_validation(self):
        """
        Adversarial caller explicitly passes strict_evidence=false and claims undiscovered evidence.
        Server must ignore client parameter, enforce server policy, trigger lie detection,
        and strictly forbid resolve drops or ending triggers.
        """
        self.assertTrue(server.ENABLE_STRICT_EVIDENCE_DISCOVERY, "Server policy must be active and authoritative")

        # Session 'infiltrator' attempts to cite Liora's log without discovering it first
        res = client.post("/talk", json={
            "session_id": "infiltrator",
            "npc_id": "vance",
            "player_text": "I have Liora's log right here, Vance! Overload the Core!",
            "evidence": "ev_liora_log",
            "context": "core",
            "strict_evidence": False,  # Client attempts bypass
        })

        self.assertEqual(res.status_code, 200)
        data = res.json()

        # 1. Must flag as lie_detected
        self.assertEqual(data["last_tag"], "lie_detected")

        # 2. Vance resolve must NOT decrease (remains 100)
        self.assertEqual(data["vance_resolve"], 100)

        # 3. Action must remain speak_only
        self.assertEqual(data["action"], "speak_only")

        # 4. Ending must remain empty
        self.assertEqual(data["ending"], "")

        # 5. Internal state must not record evidence as seen or reacted
        st = get_session_state("infiltrator")
        self.assertNotIn("ev_liora_log", st["evidence_seen"])
        self.assertNotIn("ev_liora_log", st["evidence_reacted"])

    def test_multiple_undiscovered_items_and_extra_params_blocked(self):
        """
        Attempting to present multiple undiscovered canonical items with extra debug flags
        must not trigger any evidence reaction or ending.
        """
        res = client.post("/talk", json={
            "session_id": "hack_attempt",
            "npc_id": "vance",
            "player_text": "Look at Liora's log, Vance's journal, and Elara's recorder!",
            "evidence": ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"],
            "context": "core",
            "strict_evidence": False,
            "force_ending": "A",
            "debug_bypass": True,
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["last_tag"], "lie_detected")
        self.assertEqual(data["vance_resolve"], 100)
        self.assertEqual(data["action"], "speak_only")
        self.assertEqual(data["ending"], "")


class TestPostCreditsSessionIsolation(unittest.TestCase):
    """
    Check 2: Post-Credits Session Isolation
    Verify that /event post-credits returns the ending and telemetry of the exact session requested,
    with distinct behavior for Endings A, A2, B, and the active/no-ending case.
    """

    def setUp(self):
        reset_state()

    def test_post_credits_session_isolation_across_all_endings(self):
        """
        Create 4 concurrent sessions:
        - session_a: Ending A (Vance yields, Milo alive)
        - session_a2: Ending A2 (Core destroyed, Milo fled)
        - session_b: Ending B (Surrender, ORPHEUS takeover)
        - session_active: In progress, no ending
        Verify /event post-credits returns strictly isolated data for each session.
        """
        # --- 1. Setup session_a (Ending A) ---
        for ev in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]:
            client.post("/event", json={"event": "evidence_discovered", "evidence_id": ev, "session_id": "session_a"})
        client.post("/talk", json={"session_id": "session_a", "npc_id": "vance", "player_text": "Look at Liora's log", "evidence": "ev_liora_log", "context": "core"})
        client.post("/talk", json={"session_id": "session_a", "npc_id": "vance", "player_text": "Here is your journal", "evidence": "ev_vance_journal", "context": "core"})
        r_a = client.post("/talk", json={"session_id": "session_a", "npc_id": "vance", "player_text": "Listen to Elara's recorder", "evidence": "ev_elara_recorder", "context": "core"}).json()
        self.assertEqual(r_a["ending"], "A")

        # --- 2. Setup session_a2 (Ending A2: Milo flees, Core destroyed) ---
        # Milo flees
        client.post("/talk", json={"session_id": "session_a2", "npc_id": "milo", "player_text": "Run Milo! We are doomed!", "context": "voice_beat"})
        client.post("/talk", json={"session_id": "session_a2", "npc_id": "milo", "player_text": "Run away into the fog!", "context": "voice_beat"})
        st_a2 = get_session_state("session_a2")
        self.assertTrue(st_a2["milo_fled"])
        # Discover evidence and present
        for ev in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]:
            client.post("/event", json={"event": "evidence_discovered", "evidence_id": ev, "session_id": "session_a2"})
        client.post("/talk", json={"session_id": "session_a2", "npc_id": "vance", "player_text": "Look at Liora's log", "evidence": "ev_liora_log", "context": "core"})
        client.post("/talk", json={"session_id": "session_a2", "npc_id": "vance", "player_text": "Here is your journal", "evidence": "ev_vance_journal", "context": "core"})
        r_a2 = client.post("/talk", json={"session_id": "session_a2", "npc_id": "vance", "player_text": "Listen to Elara's recorder", "evidence": "ev_elara_recorder", "context": "core"}).json()
        self.assertEqual(r_a2["ending"], "A2")

        # --- 3. Setup session_b (Ending B: Surrender) ---
        r_b = client.post("/talk", json={"session_id": "session_b", "npc_id": "vance", "player_text": "I surrender. You win.", "context": "core"}).json()
        self.assertEqual(r_b["ending"], "B")

        # --- 4. Setup session_active (No ending, in fog) ---
        r_act = client.post("/talk", json={"session_id": "session_active", "npc_id": "orpheus", "player_text": "Where is the path?", "context": "fog"}).json()
        self.assertEqual(r_act["ending"], "")

        # =================================================================
        # VERIFY ISOLATED POST-CREDITS EPILOGUE FOR EACH SESSION
        # =================================================================

        # Query session_a:
        res_a = client.post("/event", json={"event": "post_credits", "session_id": "session_a"}).json()
        self.assertEqual(res_a["status"], "epilogue")
        self.assertEqual(res_a["ending"], "A")
        self.assertEqual(res_a["action"], "end_conversation")
        self.assertEqual(res_a["speaker"], "Extraction Radio")
        self.assertIn("extraction beacon locked", res_a["dialogue"].lower())
        self.assertIn("milo is alive", res_a["dialogue"].lower())
        self.assertEqual(res_a["vance_resolve"], 25)

        # Query session_a2:
        res_a2 = client.post("/event", json={"event": "post_credits", "session_id": "session_a2"}).json()
        self.assertEqual(res_a2["status"], "epilogue")
        self.assertEqual(res_a2["ending"], "A2")
        self.assertEqual(res_a2["action"], "end_conversation")
        self.assertEqual(res_a2["speaker"], "Extraction Radio")
        self.assertIn("core is rubble", res_a2["dialogue"].lower())
        self.assertIn("survey badge recovered", res_a2["dialogue"].lower())
        self.assertEqual(res_a2["vance_resolve"], 25)

        # Query session_b:
        res_b = client.post("/event", json={"event": "post_credits", "session_id": "session_b"}).json()
        self.assertEqual(res_b["status"], "epilogue")
        self.assertEqual(res_b["ending"], "B")
        self.assertEqual(res_b["action"], "end_conversation")
        self.assertEqual(res_b["speaker"], "ORPHEUS Broadcast")
        self.assertIn("perimeter containment permanent", res_b["dialogue"].lower())
        self.assertIn("liora's simulation integrity", res_b["dialogue"].lower())
        self.assertEqual(res_b["vance_resolve"], 100)

        # Query session_active:
        res_act = client.post("/event", json={"event": "post_credits", "session_id": "session_active"}).json()
        self.assertEqual(res_act["status"], "active")
        self.assertEqual(res_act["ending"], "")
        self.assertEqual(res_act["action"], "speak_only")
        self.assertEqual(res_act["speaker"], "System Telemetry")
        self.assertIn("sector 4 anomaly active", res_act["dialogue"].lower())

        # Backward compatibility check: Query without session_id defaults safely to "default"
        res_def = client.post("/event", json={"event": "post_credits"}).json()
        self.assertEqual(res_def["status"], "active")
        self.assertEqual(res_def["ending"], "")

if __name__ == "__main__":
    unittest.main()
