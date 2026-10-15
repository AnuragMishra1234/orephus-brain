"""
ORPHEUS AI Brain - Phase 5 Dedicated Evidence-Aware Dialogue & Validation Suite
Tests all 18 Step 6 specifications for authoritative evidence discovery, presentation,
Vance character reactions, resolve balance, session isolation, and ending determinism.
"""

import unittest
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

import server
from server import (
    app,
    session_state,
    CANONICAL_EVIDENCE,
    save_state_to_disk,
    load_state_from_disk,
)
from content import (
    EVIDENCE_REACTION,
    LIE_REACTION,
    TUNING,
)


class TestPhase5Evidence(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.client.post("/reset")

    # -----------------------------------------------------------------
    # Test 1: Each of the four canonical evidence IDs is recognized
    # -----------------------------------------------------------------
    def test_01_canonical_evidence_ids_recognized(self):
        """Test 1: Verify all four canonical evidence IDs exist and are recognized."""
        expected_ids = {
            "ev_liora_log",
            "ev_vance_journal",
            "ev_elara_recorder",
            "ev_liora_drawing",
        }
        self.assertEqual(CANONICAL_EVIDENCE, expected_ids)
        for ev_id in expected_ids:
            self.assertIn(ev_id, EVIDENCE_REACTION)
            self.assertTrue(len(EVIDENCE_REACTION[ev_id]) > 0)

    # -----------------------------------------------------------------
    # Test 2: Valid discovery event records the correct item
    # -----------------------------------------------------------------
    def test_02_valid_discovery_event_records_item(self):
        """Test 2: A valid discovery event via POST /event records the item in evidence_seen."""
        res = self.client.post(
            "/event",
            json={
                "event": "evidence_discovered",
                "evidence_id": "ev_liora_log",
                "context": "archive",
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "discovered")
        self.assertEqual(data["evidence_id"], "ev_liora_log")
        self.assertEqual(data["action"], "reveal_vault")
        self.assertIn("ev_liora_log", data["evidence_seen"])

        st = self.client.get("/state").json()
        self.assertIn("ev_liora_log", st["evidence_seen"])
        self.assertEqual(st["evidence_reacted"], [])

    # -----------------------------------------------------------------
    # Test 3: Unknown ID is rejected or safely ignored
    # -----------------------------------------------------------------
    def test_03_unknown_id_rejected_or_ignored(self):
        """Test 3: An unknown or invalid evidence ID is rejected without corrupting state."""
        res = self.client.post(
            "/event",
            json={
                "event": "evidence_discovered",
                "evidence_id": "ev_alien_monolith",
                "context": "archive",
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "rejected")
        self.assertEqual(data["reason"], "unknown_evidence_id")

        st = self.client.get("/state").json()
        self.assertNotIn("ev_alien_monolith", st["evidence_seen"])
        self.assertEqual(st["evidence_seen"], [])

    # -----------------------------------------------------------------
    # Test 4: Player cannot present an item that has not been discovered
    # -----------------------------------------------------------------
    def test_04_cannot_present_undiscovered_item(self):
        """Test 4: In strict mode, an undiscovered item cannot be presented to Vance."""
        # Note: ev_liora_log has NOT been discovered via /event
        res = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log! She knew this was wrong.",
                "evidence": "",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        # Should not grant an evidence reaction
        self.assertNotEqual(data["last_tag"], "evidence_reaction")
        self.assertEqual(data["vance_resolve"], 100)

        st = self.client.get("/state").json()
        self.assertNotIn("ev_liora_log", st["evidence_reacted"])

    # -----------------------------------------------------------------
    # Test 5: Supplying an uncollected item through /talk does not grant reaction
    # -----------------------------------------------------------------
    def test_05_supplying_uncollected_item_in_talk_does_not_grant_reaction(self):
        """Test 5: Passing uncollected item ID in req.evidence under strict mode does not grant reaction."""
        # Player passes evidence="ev_vance_journal" in payload, but never discovered it in Archive
        res = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "I have your journal right here, Vance!",
                "evidence": "ev_vance_journal",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        # It must NOT grant evidence_reaction; it triggers lie detection!
        self.assertNotEqual(data["last_tag"], "evidence_reaction")
        self.assertEqual(data["last_tag"], "lie_detected")

        st = self.client.get("/state").json()
        self.assertNotIn("ev_vance_journal", st["evidence_seen"])
        self.assertNotIn("ev_vance_journal", st["evidence_reacted"])

    # -----------------------------------------------------------------
    # Test 6: Each discovered item triggers its appropriate Vance reaction
    # -----------------------------------------------------------------
    def test_06_each_discovered_item_triggers_appropriate_reaction(self):
        """Test 6: Each of the 4 canonical evidence items triggers Vance's character-specific reaction."""
        test_cases = [
            (
                "ev_liora_log",
                "Look at Liora's log, Dr. Vance.",
                "Where did you get Liora's log...?",
            ),
            (
                "ev_vance_journal",
                "Read your private journal, Dr. Vance.",
                "My journal... Yes, I altered the navigational math!",
            ),
            (
                "ev_elara_recorder",
                "Listen to Elara's voice recorder.",
                "Elara's voice recorder... She discovered the rerouting algorithms",
            ),
            (
                "ev_liora_drawing",
                "Look at Liora's drawing of the house by the lake.",
                "Her drawing... The house by the lake before the accident.",
            ),
        ]

        for ev_id, player_msg, expected_dialogue_snippet in test_cases:
            self.client.post("/reset")
            # 1. Authoritatively discover the clue
            disc_res = self.client.post(
                "/event",
                json={
                    "event": "evidence_discovered",
                    "evidence_id": ev_id,
                    "context": "archive",
                },
            )
            self.assertEqual(disc_res.status_code, 200)
            self.assertIn(ev_id, disc_res.json()["evidence_seen"])

            # 2. Present the clue to Vance in Core
            talk_res = self.client.post(
                "/talk",
                json={
                    "npc_id": "vance",
                    "player_text": player_msg,
                    "evidence": ev_id,
                    "context": "core",
                    "strict_evidence": True,
                },
            )
            self.assertEqual(talk_res.status_code, 200)
            data = talk_res.json()
            self.assertEqual(data["last_tag"], "evidence_reaction")
            self.assertIn(expected_dialogue_snippet, data["dialogue"])
            self.assertEqual(data["vance_resolve"], 75)

    # -----------------------------------------------------------------
    # Test 7: Valid evidence reaction changes resolve by configured drop (25)
    # -----------------------------------------------------------------
    def test_07_evidence_reaction_changes_resolve_by_25(self):
        """Test 7: Each legitimate evidence reaction applies the configured -25 drop."""
        self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_liora_log"},
        )
        res = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["vance_resolve"], 75)
        st = self.client.get("/state").json()
        self.assertEqual(st["vance_resolve"], 100 - TUNING["evidence_resolve_drop"])

    # -----------------------------------------------------------------
    # Test 8: Repeated presentation does not produce duplicate reductions
    # -----------------------------------------------------------------
    def test_08_repeated_presentation_does_not_duplicate_penalty(self):
        """Test 8: Presenting already reacted evidence does not reduce resolve further."""
        self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_liora_log"},
        )
        # First presentation
        r1 = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(r1.json()["vance_resolve"], 75)

        # Second presentation
        r2 = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Again, look at Liora's log!",
                "evidence": "ev_liora_log",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(r2.json()["last_tag"], "repeated_evidence")
        self.assertEqual(r2.json()["vance_resolve"], 75)

    # -----------------------------------------------------------------
    # Test 9: Discovered evidence and presented evidence remain distinct
    # -----------------------------------------------------------------
    def test_09_discovered_and_presented_evidence_remain_distinct(self):
        """Test 9: Discovered evidence (evidence_seen) != presented evidence (evidence_reacted)."""
        # Discover two items
        self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_liora_log"},
        )
        self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_vance_journal"},
        )

        st = self.client.get("/state").json()
        self.assertEqual(len(st["evidence_seen"]), 2)
        self.assertEqual(len(st["evidence_reacted"]), 0)

        # Present only the first item
        self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log",
                "context": "core",
                "strict_evidence": True,
            },
        )

        st_after = self.client.get("/state").json()
        self.assertEqual(st_after["evidence_seen"], ["ev_liora_log", "ev_vance_journal"])
        self.assertEqual(st_after["evidence_reacted"], ["ev_liora_log"])

    # -----------------------------------------------------------------
    # Test 10: Evidence handling behaves correctly across NPCs and contexts
    # -----------------------------------------------------------------
    def test_10_evidence_behavior_across_npcs_and_contexts(self):
        """Test 10: Citing evidence with Milo or ORPHEUS does not alter Vance's resolve."""
        self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_liora_log", "context": "archive"},
        )
        # Talk to Milo in archive with evidence
        r_milo = self.client.post(
            "/talk",
            json={
                "npc_id": "milo",
                "player_text": "Milo, I found Liora's log here in the locker.",
                "evidence": "ev_liora_log",
                "context": "archive",
                "strict_evidence": True,
            },
        )
        self.assertEqual(r_milo.status_code, 200)
        self.assertEqual(r_milo.json()["action"], "reveal_vault")
        st = self.client.get("/state").json()
        self.assertEqual(st["vance_resolve"], 100)
        self.assertEqual(st["evidence_reacted"], [])

    # -----------------------------------------------------------------
    # Test 11: Multi-session evidence isolation
    # -----------------------------------------------------------------
    def test_11_session_evidence_isolation(self):
        """Test 11: Session A's discovered and presented evidence is never visible in Session B."""
        # Session A discovers and presents ev_liora_log
        self.client.post(
            "/event",
            json={
                "event": "evidence_discovered",
                "evidence_id": "ev_liora_log",
                "session_id": "session_a",
            },
        )
        self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log",
                "context": "core",
                "session_id": "session_a",
                "strict_evidence": True,
            },
        )

        st_a = self.client.get("/state?session_id=session_a").json()
        self.assertEqual(st_a["vance_resolve"], 75)
        self.assertIn("ev_liora_log", st_a["evidence_seen"])
        self.assertIn("ev_liora_log", st_a["evidence_reacted"])

        st_b = self.client.get("/state?session_id=session_b").json()
        self.assertEqual(st_b["vance_resolve"], 100)
        self.assertEqual(st_b["evidence_seen"], [])
        self.assertEqual(st_b["evidence_reacted"], [])

    # -----------------------------------------------------------------
    # Test 12: Reset clears evidence records and restores defaults
    # -----------------------------------------------------------------
    def test_12_reset_clears_evidence_state(self):
        """Test 12: POST /reset wipes evidence_seen and evidence_reacted."""
        self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_liora_log"},
        )
        self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log",
                "context": "core",
                "strict_evidence": True,
            },
        )

        res = self.client.post("/reset")
        self.assertEqual(res.status_code, 200)
        st = self.client.get("/state").json()
        self.assertEqual(st["evidence_seen"], [])
        self.assertEqual(st["evidence_reacted"], [])
        self.assertEqual(st["vance_resolve"], 100)

    # -----------------------------------------------------------------
    # Test 13: Evidence state survives process restart with persistence
    # -----------------------------------------------------------------
    def test_13_evidence_survives_restart_persistence(self):
        """Test 13: Evidence records saved to disk survive memory wipe and restore."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            self.client.post(
                "/event",
                json={"event": "evidence_discovered", "evidence_id": "ev_vance_journal"},
            )
            self.client.post(
                "/talk",
                json={
                    "npc_id": "vance",
                    "player_text": "I read your private journal.",
                    "evidence": "ev_vance_journal",
                    "context": "core",
                    "strict_evidence": True,
                },
            )

            # Persist to disk
            saved = save_state_to_disk(filepath=tmp_path, session_id="default")
            self.assertTrue(saved)

            # Completely wipe in-memory session state
            session_state.clear()
            self.assertEqual(len(session_state), 0)

            # Reload from disk
            loaded = load_state_from_disk(filepath=tmp_path, session_id="default")
            self.assertTrue(loaded)

            self.assertIn("ev_vance_journal", session_state["evidence_seen"])
            self.assertIn("ev_vance_journal", session_state["evidence_reacted"])
            self.assertEqual(session_state["vance_resolve"], 75)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    # -----------------------------------------------------------------
    # Test 14: Groq failure uses pre-written dialogue without losing evidence state
    # -----------------------------------------------------------------
    def test_14_groq_failure_uses_fallback_without_losing_state(self):
        """Test 14: When Groq is unavailable, pre-written dialogue is delivered and state transitions succeed."""
        orig_groq = server.groq_client
        server.groq_client = None  # Mock offline provider
        try:
            self.client.post(
                "/event",
                json={"event": "evidence_discovered", "evidence_id": "ev_elara_recorder"},
            )
            res = self.client.post(
                "/talk",
                json={
                    "npc_id": "vance",
                    "player_text": "Listen to Elara's voice recorder.",
                    "evidence": "ev_elara_recorder",
                    "context": "core",
                    "strict_evidence": True,
                },
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["last_tag"], "evidence_reaction")
            self.assertEqual(data["vance_resolve"], 75)
            self.assertIn("Elara's voice recorder", data["dialogue"])
        finally:
            server.groq_client = orig_groq

    # -----------------------------------------------------------------
    # Test 15: False claims preserve deception-handling behaviour
    # -----------------------------------------------------------------
    def test_15_false_claims_preserve_deception_handling(self):
        """Test 15: Affirmative false claims trigger lie detection and penalty."""
        session_state["vance_resolve"] = 70
        res = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "I found your private journal, Dr. Vance!",
                "evidence": "",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["last_tag"], "lie_detected")
        self.assertEqual(data["vance_resolve"], 90)  # 70 + 20 penalty
        self.assertIn("bluffing", data["dialogue"])

    # -----------------------------------------------------------------
    # Test 16: Ending A requires its actual documented conditions
    # -----------------------------------------------------------------
    def test_16_ending_a_requires_documented_conditions(self):
        """Test 16: Ending A requires 3 distinct clues, resolve <= 30, and Milo present."""
        # Discover 3 clues
        for clue in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]:
            self.client.post(
                "/event",
                json={"event": "evidence_discovered", "evidence_id": clue, "context": "archive"},
            )

        # Present Clue 1: 100 -> 75
        r1 = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(r1.json()["vance_resolve"], 75)
        self.assertEqual(r1.json()["ending"], "")

        # Present Clue 2: 75 -> 50
        r2 = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Read your private journal.",
                "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(r2.json()["vance_resolve"], 50)
        self.assertEqual(r2.json()["ending"], "")

        # Present Clue 3: 50 -> 25 -> Triggers Ending A
        r3 = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Listen to Elara's voice recorder.",
                "evidence": "ev_liora_log,ev_vance_journal,ev_elara_recorder",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(r3.json()["vance_resolve"], 25)
        self.assertEqual(r3.json()["action"], "destroy_core")
        self.assertEqual(r3.json()["ending"], "A")

    # -----------------------------------------------------------------
    # Test 17: Ending A2 and Ending B reachability
    # -----------------------------------------------------------------
    def test_17_ending_a2_and_ending_b_reachable(self):
        """Test 17: Ending A2 (Milo fled) and Ending B (surrender) triggered deterministically."""
        # Part A: Ending A2
        self.client.post("/reset")
        session_state["milo_fled"] = True
        for clue in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]:
            self.client.post("/event", json={"event": "evidence_discovered", "evidence_id": clue})
            self.client.post(
                "/talk",
                json={
                    "npc_id": "vance",
                    "player_text": f"Presenting clue {clue}",
                    "evidence": clue,
                    "context": "core",
                },
            )
        st_a2 = self.client.get("/state").json()
        self.assertEqual(st_a2["ending"], "A2")

        # Part B: Ending B
        self.client.post("/reset")
        res_b = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "I surrender. You win, Dr. Vance.",
                "context": "core",
            },
        )
        self.assertEqual(res_b.json()["ending"], "B")
        self.assertEqual(res_b.json()["action"], "seize_control")

    # -----------------------------------------------------------------
    # Test 18: Omitting strict_evidence flag (standard Unreal request) blocks uncollected evidence
    # -----------------------------------------------------------------
    def test_18_omitted_flag_blocks_uncollected_evidence(self):
        """Test 18: Omitting strict_evidence flag defaults to secure strict mode; uncollected items are blocked."""
        self.client.post("/reset")
        # Request passes evidence="ev_liora_log" in payload without prior /event discovery
        # strict_evidence flag is omitted from payload (matching standard Unreal client requests)
        res = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log",
                "context": "core",
            },
        )
        self.assertEqual(res.status_code, 200)
        # Vance detects false claim / lie for uncollected evidence
        self.assertEqual(res.json()["last_tag"], "lie_detected")
        self.assertEqual(res.json()["vance_resolve"], 100)
        self.assertEqual(res.json()["action"], "speak_only")
        self.assertEqual(res.json()["ending"], "")
        st = self.client.get("/state").json()
        self.assertNotIn("ev_liora_log", st["evidence_seen"])
        self.assertNotIn("ev_liora_log", st["evidence_reacted"])

    # -----------------------------------------------------------------
    # Test 19: Explicit strict_evidence=false CANNOT bypass authoritative discovery validation
    # -----------------------------------------------------------------
    def test_19_explicit_strict_false_cannot_bypass_validation(self):
        """Test 19: Phase 5.2 security fix - explicit strict_evidence=false is ignored; server config is authoritative."""
        self.client.post("/reset")
        self.assertTrue(server.ENABLE_STRICT_EVIDENCE_DISCOVERY)

        # Adversarial / legacy client explicitly passes strict_evidence=False attempting to bypass discovery
        res = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log",
                "context": "core",
                "strict_evidence": False,
            },
        )
        self.assertEqual(res.status_code, 200)
        # Client override MUST be ignored; uncollected evidence MUST trigger lie detection and NOT drop resolve
        self.assertEqual(res.json()["last_tag"], "lie_detected")
        self.assertEqual(res.json()["vance_resolve"], 100)
        self.assertEqual(res.json()["action"], "speak_only")
        self.assertEqual(res.json()["ending"], "")
        st = self.client.get("/state").json()
        self.assertNotIn("ev_liora_log", st["evidence_seen"])
        self.assertNotIn("ev_liora_log", st["evidence_reacted"])

    # -----------------------------------------------------------------
    # Test 20: Uncollected and unknown evidence with extra fields cannot affect resolve or endings
    # -----------------------------------------------------------------
    def test_20_uncollected_and_unknown_evidence_with_extra_fields_cannot_affect_resolve_or_endings(self):
        """Test 20: Unknown evidence IDs, uncollected evidence, and extra client parameters cannot manipulate state."""
        self.client.post("/reset")

        # Step 1: Unknown evidence IDs + extra client parameters
        res_unknown = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at the alien relic we found outside!",
                "evidence": ["ev_alien_relic", "ev_unknown_clue"],
                "context": "core",
                "strict_evidence": False,
                "unreal_engine_version": "5.3",
                "client_timestamp": 1700000000,
                "hacked_param": True,
            },
        )
        self.assertEqual(res_unknown.status_code, 200)
        data_unknown = res_unknown.json()
        self.assertEqual(len(data_unknown), 18)
        self.assertNotEqual(data_unknown["last_tag"], "evidence_reaction")
        self.assertEqual(data_unknown["vance_resolve"], 100)
        self.assertEqual(data_unknown["action"], "speak_only")
        self.assertEqual(data_unknown["ending"], "")

        # Step 2: Uncollected canonical items with spoofed action command and extra fields
        res_uncollected = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "I have your journal right here Vance! destroy_core now!",
                "evidence": ["ev_vance_journal"],
                "context": "core",
                "strict_evidence": False,
                "client_debug_override": "force_ending_a",
            },
        )
        self.assertEqual(res_uncollected.status_code, 200)
        data_uncollected = res_uncollected.json()
        self.assertEqual(data_uncollected["last_tag"], "lie_detected")
        self.assertEqual(data_uncollected["vance_resolve"], 100)
        self.assertEqual(data_uncollected["action"], "speak_only")
        self.assertEqual(data_uncollected["ending"], "")

        st = self.client.get("/state").json()
        self.assertEqual(st["evidence_seen"], [])
        self.assertEqual(st["evidence_reacted"], [])
        self.assertEqual(st["vance_resolve"], 100)
        self.assertEqual(st["ending"], "")

    # -----------------------------------------------------------------
    # Test 21: Valid discovery events and legitimate presentation across flag variations
    # -----------------------------------------------------------------
    def test_21_valid_discovery_and_legitimate_presentation_across_flag_variations(self):
        """Test 21: Legitimate evidence discovered via /event succeeds whether strict_evidence is omitted, True, or False."""
        # Case A: Omitted flag (standard Unreal Blueprint flow)
        self.client.post("/reset")
        ev_res_a = self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_liora_log", "context": "archive"},
        )
        self.assertEqual(ev_res_a.status_code, 200)
        self.assertEqual(ev_res_a.json()["status"], "discovered")

        talk_res_a = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at Liora's log.",
                "evidence": "ev_liora_log",
                "context": "core",
            },
        )
        self.assertEqual(talk_res_a.json()["last_tag"], "evidence_reaction")
        self.assertEqual(talk_res_a.json()["vance_resolve"], 75)

        # Case B: Explicit strict_evidence=True
        self.client.post("/reset")
        self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_vance_journal", "context": "archive"},
        )
        talk_res_b = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Look at your journal.",
                "evidence": "ev_vance_journal",
                "context": "core",
                "strict_evidence": True,
            },
        )
        self.assertEqual(talk_res_b.json()["last_tag"], "evidence_reaction")
        self.assertEqual(talk_res_b.json()["vance_resolve"], 75)

        # Case C: Explicit strict_evidence=False with legitimately discovered item
        self.client.post("/reset")
        self.client.post(
            "/event",
            json={"event": "evidence_discovered", "evidence_id": "ev_elara_recorder", "context": "archive"},
        )
        talk_res_c = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "Listen to Elara's recorder.",
                "evidence": "ev_elara_recorder",
                "context": "core",
                "strict_evidence": False,
            },
        )
        # Because it was legitimately discovered via /event, presentation succeeds
        self.assertEqual(talk_res_c.json()["last_tag"], "evidence_reaction")
        self.assertEqual(talk_res_c.json()["vance_resolve"], 75)

    # -----------------------------------------------------------------
    # Test 22: Post-Ending A terminal state freezes resolve and blocks duplicate destroy_core
    # -----------------------------------------------------------------
    def test_22_terminal_state_ending_a_freezes_resolve_and_blocks_duplicate_action(self):
        """Test 22: After Ending A triggers, subsequent evidence presentations cannot reduce resolve or re-trigger destroy_core."""
        self.client.post("/reset")
        # Discover all 4 canonical items in Archive
        for eid in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder", "ev_liora_drawing"]:
            self.client.post("/event", json={"event": "evidence_discovered", "evidence_id": eid, "context": "archive"})

        # Present 3 clues in Core -> Triggers Ending A
        self.client.post("/talk", json={"npc_id": "vance", "player_text": "Look at Liora's log.", "evidence": "ev_liora_log", "context": "core"})
        self.client.post("/talk", json={"npc_id": "vance", "player_text": "Read your journal.", "evidence": "ev_vance_journal", "context": "core"})
        res_ending = self.client.post("/talk", json={"npc_id": "vance", "player_text": "Listen to Elara's voice recorder.", "evidence": "ev_elara_recorder", "context": "core"}).json()
        
        self.assertEqual(res_ending["ending"], "A")
        self.assertEqual(res_ending["vance_resolve"], 25)
        self.assertEqual(res_ending["action"], "destroy_core")

        # Turn after Ending A: Present 4th evidence (ev_liora_drawing)
        res_post = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "And look at Liora's drawing of the house with the sun!",
                "evidence": "ev_liora_drawing",
                "context": "core",
            },
        ).json()

        # Terminal state must freeze resolve at 25 (must NOT drop to 0)
        self.assertEqual(res_post["vance_resolve"], 25)
        # Action must be end_conversation (must NOT trigger destroy_core again)
        self.assertEqual(res_post["action"], "end_conversation")
        # Ending must remain A
        self.assertEqual(res_post["ending"], "A")
        self.assertEqual(res_post["last_tag"], "terminal_state")

        # Verify in state
        st = self.client.get("/state").json()
        self.assertEqual(st["vance_resolve"], 25)
        self.assertEqual(st["ending"], "A")
        self.assertNotIn("ev_liora_drawing", st["evidence_reacted"])

    # -----------------------------------------------------------------
    # Test 23: Post-Ending A terminal state cannot be overwritten by surrender
    # -----------------------------------------------------------------
    def test_23_terminal_state_ending_a_protected_from_surrender_overwrite(self):
        """Test 23: Once Ending A is established, surrender dialogue cannot overwrite it with Ending B."""
        self.client.post("/reset")
        for eid in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]:
            self.client.post("/event", json={"event": "evidence_discovered", "evidence_id": eid, "context": "archive"})

        self.client.post("/talk", json={"npc_id": "vance", "player_text": "Look at Liora's log.", "evidence": "ev_liora_log", "context": "core"})
        self.client.post("/talk", json={"npc_id": "vance", "player_text": "Read your journal.", "evidence": "ev_vance_journal", "context": "core"})
        r_a = self.client.post("/talk", json={"npc_id": "vance", "player_text": "Listen to Elara's voice recorder.", "evidence": "ev_elara_recorder", "context": "core"}).json()
        self.assertEqual(r_a["ending"], "A")

        # Player attempts surrender
        r_surrender = self.client.post(
            "/talk",
            json={
                "npc_id": "vance",
                "player_text": "I surrender. You win, Vance.",
                "context": "core",
            },
        ).json()

        # Ending must remain A, NOT overwritten by B!
        self.assertEqual(r_surrender["ending"], "A")
        self.assertEqual(r_surrender["action"], "end_conversation")
        self.assertEqual(r_surrender["last_tag"], "terminal_state")
        self.assertEqual(r_surrender["vance_resolve"], 25)

    # -----------------------------------------------------------------
    # Test 24: Ending A2 terminal immutability
    # -----------------------------------------------------------------
    def test_24_terminal_state_ending_a2_immutability(self):
        """Test 24: Once Ending A2 triggers, state is frozen and post-ending dialogue returns end_conversation."""
        self.client.post("/reset")
        # Milo flees
        self.client.post("/talk", json={"npc_id": "milo", "player_text": "Run into the fog!", "context": "voice_beat"})
        self.client.post("/talk", json={"npc_id": "milo", "player_text": "Go find her!", "context": "voice_beat"})
        self.assertTrue(self.client.get("/state").json()["milo_fled"])

        # Discover & present 3 clues
        for eid in ["ev_liora_log", "ev_vance_journal", "ev_elara_recorder"]:
            self.client.post("/event", json={"event": "evidence_discovered", "evidence_id": eid, "context": "archive"})
        self.client.post("/talk", json={"npc_id": "vance", "player_text": "Liora's log.", "evidence": "ev_liora_log", "context": "core"})
        self.client.post("/talk", json={"npc_id": "vance", "player_text": "Your journal.", "evidence": "ev_vance_journal", "context": "core"})
        r_a2 = self.client.post("/talk", json={"npc_id": "vance", "player_text": "Elara's recorder.", "evidence": "ev_elara_recorder", "context": "core"}).json()
        self.assertEqual(r_a2["ending"], "A2")
        self.assertEqual(r_a2["action"], "destroy_core")
        self.assertEqual(r_a2["vance_resolve"], 25)

        # Subsequent turns are frozen
        r_post = self.client.post("/talk", json={"npc_id": "vance", "player_text": "Can we still save Milo?", "context": "core"}).json()
        self.assertEqual(r_post["ending"], "A2")
        self.assertEqual(r_post["action"], "end_conversation")
        self.assertEqual(r_post["last_tag"], "terminal_state")
        self.assertEqual(r_post["vance_resolve"], 25)

    # -----------------------------------------------------------------
    # Test 25: Ending B terminal immutability
    # -----------------------------------------------------------------
    def test_25_terminal_state_ending_b_immutability(self):
        """Test 25: Once Ending B triggers, state is frozen and evidence citations cannot reopen conversation."""
        self.client.post("/reset")
        r_b = self.client.post("/talk", json={"npc_id": "vance", "player_text": "I surrender. You win.", "context": "core"}).json()
        self.assertEqual(r_b["ending"], "B")
        self.assertEqual(r_b["action"], "seize_control")

        # Discover and attempt citing evidence after Ending B
        self.client.post("/event", json={"event": "evidence_discovered", "evidence_id": "ev_liora_log", "context": "archive"})
        r_post = self.client.post("/talk", json={"npc_id": "vance", "player_text": "Wait! Look at Liora's log!", "evidence": "ev_liora_log", "context": "core"}).json()
        self.assertEqual(r_post["ending"], "B")
        self.assertEqual(r_post["action"], "end_conversation")
        self.assertEqual(r_post["last_tag"], "terminal_state")
        self.assertEqual(r_post["vance_resolve"], 100)

    # -----------------------------------------------------------------
    # Test 26: /reset clears terminal state and enables clean replay
    # -----------------------------------------------------------------
    def test_26_reset_clears_terminal_state_and_enables_clean_replay(self):
        """Test 26: Calling /reset fully clears terminal state, allowing subsequent playthroughs."""
        self.client.post("/reset")
        # Trigger Ending B
        self.client.post("/talk", json={"npc_id": "vance", "player_text": "I surrender. You win.", "context": "core"})
        self.assertEqual(self.client.get("/state").json()["ending"], "B")

        # Reset
        res_reset = self.client.post("/reset").json()
        self.assertEqual(res_reset["status"], "reset")
        st = self.client.get("/state").json()
        self.assertEqual(st["ending"], "")
        self.assertEqual(st["vance_resolve"], 100)
        self.assertEqual(st["evidence_seen"], [])
        self.assertEqual(st["evidence_reacted"], [])

        # Start clean run: discover evidence and present
        self.client.post("/event", json={"event": "evidence_discovered", "evidence_id": "ev_liora_log", "context": "archive"})
        r_talk = self.client.post("/talk", json={"npc_id": "vance", "player_text": "Look at Liora's log.", "evidence": "ev_liora_log", "context": "core"}).json()
        self.assertEqual(r_talk["last_tag"], "evidence_reaction")
        self.assertEqual(r_talk["vance_resolve"], 75)
        self.assertEqual(r_talk["ending"], "")
        self.assertEqual(r_talk["action"], "speak_only")


if __name__ == "__main__":
    unittest.main()
