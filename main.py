"""Motion-controlled interface for Subway Surfers on Android TV.

This script captures a video stream from a smartphone running an IP Webcam
app, tracks the player's body with Google's MediaPipe Pose Landmarker, and
translates physical movements into ADB key events that are sent wirelessly to
an Android TV.

Detected actions:
    * Move Left  -> DPAD_LEFT
    * Move Right -> DPAD_RIGHT
    * Jump       -> DPAD_UP
    * Duck/Slide -> DPAD_DOWN

Run with:  python main.py
Press 'q' in the preview window to quit, or 'r' to re-calibrate.
"""

import subprocess
import time

import cv2

# MediaPipe's top-level ``mp.solutions`` attribute is populated lazily and, on
# some builds/platforms, is not available until the submodules are imported
# directly. Importing them explicitly avoids the common
# "module 'mediapipe' has no attribute 'solutions'" error. If MediaPipe itself
# is missing or broken (e.g. installed under an unsupported Python version),
# we raise a clear, actionable message instead of a cryptic traceback.
try:
    from mediapipe.python.solutions import drawing_styles as mp_drawing_styles
    from mediapipe.python.solutions import drawing_utils as mp_drawing
    from mediapipe.python.solutions import pose as mp_pose
except (ImportError, AttributeError) as exc:  # pragma: no cover - env issue
    import sys

    raise SystemExit(
        "Failed to load MediaPipe Pose.\n"
        f"  Underlying error: {exc}\n\n"
        "Fixes:\n"
        "  1. Install it:  pip install -r requirements.txt\n"
        "  2. MediaPipe ships wheels for Python 3.8-3.11 (and some 3.12). "
        f"You are on Python {sys.version.split()[0]}. If that is 3.13+, "
        "create a venv with a supported version, e.g.:\n"
        "       python3.11 -m venv .venv && source .venv/bin/activate\n"
        "       pip install -r requirements.txt\n"
        "  3. Make sure no file named 'mediapipe.py' exists in this folder."
    )

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
# Change these to match your setup. The camera URL comes from the IP Webcam
# app on your phone; the TV address is the one you used with `adb connect`.

# IP Webcam stream (note the trailing "/video"). Use 0 for a local webcam.
CAMERA_URL = "http://192.168.1.100:8080/video"

# Android TV address as seen by ADB, e.g. "192.168.1.50:5555".
# Leave as None to send to the only / default connected device.
ANDROID_TV_ADDRESS = "192.168.1.50:5555"

# ---------------------------------------------------------------------------
# CALIBRATION & SENSITIVITY THRESHOLDS
# ---------------------------------------------------------------------------
# All thresholds are expressed as a fraction (0.0 - 1.0) of the frame
# dimensions, so they stay consistent regardless of camera resolution.
#
# The script captures a neutral "standing" pose during calibration and then
# compares your live position against it. Increase a threshold to make an
# action harder to trigger (less sensitive); decrease it to make it easier.

# How far (fraction of frame height) your reference point must rise above the
# calibrated neutral position to count as a JUMP.
JUMP_THRESHOLD = 0.06

# How far it must drop below neutral to count as a DUCK / SLIDE.
DUCK_THRESHOLD = 0.08

# How far (fraction of frame width) you must lean left/right of neutral to
# trigger a horizontal move.
HORIZONTAL_THRESHOLD = 0.08

# Minimum seconds between two commands of ANY kind (global rate limit).
GLOBAL_COOLDOWN = 0.35

# Minimum seconds before the SAME action can fire again (debounce). This stops
# one sustained movement from spamming the TV with repeated key events.
PER_ACTION_COOLDOWN = 0.70

# Minimum landmark visibility (0-1) required to trust a detection.
MIN_VISIBILITY = 0.6

# Number of frames to average while calibrating the neutral pose.
CALIBRATION_FRAMES = 30

# ---------------------------------------------------------------------------
# ADB KEY EVENT MAPPING
# ---------------------------------------------------------------------------
# Android key event codes for the D-pad directions.
KEYEVENTS = {
    "LEFT": 21,   # KEYCODE_DPAD_LEFT
    "RIGHT": 22,  # KEYCODE_DPAD_RIGHT
    "JUMP": 19,   # KEYCODE_DPAD_UP
    "DUCK": 20,   # KEYCODE_DPAD_DOWN
}

# On-screen colours (BGR) for each action's text indicator.
ACTION_COLORS = {
    "LEFT": (0, 255, 255),
    "RIGHT": (0, 255, 255),
    "JUMP": (0, 255, 0),
    "DUCK": (0, 165, 255),
}

# ---------------------------------------------------------------------------
# ADB HELPERS
# ---------------------------------------------------------------------------
def build_adb_command(args):
    """Return an ADB command list, targeting a specific device if configured."""
    cmd = ["adb"]
    if ANDROID_TV_ADDRESS:
        cmd += ["-s", ANDROID_TV_ADDRESS]
    return cmd + args


def check_adb_connection():
    """Verify ADB is installed and (optionally) that the TV is connected.

    Returns True if we are confident a device is reachable, False otherwise.
    The script still runs if this fails so you can debug the camera/pose side
    without a TV attached, but key events obviously will not land.
    """
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        print("[ADB] 'adb' not found on PATH. Install Android platform-tools.")
        return False
    except subprocess.SubprocessError as exc:
        print(f"[ADB] Could not query devices: {exc}")
        return False

    print("[ADB] Connected devices:\n" + result.stdout.strip())

    # Count lines that report a device in the "device" state.
    online = [
        line
        for line in result.stdout.splitlines()[1:]
        if line.strip() and line.strip().endswith("device")
    ]
    if not online:
        print(
            "[ADB] No devices online. Run 'adb connect <TV_IP>:5555' first. "
            "Continuing anyway so you can test motion detection."
        )
        return False

    if ANDROID_TV_ADDRESS and not any(
        line.startswith(ANDROID_TV_ADDRESS) for line in online
    ):
        print(
            f"[ADB] Target {ANDROID_TV_ADDRESS} is not in the online list. "
            "Check the address or run 'adb connect' again."
        )
        return False

    return True


def send_keyevent(action):
    """Send a single ADB key event for the given action name.

    Runs without blocking the capture loop for long; failures are logged but
    never crash the program.
    """
    keycode = KEYEVENTS.get(action)
    if keycode is None:
        return
    cmd = build_adb_command(["shell", "input", "keyevent", str(keycode)])
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except subprocess.SubprocessError as exc:
        print(f"[ADB] Failed to send {action}: {exc}")


# ---------------------------------------------------------------------------
# MOTION DETECTION
# ---------------------------------------------------------------------------
class ActionDebouncer:
    """Rate-limits actions so one physical motion fires at most one command."""

    def __init__(self, global_cooldown, per_action_cooldown):
        self.global_cooldown = global_cooldown
        self.per_action_cooldown = per_action_cooldown
        self._last_global = 0.0
        self._last_by_action = {}

    def allow(self, action):
        """Return True if `action` may fire now, updating the timers if so."""
        now = time.monotonic()
        if now - self._last_global < self.global_cooldown:
            return False
        last = self._last_by_action.get(action, 0.0)
        if now - last < self.per_action_cooldown:
            return False
        self._last_global = now
        self._last_by_action[action] = now
        return True


def get_reference_point(landmarks):
    """Return the (x, y) tracking point and whether it is reliable.

    We use the midpoint between the two shoulders as the primary reference:
    it is stable, central, and moves clearly for all four gestures. The nose
    is used as a fallback if the shoulders are not visible.
    """
    left_shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
    right_shoulder = landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]

    if (
        left_shoulder.visibility >= MIN_VISIBILITY
        and right_shoulder.visibility >= MIN_VISIBILITY
    ):
        x = (left_shoulder.x + right_shoulder.x) / 2.0
        y = (left_shoulder.y + right_shoulder.y) / 2.0
        return (x, y), True

    nose = landmarks[mp_pose.PoseLandmark.NOSE.value]
    if nose.visibility >= MIN_VISIBILITY:
        return (nose.x, nose.y), True

    return (0.0, 0.0), False


def classify_motion(point, neutral):
    """Compare the live point to the calibrated neutral pose.

    Returns one of "JUMP", "DUCK", "LEFT", "RIGHT", or None. Vertical motion
    is checked before horizontal so that a clear jump/duck is never masked by
    a small sideways drift.
    """
    dx = point[0] - neutral[0]
    dy = point[1] - neutral[1]  # image y grows downward

    # Vertical first (MediaPipe y increases downward, so rising = negative dy).
    if -dy > JUMP_THRESHOLD:
        return "JUMP"
    if dy > DUCK_THRESHOLD:
        return "DUCK"

    # Horizontal. The camera faces you, so leaning to YOUR left increases x in
    # the (mirrored) frame; we mirror the frame below so controls feel natural.
    if dx > HORIZONTAL_THRESHOLD:
        return "RIGHT"
    if -dx > HORIZONTAL_THRESHOLD:
        return "LEFT"

    return None


# ---------------------------------------------------------------------------
# OVERLAY DRAWING
# ---------------------------------------------------------------------------
def draw_overlay(frame, action, calibrating, neutral, fps):
    """Draw status text and calibration guides on the frame."""
    h, w = frame.shape[:2]

    if calibrating:
        cv2.putText(
            frame,
            "CALIBRATING - stand still in a neutral pose",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
    else:
        label = action if action else "---"
        color = ACTION_COLORS.get(action, (200, 200, 200))
        cv2.putText(
            frame,
            f"ACTION: {label}",
            (20, 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.1,
            color,
            3,
            cv2.LINE_AA,
        )

        # Draw the calibrated neutral position as a crosshair.
        if neutral is not None:
            nx, ny = int(neutral[0] * w), int(neutral[1] * h)
            cv2.drawMarker(
                frame, (nx, ny), (255, 255, 255), cv2.MARKER_CROSS, 20, 1
            )

    cv2.putText(
        frame,
        f"{fps:4.1f} FPS   [q] quit   [r] recalibrate",
        (20, h - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )
    return frame


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
def main():
    print("[INIT] Checking ADB connection...")
    check_adb_connection()

    print(f"[INIT] Opening camera stream: {CAMERA_URL}")
    cap = cv2.VideoCapture(CAMERA_URL)
    if not cap.isOpened():
        print(
            "[ERROR] Could not open the camera stream. Check CAMERA_URL, that "
            "the IP Webcam app is running, and that both devices share a "
            "network."
        )
        return

    debouncer = ActionDebouncer(GLOBAL_COOLDOWN, PER_ACTION_COOLDOWN)

    # Calibration state.
    neutral = None
    calib_samples = []
    calibrating = True

    prev_time = time.monotonic()
    fps = 0.0

    with mp_pose.Pose(
        model_complexity=1,
        smooth_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as pose:
        print("[RUN] Starting. Stand still to calibrate your neutral pose.")
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[WARN] Dropped frame from stream; retrying...")
                time.sleep(0.05)
                continue

            # Mirror the frame so moving to YOUR right moves right on screen.
            frame = cv2.flip(frame, 1)

            # MediaPipe expects RGB.
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            results = pose.process(rgb)

            action = None
            if results.pose_landmarks:
                mp_drawing.draw_landmarks(
                    frame,
                    results.pose_landmarks,
                    mp_pose.POSE_CONNECTIONS,
                    landmark_drawing_spec=mp_drawing_styles
                    .get_default_pose_landmarks_style(),
                )

                point, reliable = get_reference_point(
                    results.pose_landmarks.landmark
                )

                if reliable:
                    if calibrating:
                        calib_samples.append(point)
                        if len(calib_samples) >= CALIBRATION_FRAMES:
                            avg_x = sum(p[0] for p in calib_samples) / len(
                                calib_samples
                            )
                            avg_y = sum(p[1] for p in calib_samples) / len(
                                calib_samples
                            )
                            neutral = (avg_x, avg_y)
                            calibrating = False
                            print(
                                f"[CALIB] Neutral pose set at "
                                f"({neutral[0]:.3f}, {neutral[1]:.3f})."
                            )
                    else:
                        action = classify_motion(point, neutral)
                        if action and debouncer.allow(action):
                            print(f"[ACTION] {action}")
                            send_keyevent(action)

            # FPS (exponential moving average).
            now = time.monotonic()
            dt = now - prev_time
            prev_time = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)

            draw_overlay(frame, action, calibrating, neutral, fps)
            cv2.imshow("Subway Surfers Motion Control", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                print("[CALIB] Re-calibrating...")
                calibrating = True
                calib_samples = []
                neutral = None

    cap.release()
    cv2.destroyAllWindows()
    print("[EXIT] Stopped.")


if __name__ == "__main__":
    main()
