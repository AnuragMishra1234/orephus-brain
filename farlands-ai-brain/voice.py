"""
ORPHEUS AI Brain - Voice Engine & Speech Synthesis
File Owner: Anurag (AI Server Engineer)

This module handles spoken dialogue generation, non-blocking playback,
and instant audio cancellation for the hackathon demo.
Provides robust fallback to subtitle-only mode when audio is disabled or unavailable.
"""

import os
import logging
import threading
from typing import Optional

logger = logging.getLogger("orpheus.voice")

# Global locks and speech state
_audio_lock = threading.Lock()
_sapi_voice = None
_is_playing = False
_sapi_initialized = False

def _get_sapi_voice():
    """Lazily initialize Windows SAPI voice if on Windows and available."""
    global _sapi_voice, _sapi_initialized
    if not _sapi_initialized:
        _sapi_initialized = True
        try:
            import win32com.client
            _sapi_voice = win32com.client.Dispatch("SAPI.SpVoice")
            _sapi_voice.Rate = 1  # Natural conversational tempo
            logger.info("[VOICE] Windows SAPI voice engine initialized successfully.")
        except Exception as e:
            logger.info(f"[VOICE] Native SAPI voice not available: {e}. Falling back to subtitle mode.")
            _sapi_voice = None
    return _sapi_voice

def is_voice_enabled() -> bool:
    """Check if voice generation is enabled via environment."""
    return os.getenv("VOICE", "off").strip().lower() in ("on", "true", "1", "yes")

def is_playing() -> bool:
    """Return whether audio is currently playing."""
    return _is_playing

def calculate_speech_duration(text: str) -> float:
    """
    Calculate spoken duration in seconds based on word count.
    Clamped between 1.5s (minimum bark) and 12.0s (maximum dialogue limit).
    """
    clean_text = text.strip()
    if not clean_text:
        return 0.0
    word_count = len(clean_text.split())
    # Average reading/speech rate ~ 140 WPM (approx 0.35s per word with cadence)
    duration = max(1.5, min(12.0, round(word_count * 0.35, 1)))
    return duration

def speak(who: str, text: str) -> dict:
    """
    Trigger voice playback for character dialogue asynchronously.
    If VOICE is disabled or audio fails, returns subtitle-only duration safely.
    Never blocks the FastAPI request worker.
    """
    global _is_playing
    duration = calculate_speech_duration(text)
    
    if not is_voice_enabled():
        logger.debug(f"[VOICE:OFF] Subtitles only for {who}: '{text[:40]}...' ({duration}s)")
        return {
            "status": "subtitles_only",
            "who": who,
            "duration": duration,
            "played": False,
        }

    # Attempt playback using Windows SAPI (async flag 1 = SVSFlagsAsync)
    try:
        with _audio_lock:
            sapi = _get_sapi_voice()
            if sapi is not None:
                # Flag 1 = SVSFlagsAsync (asynchronous, non-blocking)
                # Flag 2 = SVSFPurgeBeforeSpeak (purges prior speech before playing new line)
                sapi.Speak(text, 1 | 2)
                _is_playing = True
                
                # Auto-reset _is_playing flag after duration in background
                def _track_completion():
                    import time
                    time.sleep(duration)
                    global _is_playing
                    with _audio_lock:
                        _is_playing = False

                t = threading.Thread(target=_track_completion, daemon=True)
                t.start()

                logger.info(f"[VOICE PLAYING] Spoke line for {who} asynchronously ({duration}s)")
                return {
                    "status": "playing",
                    "who": who,
                    "duration": duration,
                    "played": True,
                }
    except Exception as e:
        logger.warning(f"[VOICE ERROR] SAPI async speak failed: {e}")

    # Fallback to pyttsx3 in worker thread if SAPI is unavailable
    try:
        def _pyttsx3_worker():
            global _is_playing
            with _audio_lock:
                try:
                    import pyttsx3
                    engine = pyttsx3.init()
                    engine.setProperty('rate', 140)
                    _is_playing = True
                    engine.say(text)
                    engine.runAndWait()
                except Exception as ex:
                    logger.debug(f"[VOICE] pyttsx3 worker finished/unavailable: {ex}")
                finally:
                    _is_playing = False

        worker = threading.Thread(target=_pyttsx3_worker, daemon=True)
        worker.start()
        
        return {
            "status": "playing",
            "who": who,
            "duration": duration,
            "played": True,
        }
    except Exception as e:
        logger.debug(f"[VOICE FALLBACK] Thread creation failed: {e}")

    return {
        "status": "subtitles_only",
        "who": who,
        "duration": duration,
        "played": False,
    }

def stop() -> dict:
    """
    Instantly halt any active audio playback.
    Safe to call when nothing is playing, in voice-off mode, or repeatedly.
    """
    global _is_playing
    with _audio_lock:
        _is_playing = False
        try:
            sapi = _get_sapi_voice()
            if sapi is not None:
                # Flag 2 = SVSFPurgeBeforeSpeak: cancels current speech buffer immediately
                sapi.Speak("", 2)
                logger.info("[VOICE] Audio playback purged and stopped.")
        except Exception as e:
            logger.debug(f"[VOICE] Stop audio SAPI call handled: {e}")

    return {"status": "stopped"}
