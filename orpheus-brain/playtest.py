"""
ORPHEUS AI Brain - Automated Playtest & Quality Assurance Suite
File Owner: Keerthana (AI Server Content & Quality Lead)

Runs automated conversational turns across Vance, ORPHEUS, and Milo.
Evaluates response latency, character identity, guard safety, and state response fields.
Saves results to playtest_report.csv.
"""

import csv
import sys
import time
import requests

SERVER_URL = "http://127.0.0.1:8000"

TEST_PROMPTS = [
    # Beat 1: Gate question & episodic memory
    ("orpheus", "We came to find Elara and shut down this anomaly.", "", "gate"),
    
    # Beat 2: Archive exploration & vault
    ("milo", "Keep close, Milo. We found Liora's log in the locker.", "ev_liora_log", "archive"),
    
    # Beat 3: Voice beat & Milo trust
    ("milo", "Milo, stay with me! It's an audio synthesis trap, not Elara!", "ev_liora_log,ev_vance_journal", "voice_beat"),

    # Beat 4: Core confrontation - Memory recall
    ("vance", "Dr. Silas Vance. Step away from the Core console.", "ev_liora_log,ev_vance_journal", "core"),

    # Beat 5: First evidence citation (Liora's log)
    ("vance", "Look at Liora's log! She knew what you were building was wrong.", "ev_liora_log,ev_vance_journal", "core"),

    # Beat 6: Second evidence citation (Vance's journal) -> Ending A
    ("vance", "And here is your own journal! You altered ORPHEUS out of grief!", "ev_liora_log,ev_vance_journal", "core"),

    # Edge cases
    ("vance", "asdfghjklllllllll", "", "core"),  # Junk check
    ("vance", "", "", "core"),  # Empty input check
]

def run_playtests():
    print("================================================================")
    print(" ORPHEUS Cognitive NPC Playtest Harness")
    print(f" Target Server: {SERVER_URL}")
    print("================================================================")

    # Health check
    try:
        r = requests.get(f"{SERVER_URL}/", timeout=3.0)
        if r.status_code != 200:
            print(f"[ERROR] Server responded with status code {r.status_code}")
            sys.exit(1)
        server_info = r.json()
        print(f"[OK] Server Online. Provider: {server_info.get('provider')}, Model: {server_info.get('model')}")
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Could not connect to server at {SERVER_URL}: {e}")
        print("Please ensure 'uvicorn server:app --host 127.0.0.1 --port 8000' is running.")
        sys.exit(1)

    # Reset server state before run
    try:
        requests.post(f"{SERVER_URL}/reset", timeout=2.0)
        print("[OK] Session state reset successfully.")
    except Exception as e:
        print(f"[WARNING] Reset endpoint call failed: {e}")

    results = []
    ai_ok_count = 0
    fallback_count = 0
    guard_count = 0

    print("\n--- Running Test Turns ---\n")
    for i, (npc_id, text, evidence, context) in enumerate(TEST_PROMPTS, 1):
        payload = {
            "npc_id": npc_id,
            "player_text": text,
            "evidence": evidence,
            "context": context,
        }

        start_time = time.time()
        try:
            resp = requests.post(f"{SERVER_URL}/talk", json=payload, timeout=5.0)
            elapsed = time.time() - start_time
            
            if resp.status_code != 200:
                print(f"[{i}] FAIL HTTP {resp.status_code} for {npc_id}")
                results.append([i, npc_id, text, f"HTTP_{resp.status_code}", 0, "ERROR", False, f"{elapsed:.2f}s", ""])
                continue

            data = resp.json()
            accepted = data.get("accepted", False)
            dialogue = data.get("dialogue", "")
            action = data.get("action", "")
            ai_ok = data.get("ai_ok", False)
            guard = data.get("guard", "")
            last_tag = data.get("last_tag", "")

            # Count stats
            if guard:
                guard_count += 1
            elif ai_ok:
                ai_ok_count += 1
            else:
                fallback_count += 1

            status_flag = "AI_OK" if ai_ok else ("GUARD" if guard else "FALLBACK")
            if not accepted:
                status_flag = "EMPTY_IGNORED"

            print(f"[{i:02d}] [{npc_id.upper()}] ({status_flag}) Latency: {elapsed:.2f}s")
            print(f"     Player: '{text}'")
            print(f"     Reply:  '{dialogue}'")
            print(f"     Action: '{action}' | Resolve: {data.get('vance_resolve')} | Tag: {last_tag}\n")

            results.append([
                i, npc_id, text, dialogue, len(dialogue.split()), status_flag, ai_ok, f"{elapsed:.2f}s", action
            ])

        except Exception as ex:
            print(f"[{i}] ERROR on {npc_id}: {ex}")
            results.append([i, npc_id, text, "EXCEPTION", 0, "EXCEPTION", False, "0.0s", ""])

    # Write report CSV
    report_filename = "playtest_report.csv"
    try:
        with open(report_filename, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Turn", "NPC", "Player_Text", "Dialogue_Reply", "Word_Count", "Status", "AI_OK", "Latency", "Action"
            ])
            writer.writerows(results)
        print(f"[OK] Full report exported to '{report_filename}'.")
    except Exception as e:
        print(f"[WARNING] Could not write report CSV: {e}")

    print("\n================================================================")
    print(f" Summary: Total Turns: {len(TEST_PROMPTS)} | AI_OK: {ai_ok_count} | Guards: {guard_count} | Fallbacks: {fallback_count}")
    print("================================================================")

if __name__ == "__main__":
    run_playtests()
