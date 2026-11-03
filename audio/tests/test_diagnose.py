import numpy as np

from musefx_audio.diagnose import analyze, best_candidate, diagnose, revise_prompt, severe_issues

SR = 16000


def tone(seconds=0.4, freq=1200, amp=0.7, lead=0.0, decay=8.0):
    t = np.arange(int(SR * seconds)) / SR
    body = amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)
    if lead:
        body = np.concatenate([np.zeros(int(SR * lead)), body])
    return body.astype(np.float32)


def test_analyze_and_diagnose_silence_and_soft_attack():
    silent = np.zeros(SR, dtype=np.float32)
    assert "near_silent" in diagnose(analyze(silent, SR), True, 0.4)
    soft = tone(lead=0.25)
    issues = diagnose(analyze(soft, SR), True, 0.4)
    assert "very_soft_attack" in issues and severe_issues(issues)


def test_diagnose_too_long_and_too_quiet():
    long_quiet = tone(seconds=3.0, amp=0.01, decay=0.2)
    issues = diagnose(analyze(long_quiet, SR), False, 0.4)
    assert "far_too_long" in issues
    sharp = tone()
    assert diagnose(analyze(sharp, SR), True, 0.4) == [] or "too_short" not in diagnose(analyze(sharp, SR), True, 0.4)


def test_revise_prompt_targets_issues():
    out = revise_prompt("boot on gravel", ["very_soft_attack", "dull_for_percussive"])
    assert "sharp immediate attack" in out and "bright, crisp" in out
    assert revise_prompt("boot on gravel", []) == "boot on gravel"


def test_best_candidate_prefers_sharp_over_soft():
    soft = tone(lead=0.2)
    sharp = tone()
    picked = best_candidate([soft, sharp], SR, True, 0.4)
    assert picked.index == 1
    assert not severe_issues(picked.issues)
