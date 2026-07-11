import cv2
import json
import os
from tkinter import Tk, filedialog

points = {}
rois = {}
current_action = None
current_action_text = "Press a key before clicking"

def click_event(event, x, y, flags, param):
    global current_action
    if event == cv2.EVENT_LBUTTONDOWN:
        if current_action is None:
            print("⚠️ Press a key before clicking.")
            return
        # Handle point selections
        if current_action == "base":
            points["angle_base_point"] = {"x": x, "y": y}
            print(f"Base set at {x},{y}")
        elif current_action == "tip":
            points["angle_tip_point"] = {"x": x, "y": y}
            print(f"Tip set at {x},{y}")
        # Handle ROI selections for actuator only
        elif current_action.startswith("roi_actuator_"):
            rois[current_action] = {"x": x, "y": y}
            print(f"{current_action} corner set at {x},{y}")

def choose_video():
    root = Tk()
    root.withdraw()
    video_path = filedialog.askopenfilename(
        title="Select experiment video",
        filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv")]
    )
    root.destroy()
    return video_path

def resize_window_preserve_aspect(window_name, frame, max_width=1000, max_height=700):
    h, w = frame.shape[:2]
    scale = min(max_width / w, max_height / h, 1.0)
    new_w, new_h = int(w * scale), int(h * scale)
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, new_w, new_h)

def main():
    global current_action, current_action_text

    video_path = choose_video()
    if not video_path:
        print("No video selected. Exiting.")
        return

    cap = cv2.VideoCapture(video_path)
    ret, frame = cap.read()
    cap.release()

    if frame is None:
        print("Failed to read a frame from the video. Exiting.")
        return

    resize_window_preserve_aspect("Frame", frame)
    cv2.setMouseCallback("Frame", click_event)

    print("Instructions:")
    print("b = set actuator base")
    print("t = set actuator tip")
    print("a = set Actuator ROI (top-left then bottom-right)")
    print("s = save JSON and preview")
    print("q = quit without saving")

    while True:
        disp = frame.copy()

        # Draw points
        for k, v in points.items():
            cv2.circle(disp, (v["x"], v["y"]), 5, (0, 0, 255), -1)
            cv2.putText(disp, k, (v["x"]+5, v["y"]+5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)

        # Draw Actuator ROI
        if "roi_actuator_tl" in rois and "roi_actuator_br" in rois:
            tl = (rois["roi_actuator_tl"]["x"], rois["roi_actuator_tl"]["y"])
            br = (rois["roi_actuator_br"]["x"], rois["roi_actuator_br"]["y"])
            cv2.rectangle(disp, tl, br, (0, 255, 0), 2)
            cv2.putText(disp, f"Actuator ROI: {br[0]-tl[0]}x{br[1]-tl[1]}",
                        (tl[0], tl[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

        # Overlay instructions
        cv2.putText(disp, "Hotkeys: b=base, t=tip, a=actuator ROI, s=save, q=quit",
                    (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
        cv2.putText(disp, f"Current action: {current_action_text}",
                    (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,255), 2)

        cv2.imshow("Frame", disp)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('b'):
            current_action = "base"
            current_action_text = "Click actuator base"
        elif key == ord('t'):
            current_action = "tip"
            current_action_text = "Click actuator tip"
        elif key == ord('a'):
            if "roi_actuator_tl" not in rois:
                current_action = "roi_actuator_tl"
                current_action_text = "Click Actuator ROI top-left"
            else:
                current_action = "roi_actuator_br"
                current_action_text = "Click Actuator ROI bottom-right"
        elif key == ord('s'):
            break
        elif key == ord('q'):
            print("Quit without saving.")
            cv2.destroyAllWindows()
            return

    cv2.destroyAllWindows()

    # Build config dictionary (actuator-only)
    cfg = {
        "angle_base_point": points.get("angle_base_point", {}),
        "angle_tip_point": points.get("angle_tip_point", {}),
        "actuator_roi": {
            "x": rois.get("roi_actuator_tl", {}).get("x", 0),
            "y": rois.get("roi_actuator_tl", {}).get("y", 0),
            "w": abs(rois.get("roi_actuator_br", {}).get("x", 100) - rois.get("roi_actuator_tl", {}).get("x", 0)),
            "h": abs(rois.get("roi_actuator_br", {}).get("y", 100) - rois.get("roi_actuator_tl", {}).get("y", 0))
        }
    }

    # Save config next to the video
    base, ext = os.path.splitext(video_path)
    out_json = base + "_config.json"
    with open(out_json, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Saved config to {out_json}")
    print("Config values:", json.dumps(cfg, indent=2))

    # --- Preview step
    preview = frame.copy()
    if "roi_actuator_tl" in rois and "roi_actuator_br" in rois:
        tl = (rois["roi_actuator_tl"]["x"], rois["roi_actuator_tl"]["y"])
        br = (rois["roi_actuator_br"]["x"], rois["roi_actuator_br"]["y"])
        cv2.rectangle(preview, tl, br, (0, 255, 0), 2)

    resize_window_preserve_aspect("Preview", preview)
    cv2.imshow("Preview", preview)
    print("Preview window open. Press any key to close.")
    cv2.waitKey(0)
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()