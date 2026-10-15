"""
ORPHEUS AI Brain - Phase 4: Reliable Character Memory Test Suite
Author: Senior AI Backend Engineer
Project: FARLANDS 2026 - Innovation Mission 5 (Cognitive NPCs)

Comprehensive test coverage for Phase 4 Memory Specifications:
1. test_gate_answer_stored: Gate answer correctly recorded in session_state['gate_answer'] and session_state['memories']['gate_intent'].
2. test_gate_answer_persists_across_contexts: Gate memory persists across gate -> archive -> voice_beat -> core transitions.
3. test_direct_gate_recall_query: Directly querying ORPHEUS returns verbatim gate answer and last_tag='gate_recalled'.
4. test_direct_gate_recall_empty: Asking ORPHEUS before gate answer is recorded returns canonical unrecorded message and last_tag='gate_not_recorded'.
5. test_core_interruption_uses_memory: Core turn 1 interruption accurately quotes the recorded gate answer.
6. test_core_interruption_empty_memory: Core turn 1 interruption without gate answer uses unverified telemetry line.
7. test_structured_memories_populated: Decisions (Milo reassurance/panic, Vance stance, explicit promises) are recorded in session_state['memories'].
8. test_recall_does_not_modify_state: Direct memory queries do not alter resolve, evidence lists, or trigger endings.
9. test_reset_clears_all_memories: POST /reset reinitializes all memory structures and gate_answer.
10. test_state_endpoint_exposes_memories: GET /state exposes structured memories dictionary alongside existing telemetry fields.
"""

import unittest
from unittest.mock import patch
from starlette.testclient import TestClient

import server
from server import app, session_state, reset_state

client = TestClient(app)

REQUIRED_SCHEMA_FIELDS = {
    "accepted", "speaker", "player_line", "player_seconds", "dialogue",
    "npc_seconds", "interrupt_speaker", "interrupt_line", "interrupt_seconds",
    "guard", "action", "ending", "fog_density", "vance_resolve", "milo_trust",
    "orpheus_suspicion", "last_tag", "ai_ok",
}


class TestPhase4Memory(unittest.TestCase):

    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()

    # -------------------------------------------------------------------------
    # 1. Gate Answer Stored
    # -------------------------------------------------------------------------
    def test_gate_answer_stored(self):
        """Gate answer is accurately stored in both top-level gate_answer and memories['gate_intent']."""
        gate_statement = "We came to shut down the resonance and protect the research staff."
        resp = client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": gate_statement,
            "context": "gate"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["action"], "open_gate")
        self.assertEqual(data["last_tag"], "gate_answered")
        self.assertEqual(session_state["gate_answer"], gate_statement)
        self.assertEqual(session_state["memories"]["gate_intent"], gate_statement)

    # -------------------------------------------------------------------------
    # 2. Cross-Context Continuity
    # -------------------------------------------------------------------------
    def test_gate_answer_persists_across_contexts(self):
        """Memory survives context transitions: gate -> archive -> voice_beat -> core."""
        gate_statement = "Investigating unauthorized signals on behalf of the perimeter team."
        
        # 1. Gate beat
        client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": gate_statement,
            "context": "gate"
        })
        self.assertEqual(session_state["gate_answer"], gate_statement)

        # 2. Archive beat (authoritative discovery in Archive)
        client.post("/event", json={"event": "evidence_discovered", "evidence_id": "ev_liora_log", "context": "archive"})
        client.post("/talk", json={
            "npc_id": "milo",
            "player_text": "Keep your eyes open, Milo.",
            "evidence": "ev_liora_log",
            "context": "archive"
        })
        self.assertEqual(session_state["gate_answer"], gate_statement)
        self.assertEqual(session_state["memories"]["gate_intent"], gate_statement)

        # 3. Voice beat
        client.post("/talk", json={
            "npc_id": "milo",
            "player_text": "Milo, listen to me, that's an acoustic synthesis trap!",
            "context": "voice_beat"
        })
        self.assertEqual(session_state["gate_answer"], gate_statement)
        self.assertEqual(session_state["memories"]["gate_intent"], gate_statement)

        # 4. Core beat
        client.post("/talk", json={
            "npc_id": "vance",
            "player_text": "Dr. Vance, step away from the primary controls.",
            "context": "core"
        })
        self.assertEqual(session_state["gate_answer"], gate_statement)
        self.assertEqual(session_state["memories"]["gate_intent"], gate_statement)

    # -------------------------------------------------------------------------
    # 3. Direct Gate Recall Query
    # -------------------------------------------------------------------------
    def test_direct_gate_recall_query(self):
        """Direct queries to ORPHEUS recall the exact recorded gate answer with tag 'gate_recalled'."""
        gate_statement = "To secure the facility and prevent catastrophic core collapse."
        client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": gate_statement,
            "context": "gate"
        })

        test_queries = [
            "What did I say at the gate?",
            "Do you remember why I came here?",
            "What was my stated intent?",
            "ORPHEUS, what was our stated intent at the gate?",
            "Recall my gate answer",
        ]

        expected_prefix = f"Archival telemetry record: At the Sector 4 perimeter, your stated intent was: '{gate_statement}'."

        for query in test_queries:
            resp = client.post("/talk", json={
                "npc_id": "orpheus",
                "player_text": query,
                "context": "archive"
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(REQUIRED_SCHEMA_FIELDS.issubset(data.keys()))
            self.assertEqual(data["last_tag"], "gate_recalled")
            self.assertEqual(data["action"], "speak_only")
            self.assertIn(expected_prefix, data["dialogue"])
            self.assertIn("Directive remains logged in active memory.", data["dialogue"])

    # -------------------------------------------------------------------------
    # 4. Direct Gate Recall When Empty
    # -------------------------------------------------------------------------
    def test_direct_gate_recall_empty(self):
        """Direct query to ORPHEUS before gate statement returns canonical unrecorded message."""
        self.assertEqual(session_state["gate_answer"], "")
        
        resp = client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": "What was my stated intent at the gate?",
            "context": "archive"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(REQUIRED_SCHEMA_FIELDS.issubset(data.keys()))
        self.assertEqual(data["last_tag"], "gate_not_recorded")
        self.assertEqual(data["action"], "speak_only")
        self.assertEqual(
            data["dialogue"],
            "Telemetry error: No stated intent was logged at the perimeter gate. Memory record empty."
        )

    # -------------------------------------------------------------------------
    # 5. Core Interruption Uses Memory
    # -------------------------------------------------------------------------
    def test_core_interruption_uses_memory(self):
        """Core turn 1 interruption correctly quotes the gate answer."""
        gate_statement = "We came to find Elara and shut down this anomaly."
        client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": gate_statement,
            "context": "gate"
        })

        resp = client.post("/talk", json={
            "npc_id": "vance",
            "player_text": "Dr. Vance, answer me.",
            "context": "core"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["interrupt_speaker"], "orpheus")
        self.assertIn(f"stated intent: '{gate_statement}'", data["interrupt_line"])
        self.assertIn("Yet archival logs confirm all external hope ceased forty days ago.", data["interrupt_line"])

    # -------------------------------------------------------------------------
    # 6. Core Interruption With Empty Memory
    # -------------------------------------------------------------------------
    def test_core_interruption_empty_memory(self):
        """Core turn 1 interruption without gate answer uses unverified telemetry fallback."""
        self.assertEqual(session_state["gate_answer"], "")

        resp = client.post("/talk", json={
            "npc_id": "vance",
            "player_text": "Dr. Silas Vance, step away from the machine.",
            "context": "core"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["interrupt_speaker"], "orpheus")
        self.assertEqual(
            data["interrupt_line"],
            "Telemetry playback: Perimeter intrusion unverified. No stated intent logged at gate."
        )

    # -------------------------------------------------------------------------
    # 7. Structured Memories Populated (Decisions & Promises)
    # -------------------------------------------------------------------------
    def test_structured_memories_populated(self):
        """Decisions across Milo, Vance, and explicit promises are tracked in session_state['memories']."""
        # Milo reassurance
        client.post("/talk", json={
            "npc_id": "milo",
            "player_text": "Milo, stay with me! It's an audio synthesis trap, not Elara!",
            "context": "voice_beat"
        })
        self.assertEqual(session_state["memories"]["milo_decision"], "reassured")

        # Explicit promise tracking
        promise_phrase = "I promise I will protect you and get us out of here alive."
        client.post("/talk", json={
            "npc_id": "milo",
            "player_text": promise_phrase,
            "context": "voice_beat"
        })
        self.assertIn(promise_phrase, session_state["memories"]["promises"])

        # Vance confrontational stance
        client.post("/event", json={"event": "evidence_discovered", "evidence_id": "ev_liora_log", "context": "archive"})
        client.post("/talk", json={
            "npc_id": "vance",
            "player_text": "Look at Liora's log! We are shutting down this facility!",
            "evidence": "ev_liora_log",
            "context": "core"
        })
        self.assertEqual(session_state["memories"]["vance_stance"], "confrontational")

        # Vance sympathetic stance
        client.post("/talk", json={
            "npc_id": "vance",
            "player_text": "Dr. Vance, I understand your grief over Liora, but this machine must stop.",
            "context": "core"
        })
        self.assertEqual(session_state["memories"]["vance_stance"], "sympathetic")

    # -------------------------------------------------------------------------
    # 8. Memory Recall Does Not Modify State or Trigger Endings
    # -------------------------------------------------------------------------
    def test_recall_does_not_modify_state(self):
        """Querying memory must never alter resolve, change evidence list, or trigger endings."""
        # Establish base state
        session_state["gate_answer"] = "Peaceful investigation"
        session_state["memories"]["gate_intent"] = "Peaceful investigation"
        session_state["vance_resolve"] = 25  # Below win_resolve (30)
        session_state["evidence_seen"] = ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]
        session_state["evidence_reacted"] = ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]
        session_state["ending"] = ""

        # Directly ask ORPHEUS in core context
        resp = client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": "What was my stated intent at the gate?",
            "context": "core"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Verify no state corruption or unintended ending trigger
        self.assertEqual(data["action"], "speak_only")
        self.assertEqual(data["ending"], "")
        self.assertEqual(data["vance_resolve"], 25)
        self.assertEqual(data["last_tag"], "gate_recalled")
        self.assertEqual(session_state["vance_resolve"], 25)
        self.assertEqual(session_state["ending"], "")
        self.assertEqual(len(session_state["evidence_reacted"]), 3)

    # -------------------------------------------------------------------------
    # 9. Reset Clears All Memories
    # -------------------------------------------------------------------------
    def test_reset_clears_all_memories(self):
        """POST /reset resets session_state['memories'] and top-level gate_answer."""
        session_state["gate_answer"] = "Explore the deep sector"
        session_state["memories"]["gate_intent"] = "Explore the deep sector"
        session_state["memories"]["milo_decision"] = "reassured"
        session_state["memories"]["vance_stance"] = "confrontational"
        session_state["memories"]["promises"] = ["I will save you"]

        resp = client.post("/reset")
        self.assertEqual(resp.status_code, 200)
        
        self.assertEqual(session_state["gate_answer"], "")
        self.assertEqual(session_state["memories"]["gate_intent"], "")
        self.assertEqual(session_state["memories"]["milo_decision"], "")
        self.assertEqual(session_state["memories"]["vance_stance"], "")
        self.assertEqual(session_state["memories"]["promises"], [])

    # -------------------------------------------------------------------------
    # 10. State Endpoint Exposes Memories Dictionary
    # -------------------------------------------------------------------------
    def test_state_endpoint_exposes_memories(self):
        """GET /state returns structured memories dictionary alongside existing fields."""
        session_state["gate_answer"] = "Rescue team deployment"
        session_state["memories"]["gate_intent"] = "Rescue team deployment"
        session_state["memories"]["milo_decision"] = "reassured"
        session_state["memories"]["vance_stance"] = "sympathetic"
        session_state["memories"]["promises"] = ["I promise to return"]

        resp = client.get("/state")
        self.assertEqual(resp.status_code, 200)
        state_data = resp.json()

        self.assertIn("memories", state_data)
        self.assertEqual(state_data["memories"]["gate_intent"], "Rescue team deployment")
        self.assertEqual(state_data["memories"]["milo_decision"], "reassured")
        self.assertEqual(state_data["memories"]["vance_stance"], "sympathetic")
        self.assertEqual(state_data["memories"]["promises"], ["I promise to return"])
        self.assertEqual(state_data["gate_answer"], "Rescue team deployment")

    # -------------------------------------------------------------------------
    # 11. Persistence Across Python Server Restart
    # -------------------------------------------------------------------------
    def test_persistence_across_server_restart(self):
        """Memories serialized to disk survive complete Python process restart simulation."""
        import tempfile
        from pathlib import Path
        from server import save_state_to_disk, load_state_from_disk

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_state_path = Path(tmp_dir) / "test_session_state.json"

            # 1. Player provides gate statement
            gate_phrase = "Deploying search beacon for lost survey team."
            client.post("/talk", json={
                "npc_id": "orpheus",
                "player_text": gate_phrase,
                "context": "gate"
            })
            self.assertEqual(session_state["gate_answer"], gate_phrase)

            # 2. Serialize memory to disk
            saved = save_state_to_disk(filepath=temp_state_path)
            self.assertTrue(saved)
            self.assertTrue(temp_state_path.exists())

            # 3. Simulate total server shutdown & memory wipe
            session_state.clear()
            self.assertEqual(session_state.get("gate_answer"), None)

            # 4. Simulate server restart: restore state from disk
            loaded = load_state_from_disk(filepath=temp_state_path)
            self.assertTrue(loaded)
            self.assertEqual(session_state["gate_answer"], gate_phrase)
            self.assertEqual(session_state["memories"]["gate_intent"], gate_phrase)

            # 5. Direct recall query proves ORPHEUS recalls memory after restart
            resp = client.post("/talk", json={
                "npc_id": "orpheus",
                "player_text": "What was my stated intent at the gate?",
                "context": "archive"
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["last_tag"], "gate_recalled")
            self.assertIn(gate_phrase, data["dialogue"])

    # -------------------------------------------------------------------------
    # 12. Separate-Player Memory Isolation
    # -------------------------------------------------------------------------
    def test_multi_session_memory_isolation(self):
        """Two simultaneous player sessions maintain strictly isolated memories and states."""
        session_a = "player_alpha"
        session_b = "player_beta"

        # Player A answers at gate
        statement_a = "We came to shut down the anomaly and rescue Dr. Vance."
        client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": statement_a,
            "context": "gate",
            "session_id": session_a
        })

        # Player B answers at gate with completely different intent
        statement_b = "Classified reconnaissance protocol for deep sector anomalies."
        client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": statement_b,
            "context": "gate",
            "session_id": session_b
        })

        # Player A queries memory -> gets statement A
        resp_a = client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": "ORPHEUS, what was our stated intent at the gate?",
            "context": "archive",
            "session_id": session_a
        }).json()
        self.assertEqual(resp_a["last_tag"], "gate_recalled")
        self.assertIn(statement_a, resp_a["dialogue"])
        self.assertNotIn(statement_b, resp_a["dialogue"])

        # Player B queries memory -> gets statement B
        resp_b = client.post("/talk", json={
            "npc_id": "orpheus",
            "player_text": "ORPHEUS, what was our stated intent at the gate?",
            "context": "archive",
            "session_id": session_b
        }).json()
        self.assertEqual(resp_b["last_tag"], "gate_recalled")
        self.assertIn(statement_b, resp_b["dialogue"])
        self.assertNotIn(statement_a, resp_b["dialogue"])

        # Player A discovers and presents evidence -> drops Vance resolve in Session A to 75
        client.post("/event", json={
            "event": "evidence_discovered",
            "evidence_id": "ev_liora_log",
            "context": "archive",
            "session_id": session_a
        })
        resp_ev_a = client.post("/talk", json={
            "npc_id": "vance",
            "player_text": "Look at Liora's log!",
            "evidence": "ev_liora_log",
            "context": "core",
            "session_id": session_a
        }).json()
        self.assertEqual(resp_ev_a["vance_resolve"], 75)

        # Player B checks state -> Vance resolve in Session B is untouched at 100
        state_b = client.get(f"/state?session_id={session_b}").json()
        self.assertEqual(state_b["vance_resolve"], 100)
        self.assertEqual(state_b["evidence_seen"], [])

        # State A confirms isolation
        state_a = client.get(f"/state?session_id={session_a}").json()
        self.assertEqual(state_a["vance_resolve"], 75)
        self.assertEqual(state_a["evidence_seen"], ["ev_liora_log"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
