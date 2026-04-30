import cv2
import config
from async_logger import logger
import argparse
import os

def group_by_x(items, x_thresh):
    """Group items by their x-coordinate to create columns of labels."""
    if not items:
        return []
    # Sort items by their left x-coordinate
    items_sorted = sorted(items, key=lambda x: x["bbox"][0])
    
    groups = []
    current_group = [items_sorted[0]]
    
    for i in range(1, len(items_sorted)):
        prev = items_sorted[i - 1]["bbox"]
        curr = items_sorted[i]["bbox"]
        
        if curr[0] - prev[0] < x_thresh:
            current_group.append(items_sorted[i])
        else:
            groups.append(current_group)
            current_group = [items_sorted[i]]
            
    groups.append(current_group)
    return groups

def adjust_vertical(group, font_height):
    """Adjust label positions vertically within a group to avoid overlapping."""
    # Sort by top y-coordinate descending (bottom to top)
    group_sorted = sorted(group, key=lambda x: x["bbox"][1], reverse=True)
    
    adjusted = []
    for i, item in enumerate(group_sorted):
        bbox = item["bbox"]
        x, y, w, h = bbox
        
        # Initial desired y (above the box)
        new_y = y - 20
        
        if i > 0:
            prev_y = adjusted[i - 1]["new_y"]
            # If too close to the label below, push it higher
            if prev_y - new_y < font_height:
                new_y = prev_y - font_height
                
        adjusted.append({
            "item": item,
            "new_y": new_y,
            "offset_idx": i
        })
    return adjusted

def draw_smart_layout(img, items, x_thresh=40, font_scale=0.4, thickness=1):
    """Draw boxes and labels using a smart non-overlapping layout."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    # Get unified font height
    (tw, th), baseline = cv2.getTextSize("Ag", font, font_scale, thickness)
    font_height = th + baseline + 6 # Height + spacing
    
    groups = group_by_x(items, x_thresh)
    
    for group in groups:
        adjusted_group = adjust_vertical(group, font_height)
        
        for adj in adjusted_group:
            item = adj["item"]
            bbox = item["bbox"]
            label = item["label"]
            color = item["color"]
            new_y = adj["new_y"]
            offset_idx = adj["offset_idx"]
            
            x, y, w, h = bbox
            (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
            text_h = th + baseline
            
            text_x = x
            text_y = int(new_y)
            
            # Connecting line x position (slightly staggered to avoid overlapping lines)
            line_x = x + offset_idx * 4
            
            # 1. Draw the actual bounding box
            cv2.rectangle(img, (x, y), (x + w, y + h), color, 1)
            
            # 2. Draw connecting line from box top to label bottom
            cv2.line(img, (line_x, y), (line_x, text_y + text_h), (200, 200, 200), 1)
            
            # 3. Draw label background (filled rectangle)
            cv2.rectangle(img, (text_x, text_y), (text_x + tw, text_y + text_h), color, -1)
            
            # 4. Draw label text (black text for better contrast on colored BG)
            cv2.putText(img, label, (text_x, text_y + text_h - baseline), 
                        font, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)

def main():
    parser = argparse.ArgumentParser(description="Show areas defined in config.py with smart layout.")
    parser.add_argument("image", help="Path to the game window image.")
    parser.add_argument("--slot", action="store_true", help="Show REGIONS (slots)")
    parser.add_argument("--strip", action="store_true", help="Show STRIP_BUFFER_REGIONS")
    parser.add_argument("--bg", action="store_true", help="Show BG_AREAS")
    args = parser.parse_args()

    if not os.path.exists(args.image):
        logger.info(f"Error: File {args.image} not found.")
        return

    img = cv2.imread(args.image)
    if img is None:
        logger.info(f"Error: Could not read image {args.image}.")
        return

    logger.info(f"Loaded image: {args.image} ({img.shape[1]}x{img.shape[0]})")
    
    overlay = img.copy()

    # Colors (BGR)
    COLOR_REGION = (0, 255, 0)      # Green
    COLOR_STRIP = (0, 165, 255)     # Orange
    COLOR_BG = (255, 100, 0)        # Blueish

    # Collect all items to draw
    draw_items = []

    # Determine what to show
    show_all = not (args.slot or args.strip or args.bg)

    # 1. Collect main REGIONS
    if hasattr(config, 'REGIONS'):
        for name, (x, y, w, h) in config.REGIONS.items():
            if show_all or args.slot:
                draw_items.append({"bbox": (x, y, w, h), "label": name, "color": COLOR_REGION})

            # 2. Collect STRIP_BUFFER_REGIONS relative to parent
            if hasattr(config, 'STRIP_BUFFER_REGIONS') and name in config.STRIP_BUFFER_REGIONS:
                if show_all or args.strip:
                    ox, oy, bw, bh = config.STRIP_BUFFER_REGIONS[name]
                    draw_items.append({"bbox": (x + ox, y + oy, bw, bh), "label": f"{name}_strip", "color": COLOR_STRIP})

            # 3. Collect BG_AREAS relative to parent
            if hasattr(config, 'BG_AREAS') and name in config.BG_AREAS:
                if show_all or args.bg:
                    ox, oy, bw, bh = config.BG_AREAS[name]
                    draw_items.append({"bbox": (x + ox, y + oy, bw, bh), "label": f"{name}_bg", "color": COLOR_BG})

    # Perform smart drawing
    draw_smart_layout(overlay, draw_items)

    # Display results
    cv2.namedWindow("Area Config Check", cv2.WINDOW_AUTOSIZE)
    cv2.imshow("Area Config Check", overlay)
    
    logger.info("\nLegend:")
    if show_all or args.slot:
        logger.info(f"  [GREEN]  Main Regions (REGIONS)")
    if show_all or args.strip:
        logger.info(f"  [ORANGE] Strip Buffer Regions (relative)")
    if show_all or args.bg:
        logger.info(f"  [BLUE]   Background Areas (relative)")
    logger.info("\nPress any key in the image window to exit.")
    
    cv2.waitKey(0)
    cv2.destroyAllWindows()
    logger.stop()

if __name__ == "__main__":
    main()
