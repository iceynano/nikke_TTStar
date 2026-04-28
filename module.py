import cv2
import numpy as np
import mss
import pyautogui
import win32gui
import time
import copy
import psutil
import win32process

import keyboard
from config import KEYS
from datetime import datetime

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
        print(f">>>>>>>>>>{inverse_KEYS[sig]} {action} delayed<<<<<<<<<<")

    if action in ['tap', 'down']:
        keyboard.press(sig)
    if action in ['tap', 'up']:
        keyboard.release(sig)
        
    tick(f'press_{sig}', True)
    if mode == 'Noise':
        print(f"{inverse_KEYS[sig]} {action}ped! Time: {datetime.now().strftime('%H_%M_%S_%f')[:-3]}")
