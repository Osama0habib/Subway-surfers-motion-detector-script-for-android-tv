# 🏃 Subway Surfers Motion Controller for Android TV

Play **Subway Surfers** on your Android TV using your whole body. This project
turns a spare smartphone into a motion-tracking camera: it streams video over
your Wi-Fi network, uses Google's **MediaPipe Pose** model to track your body
in real time, and sends the matching navigation commands to the TV wirelessly
over **ADB**.

Jump to leap over barriers, crouch to slide under them, and lean left or right
to switch lanes — no controller required.

---

## 🎥 How It Works

```
┌──────────────┐   Wi-Fi video    ┌─────────────────┐   Wi-Fi ADB     ┌──────────────┐
│  Smartphone  │  ───────────────▶│   Host Machine  │  ──────────────▶│  Android TV  │
│ (IP Webcam)  │   MJPEG stream   │ (Python + CV +  │   keyevents     │ Subway Surfers│
│              │                  │   MediaPipe)    │                 │              │
└──────────────┘                  └─────────────────┘                 └──────────────┘
```

1. The phone runs an **IP Webcam** app and exposes a stream at
   `http://<PHONE_IP>:<PORT>/video`.
2. The host machine (laptop/PC) reads that stream with OpenCV and runs
   MediaPipe Pose to find your joints.
3. Movement of your shoulders/nose relative to a calibrated "neutral" pose is
   classified into one of four actions.
4. Each action is sent to the TV as an ADB D-pad key event.

| Physical action        | Detected as | ADB key event        |
| ---------------------- | ----------- | -------------------- |
| Jump up                | `JUMP`      | `KEYCODE_DPAD_UP`    |
| Crouch / squat down    | `DUCK`      | `KEYCODE_DPAD_DOWN`  |
| Lean to your left      | `LEFT`      | `KEYCODE_DPAD_LEFT`  |
| Lean to your right     | `RIGHT`     | `KEYCODE_DPAD_RIGHT` |

---

## ✅ Prerequisites

### 1. Host machine (laptop / PC)
- **Python 3.8 – 3.11** (MediaPipe wheels are published for these versions).
- **ADB (Android Debug Bridge)** installed and on your `PATH`.
  - **Windows:** download
    [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools),
    unzip, and add the folder to your `PATH`.
  - **macOS:** `brew install android-platform-tools`
  - **Linux (Debian/Ubuntu):** `sudo apt install adb`
  - Verify with: `adb version`

### 2. Android TV
- Open **Settings → Device Preferences → About** and click **Build** seven
  times to unlock **Developer options**.
- In **Developer options**, enable:
  - **USB debugging**
  - **Wireless debugging** (sometimes labelled *Network debugging* / *ADB over
    network*).
- Note the TV's **local IP address** (Settings → Network & Internet → your
  Wi-Fi → IP address). It usually looks like `192.168.1.50`.

### 3. Smartphone (the camera)
- Install an **IP Webcam** style app:
  - Android: *IP Webcam* by Pavel Khlebovich.
  - iOS: *DroidCam*, *iVCam*, or any app that serves an MJPEG `/video` stream.
- Start the server in the app and note the URL it displays, e.g.
  `http://192.168.1.100:8080`. The stream path is usually
  `http://192.168.1.100:8080/video`.

> ⚠️ **All three devices (phone, host, TV) must be on the same Wi-Fi network.**

---

## 🚀 Setup Guide

### Step 1 — Clone and install dependencies

```bash
git clone https://github.com/osama0habib/subway-surfers-motion-detector-script-for-android-tv.git
cd subway-surfers-motion-detector-script-for-android-tv

# (recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Step 2 — Connect ADB to the Android TV

With wireless debugging enabled on the TV, from the host machine run:

```bash
adb connect 192.168.1.50:5555
```

Replace `192.168.1.50` with your TV's IP. The default ADB port is `5555`;
some TVs show a different pairing port under *Wireless debugging* — use that if
`5555` fails. Confirm the connection:

```bash
adb devices
# List of devices attached
# 192.168.1.50:5555   device
```

If the TV prompts you to **Allow debugging**, accept it (and tick *Always
allow* so you don't have to repeat this).

### Step 3 — Point the phone camera at yourself

Start the IP Webcam app on your phone and prop it up so it can see your whole
upper body (ideally your head down to your hips). Stand ~1.5–2 m away.

### Step 4 — Configure the script

Open `main.py` and edit the two settings near the top:

```python
CAMERA_URL = "http://192.168.1.100:8080/video"   # your phone's stream
ANDROID_TV_ADDRESS = "192.168.1.50:5555"          # your TV, or None for default
```

### Step 5 — Run it

```bash
python main.py
```

A preview window opens showing the camera feed with the tracked skeleton.
**Stand still in a relaxed, upright pose for a second** — the script averages
your position to set a "neutral" baseline (you'll see a `CALIBRATING` banner,
then a crosshair marking your neutral point).

- Press **`r`** at any time to re-calibrate (useful if you move the camera or
  shift position).
- Press **`q`** to quit.

---

## 🎮 How to Play

Once calibrated, make clear, deliberate movements from your neutral standing
pose:

| Action      | What to do                                                        |
| ----------- | ----------------------------------------------------------------- |
| **Jump** ⬆️ | Hop or stand up tall so your shoulders rise above neutral.        |
| **Duck** ⬇️ | Squat or crouch down so your shoulders drop below neutral.        |
| **Left** ⬅️ | Lean / step to **your** left.                                     |
| **Right** ➡️| Lean / step to **your** right.                                    |

The feed is mirrored, so moving to *your* right moves you right on the TV —
just like looking in a mirror. The current action is shown in large text on the
preview window.

> Return to the neutral pose between moves. A built-in **cooldown** stops a
> single sustained movement from firing the same command repeatedly.

---

## 🔧 Calibration & Tuning

All sensitivity settings live at the top of `main.py` and are expressed as a
fraction of the frame size, so they work at any resolution:

| Variable               | Meaning                                                      | Tip |
| ---------------------- | ------------------------------------------------------------ | --- |
| `JUMP_THRESHOLD`       | How far you must rise above neutral to jump.                 | Lower = easier to trigger. |
| `DUCK_THRESHOLD`       | How far you must drop below neutral to duck.                 | Lower = easier to trigger. |
| `HORIZONTAL_THRESHOLD` | How far you must lean sideways to switch lanes.              | Lower = more sensitive. |
| `GLOBAL_COOLDOWN`      | Minimum seconds between *any* two commands.                  | Raise if the game feels twitchy. |
| `PER_ACTION_COOLDOWN`  | Minimum seconds before the *same* action repeats (debounce). | Raise to stop command spam. |
| `MIN_VISIBILITY`       | Confidence needed to trust a landmark.                       | Raise in busy backgrounds. |
| `CALIBRATION_FRAMES`   | Frames averaged when setting the neutral pose.               | Raise for a steadier baseline. |

If actions trigger too easily, **increase** the relevant threshold. If they
don't trigger at all, **decrease** it or move closer to the camera.

---

## 🩺 Troubleshooting

| Problem | Likely fix |
| ------- | ---------- |
| `Could not open the camera stream` | Check `CAMERA_URL`, that the IP Webcam app is running, and that phone + host share a network. Open the URL in a browser to confirm it works. |
| `'adb' not found on PATH` | Install Android platform-tools and reopen your terminal. |
| `adb devices` shows nothing / `offline` | Re-run `adb connect <TV_IP>:5555`; accept the debugging prompt on the TV; make sure Wireless debugging is still on (it can time out). |
| Commands fire but the game ignores them | Make sure Subway Surfers is focused on the TV and that it responds to D-pad navigation. |
| Laggy / low FPS | Lower the phone stream resolution in the IP Webcam app, or set `model_complexity=0` in `main.py`. |
| Jittery detection | Improve lighting, use a plain background, and raise `MIN_VISIBILITY`. |

---

## 📁 Project Structure

```
.
├── main.py            # The motion controller
├── requirements.txt   # Python dependencies
├── .gitignore
└── README.md
```

---

## ⚠️ Disclaimer

This is a hobby/educational project. It sends standard D-pad navigation events
to a device you own and control via ADB; it does not modify or interact with
the Subway Surfers game internals. Use it on your own devices and network.

## 📜 License

Released under the [MIT License](LICENSE).
