"""
ORPHEUS AI Brain - Voice Engine & Speech Synthesis
File Owner: Anurag (AI Server Engineer)

This module handles spoken dialogue generation and playback for the demo laptop.
Provides robust fallback to subtitle-only mode when audio is disabled or unavailable.
"""

import os
import logging
import threading

logger = logging.getLogger("orpheus.voice")

# Global lock for audio operations
_audio_lock = threading.Lock()
_current_process = None

def is_voice_enabled() -> bool:
    """Check if voice generation is enabled via environment."""
    return os.getenv("VOICE", "off").strip().lower() in ("on", "true", "1", "yes")

def calculate_speech_duration(text: str) -> float:
    """Calculate approximate spoken duration in seconds based on word count."""
    word_count = len(text.strip().split())
    # Average speaking rate ~ 130 words per minute (approx 0.45s per word with cadence)
    duration = max(1.5, round(word_count * 0.35, 1))
    return duration

def speak(who: str, text: str) -> dict:
    """
    Trigger voice playback for character dialogue.
    If VOICE is disabled or audio fails, returns subtitle-only duration safely.
    """
    duration = calculate_speech_duration(text)
    
    if not is_voice_enabled():
        logger.debug(f"[VOICE:OFF] Subtitles only for {who}: '{text[:40]}...' ({duration}s)")
        return {
            "status": "subtitles_only",
            "who": who,
            "duration": duration,
            "played": False
        }

    try:
        # Background worker for voice playback to prevent HTTP server blocking
        def _play_worker():
            with _audio_lock:
                try:
                    import pyttsx3
                    engine = pyttsx3.init()
                    engine.setProperty('rate', 140)
                    engine.say(text)
                    engine.runAndWait()
                except ImportError:
                    logger.warning("[VOICE] pyttsx3 not installed. Running in subtitle-only mode.")
                except Exception as ex:
                    logger.error(f"[VOICE ERROR] Speech synthesis failed: {ex}")

        worker = threading.Thread(target=_play_worker, daemon=True)
        worker.start()
        
        return {
            "status": "playing",
            "who": who,
            "duration": duration,
            "played": True
        }
    except Exception as e:
        logger.error(f"[VOICE ERROR] Failed to start voice thread: {e}")
        return {
            "status": "error_fallback_to_subtitles",
            "who": who,
            "duration": duration,
            "played": False
        }

def stop() -> dict:
    """Stop any currently playing audio."""
    logger.info("[VOICE] Stop audio requested.")
    return {"status": "stopped"}
