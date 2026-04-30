import time
import os
import mss
import argparse
import threading
import queue
from datetime import datetime
import numpy as np
import keyboard

try:
    from line_profiler import profile
except ImportError:
    profile = lambda x: x

import cv2
from module import *
from async_logger import logger
from config import *


# ─── Note Detection ─────────────────────────────────────────────────────────

@profile
def _detect_cross_tap(window_img, templates, holding_flags):
    """Phase 2 — Detect cross_tap in the full_window region. Returns a detection dict or None."""
    full_region = REGIONS["full_window"]
    cross_tap_key = KEYS["cross_tap"]

    if holding_flags and holding_flags.get(cross_tap_key, False):
        return None

    full_img = crop_region(window_img, full_region)

    # Primary: HSV color region matching (brightness-invariant, survives click effects)
    hsv_profile = HSV_PROFILES.get("cross_tap")
    loc = None
    val = 0.0
    w, h = None, None

    if hsv_profile:
        loc, w, h, val = match_hsv_region(
            full_img,
            target_hsv=hsv_profile["target_hsv"],
            target_size=hsv_profile["target_size"],
            threshold=hsv_profile["threshold"],
            hue_tol=hsv_profile["hue_tol"],
            sat_range=hsv_profile["sat_range"],
            val_range=hsv_profile["val_range"],
        )

    # Fallback: template matching (for cases HSV might miss)
    # if loc is None and templates.get("cross_tap") is not None:
    #     loc, w, h, val = match_template(full_img, templates["cross_tap"], threshold=MATCH_THRESHOLD)

    if loc:
        abs_loc = (loc[0] + full_region[0], loc[1] + full_region[1])
        strip_val = 0

        offset_x, offset_y, bf_w, bf_h = STRIP_BUFFER_REGIONS.get("full_window", (0, 0, 0, 0))
        if bf_w > 0 and bf_h > 0:
            buffer_crop_box = (full_region[0] + offset_x, full_region[1] + offset_y, bf_w, bf_h)
            strip_val = check_strip_color(window_img, buffer_crop_box)

        return {"type": "cross_tap", "slot": "full_window", "loc": abs_loc, "val": val, "w": w, "h": h, "strip_val": strip_val}

    return None


@profile
def _detect_slot_note(window_img, templates, slot_key, swipe_pressed, lswipe_detected, rswipe_detected, holding_flags):
    """Phase 2 — Detect a note (tap / left_swipe / right_swipe) in a single slot. Returns (detection_dict, updated lswipe/rswipe flags) or (None, flags)."""
    region = REGIONS.get(slot_key)
    if not region:
        return None, lswipe_detected, rswipe_detected

    key = KEYS[slot_key]
    if holding_flags and holding_flags.get(key, False):
        return None, lswipe_detected, rswipe_detected

    slot_img = crop_region(window_img, region)

    # We only expect one type of note per slot at a time, break early if found
    for note_type in ["tap", "left_swipe", "right_swipe"]:
        # Skip swipe detection if the corresponding shift is already held
        if note_type == "left_swipe" and swipe_pressed and swipe_pressed.get("left", False):
            continue
        if note_type == "right_swipe" and swipe_pressed and swipe_pressed.get("right", False):
            continue
        # skip if left_swipe or right_swipe is already detected (in the same frame)
        if note_type == "left_swipe" and lswipe_detected:
            continue
        if note_type == "right_swipe" and rswipe_detected:
            continue

        # Swipe notes use HSV color detection (immune to ±15px screen shake)
        hsv_profile = HSV_PROFILES.get(note_type)
        if note_type in ("left_swipe", "right_swipe") and hsv_profile:
            loc, w, h, val = match_hsv_region(
                slot_img,
                target_hsv=hsv_profile["target_hsv"],
                target_size=hsv_profile["target_size"],
                threshold=hsv_profile["threshold"],
                hue_tol=hsv_profile["hue_tol"],
                sat_range=hsv_profile["sat_range"],
                val_range=hsv_profile["val_range"],
            )
        elif templates.get(note_type) is not None:
            # Tap notes: still use template matching (tap doesn't trigger shift, no shake)
            loc, w, h, val = match_template(slot_img, templates[note_type], threshold=MATCH_THRESHOLD)
        else:
            continue

        if loc:
            abs_loc = (loc[0] + region[0], loc[1] + region[1])
            strip_val = 0

            if note_type == "tap":
                offset_x, offset_y, bf_w, bf_h = STRIP_BUFFER_REGIONS.get(slot_key, (0, 0, 0, 0))
                if bf_w > 0 and bf_h > 0:
                    buffer_crop_box = (region[0] + offset_x, region[1] + offset_y, bf_w, bf_h)
                    strip_val = check_strip_color(window_img, buffer_crop_box)

            detection = {"type": note_type, "slot": slot_key, "loc": abs_loc, "val": val, "w": w, "h": h, "strip_val": strip_val}
            if note_type == "left_swipe":
                lswipe_detected = True
            if note_type == "right_swipe":
                rswipe_detected = True
            return detection, lswipe_detected, rswipe_detected

    return None, lswipe_detected, rswipe_detected


@profile
def detect_notes(window_img, templates, holding_flags=None, swipe_pressed=None):
    """Scan the window for all note types across all slots. Returns a list of detection dicts."""
    detected = []

    # 1. Check for cross_tap in full_window
    cross_tap = _detect_cross_tap(window_img, templates, holding_flags)
    if cross_tap:
        detected.append(cross_tap)

    # If cross_tap is currently held OR we just detected it, we can skip the individual slots
    # cross_tap_key = KEYS["cross_tap"]
    # is_cross_tap_held = holding_flags and holding_flags.get(cross_tap_key, False)
    # if is_cross_tap_held or cross_tap:
    #     return detected

    # 2. Iterate through each slot for specific notes
    lswipe_detected = False
    rswipe_detected = False

    for i in range(1, 5):
        slot_key = f"slot_{i}"
        note, lswipe_detected, rswipe_detected = _detect_slot_note(
            window_img, templates, slot_key, swipe_pressed,
            lswipe_detected, rswipe_detected, holding_flags
        )
        if note:
            detected.append(note)

    return detected


# ─── Main Loop Phases ────────────────────────────────────────────────────────

def _init_game_state():
    """Initialize all mutable state dictionaries for the main loop."""
    holding_flags = {key: False for key in KEYS.values()}
    strip_start_times = {key: 0.0 for key in KEYS.values()}
    recorded_bgs = {key: None for key in KEYS.values()}
    bg_save_times = {key: 0.0 for key in KEYS.values()}
    forzed_times = {key: 0.0 for key in KEYS.values()}

    swipe_state = {
        "L_pressed": False,
        "R_pressed": False,
        "L_slot": None,   # remembered slot key, e.g. "slot_2"
        "R_slot": None,
    }

    return holding_flags, strip_start_times, recorded_bgs, bg_save_times, forzed_times, swipe_state


@profile
def _phase1_bg_diff_check(window_img, templates, tick, current_time,
                          holding_flags, strip_start_times, recorded_bgs,
                          bg_save_times, forzed_times, save_buff, cooldown):
    """Phase 1 — Continuous background difference check for held notes (slots 1-4 + cross_tap).
    Releases keys when background changes significantly (note ended)."""
    for slot in ["full_window", "slot_1", "slot_2", "slot_3", "slot_4"]:
        key = KEYS["cross_tap"] if slot == "full_window" else KEYS[slot]
        if not holding_flags[key]:
            continue
        if current_time - strip_start_times[key] < 0.05:
            continue

        offset_x, offset_y, w, h = BG_AREAS.get(slot, (0, 0, 0, 0))
        if w <= 0:
            continue

        region = REGIONS[slot]
        crop_box = (region[0] + offset_x, region[1] + offset_y, w, h)
        bg_img_uint8 = crop_region(window_img, crop_box)
        bg_img = bg_img_uint8.astype(np.int32)

        if recorded_bgs[key] is None:
            recorded_bgs[key] = bg_img
            continue

        diff = np.mean(np.abs(bg_img - recorded_bgs[key]))

        # cross_tap has much more interference, so the threshold is higher
        threshold_met = (diff >= BG_DIFF_THRESHOLD and slot != "full_window") or \
                        (diff >= 100 and slot == "full_window")

        if threshold_met:
            is_ignore = False
            # detect strip_ignore
            if templates.get("strip_ignore") is not None:
                ignore_loc, _, _, _ = match_template(bg_img_uint8, templates["strip_ignore"], threshold=0.9)
                if ignore_loc:
                    is_ignore = True
                    forzed_times[key] = current_time
                    logger.info(f"SUSPECT detect ignored for {slot}")

            if not is_ignore and current_time - forzed_times[key] > 0.08:
                if current_time - tick(f'press_{key}') > cooldown:
                    newpress(key, tick, action='up')
                    holding_flags[key] = False
                    recorded_bgs[key] = None

        # save debug images at through and end of release
        if save_buff and (current_time - bg_save_times[key] >= 0.2 or diff >= BG_DIFF_THRESHOLD):
            filename = f"assets\\test\\material\\{slot}_{int(diff)}_{datetime.now().strftime('%H_%M_%S_%f')[:-3]}.png"
            cv2.imwrite(filename, bg_img_uint8)
            bg_save_times[key] = current_time


@profile
def _phase15_swipe_sustain(window_img, templates, tick, current_time,
                           swipe_state, cooldown):
    """Phase 1.5 — Check if held swipe-strip notes should be released."""
    # Left swipe sustain
    if swipe_state["L_pressed"] and swipe_state["L_slot"] is not None:
        still_present = sustain_swipe_strip(
            window_img, templates, "left", swipe_state["L_slot"],
            TRANSFORM_SLOTS, TRANSFORM_AREA, TRANSFORM_SIZE
        )
        if not still_present:
            lkey = KEYS["left_swipe"]
            if current_time - tick(f'press_{lkey}') > cooldown:
                newpress(lkey, tick, action='up')
            swipe_state["L_pressed"] = False
            swipe_state["L_slot"] = None

    # Right swipe sustain
    if swipe_state["R_pressed"] and swipe_state["R_slot"] is not None:
        still_present = sustain_swipe_strip(
            window_img, templates, "right", swipe_state["R_slot"],
            TRANSFORM_SLOTS, TRANSFORM_AREA, TRANSFORM_SIZE
        )
        if not still_present:
            rkey = KEYS["right_swipe"]
            if current_time - tick(f'press_{rkey}') > cooldown:
                newpress(rkey, tick, action='up')
            swipe_state["R_pressed"] = False
            swipe_state["R_slot"] = None


def _handle_tap_or_cross(note, tick, current_time, holding_flags,
                         strip_start_times, recorded_bgs, cooldown, save_buff, window_img):
    """Handle tap / cross_tap note action (press-and-hold or simple tap based on strip_val)."""
    slot = note["slot"]
    key = KEYS["cross_tap"] if note["type"] == "cross_tap" else KEYS[slot]
    strip_val = note.get("strip_val", 0)

    if holding_flags[key]:
        # Slots 1-4 and full_window are released by continuous BG checking now.
        return

    hold_threshold = 15000 if note["type"] == "cross_tap" else 7500
    if strip_val > hold_threshold:
        if current_time - tick(f'press_{key}') > cooldown:
            newpress(key, tick, action='down')
            holding_flags[key] = True
            strip_start_times[key] = current_time
            recorded_bgs[key] = None
            if save_buff:
                filename = f"assets\\test\\material\\{slot}_raw_{datetime.now().strftime('%H_%M_%S_%f')[:-3]}.png"
                cv2.imwrite(filename, window_img)
    else:
        if current_time - tick(f'press_{key}') > cooldown:
            newpress(key, tick, action='tap')


def _handle_swipe(note, window_img, templates, tick, current_time,
                  swipe_state, cooldown):
    """Handle left_swipe / right_swipe note action (check for strip, then hold or tap)."""
    key = KEYS[note["type"]]

    if note["type"] == "left_swipe":
        matched_slot = check_swipe_strip(
            window_img, templates, "left",
            TRANSFORM_PRE_SLOT, TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE
        )
        if matched_slot is not None:
            lkey = KEYS["left_swipe"]
            if current_time - tick(f'press_{lkey}') > cooldown:
                newpress(lkey, tick, action='down')
                swipe_state["L_pressed"] = True
                swipe_state["L_slot"] = matched_slot
                logger.info(f"L_Strip detected in {matched_slot}, holding left shift")
        else:
            if current_time - tick(f'press_{key}') > cooldown:
                newpress(key, tick, action='tap')

    elif note["type"] == "right_swipe":
        matched_slot = check_swipe_strip(
            window_img, templates, "right",
            TRANSFORM_PRE_SLOT, TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE
        )
        if matched_slot is not None:
            rkey = KEYS["right_swipe"]
            if current_time - tick(f'press_{rkey}') > cooldown:
                newpress(rkey, tick, action='down')
                swipe_state["R_pressed"] = True
                swipe_state["R_slot"] = matched_slot
                logger.info(f"R_Strip detected in {matched_slot}, holding right shift")
        else:
            if current_time - tick(f'press_{key}') > cooldown:
                newpress(key, tick, action='tap')

    else:
        # Unknown swipe variant — just tap
        if current_time - tick(f'press_{key}') > cooldown:
            newpress(key, tick, action='tap')


@profile
def _phase23_process_notes(detected_notes, window_img, templates, tick, current_time,
                           holding_flags, strip_start_times, recorded_bgs,
                           swipe_state, cooldown, save_buff):
    """Phase 2+3 — Process detected notes: trigger key presses, holds, and swipe-strip checks."""
    for note in detected_notes:
        # if cross_tap_detected and note["type"] != "cross_tap":
        #     continue # If cross_tap is present, ignore other notes

        if note["type"] in ["tap", "cross_tap"]:
            _handle_tap_or_cross(
                note, tick, current_time, holding_flags,
                strip_start_times, recorded_bgs, cooldown, save_buff, window_img
            )
        else:
            _handle_swipe(
                note, window_img, templates, tick, current_time,
                swipe_state, cooldown
            )


# ─── Benchmark Helpers ───────────────────────────────────────────────────────

def image_saver_worker(q):
    """Background thread worker that writes images from a queue to disk."""
    while True:
        item = q.get()
        if item is None:
            break
        img, filename = item
        cv2.imwrite(filename, img)
        q.task_done()


def _setup_benchmark():
    """Create benchmark output directory and start background saver thread."""
    benchmark_dir = os.path.join("assets", "test", "benchmark", "screenshots", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(benchmark_dir, exist_ok=True)
    benchmark_queue = queue.Queue()
    threading.Thread(target=image_saver_worker, args=(benchmark_queue,), daemon=True).start()
    logger.info(f"Benchmark mode enabled. Saving screenshots to {benchmark_dir}")
    return benchmark_dir, benchmark_queue


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Automated Rhythm Game Script")
    parser.add_argument('--benchmark', action='store_true', help='Enable benchmark mode to save all frames asynchronously')
    args = parser.parse_args()

    # Benchmark setup
    benchmark_queue = None
    benchmark_dir = ""
    if args.benchmark:
        benchmark_dir, benchmark_queue = _setup_benchmark()

    # Load templates
    logger.info("Loading templates...")
    templates = load_templates()

    # Wait for game window
    logger.info(f"Waiting for game window ({PROCESS_NAME} -> {SUBPROCESS_NAME})...")
    hwnd = None
    while not hwnd:
        hwnd = find_window_by_process(PROCESS_NAME, SUBPROCESS_NAME)
        if not hwnd:
            time.sleep(1)

    logger.info(f"Found game window! Handle: {hwnd}")
    logger.info("Starting detection loop. Press Ctrl+C to stop.")

    # Initialize state
    tick = id_timer()
    COOLDOWN_TIME = 0.05  # Adjust based on game's note speed

    holding_flags, strip_start_times, recorded_bgs, bg_save_times, forzed_times, swipe_state = _init_game_state()

    for key in KEYS.values():
        tick(f'press_{key}')
        # Pre-warm the key mappings for the keyboard module hook install
        keyboard.key_to_scan_codes(key)

    save_buff = False
    os.makedirs("assets\\test\\material", exist_ok=True)

    try:
        with mss.MSS() as sct:
            while True:
                # 1. Capture the game window
                window_img = capture_window(hwnd, sct)
                if window_img is None:
                    continue

                if benchmark_queue is not None:
                    filename = os.path.join(benchmark_dir, f"running_{datetime.now().strftime('%H_%M_%S_%f')[:-3]}.png")
                    benchmark_queue.put((window_img.copy(), filename))

                current_time = time.time()

                # 2. Phase 1 — Background difference check for held notes
                _phase1_bg_diff_check(
                    window_img, templates, tick, current_time,
                    holding_flags, strip_start_times, recorded_bgs,
                    bg_save_times, forzed_times, save_buff, COOLDOWN_TIME
                )

                # 3. Phase 1.5 — Swipe-strip sustain check
                _phase15_swipe_sustain(
                    window_img, templates, tick, current_time,
                    swipe_state, COOLDOWN_TIME
                )

                # 4. Phase 2 — Detect notes (skip swipe detection if already held)
                swipe_pressed = {"left": swipe_state["L_pressed"], "right": swipe_state["R_pressed"]}
                detected = detect_notes(window_img, templates, holding_flags=holding_flags, swipe_pressed=swipe_pressed)

                # 5. Phase 2+3 — Process detections and trigger key actions
                _phase23_process_notes(
                    detected, window_img, templates, tick, current_time,
                    holding_flags, strip_start_times, recorded_bgs,
                    swipe_state, COOLDOWN_TIME, save_buff
                )

                # Small sleep to prevent 100% CPU usage, adjust based on required frame rate
                # time.sleep(0.01)

    except KeyboardInterrupt:
        logger.info("Loop stopped by user.")
        logger.stop()
        newpress(KEYS["left_swipe"], tick, action='up')
        newpress(KEYS["right_swipe"], tick, action='up')

if __name__ == "__main__":
    main()
