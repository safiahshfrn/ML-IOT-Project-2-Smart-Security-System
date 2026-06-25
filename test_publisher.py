"""
test_publisher.py — Fake Raspberry Pi for dashboard testing.

Publishes realistic telemetry to the SAME broker/topics the dashboard listens on,
so you can watch the whole dashboard come alive on your laptop before the real Pi
(and Student 3's firmware changes) are ready.

What it sends:
  • home/security/heartbeat       ~1/sec, matching the agreed heartbeat schema
  • home/security/fusion_engine   occasional alarms, matching the real alarm schema,
                                  WITH a base64 snapshot image embedded (snapshot_b64)

It mimics a believable scene: mostly calm, with the relevant signal ramping up just
before an alarm fires, so the charts and the alarm log line up causally.

Usage:
  pip install paho-mqtt pillow
  python test_publisher.py            # publish live to the broker
  python test_publisher.py --dry-run  # print payloads instead of publishing (no network)
  python test_publisher.py --fast     # alarms every ~8s instead of ~20s
"""

import sys
import json
import time
import base64
import random
import io
from datetime import datetime

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
TOPIC_HEARTBEAT = "home/security/fusion_engine/telemetry"
TOPIC_ALARM = "home/security/fusion_engine/alarm"

DRY_RUN = "--dry-run" in sys.argv
FAST = "--fast" in sys.argv
ALARM_EVERY = (8, 14) if FAST else (18, 32)   # random seconds between alarms

AUDIO_CLASSES = ["GLASS_BREAK", "SCREAM", "FOOTSTEP", "DOOR_FORCE", "SHOUT", "BANG"]


def now_str():
    return datetime.now().strftime("%d%m%y %H%M%S")


def make_snapshot_b64(pathway, cam_conf, aoi):
    """Generate a small annotated JPEG that looks like a real capture, return base64.
    Falls back to a 1x1 placeholder if Pillow isn't installed."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        # 1x1 black JPEG so the dashboard still has something to decode
        return ("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////"
                "////////////////////////////////////////////////////wgALCAAB"
                "AAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")
    img = Image.new("RGB", (640, 480), (28, 30, 36))
    d = ImageDraw.Draw(img)
    # Fake "area of interest" tripwire box
    box_col = (220, 60, 60) if aoi else (70, 200, 120)
    d.rectangle([160, 160, 480, 440], outline=box_col, width=4)
    # Fake human bounding box
    d.rectangle([260, 200, 380, 430], outline=(0, 230, 230), width=3)
    d.text((266, 184), f"Human {cam_conf:.0f}%", fill=(0, 230, 230))
    d.text((168, 140),
           "CRITICAL PERIMETER VIOLATION" if aoi else "PERIMETER SECURE",
           fill=box_col)
    d.text((12, 12), f"TRIPPED BY: {pathway}", fill=(240, 240, 240))
    d.text((12, 30), now_str(), fill=(180, 180, 180))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def heartbeat_payload(z, db, cam, pir):
    return {
        "timestamp": now_str(),
        "z_score": round(z, 2),
        "current_db": round(db, 1),
        "cam_confidence": round(cam, 1),
        "pir_active": bool(pir),
        "armed": True,
    }


def alarm_payload(pathway, z, db, cam, pir, aoi):
    cls1 = random.choice(AUDIO_CLASSES)
    cls2 = random.choice(AUDIO_CLASSES)
    return {
        "timestamp": now_str(),
        "tripped_pathways": [pathway],
        "audio": {
            "z_score": round(z, 2),
            "std_deviation": round(random.uniform(0.005, 0.05), 4),
            "max_delta_db": round(db, 1),
            "best_class_1": f"{cls1} ({random.uniform(30, 80):.1f}%)",
            "best_class_2": f"{cls2} ({random.uniform(5, 25):.1f}%)",
        },
        "pir_detected": bool(pir),
        "camera": {
            "confidence_pct": round(cam, 1),
            "aoi_breached": bool(aoi),
            "saved_snapshot": f"/home/user/iot_project/BESTMODEL/alerts/"
                              f"tripwire_breach_{datetime.now():%Y%m%d-%H%M%S}.jpg",
            "snapshot_b64": make_snapshot_b64(pathway, cam, aoi),
        },
    }


def publish(client, topic, payload):
    text = json.dumps(payload)
    if DRY_RUN:
        short = {k: v for k, v in payload.items() if k != "camera"}
        if "camera" in payload:
            cam = dict(payload["camera"])
            cam["snapshot_b64"] = f"<{len(cam.get('snapshot_b64',''))} b64 chars>"
            short["camera"] = cam
        print(f"[{topic}] {json.dumps(short)}")
    else:
        client.publish(topic, text, qos=1 if topic == TOPIC_ALARM else 0)


def main():
    client = None
    if not DRY_RUN:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        print(f"Connected to {MQTT_BROKER}. Publishing... (Ctrl+C to stop)")
    else:
        print("DRY RUN — printing payloads, not publishing.\n")

    # Baselines for a calm room
    next_alarm = time.time() + random.uniform(*ALARM_EVERY)
    ramp = 0          # >0 means we're building toward an alarm
    ramp_kind = None
    loops = 0

    try:
        while True:
            loops += 1
            # Calm-room jitter
            z = random.gauss(0.2, 0.4)
            db = random.gauss(48, 4)
            cam = random.gauss(15, 8)
            pir = random.random() < 0.1

            # If we're ramping toward an alarm, push the relevant signal up
            if ramp > 0:
                if ramp_kind == "AUDIO_Z_SCORE":
                    z = random.uniform(1.5, 2.6)
                    db = random.gauss(62, 5)
                elif ramp_kind == "CAMERA":
                    cam = random.uniform(70, 95)
                    pir = True
                elif ramp_kind == "PIR+DECIBEL":
                    db = random.uniform(78, 92)
                    pir = True
                ramp -= 1

            cam = max(0, min(cam, 100))
            db = max(0, db)
            publish(client, TOPIC_HEARTBEAT, heartbeat_payload(z, db, cam, pir))

            # Time to start a ramp toward the next alarm?
            if ramp == 0 and time.time() >= next_alarm and ramp_kind is None:
                ramp_kind = random.choice(["AUDIO_Z_SCORE", "CAMERA", "PIR+DECIBEL"])
                ramp = 3  # build for 3 heartbeats, then fire

            # Ramp just finished -> fire the alarm
            elif ramp == 0 and ramp_kind is not None:
                aoi = ramp_kind == "CAMERA"
                fire_z = z if ramp_kind == "AUDIO_Z_SCORE" else random.uniform(0.5, 1.5)
                fire_db = db if ramp_kind in ("PIR+DECIBEL",) else random.gauss(55, 5)
                fire_cam = cam if ramp_kind == "CAMERA" else random.uniform(20, 60)
                payload = alarm_payload(ramp_kind, fire_z, fire_db, fire_cam,
                                        pir=ramp_kind != "AUDIO_Z_SCORE", aoi=aoi)
                publish(client, TOPIC_ALARM, payload)
                print(f">>> ALARM fired: {ramp_kind}")
                ramp_kind = None
                next_alarm = time.time() + random.uniform(*ALARM_EVERY)

            if DRY_RUN and loops >= 12:
                print("\n(dry run: stopping after 12 loops)")
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping publisher.")
    finally:
        if client is not None:
            client.loop_stop()
            client.disconnect()


if __name__ == "__main__":
    main()
