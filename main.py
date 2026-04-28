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
from config import PROCESS_NAME, SUBPROCESS_NAME, KEYS, REGIONS, MATCH_THRESHOLD, STRIP_BUFFER_REGIONS, BG_AREAS, BG_DIFF_THRESHOLD, HSV_PROFILES

def load_templates():
    base_path = os.path.join("assets", "template")
    templates = {}
    for name in ["cross_tap", "tap", "left_swipe", "right_swipe", "strip_ignore"]:
        path = os.path.join(base_path, f"{name}.png")
        if os.path.exists(path):
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is not None:
                templates[name] = img
            else:
                print(f"Warning: Template {name}.png could not be loaded at {path}")
                templates[name] = None
        else:
            print(f"Warning: Template {name}.png not found at {path}")
            templates[name] = None
    return templates

def crop_region(image: np.ndarray, region: tuple):
    left, top, width, height = region
    return image[top:top + height, left:left + width]

def check_strip_color(window_img, crop_box):
    strip_img = crop_region(window_img, crop_box)
    if strip_img.size == 0: return 0
    mean_color = np.mean(strip_img, axis=(0, 1))
    mean_sq_sum = np.sum(mean_color ** 2)
    return mean_sq_sum

@profile
def detect_notes(window_img, templates, holding_flags=None):
    detected = []

    # 1. Check for cross_tap in full_window
    full_region = REGIONS["full_window"]
    cross_tap_key = KEYS["cross_tap"]
    is_cross_tap_held = holding_flags and holding_flags.get(cross_tap_key, False)
    cross_tap_detected = False

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
        if loc is None and templates.get("cross_tap") is not None:
            loc, w, h, val = match_template(full_img, templates["cross_tap"], threshold=MATCH_THRESHOLD)

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
    if is_cross_tap_held or cross_tap_detected:
        return detected

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

            if templates.get(note_type) is not None:
                loc, w, h, val = match_template(slot_img, templates[note_type], threshold=MATCH_THRESHOLD)

                if loc:
                    abs_loc = (loc[0] + region[0], loc[1] + region[1])
                    strip_val = 0

                    if note_type == "tap":
                        offset_x, offset_y, bf_w, bf_h = STRIP_BUFFER_REGIONS.get(slot_key, (0, 0, 0, 0))
                        if bf_w > 0 and bf_h > 0:
                            buffer_crop_box = (region[0] + offset_x, region[1] + offset_y, bf_w, bf_h)
                            strip_val = check_strip_color(window_img, buffer_crop_box)

                    detected.append({"type": note_type, "slot": slot_key, "loc": abs_loc, "val": val, "w": w, "h": h, "strip_val": strip_val})
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
        print(f"Benchmark mode enabled. Saving screenshots to {benchmark_dir}")

    print("Loading templates...")
    templates = load_templates()

    print(f"Waiting for game window ({PROCESS_NAME} -> {SUBPROCESS_NAME})...")
    hwnd = None
    while not hwnd:
        hwnd = find_window_by_process(PROCESS_NAME, SUBPROCESS_NAME)
        if not hwnd:
            time.sleep(1)

    print(f"Found game window! Handle: {hwnd}")
    print("Starting detection loop. Press Ctrl+C to stop.")

    tick = id_timer()

    # Adjust cooldown interval as needed based on the game's note speed
    COOLDOWN_TIME = 0.15

    holding_flags = {key: False for key in KEYS.values()}
    strip_start_times = {key: 0.0 for key in KEYS.values()}
    recorded_bgs = {key: None for key in KEYS.values()}
    bg_save_times = {key: 0.0 for key in KEYS.values()}
    forzed_times = {key: 0.0 for key in KEYS.values()}
    for key in KEYS.values():
        tick(f'press_{key}')
        # Pre-warm the key mappings for the keyboard module hook install
        keyboard.key_to_scan_codes(key)
    save_buff = False

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

                # 2. Continuous Background Difference Check for slots 1-4 and full_window (cross_tap)
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
                                            print(f"SUSPECT detect ignored for {slot}")
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

                # 3. Detect notes
                detected_notes = detect_notes(window_img, templates, holding_flags=holding_flags)

                # Check if cross_tap is triggered to prevent conflicts
                cross_tap_detected = any(note["type"] == "cross_tap" for note in detected_notes)
                
                for note in detected_notes:
                    if cross_tap_detected and note["type"] != "cross_tap":
                        continue # If cross_tap is present, ignore other notes

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
                        if current_time - tick(f'press_{key}') > COOLDOWN_TIME:
                            newpress(key, tick, action='tap')

                # Small sleep to prevent 100% CPU usage, adjust based on required frame rate
                # time.sleep(0.01)

    except KeyboardInterrupt:
        print("Loop stopped by user.")

if __name__ == "__main__":
    main()
