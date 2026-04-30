import os
import cv2
import numpy as np
import mss
import win32gui
import time
import copy
import psutil
import win32process

import keyboard
from config import KEYS, MATCH_THRESHOLD, HSV_PROFILES
from async_logger import logger

try:
    from line_profiler import profile
except ImportError:
    profile = lambda x: x
inverse_KEYS = {v: k for k, v in KEYS.items()}

class Timer():
    last_time: float

def dcp(obj):
    return copy.deepcopy(obj)

def id_timer():
    """
    return id's last use time
    """
    timers = {}

    def timer(id, clear = False):
        if id in timers:
            last_time = timers[id]
            if clear:
                timers[id] = time.time()
            return last_time
        else:
            timers[id] = time.time()
            return 0

    return timer

def find_window_by_process(process_name, subprocess_name):
    def find_child_processes(parent_pid):
        children = []
        try:
            parent = psutil.Process(parent_pid)
            children = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            pass
        return children

    def callback(hwnd, hwnds):
        _, process_id = win32process.GetWindowThreadProcessId(hwnd)
        if process_id == pid and win32gui.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    hwnds = []
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'] == process_name:
            pid = proc.info['pid']
            child_processes = find_child_processes(pid)
            find = False
            for child in child_processes:
                if child.name() == subprocess_name:
                    pid = child.pid
                    find = True
                    break

            win32gui.EnumWindows(callback, hwnds)

            return hwnds[0] if hwnds and find else None

@profile
def capture_window(hwnd, sct=None):
    rect = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect

    monitor = {
        "top": top,
        "left": left + 7,
        "width": right - left - 14,
        "height": bottom - top - 7
    }

    if sct is None:
        with mss.mss() as temp_sct:
            sct_img = temp_sct.grab(monitor)
    else:
        sct_img = sct.grab(monitor)

    img_bgra = np.array(sct_img)
    return cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

@profile
def match_template(window_img, template_img, innerscale=True, threshold=0.7):
    res = cv2.matchTemplate(window_img, template_img, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

    if max_val >= threshold:
        return max_loc, template_img.shape[1], template_img.shape[0], max_val
    else:
        return None, None, None, max_val

@profile
def match_hsv_region(window_img, target_hsv, target_size, threshold=0.65,
                     hue_tol=8, sat_range=(100, 255), val_range=(80, 255)):
    """
    Brightness-invariant color region detector using HSV color space.

    Instead of template matching (which correlates raw pixel intensities and
    fails when brightness changes from visual effects), this algorithm:
    1. Converts the search area to HSV.
    2. Creates a binary mask of pixels whose H and S fall within tolerance
       of the target color (V/brightness is only loosely constrained).
    3. Slides a window of `target_size` across the mask and computes the
       fraction of "color-hit" pixels (hit_ratio) in each window.
    4. Returns the location with the highest hit_ratio if it exceeds
       `threshold`.

    Args:
        window_img: BGR image (the cropped search region).
        target_hsv: (H, S, V) tuple — the base color to detect.
                    H is in [0,179], S and V in [0,255] for OpenCV.
        target_size: (width, height) of the expected note region.
        threshold: Minimum hit_ratio to consider a detection valid.
        hue_tol: Tolerance around the target Hue value (+/-).
        sat_range: (min_sat, max_sat) acceptable saturation range.
        val_range: (min_val, max_val) acceptable brightness range.
                   Set wide to tolerate brightness boosts from effects.

    Returns:
        (loc, w, h, hit_ratio) — same shape as match_template's return.
        loc is None if no region exceeds the threshold.
    """
    tw, th = target_size
    img_h, img_w = window_img.shape[:2]

    if img_h < th or img_w < tw:
        return None, None, None, 0.0

    hsv = cv2.cvtColor(window_img, cv2.COLOR_BGR2HSV)

    target_h = target_hsv[0]

    # Build the inRange bounds
    # Hue in OpenCV is [0, 179]. Handle wrap-around for reds near 0/179.
    h_low = target_h - hue_tol
    h_high = target_h + hue_tol

    if h_low < 0 or h_high > 179:
        # Hue wraps around — create two masks and OR them
        lower1 = np.array([max(h_low % 180, 0), sat_range[0], val_range[0]], dtype=np.uint8)
        upper1 = np.array([179, sat_range[1], val_range[1]], dtype=np.uint8)
        lower2 = np.array([0, sat_range[0], val_range[0]], dtype=np.uint8)
        upper2 = np.array([min(h_high % 180, 179), sat_range[1], val_range[1]], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower1, upper1) | cv2.inRange(hsv, lower2, upper2)
    else:
        lower = np.array([h_low, sat_range[0], val_range[0]], dtype=np.uint8)
        upper = np.array([h_high, sat_range[1], val_range[1]], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)

    # mask is 0/255 uint8. Convert to 0/1 float32 for integral image.
    mask_f = (mask > 0).astype(np.float32)

    # Use integral image for fast sliding window sum
    integral = cv2.integral(mask_f)  # shape: (img_h+1, img_w+1)

    area = tw * th
    # Compute hit count for every possible window position using integral image
    # sum = integral[y+th, x+tw] - integral[y, x+tw] - integral[y+th, x] + integral[y, x]
    hit_counts = (
        integral[th:, tw:] - integral[:img_h - th + 1, tw:]
        - integral[th:, :img_w - tw + 1] + integral[:img_h - th + 1, :img_w - tw + 1]
    )

    hit_ratios = hit_counts / area
    max_ratio = np.max(hit_ratios)

    if max_ratio >= threshold:
        max_idx = np.unravel_index(np.argmax(hit_ratios), hit_ratios.shape)
        loc = (int(max_idx[1]), int(max_idx[0]))  # (x, y) to match OpenCV convention
        return loc, tw, th, float(max_ratio)
    else:
        return None, None, None, float(max_ratio)


@profile
def newpress(sig, tick, action='tap', mode='Noise', interval=0.15):
    """
    send press key event for specify sig
    action can be 'tap', 'down', or 'up'
    """
    delayed = False
    while time.time() - tick(f'press_{sig}') <= interval: 
        delayed = True
        continue
    
    if delayed:
        logger.warn(f"{inverse_KEYS[sig]} {action} delayed")

    if action in ['tap', 'down']:
        keyboard.press(sig)
    if action in ['tap', 'up']:
        keyboard.release(sig)
        
    tick(f'press_{sig}', True)
    if mode == 'Noise':
        logger.info(f"{inverse_KEYS[sig]} {action}ped!")


# ─── Reusable image / detection utilities ────────────────────────────────────

def load_templates():
    """Load all note template images from assets/template/."""
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
    """Crop a rectangular region from an image. region = (left, top, width, height)."""
    left, top, width, height = region
    return image[top:top + height, left:left + width]


def _sort_area_points(area_set):
    """Sort a set of 4 corner points into order: TL, TR, BL, BR (by y then x)."""
    pts = list(area_set)
    pts.sort(key=lambda p: (p[1], p[0]))
    return np.array(pts, dtype=np.float32)


@profile
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
def check_strip_color(window_img, crop_box):
    """Calculate mean squared color sum in a crop region (for long-press strip highlight detection)."""
    strip_img = crop_region(window_img, crop_box)
    if strip_img.size == 0: return 0
    mean_color = np.mean(strip_img, axis=(0, 1))
    mean_sq_sum = np.sum(mean_color ** 2)
    return mean_sq_sum


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
