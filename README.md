

# 🛡️ Smart Security IoT System — Sensor-Fusion Intruder Alert

**SRTA 3353 — Machine Learning for IoT · Project 2 · Academic Session 2025/2026**

A real-time, edge-computing intruder alert system that fuses three independent
sensors — motion, sound, and vision — on a Raspberry Pi. Sensor readings are
processed on-device with two machine-learning models, combined through a
multi-pathway decision engine, and streamed over MQTT to a live monitoring
dashboard.

---

## Overview

The system continuously watches a space using three sensors and raises an alarm the
instant *any* of three independent detection pathways is triggered. Because the
pathways are independent, a single failed or fooled sensor does not blind the system
— sound can catch what the camera misses, and vice versa.

On an alarm it sounds a buzzer, lights an LED, captures an annotated camera
snapshot, and publishes a structured alert over MQTT. In parallel it publishes
continuous telemetry once per second so a dashboard can show live monitoring even
when nothing is wrong.

---

## How it works — the three detection pathways

An alarm fires when **any** of the following conditions is true:

| # | Pathway | Trigger condition |
|---|---------|-------------------|
| 1 | **Camera** | Human detected with confidence > 80% **and** inside the defined area-of-interest (tripwire zone) |
| 2 | **Audio anomaly** | Sound-anomaly z-score > 2.0 (the sound is statistically far from the recent ambient baseline) |
| 3 | **Motion + Loudness** | PIR motion active **and** sound level > 80 dB |

### Data handling / preprocessing
- **Audio:** rolling 1-second window, downsampling, quantization, softmax over class
  probabilities, and a running mean/standard-deviation **z-score** to flag anomalies
  relative to the room's recent baseline.
- **Camera:** frame resize + normalization, ONNX inference, confidence thresholding,
  and an intersection test of the human bounding box against the area-of-interest.
- **PIR:** a software "decay" timer holds motion as active for a few seconds after a
  pulse, smoothing the raw sensor's flicker.

---

## System architecture / data flow

```
 PIR  ┐
 Mic  ┼─►  Raspberry Pi  ──►  on-device inference  ──►  decision engine  ──►  MQTT publish
 Cam  ┘    (sensor read)      • MobileNetV2 (audio)     (3 pathways)          • telemetry  ~1/s
                              • YOLO11n   (camera)                            • alarm  (on trip)
                                                                                    │
                              buzzer + LED + saved snapshot  ◄── on alarm           ▼
                                                                       Public MQTT broker
                                                                       (broker.hivemq.com)
                                                                                    │  subscribe
                                                                                    ▼
                                                                       Streamlit live dashboard
                                                                       • status banner • live charts
                                                                       • alarm log     • snapshot
```

---

## Hardware components

| Component | Role | Pi connection |
|-----------|------|---------------|
| Raspberry Pi | Edge compute (sensing, inference, decision) | — |
| PIR motion sensor | Motion detection | GPIO 18 |
| Microphone | Audio capture for sound classification | USB / I2S (via PyAudio) |
| Camera | Vision capture for human detection | USB / CSI (via OpenCV, 640×480) |
| Active buzzer | Audible alarm output | GPIO 23 |
| LED | Visual alarm indicator | GPIO 17 |

> Confirm the exact sensor part numbers against your physical build before
> submission.

---

## Machine-learning models

| Model | Task | Format |
|-------|------|--------|
| **MobileNetV2** | Audio sound classification (normal vs abnormal) | `.tflite` (+ `.h5`, `.keras`) |
| **YOLO11n** | Human detection in camera frames | `.onnx` (+ `.pt`) |

Models live in `models/`. Training artifacts (confusion matrix, training curves) are
in `plots/`.

---

## Repository structure

```
.
├── Optimized_ContinuousPublish.py   # Main Raspberry Pi engine (sensing + ML + MQTT)
├── app.py                           # Streamlit live monitoring dashboard
├── test_publisher.py                # Simulator: fakes the Pi for dashboard testing
├── requirements.txt                 # Dashboard Python dependencies
├── Documentation.md                 # Pi setup, SSH, autostart, troubleshooting
├── iot.ipynb                        # Model training / experimentation notebook
├── detection_result.jpg             # Sample detection output
├── models/                          # Trained models (.tflite, .onnx, etc.)
└── plots/                           # Training curves & confusion matrix
```

---

## Communication — MQTT

- **Broker:** `broker.hivemq.com`  **Port:** `1883`

| Topic | Direction | When | Purpose |
|-------|-----------|------|---------|
| `home/security/fusion_engine/telemetry` | Pi → broker | ~1 / second | Live readings for the dashboard |
| `home/security/fusion_engine/alarm` | Pi → broker | On alarm only | Full alert event + snapshot reference |

### Telemetry payload (heartbeat)
```json
{
  "timestamp": "2026-06-25 14:03:21",
  "z_score": 0.42,
  "current_db": 48.3,
  "cam_confidence": 12.5,
  "pir_active": 0
}
```

### Alarm payload
```json
{
  "timestamp": "250626 140322",
  "tripped_pathways": ["CAMERA", "AUDIO_Z_SCORE"],
  "audio": { "z_score": 3.1, "std_deviation": 0.012, "max_delta_db": 62.0,
             "best_class_1": "CLASS3 (55.0%)", "best_class_2": "CLASS7 (18.0%)" },
  "pir_detected": true,
  "camera": { "confidence_pct": 88.5, "aoi_breached": true,
              "saved_snapshot": "http://<pi-ip>:8080/camera_alerts/tripwire_breach_*.jpg" }
}
```

---

## The dashboard (`app.py`)

A Streamlit app that subscribes to both MQTT topics and renders a live security
console: an ARMED/ALARM status banner, live charts for z-score / decibel / camera
confidence, a "which pathway tripped" breakdown, an alarm event log, and the captured
snapshot.

### Run it locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
Opens at `http://localhost:8501`. It connects straight to the public broker, so it
shows live data the moment the Pi (or the simulator) publishes.

### Test without the Pi
```bash
pip install paho-mqtt pillow
python3 test_publisher.py --fast
```
Simulates the Pi by publishing realistic telemetry and periodic alarms, so the whole
dashboard can be demonstrated with no hardware.

> **Snapshot note:** the Pi serves alarm snapshots from a local network address. The
> dashboard displays the image only when it runs on a device joined to the **same
> network/hotspot** as the Pi. To make snapshots viewable from anywhere (e.g. a
> cloud-hosted dashboard), have the Pi base64-embed the JPEG in the alarm payload.

---

## Raspberry Pi setup

Full setup — SSH access, broker install, dependencies, autostart on boot, and
troubleshooting — is documented in **`Documentation.md`**.

Quick manual run on the Pi:
```bash
python3 Optimized_ContinuousPublish.py
```

---



---

## Tech stack

- **Hardware:** Raspberry Pi, PIR sensor, microphone, camera, buzzer, LED
- **ML:** YOLO11n (human detection), MobileNetV2 (audio classification)
- **Edge runtime:** ONNX Runtime, LiteRT (TFLite), OpenCV, PyAudio
- **Communication:** MQTT (paho-mqtt)
- **Dashboard:** Streamlit
- **Language:** Python
