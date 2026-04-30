import argparse
import subprocess
import json
import time
import cv2
import numpy as np

from main import detect_notes
from module import load_templates, crop_region, check_swipe_strip
from config import (TEST_GAME_WINDOW_REGION, TRANSFORM_PRE_SLOT, 
                    TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE)
from async_logger import logger

def log_results(notes, prefix=""):
    if not notes:
        logger.info(f"{prefix}No notes detected.")
    else:
        for note in notes:
            info = f"{prefix}Found [{note['type']}] in [{note['slot']}] at {note['loc']} (Conf: {note['val']:.3f}, Strip: {note['strip_val']:.0f})"
            if "swipe_strip" in note:
                info += f" [SWIPE_STRIP: {note['swipe_strip']}]"
            logger.info(info)

def test_image(image_path, templates, args):
    logger.info(f"Testing Image: {image_path}")
    try:
        img = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Image could not be loaded")
    except Exception as e:
        logger.info(f"Error loading image: {e}")
        return

    game_window_img = crop_region(img, TEST_GAME_WINDOW_REGION)
    st = time.time()
    notes = detect_notes(game_window_img, templates)
    
    # Phase 3 — Swipe-Strip Detection for test
    for note in notes:
        if note["type"] == "left_swipe":
            matched_slot = check_swipe_strip(
                game_window_img, templates, "left",
                TRANSFORM_PRE_SLOT, TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE
            )
            if matched_slot:
                note["swipe_strip"] = matched_slot
        elif note["type"] == "right_swipe":
            matched_slot = check_swipe_strip(
                game_window_img, templates, "right",
                TRANSFORM_PRE_SLOT, TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE
            )
            if matched_slot:
                note["swipe_strip"] = matched_slot

    ed = time.time()
    logger.info(f"Detection Time: {(ed - st) * 1000:.2f} ms")
    log_results(notes, "  - ")
    
    # Visual preview
    preview_img = game_window_img.copy()
    for note in notes:
        x, y = note["loc"]
        w, h = note["w"], note["h"]
        label = f"{note['type']} ({note['val']:.2f})"
        strip_val = note.get("strip_val", 0)
        if strip_val > 0:
            label += f" [STRIP:{strip_val:.0f}]"
        
        if "swipe_strip" in note:
            label += f" [S_STRIP:{note['swipe_strip']}]"

        cv2.rectangle(preview_img, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(preview_img, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
    cv2.imshow("Preview - Press any key to close", preview_img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

def get_video_info(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", 
        "-show_entries", "stream=width,height", "-of", "json", video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        return info["streams"][0]["width"], info["streams"][0]["height"]
    except Exception as e:
        logger.info(f"Error getting video info with ffprobe: {e}")
        return None, None

def test_video(video_path, interval, templates, args):
    logger.info(f"Testing Video: {video_path} (Extracting every {interval} frames via ffmpeg pipe)")
    width, height = get_video_info(video_path)
    if not width or not height:
        return

    frame_size = width * height * 3

    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vf", f"select='not(mod(n,{interval}))'",
        "-vsync", "vfr",  # Older ffmpeg support, drops duplicates
        "-f", "image2pipe",
        "-pix_fmt", "bgr24",
        "-vcodec", "rawvideo",
        "-"
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logger.info("Error: ffmpeg not found. Please ensure ffmpeg is installed and in your PATH.")
        return

    frame_count = 0
    extracted_idx = 0
    
    while True:
        raw_frame = process.stdout.read(frame_size)
        if len(raw_frame) != frame_size:
            break

        frame_count += 1
        extracted_idx = (frame_count - 1) * interval

        image = np.frombuffer(raw_frame, dtype=np.uint8).reshape((height, width, 3))
        game_window_img = crop_region(image, TEST_GAME_WINDOW_REGION)

        st = time.time()
        notes = detect_notes(game_window_img, templates)
        
        # Phase 3 — Swipe-Strip Detection for test
        for note in notes:
            if note["type"] == "left_swipe":
                matched_slot = check_swipe_strip(
                    game_window_img, templates, "left",
                    TRANSFORM_PRE_SLOT, TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE
                )
                if matched_slot:
                    note["swipe_strip"] = matched_slot
            elif note["type"] == "right_swipe":
                matched_slot = check_swipe_strip(
                    game_window_img, templates, "right",
                    TRANSFORM_PRE_SLOT, TRANSFORM_PRE_AREA, TRANSFORM_PRE_SIZE
                )
                if matched_slot:
                    note["swipe_strip"] = matched_slot

        ed = time.time()

        logger.info(f"--- Frame {extracted_idx} ({(ed-st)*1000:.2f}ms) ---")
        log_results(notes, "  ")

    process.wait()
    logger.info("Video processing complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Rhythm Game Automation Detection")
    parser.add_argument("--image", type=str, help="Path to a screenshot image to test")
    parser.add_argument("--video", type=str, help="Path to a video file to test")
    parser.add_argument("--interval", type=int, default=10, help="Frame extraction interval for video (default: 10)")
    args = parser.parse_args()

    if not args.image and not args.video:
        logger.info("Please provide either --image or --video argument.")
        exit(1)

    logger.info("Loading templates...")
    templates = load_templates()

    if args.image:
        test_image(args.image, templates, args)

    if args.video:
        test_video(args.video, args.interval, templates, args)
