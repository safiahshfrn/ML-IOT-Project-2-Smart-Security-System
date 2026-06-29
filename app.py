"""
SRTA 3353 — Machine Learning for IoT, Project 2
Intruder Alert System — Live Monitoring Dashboard
Student 5: Dashboard, Visualization & Connectivity Engineer

Subscribes to the group's two MQTT topics on a public broker and renders a live
security console: armed/alarm status, live signal charts, alarm event log, and the
captured intruder snapshot.

Architecture note (why it is built this way):
Streamlit re-runs this whole script top-to-bottom on every refresh. A long-lived
MQTT connection therefore cannot live in the script body — it would be torn down
and recreated on every rerun. Instead, the MQTT client and a thread-safe data store
are created exactly once via @st.cache_resource, and paho-mqtt's loop_start() runs
its network loop in a background thread. The UI just reads the shared store.
"""

import base64
import json
import random
import threading
import time
from collections import deque
from datetime import datetime

import paho.mqtt.client as mqtt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ==========================================================================
# 1. CONFIGURATION  (the only things you should ever need to edit)
# ==========================================================================
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883

TOPIC_HEARTBEAT = "home/security/fusion_engine/telemetry"   # continuous live telemetry (~1/s)
TOPIC_ALARM = "home/security/fusion_engine/alarm"           # fired only when the alarm trips

MAX_POINTS = 120          # rolling window length for the live charts
MAX_ALARMS = 200          # how many past alarm events to keep in the log
ALARM_HOLD_SECONDS = 8    # how long the banner stays red after the last alarm
STALE_AFTER_SECONDS = 6   # no data for this long => "waiting / stale" indicator

# NOTE ON A PUBLIC BROKER: broker.hivemq.com is shared with the whole internet, so
# anyone could in theory publish to these topic names. For the demo this is fine. If
# you want isolation, agree a unique suffix with Student 3 (firmware) and change it
# in BOTH places, e.g. "home/security/heartbeat/grp7x9".


# ==========================================================================
# 2. THREAD-SAFE SHARED STORE
# ==========================================================================
class SharedState:
    """Written by the MQTT background thread, read by the Streamlit UI thread.
    Every access is guarded by a lock because the two run concurrently."""

    def __init__(self):
        self.lock = threading.Lock()
        self.heartbeats = deque(maxlen=MAX_POINTS)   # list of dicts (telemetry)
        self.alarms = deque(maxlen=MAX_ALARMS)       # list of dicts (alarm events)
        self.last_msg_time = 0.0                     # local time any msg arrived
        self.last_alarm_time = 0.0                   # local time last alarm arrived
        self.latest_snapshot = None                  # ("b64", data) or ("url", link)
        self.connected = False
        self.total_messages = 0

    def add_heartbeat(self, payload):
        with self.lock:
            self.heartbeats.append({
                "t": time.time(),
                "z_score": _num(payload.get("z_score")),
                "decibel": _num(payload.get("current_db", payload.get("decibel"))),
                "cam_confidence": _num(payload.get("cam_confidence")),
                "pir_active": bool(payload.get("pir_active", False)),
                "armed": bool(payload.get("armed", True)),
            })
            self.last_msg_time = time.time()
            self.total_messages += 1

    def add_alarm(self, payload):
        with self.lock:
            audio = payload.get("audio", {}) or {}
            camera = payload.get("camera", {}) or {}
            self.alarms.append({
                "timestamp": payload.get("timestamp", _now_str()),
                "pathways": ", ".join(payload.get("tripped_pathways", []) or []),
                "cam_confidence": _num(camera.get("confidence_pct")),
                "aoi_breached": bool(camera.get("aoi_breached", False)),
                "z_score": _num(audio.get("z_score")),
                "max_delta_db": _num(audio.get("max_delta_db")),
                "best_class_1": audio.get("best_class_1", "—"),
            })
            # Snapshot can arrive two ways: base64-embedded in the payload, or as a
            # URL the Pi serves over its local network. Support both.
            snap_b64 = payload.get("snapshot_b64")
            snap_url = camera.get("saved_snapshot")
            if snap_b64:
                self.latest_snapshot = ("b64", snap_b64)
            elif snap_url and str(snap_url).startswith("http"):
                self.latest_snapshot = ("url", snap_url)
            self.last_msg_time = time.time()
            self.last_alarm_time = time.time()
            self.total_messages += 1


def _num(v):
    """Coerce anything to float, defaulting to 0.0 — the Pi sometimes sends strings."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _now_str():
    return datetime.now().strftime("%d%m%y %H%M%S")


# ==========================================================================
# 3. MQTT CLIENT  (created once, runs in a background thread)
# ==========================================================================
def _on_connect(client, userdata, flags, reason_code, properties=None):
    # Subscribe inside on_connect so we also re-subscribe automatically after any
    # dropped-and-restored connection.
    userdata.connected = (reason_code == 0)
    client.subscribe([(TOPIC_HEARTBEAT, 0), (TOPIC_ALARM, 1)])


def _on_disconnect(client, userdata, *args):
    userdata.connected = False  # paho will keep retrying because of loop_start()


def _on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return  # ignore malformed messages rather than crashing the listener
    if msg.topic == TOPIC_HEARTBEAT:
        userdata.add_heartbeat(payload)
    elif msg.topic == TOPIC_ALARM:
        userdata.add_alarm(payload)


@st.cache_resource
def get_mqtt_system():
    """Runs ONCE for the lifetime of the app (cached across every rerun)."""
    state = SharedState()
    client = mqtt.Client(
        client_id=f"streamlit-intruder-dash-{random.randint(1000, 9999)}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.user_data_set(state)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_start()  # spawns the background network thread
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the page
        st.session_state["mqtt_error"] = str(exc)
    return client, state


# ==========================================================================
# 4. PAGE + AUTO-REFRESH
# ==========================================================================
st.set_page_config(page_title="Intruder Alert — Live Monitor",
                   page_icon="🛡️", layout="wide")

# Re-run the script every second so the charts/banner stay live.
st_autorefresh(interval=1000, key="auto_refresh")

client, state = get_mqtt_system()

# Take a consistent snapshot of shared data under the lock, then release it fast.
with state.lock:
    heartbeats = list(state.heartbeats)
    alarms = list(state.alarms)
    connected = state.connected
    last_msg_time = state.last_msg_time
    last_alarm_time = state.last_alarm_time
    snapshot = state.latest_snapshot
    total_messages = state.total_messages

now = time.time()
data_is_fresh = last_msg_time > 0 and (now - last_msg_time) < STALE_AFTER_SECONDS
in_alarm = last_alarm_time > 0 and (now - last_alarm_time) < ALARM_HOLD_SECONDS
latest_hb = heartbeats[-1] if heartbeats else None


# ==========================================================================
# 5. HEADER + CONNECTION INDICATOR
# ==========================================================================
head_l, head_r = st.columns([3, 1])
with head_l:
    st.title("🛡️ Intruder Alert — Live Monitor")
    st.caption("3-sensor fusion (PIR · microphone · camera) → MQTT → Streamlit Cloud")
with head_r:
    if connected and data_is_fresh:
        st.success("● Live — data flowing")
    elif connected and not data_is_fresh:
        st.warning("● Connected — no recent data")
    else:
        st.error("● Disconnected from broker")
    st.caption(f"Messages received: {total_messages}")


# ==========================================================================
# 6. STATUS BANNER  (ARMED green / ALARM red)
# ==========================================================================
if in_alarm:
    banner_color, banner_text, sub = "#A32D2D", "🚨 ALARM — INTRUSION DETECTED", \
        "Triggered pathways: " + (alarms[-1]["pathways"] if alarms else "unknown")
elif latest_hb is not None or connected:
    armed = (latest_hb["armed"] if latest_hb else True)
    banner_color = "#3B6D11" if armed else "#5F5E5A"
    banner_text = "🟢 ARMED — MONITORING" if armed else "⚪ DISARMED"
    sub = "System nominal. Watching all three sensors."
else:
    banner_color, banner_text, sub = "#5F5E5A", "… WAITING FOR DEVICE", \
        "No telemetry yet. Confirm the Pi is publishing to the broker."

st.markdown(
    f"""
    <div style="background:{banner_color};color:#fff;padding:18px 22px;
                border-radius:12px;margin:6px 0 14px;">
      <div style="font-size:26px;font-weight:600;">{banner_text}</div>
      <div style="font-size:15px;opacity:.9;margin-top:4px;">{sub}</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ==========================================================================
# 7. LIVE METRIC TILES  (current heartbeat reading)
# ==========================================================================
m1, m2, m3, m4 = st.columns(4)
if latest_hb is not None:
    m1.metric("Audio z-score", f"{latest_hb['z_score']:.2f} σ")
    m2.metric("Decibel", f"{latest_hb['decibel']:.1f} dB")
    m3.metric("Camera confidence", f"{latest_hb['cam_confidence']:.1f} %")
    m4.metric("PIR motion", "YES" if latest_hb["pir_active"] else "no")
else:
    m1.metric("Audio z-score", "—")
    m2.metric("Decibel", "—")
    m3.metric("Camera confidence", "—")
    m4.metric("PIR motion", "—")


# ==========================================================================
# 8. LIVE SIGNAL CHARTS  (rolling window, from heartbeat telemetry)
# ==========================================================================
st.subheader("Live sensor signals")
if heartbeats:
    df = pd.DataFrame(heartbeats)
    df["time"] = pd.to_datetime(df["t"], unit="s")
    df = df.set_index("time")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.caption("Audio anomaly z-score (trips at 2.0σ)")
        st.line_chart(df[["z_score"]], height=220)
    with c2:
        st.caption("Sound level (dB)")
        st.line_chart(df[["decibel"]], height=220)
    with c3:
        st.caption("Camera human confidence (%)")
        st.line_chart(df[["cam_confidence"]], height=220)
else:
    st.info(
        "Live charts will populate once the **heartbeat** topic is publishing "
        "(`home/security/heartbeat`, ~1/sec). Until then, alarm events below still "
        "work. Ask Student 3 to enable the heartbeat publish for continuous monitoring."
    )


# ==========================================================================
# 9. LATEST SNAPSHOT + WHICH PATHWAY TRIPPED
# ==========================================================================
st.subheader("Latest alarm detail")
snap_col, info_col = st.columns([1, 1])

with snap_col:
    if snapshot and snapshot[0] == "b64":
        try:
            st.image(base64.b64decode(snapshot[1]),
                     caption="Captured at last alarm", use_container_width=True)
        except Exception:  # noqa: BLE001
            st.warning("Snapshot received but could not be decoded.")
    elif snapshot and snapshot[0] == "url":
        try:
            st.image(snapshot[1], caption="Captured at last alarm (served from the Pi)",
                     use_container_width=True)
        except Exception:  # noqa: BLE001
            st.info(
                "An image URL was received but could not be loaded. The Pi serves "
                "snapshots on its local hotspot address, so the image only loads when "
                "this dashboard runs on a device joined to the **same hotspot** as the "
                "Pi. For a snapshot that works from anywhere, ask Student 3 to "
                "base64-embed the JPEG (see README)."
            )
        st.caption(snapshot[1])
    elif alarms:
        st.info("No image available for the last alarm.")
    else:
        st.caption("No alarms yet — the captured intruder image will appear here.")

with info_col:
    if alarms:
        last = alarms[-1]
        pathways = last["pathways"] or "—"
        st.markdown(f"**Tripped pathways:** {pathways}")
        st.markdown(f"**Area-of-interest breached:** "
                    f"{'🔴 YES' if last['aoi_breached'] else 'no'}")
        st.markdown(f"**Camera confidence:** {last['cam_confidence']:.1f} %")
        st.markdown(f"**Audio z-score:** {last['z_score']:.2f} σ  ·  "
                    f"**Δ dB:** {last['max_delta_db']:.1f}")
        st.markdown(f"**Top audio class:** {last['best_class_1']}")
        st.markdown(f"**Time:** {last['timestamp']}")
    else:
        st.caption("Pathway breakdown for the most recent alarm shows here.")


# ==========================================================================
# 10. ALARM EVENT LOG
# ==========================================================================
st.subheader("Alarm event log")
if alarms:
    log_df = pd.DataFrame(list(reversed(alarms)))[
        ["timestamp", "pathways", "cam_confidence", "aoi_breached", "z_score"]
    ]
    log_df.columns = ["Time", "Tripped by", "Camera %", "AoI breach", "Audio σ"]
    st.dataframe(log_df, use_container_width=True, hide_index=True)
else:
    st.caption("No alarm events recorded this session.")


# ==========================================================================
# 11. SIDEBAR  (config visibility + housekeeping)
# ==========================================================================
with st.sidebar:
    st.header("Connection")
    st.text(f"Broker : {MQTT_BROKER}:{MQTT_PORT}")
    st.text(f"Heartbeat : {TOPIC_HEARTBEAT}")
    st.text(f"Alarm : {TOPIC_ALARM}")
    if "mqtt_error" in st.session_state:
        st.error(f"MQTT error: {st.session_state['mqtt_error']}")
    st.divider()
    if st.button("Clear session data"):
        with state.lock:
            state.heartbeats.clear()
            state.alarms.clear()
            state.latest_snapshot = None
        st.rerun()
    st.caption("Auto-refreshing every second.")
