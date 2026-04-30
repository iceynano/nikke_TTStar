import cv2
import numpy as np
import argparse
import os
import config
from async_logger import logger

def main():
    parser = argparse.ArgumentParser(description="Transform image area based on config settings.")
    parser.add_argument("--image", type=str, required=True, help="Path to the input image.")
    args = parser.parse_args()

    img_path = args.image
    if not os.path.exists(img_path):
        logger.info(f"Error: Image not found at {img_path}")
        return

    # Load image
    img = cv2.imread(img_path)
    if img is None:
        logger.info(f"Error: Could not decode image at {img_path}")
        return

    # TRANSFORM_AREA is a set in config.py, we need to convert it to a sorted list of points.
    # Order: TL, TR, BL, BR
    # Based on (y, x) sorting:
    # 1. Sort by y coordinate (rows)
    # 2. Within each row, sort by x coordinate (columns)
    pts = list(config.TRANSFORM_AREA)
    pts.sort(key=lambda p: (p[1], p[0]))
    
    # pts[0]: TL, pts[1]: TR, pts[2]: BL, pts[3]: BR
    src_pts = np.array(pts, dtype=np.float32)
    
    width, height = config.TRANSFORM_SIZE
    
    # Destination points mapping to the full size of the output rectangle
    dst_pts = np.array([
        [0, 0],              # Top-Left
        [width, 0],          # Top-Right
        [0, height],         # Bottom-Left
        [width, height]      # Bottom-Right
    ], dtype=np.float32)

    # Calculate transformation matrix and warp
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped = cv2.warpPerspective(img, M, (width, height))

    # Construct output path
    img_name = os.path.basename(img_path)
    img_base, _ = os.path.splitext(img_name)
    output_dir = os.path.join("assets", "test", "material")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    output_path = os.path.join(output_dir, f"{img_base}_transted.png")

    # Save the result
    cv2.imwrite(output_path, warped)
    logger.info(f"Successfully transformed area from {img_path}")
    logger.info(f"Source points: {pts}")
    logger.info(f"Target size: {width}x{height}")
    logger.info(f"Saved to: {output_path}")
    logger.stop()

if __name__ == "__main__":
    main()
