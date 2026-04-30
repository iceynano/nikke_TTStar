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
from module import find_window_by_process, capture_window, match_template, match_hsv_region, newpress, id_timer
from async_logger import logger
from config import (PROCESS_NAME, SUBPROCESS_NAME, KEYS, REGIONS, MATCH_THRESHOLD, STRIP_BUFFER_REGIONS,
                     BG_AREAS, BG_DIFF_THRESHOLD, HSV_PROFILES,
                     TRANSFORM_AREA, TRANSFORM_SIZE, TRANSFORM_SLOTS,
                     TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE, TRANSFORM_PRE_SLOT)

def load_templates():
    base_path = os.path.join("assets", "template")
    templates = {}
    for name in ["cross_tap", "tap", "left_swipe", "right_swipe", "strip_ignore", "L_Strip", "R_Strip"]:
        path = os.path.join(base_path, f"{name}.png")
        if os.path.exists(path):
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is not None:
                templates[name] = img
            else:
                logger.warn(f"Template {name}.png could not be loaded at {path}")
                templates[name] = None
        else:
            logger.warn(f"Template {name}.png not found at {path}")
            templates[name] = None
    return templates

def crop_region(image: np.ndarray, region: tuple):
    left, top, width, height = region
    return image[top:top + height, left:left + width]

@profile
def check_strip_color(window_img, crop_box):
    strip_img = crop_region(window_img, crop_box)
    if strip_img.size == 0: return 0
    mean_color = np.mean(strip_img, axis=(0, 1))
    mean_sq_sum = np.sum(mean_color ** 2)
    return mean_sq_sum

def _sort_area_points(area_set):
    """Sort a set of 4 corner points into order: TL, TR, BL, BR (by y then x)."""
    pts = list(area_set)
    pts.sort(key=lambda p: (p[1], p[0]))
    return np.array(pts, dtype=np.float32)

def perspective_warp(image, area_set, target_size):
    """
    Crop and perspective-warp a quadrilateral region from image into a rectangle.
    area_set: set of 4 (x, y) corner points
    target_size: (width, height) of the output rectangle
    """
    src_pts = _sort_area_points(area_set)
    width, height = target_size
    dst_pts = np.array([
        [0, 0],
        [width, 0],
        [0, height],
        [width, height]
    ], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    return cv2.warpPerspective(image, M, (width, height))

@profile
def check_swipe_strip(window_img, templates, swipe_direction, slot_config, area_set, target_size, threshold=MATCH_THRESHOLD):
    """
    Phase 3 Swipe-Strip Detection: perspective warp TRANSFORM_PRE_AREA, split into slots,
    and match L_Strip or R_Strip via HSV color detection. Returns the matched slot key or None.
    
    swipe_direction: 'left' or 'right'
    """
    template_name = "L_Strip" if swipe_direction == "left" else "R_Strip"
    hsv_profile = HSV_PROFILES.get(template_name)
    if hsv_profile is None:
        return None

    warped = perspective_warp(window_img, area_set, target_size)

    for slot_key, (x_start, x_end) in slot_config.items():
        slot_img = warped[:, x_start:x_end]
        if slot_img.size == 0:
            continue
        loc, w, h, val = match_hsv_region(
            slot_img,
            target_hsv=hsv_profile["target_hsv"],
            target_size=hsv_profile["target_size"],
            threshold=hsv_profile["threshold"],
            hue_tol=hsv_profile["hue_tol"],
            sat_range=hsv_profile["sat_range"],
            val_range=hsv_profile["val_range"],
        )
        if loc is not None:
            return slot_key  # Found a match — remember this slot and break

    return None

@profile
def sustain_swipe_strip(window_img, templates, swipe_direction, remembered_slot, slot_config, area_set, target_size, threshold=MATCH_THRESHOLD):
    """
    Phase 1.5 Swipe-Strip Sustain Check: perspective warp TRANSFORM_AREA,
    check only the remembered slot for L/R_Strip via HSV color detection.
    Returns True if the strip is still present, False if it should be released.
    """
    template_name = "L_Strip" if swipe_direction == "left" else "R_Strip"
    hsv_profile = HSV_PROFILES.get(template_name)
    if hsv_profile is None:
        return False

    warped = perspective_warp(window_img, area_set, target_size)

    x_start, x_end = slot_config.get(remembered_slot, (0, 0))
    if x_end <= x_start:
        return False

    slot_img = warped[:, x_start:x_end]
    if slot_img.size == 0:
        return False

    loc, w, h, val = match_hsv_region(
        slot_img,
        target_hsv=hsv_profile["target_hsv"],
        target_size=hsv_profile["target_size"],
        threshold=hsv_profile["threshold"],
        hue_tol=hsv_profile["hue_tol"],
        sat_range=hsv_profile["sat_range"],
        val_range=hsv_profile["val_range"],
    )
    return loc is not None

@profile
def detect_notes(window_img, templates, holding_flags=None, swipe_pressed=None):
    detected = []

    # 1. Check for cross_tap in full_window
    full_region = REGIONS["full_window"]
    cross_tap_key = KEYS["cross_tap"]
    is_cross_tap_held = holding_flags and holding_flags.get(cross_tap_key, False)
    cross_tap_detected = False
    lswipe_detected = False
    rswipe_detected = False

    if not is_cross_tap_held:
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

            detected.append({"type": "cross_tap", "slot": "full_window", "loc": abs_loc, "val": val, "w": w, "h": h, "strip_val": strip_val})
            cross_tap_detected = True

    # If cross_tap is currently held OR we just detected it, we can skip the individual slots
    # if is_cross_tap_held or cross_tap_detected:
    #     return detected

    # 2. Iterate through each slot for specific notes
    for i in range(1, 5):
        slot_key = f"slot_{i}"
        region = REGIONS.get(slot_key)
        if not region:
            continue

        slot_img = crop_region(window_img, region)

        key = KEYS[slot_key]
        if holding_flags and holding_flags.get(key, False):
            continue

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

                detected.append({"type": note_type, "slot": slot_key, "loc": abs_loc, "val": val, "w": w, "h": h, "strip_val": strip_val})
                if note_type == "left_swipe":
                    lswipe_detected = True
                if note_type == "right_swipe":
                    rswipe_detected = True
                break

    return detected

def image_saver_worker(q):
    while True:
        item = q.get()
        if item is None:
            break
        img, filename = item
        cv2.imwrite(filename, img)
        q.task_done()

def main():
    parser = argparse.ArgumentParser(description="Automated Rhythm Game Script")
    parser.add_argument('--benchmark', action='store_true', help='Enable benchmark mode to save all frames asynchronously')
    args = parser.parse_args()

    benchmark_queue = queue.Queue()
    benchmark_dir = ""
    if args.benchmark:
        benchmark_dir = os.path.join("assets", "test", "benchmark", "screenshots", datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(benchmark_dir, exist_ok=True)
        threading.Thread(target=image_saver_worker, args=(benchmark_queue,), daemon=True).start()
        logger.info(f"Benchmark mode enabled. Saving screenshots to {benchmark_dir}")

    logger.info("Loading templates...")
    templates = load_templates()

    logger.info(f"Waiting for game window ({PROCESS_NAME} -> {SUBPROCESS_NAME})...")
    hwnd = None
    while not hwnd:
        hwnd = find_window_by_process(PROCESS_NAME, SUBPROCESS_NAME)
        if not hwnd:
            time.sleep(1)

    logger.info(f"Found game window! Handle: {hwnd}")
    logger.info("Starting detection loop. Press Ctrl+C to stop.")

    tick = id_timer()

    # Adjust cooldown interval as needed based on the game's note speed
    COOLDOWN_TIME = 0.05

    holding_flags = {key: False for key in KEYS.values()}
    strip_start_times = {key: 0.0 for key in KEYS.values()}
    recorded_bgs = {key: None for key in KEYS.values()}
    bg_save_times = {key: 0.0 for key in KEYS.values()}
    forzed_times = {key: 0.0 for key in KEYS.values()}
    # Swipe-hold state (hard mode)
    L_swipe_pressed = False
    R_swipe_pressed = False
    L_remembered_slot = None  # single slot key, e.g. "slot_2"
    R_remembered_slot = None
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

                if args.benchmark:
                    filename = os.path.join(benchmark_dir, f"running_{datetime.now().strftime('%H_%M_%S_%f')[:-3]}.png")
                    benchmark_queue.put((window_img.copy(), filename))

                current_time = time.time()

                # 2. Phase 1 — Continuous Background Difference Check for slots 1-4 and full_window (cross_tap)
                for slot in ["full_window", "slot_1", "slot_2", "slot_3", "slot_4"]:
                    key = KEYS["cross_tap"] if slot == "full_window" else KEYS[slot]
                    if holding_flags[key] and (current_time - strip_start_times[key] >= 0.05):
                        offset_x, offset_y, w, h = BG_AREAS.get(slot, (0, 0, 0, 0))
                        if w > 0:
                            region = REGIONS[slot]
                            crop_box = (region[0] + offset_x, region[1] + offset_y, w, h)
                            bg_img_uint8 = crop_region(window_img, crop_box)
                            bg_img = bg_img_uint8.astype(np.int32)
                            if recorded_bgs[key] is None:
                                recorded_bgs[key] = bg_img
                            else:
                                diff = np.mean(np.abs(bg_img - recorded_bgs[key]))
                                # cross_tap has much more interference, so the threshold is higher
                                if (diff >= BG_DIFF_THRESHOLD and slot != "full_window") or (diff >= 100 and slot == "full_window"):
                                    is_ignore = False
                                    # detect strip_ignore 
                                    if templates.get("strip_ignore") is not None:
                                        ignore_loc, _, _, _ = match_template(bg_img_uint8, templates["strip_ignore"], threshold=0.9)
                                        if ignore_loc:
                                            is_ignore = True
                                            forzed_times[key] = current_time
                                            logger.info(f"SUSPECT detect ignored for {slot}")
                                    if not is_ignore and current_time - forzed_times[key] > 0.08:
                                        if current_time - tick(f'press_{key}') > COOLDOWN_TIME:
                                            newpress(key, tick, action='up')
                                            holding_flags[key] = False
                                            recorded_bgs[key] = None
                                # save debug images at through and end of release
                                if save_buff and (current_time - bg_save_times[key] >= 0.2 or diff >= BG_DIFF_THRESHOLD):
                                    filename = f"assets\\test\\material\\{slot}_{int(diff)}_{datetime.now().strftime('%H_%M_%S_%f')[:-3]}.png"
                                    cv2.imwrite(filename, bg_img_uint8)
                                    bg_save_times[key] = current_time

                # 2.5. Phase 1.5 — Swipe-Strip Sustain Check
                if L_swipe_pressed and L_remembered_slot is not None:
                    still_present = sustain_swipe_strip(
                        window_img, templates, "left", L_remembered_slot,
                        TRANSFORM_SLOTS, TRANSFORM_AREA, TRANSFORM_SIZE
                    )
                    if not still_present:
                        lkey = KEYS["left_swipe"]
                        if current_time - tick(f'press_{lkey}') > COOLDOWN_TIME:
                            newpress(lkey, tick, action='up')
                        L_swipe_pressed = False
                        L_remembered_slot = None

                if R_swipe_pressed and R_remembered_slot is not None:
                    still_present = sustain_swipe_strip(
                        window_img, templates, "right", R_remembered_slot,
                        TRANSFORM_SLOTS, TRANSFORM_AREA, TRANSFORM_SIZE
                    )
                    if not still_present:
                        rkey = KEYS["right_swipe"]
                        if current_time - tick(f'press_{rkey}') > COOLDOWN_TIME:
                            newpress(rkey, tick, action='up')
                        R_swipe_pressed = False
                        R_remembered_slot = None

                # 3. Phase 2 — Detect notes (skip swipe detection if already held)
                swipe_pressed = {"left": L_swipe_pressed, "right": R_swipe_pressed}
                detected_notes = detect_notes(window_img, templates, holding_flags=holding_flags, swipe_pressed=swipe_pressed)

                # Check if cross_tap is triggered to prevent conflicts
                cross_tap_detected = any(note["type"] == "cross_tap" for note in detected_notes)
                
                for note in detected_notes:
                    # if cross_tap_detected and note["type"] != "cross_tap":
                    #     continue # If cross_tap is present, ignore other notes

                    slot = note["slot"]
                    if note["type"] == "cross_tap":
                        key = KEYS["cross_tap"]
                    elif note["type"] == "tap":
                        key = KEYS[slot]
                    else:
                        key = KEYS[note["type"]]

                    if note["type"] in ["tap", "cross_tap"]:
                        strip_val = note.get("strip_val", 0)
                        if holding_flags[key]:
                            # Slots 1-4 and full_window are released by continuous BG checking now.
                            pass
                        else:
                            if (strip_val > 7500 and note["type"] == "tap") or (strip_val > 15000 and note["type"] == "cross_tap") :
                                if current_time - tick(f'press_{key}') > COOLDOWN_TIME:
                                    newpress(key, tick, action='down')
                                    holding_flags[key] = True
                                    strip_start_times[key] = current_time
                                    recorded_bgs[key] = None
                                    if save_buff:
                                        filename = f"assets\\test\\material\\{slot}_raw_{datetime.now().strftime('%H_%M_%S_%f')[:-3]}.png"
                                        cv2.imwrite(filename, window_img)
                            else:
                                if current_time - tick(f'press_{key}') > COOLDOWN_TIME:
                                    newpress(key, tick, action='tap')
                    else:
                        # Phase 3 — Swipe-Strip Detection for left/right swipe
                        if note["type"] == "left_swipe":
                            matched_slot = check_swipe_strip(
                                window_img, templates, "left",
                                TRANSFORM_PRE_SLOT, TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE
                            )
                            if matched_slot is not None:
                                # Strip found — hold the key
                                lkey = KEYS["left_swipe"]
                                if current_time - tick(f'press_{lkey}') > COOLDOWN_TIME:
                                    newpress(lkey, tick, action='down')
                                    L_swipe_pressed = True
                                    L_remembered_slot = matched_slot
                                    logger.info(f"L_Strip detected in {matched_slot}, holding left shift")
                            else:
                                # No strip — normal tap
                                if current_time - tick(f'press_{key}') > COOLDOWN_TIME:
                                    newpress(key, tick, action='tap')
                        elif note["type"] == "right_swipe":
                            matched_slot = check_swipe_strip(
                                window_img, templates, "right",
                                TRANSFORM_PRE_SLOT, TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE
                            )
                            if matched_slot is not None:
                                # Strip found — hold the key
                                rkey = KEYS["right_swipe"]
                                if current_time - tick(f'press_{rkey}') > COOLDOWN_TIME:
                                    newpress(rkey, tick, action='down')
                                    R_swipe_pressed = True
                                    R_remembered_slot = matched_slot
                                    logger.info(f"R_Strip detected in {matched_slot}, holding right shift")
                            else:
                                # No strip — normal tap
                                if current_time - tick(f'press_{key}') > COOLDOWN_TIME:
                                    newpress(key, tick, action='tap')
                        else:
                            if current_time - tick(f'press_{key}') > COOLDOWN_TIME:
                                newpress(key, tick, action='tap')

                # Small sleep to prevent 100% CPU usage, adjust based on required frame rate
                # time.sleep(0.01)

    except KeyboardInterrupt:
        logger.info("Loop stopped by user.")
        logger.stop()
        newpress(KEYS["left_swipe"], tick, action='up')
        newpress(KEYS["right_swipe"], tick, action='up')

if __name__ == "__main__":
    main()
