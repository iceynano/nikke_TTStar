# config.py

PROCESS_NAME = "nikke_launcher.exe"
SUBPROCESS_NAME = 'nikke.exe'

# Key mappings for the tracks
# Use 'left shift' and 'right shift' for the keyboard module
KEYS = {
    "slot_1": "a",
    "slot_2": "d",
    "slot_3": ";",
    "slot_4": "'",
    "cross_tap": "space",
    "left_swipe": "left shift",
    "right_swipe": "right shift", 
}

# Regions: (left, top, width, height)
# All coordinates are relative to the game window's top-left corner (after capture_window)
# Please update these hardcoded placeholders according to your game window layout
# Region is used to detect notes. Template matching happens here.
REGIONS = {
    "full_window": (50, 610, 430, 105), # General region for overall scanning if needed
    "slot_1": (30, 620, 120, 100),
    "slot_2": (155, 620, 110, 100),
    "slot_3": (270, 620, 100, 100),
    "slot_4": (375, 620, 100, 100),
}

# Strip buffer regions for each slot relative to the slot's top-left corner: (offset_x, offset_y, bf_w, bf_h)
# Adjust offset_x and offset_y so the buffer area sits exactly where the long-press strip appears.
# Strip area is used to calculate *highlight* effect(colored area moving along the strip) for long note press.
STRIP_BUFFER_REGIONS = {
    "full_window": (50, -30, 60, 20), # Buffer region for cross_tap strip
    "slot_1": (50, -20, 60, 20),
    "slot_2": (40, -20, 60, 20),
    "slot_3": (0, -20, 60, 20),
    "slot_4": (0, -20, 60, 20),
}

# Template matching threshold
MATCH_THRESHOLD = 0.95

# --- TEST MODE SETTINGS ---
# For testing with full screen images/videos, specify where the game window is located.
# (left, top, width, height)
TEST_GAME_WINDOW_REGION = (1085, 25, 537, 983)
TEST_GAME_WINDOW_REGION = (0, 0, 537, 983)

# Background areas for background difference long note release.
# Format: (offset_x, offset_y, w, h) relative to the slot's top-left corner.
BG_AREAS = {
    "full_window": (55, -30, 60, 30),
    "slot_1": (55, -30, 70, 40),
    "slot_2": (30, -20, 70, 40),
    "slot_3": (0, -20, 50, 30),
    "slot_4": (0, -20, 60, 30),
}

# mid-line: 268*2=536
TRANSFORM_AREA = {(50, 600), (486, 600), (41, 620), (495, 620)} # (top-left, top-right, bottom-left, bottom-right)
TRANSFORM_SIZE = (436, 20)
TRANSFORM_PRE_AREA = {(69, 560), (467, 560), (60, 580), (476, 580)}
TRANSFORM_PRE_SIZE = (398, 20)

TRANSFORM_SLOTS = {
    "slot_1": (0, 100),
    "slot_2": (140, 200),
    "slot_3": (240, 300),
    "slot_4": (345, 436),
}
TRANSFORM_PRE_SLOT = {
    "slot_1": (0, 90),
    "slot_2": (125, 175),
    "slot_3": (240, 290),
    "slot_4": (315, 398),
}

# Threshold for background difference mean absolute error.
BG_DIFF_THRESHOLD = 75.0

# HSV color profiles for brightness-invariant detection.
# Each entry: {
#   "target_hsv": (H, S, V) — the base color in OpenCV HSV space (H: 0-179, S/V: 0-255),
#   "target_size": (width, height) — expected note region size in pixels,
#   "threshold": minimum hit_ratio (fraction of matching pixels) to trigger detection,
#   "hue_tol": +/- tolerance on the Hue channel,
#   "sat_range": (min_sat, max_sat) acceptable saturation range,
#   "val_range": (min_val, max_val) acceptable brightness range (set wide to survive effects),
# }
# Values derived from cross_tap.png template analysis: H≈22(21-24), S≈201(180-207), V≈202(197-215)
HSV_PROFILES = {
    "cross_tap": {
        "target_hsv": (22, 201, 202),
        "target_size": (88, 24),      # matches cross_tap.png dimensions
        "threshold": 0.85,            # fraction of pixels that must match
        "hue_tol": 10,                # H range: 12-32 (widened to handle hue shift under clipping)
        "sat_range": (180, 255),       # saturation drops when brightness maxes out, so keep floor low
        "val_range": (180, 255),       # WIDE range — this is what makes it immune to brightness boost
    },
}
