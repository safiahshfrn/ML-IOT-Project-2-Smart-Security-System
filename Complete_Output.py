import base64
import collections
import datetime
import json
import queue
import threading
import time

import ai_edge_litert.interpreter as litert
import cv2
import numpy as np
import onnxruntime as ort
import paho.mqtt.client as mqtt
import pyaudio
import RPi.GPIO as GPIO
import scipy.io.wavfile as wav

# ==========================================
# 1. HARDWARE CONFIGURATION
# ==========================================ff
BUZZER_PIN = 23
LED_PIN = 17
PIR_PIN = 18
CAMERA_INDEX = 0

GPIO.setmode(GPIO.BCM)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.setup(LED_PIN, GPIO.OUT)
GPIO.setup(PIR_PIN, GPIO.IN)

GPIO.output(BUZZER_PIN, GPIO.LOW)
GPIO.output(LED_PIN, GPIO.LOW)

# ==========================================
# 2. MQTT NETWORK TELEMETRY CONFIGURATION
# ==========================================
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_ALARM_TOPIC = "home/security/fusion_engine/alarm"
MQTT_TELEMETRY_TOPIC = "home/security/fusion_engine/telemetry"

# Thread-safe queue for telemetry and alarm payloads
mqtt_queue = queue.Queue(maxsize=100)
# Initialize these globally BEFORE starting the MQTT worker thread
alerts_enabled = True
MQTT_COMMAND_TOPIC = "home/security/fusion_engine/cmd"

def mqtt_worker_thread():
    """Background worker that continuously handles MQTT publishing without blocking main loop."""
    global alerts_enabled
    print("Connecting background MQTT Worker...")
    worker_client = mqtt.Client()

    # 🟢 NEW: Callback to handle incoming dashboard button clicks
    def on_message(client, userdata, msg):
        global alerts_enabled
        try:
            command = msg.payload.decode("utf-8").strip().upper()
            if "DISARM" in command:
                alerts_enabled = False
                print("\n🔒 System DISARMED via Dashboard Command.")
            elif "ARM" in command:
                alerts_enabled = True
                print("\n🔓 System ARMED via Dashboard Command.")
        except Exception as e:
            print(f"Error parsing incoming MQTT command: {e}")

    worker_client.on_message = on_message
    try:
        worker_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        worker_client.subscribe(MQTT_COMMAND_TOPIC, qos=1)
        worker_client.loop_start()
        print("✅ Background MQTT Engine connected successfully.")
    except Exception as e:
        print(f"⚠️ MQTT Worker initialization failed ({e}). Telemetry dropped.")
        return

    while True:
        try:
            topic, payload = mqtt_queue.get(timeout=1.0)
            worker_client.publish(topic, json.dumps(payload), qos=0)
            mqtt_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"\n⚠️ MQTT Background Worker Publish failed: {e}")

# Spawn the background worker
threading.Thread(target=mqtt_worker_thread, daemon=True).start()

# ==========================================
# 3. AUDIO & SLIDING WINDOW CONFIGURATION
# ==========================================
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 48000
CHUNK = 2048

ONE_SECOND_TOTAL_SAMPLES = 16000
audio_rolling_buffer = collections.deque(maxlen=(3*ONE_SECOND_TOTAL_SAMPLES))
audio_history_48k = collections.deque(maxlen=3*3*ONE_SECOND_TOTAL_SAMPLES)  # 3 seconds of 48kHz samples
abnormal_history = collections.deque(maxlen=50)

AUDIO_LABELS = ["class1", "class2", "class3", "class4", "class5", "class6", "class7"]

p = pyaudio.PyAudio()
stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                input=True, frames_per_buffer=CHUNK)

# ==========================================
# 4. LOAD AI MODELS (CORRECT SIDE-BY-SIDE PATHS)
# ==========================================
print("Loading MobileNetV2 Audio TFLite model...")
model_path = "/home/user/iot_project/BESTMODEL/Models/mobilenetv2_audio.tflite"
audio_interpreter = litert.Interpreter(model_path=model_path)
audio_interpreter.allocate_tensors()
audio_input_details = audio_interpreter.get_input_details()
audio_output_details = audio_interpreter.get_output_details()
expected_audio_shape = audio_input_details[0]['shape']

print("Loading YOLO11 Camera ONNX model...")
camera_session = ort.InferenceSession("/home/user/iot_project/BESTMODEL/Models/yolo11n.onnx")
camera_input_name = camera_session.get_inputs()[0].name

video_capture = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# ==========================================
# 5. MATH HOOKS & TARGET DEFINITION
# ==========================================
ART_AOI_BOX = [160, 160, 480, 480]
last_capture_time = 0
CAPTURE_COOLDOWN = 3.0
PIR_DECAY_TIME = 3.0
pir_expiration_time = 0.0

# Heartbeat interval telemetry control
last_heartbeat_time = 0
HEARTBEAT_INTERVAL = 1.0  # seconds
last_valid_db = 0.0       # 🟢 FIX: Initialized baseline volume variable globally

alerts_enabled = True
MQTT_COMMAND_TOPIC = "home/security/fusion_engine/cmd"

def preprocess_frame(frame):
    resized = cv2.resize(frame, (640, 640))
    rgb_img = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    blob = np.float32(rgb_img) / 255.0
    blob = np.transpose(blob, (2, 0, 1))
    blob = np.expand_dims(blob, axis=0)
    return blob

def check_intersection(box_a, box_b):
    xA = max(box_a[0], box_b[0])
    yA = max(box_a[1], box_b[1])
    xB = min(box_a[2], box_b[2])
    yB = min(box_a[3], box_b[3])
    inter_area = max(0, xB - xA) * max(0, yB - yA)
    return inter_area > 0

def quantize_audio(samples_float32):
    quantized_int8 = np.clip(samples_float32 * 127.0, -128, 127).astype(np.int8)
    return quantized_int8.astype(np.float32) / 127.0

print("🏠 High-Performance Fused Security Engine Active & Calibrating...")
time.sleep(3)
print("⚡ Monitoring Guard Active.")

try:
    while True:
        # 🟢 STEP 1: PIR DETECTION WITH SOFTWARE DECAY
        raw_pir = GPIO.input(PIR_PIN)
        current_time = time.time()

        if raw_pir == 1:
            pir_expiration_time = current_time + PIR_DECAY_TIME

        pir_active = current_time < pir_expiration_time
        pir_string = "YES" if pir_active else "NO"

        # 🟢 STEP 2: AUDIO MIC CAPTURE WITH BUZZER IMMUNITY
        try:
            raw_data = stream.read(CHUNK, exception_on_overflow=False)
            signal_samples = np.frombuffer(raw_data, dtype=np.int16)
            audio_history_48k.extend(signal_samples)
        except IOError:
            continue

        # Calculate raw sound levels
        rms = np.sqrt(np.mean(signal_samples.astype(np.float32)**2)) if len(signal_samples) > 0 else 0
        current_db = 20 * np.log10(rms) if rms > 0 else 0

        # 🧠 SMART SHIELD: Hold the last valid room volume instead of dropping to 0
        if GPIO.input(BUZZER_PIN) == GPIO.HIGH:
            current_db = last_valid_db      # ← uses last_valid_db
        else:
            last_valid_db = current_db      # ← only defined here

        # Downsample and run quantization process
        downsampled_samples = signal_samples[::3].astype(np.float32) / 32768.0
        quantized_samples = quantize_audio(downsampled_samples)

        # 🟢 FIX: Only push once to avoid timeline shifting / duplication bugs
        audio_rolling_buffer.extend(quantized_samples)

        if len(audio_rolling_buffer) < ONE_SECOND_TOTAL_SAMPLES:
            continue

        full_second_snapshot = np.array(audio_rolling_buffer)
        audio_tensor_input = np.zeros(expected_audio_shape, dtype=np.float32)
        filled_length = min(len(full_second_snapshot), audio_tensor_input.size)
        audio_tensor_input.flat[:filled_length] = full_second_snapshot[:filled_length]

        audio_interpreter.set_tensor(audio_input_details[0]['index'], audio_tensor_input)
        audio_interpreter.invoke()
        raw_logits = audio_interpreter.get_tensor(audio_output_details[0]['index'])[0]

        exp_logits = np.exp(raw_logits - np.max(raw_logits))
        audio_probabilities = exp_logits / np.sum(exp_logits)

        normal_pool = audio_probabilities[0] + audio_probabilities[5] + audio_probabilities[6]
        abnormal_pool = 1.0 - normal_pool

        is_anomaly = False
        if len(abnormal_history) >= 30:
            current_mean = np.mean(abnormal_history)
            current_std = max(np.std(abnormal_history), 0.005)
            temp_z = (abnormal_pool - current_mean) / current_std
            if temp_z > 1.2 and abnormal_pool > 0.01:
                is_anomaly = True

        if not is_anomaly:
            abnormal_history.append(abnormal_pool)

        z_score = 0.0
        std_abnormal = 0.005
        top1_name, top2_name = "NONE", "NONE"
        top1_prob, top2_prob = 0.0, 0.0

        if len(abnormal_history) >= 30:
            mean_abnormal = np.mean(abnormal_history)
            std_abnormal = max(np.std(abnormal_history), 0.005)
            z_score = (abnormal_pool - mean_abnormal) / std_abnormal

            sorted_indices = list(np.argsort(audio_probabilities))
            if 5 in sorted_indices:
                sorted_indices.remove(5)

            top1_idx = sorted_indices[-1]
            top2_idx = sorted_indices[-2]
            top1_name = AUDIO_LABELS[top1_idx]
            top1_prob = audio_probabilities[top1_idx] * 100
            top2_name = AUDIO_LABELS[top2_idx]
            top2_prob = audio_probabilities[top2_idx] * 100

        # 🟢 STEP 3: RUN CAMERA SCANNING CORE
        ret, frame = video_capture.read()
        cam_confidence = 0.0
        aoi_active = False
        annotated_frame = None

        if ret:
            annotated_frame = cv2.resize(frame, (640, 640))
            input_data = preprocess_frame(frame)
            vision_output = camera_session.run(None, {camera_input_name: input_data})
            raw_predictions = vision_output[0][0]
            class_confidences = raw_predictions[4:, :]
            cam_confidence = float(np.max(class_confidences)) * 100.0

            if (cam_confidence / 100.0) > 0.40:
                best_match_idx = np.argmax(np.max(class_confidences, axis=0))
                box_coords = raw_predictions[0:4, best_match_idx]
                cx, cy, w, h = box_coords
                human_box = [int(cx - w/2), int(cy - h/2), int(cx + w/2), int(cy + h/2)]

                aoi_active = check_intersection(human_box, ART_AOI_BOX)

                cv2.rectangle(annotated_frame, (human_box[0], human_box[1]), (human_box[2], human_box[3]), (0, 255, 255), 2)
                cv2.putText(annotated_frame, f"Human Conf: {(cam_confidence/100.0):.2f}", (human_box[0] + 10, human_box[1] + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

        print(f"Tracking.. Z: {z_score:4.1f}σ | PIR: {pir_string} | CAM: {cam_confidence:.1f}% ", end='\r')

        image_payload_string = "COOLDOWN_ACTIVE_NO_IMAGE"
        # 🟢 CONTINUOUS SUB-THREAD HEARTBEAT TELEMETRY FOR DASHBOARDS
        if current_time - last_heartbeat_time >= HEARTBEAT_INTERVAL:

            # 🟢 NEW: Map all labels to their current confidence percentages
            all_audio_scores = {
                AUDIO_LABELS[i]: round(float(audio_probabilities[i] * 100), 1)
                for i in range(len(AUDIO_LABELS))
            }

            heartbeat_payload = {
                "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "z_score": round(float(z_score), 2),
                "current_db": float(f"{current_db:.1f}"),
                "cam_confidence": round(float(cam_confidence), 1),
                "pir_active": int(pir_active),
                "audio_all_classes": all_audio_scores
            }
            try:
                mqtt_queue.put_nowait((MQTT_TELEMETRY_TOPIC, heartbeat_payload))
            except queue.Full:
                pass
            last_heartbeat_time = current_time

        # ==========================================
        # 🚨 STEP 4: THE 3 PATHWAY THRESHOLD EVALUATION
        # ==========================================
        Z_THRESHOLD = 8.0
        DECIBEL_TRIGGER_LIMIT = 110.0

        cond_camera = (cam_confidence > 80.0) and aoi_active
        cond_audio_spike = z_score > Z_THRESHOLD
        cond_pir_decibel = pir_active and (current_db > DECIBEL_TRIGGER_LIMIT)

        if (cond_camera or cond_audio_spike or cond_pir_decibel) and alerts_enabled:

            # Fire hardware alerts instantly for real-time responsiveness
            GPIO.output(BUZZER_PIN, GPIO.HIGH)
            GPIO.output(LED_PIN, GPIO.HIGH)

            filename = "None"
            audio_b64_string = None
            current_time_snap = time.time()

            # 🟢 FIX: Scope expanded to top level of execution block to resolve missing reference crashes
            timestamp_fs = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

            if current_time_snap - last_capture_time > CAPTURE_COOLDOWN:
                # [Base64 Encoding Logic Happens Here]
                # image_payload_string gets created here
                # 📷 Process the camera frame instantly
                if annotated_frame is not None:
                    box_color = (0, 0, 255) if aoi_active else (0, 255, 0)
                    cv2.rectangle(annotated_frame, (ART_AOI_BOX[0], ART_AOI_BOX[1]), (ART_AOI_BOX[2], ART_AOI_BOX[3]), box_color, 3)
                    cv2.putText(annotated_frame, "CRITICAL PERIMETER VIOLATION" if aoi_active else "PERIMETER SECURE",
                                (ART_AOI_BOX[0] + 10, ART_AOI_BOX[1] + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2)

                    # 🟢 NEW: Compress frame to JPEG format in-memory
                    success, encoded_image = cv2.imencode('.jpg', annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])

                    if success:
                        # 🟢 NEW: Convert JPEG bytes to a Base64 ASCII string
                        base64_image = base64.b64encode(encoded_image).decode('utf-8')
                        image_payload_string = f"data:image/jpeg;base64,{base64_image}"
                    else:
                        image_payload_string = "ERROR_ENCODING"
                    filename = f"/home/user/iot_project/BESTMODEL/camera_alerts/tripwire_breach_{timestamp_fs}.jpg"
                    cv2.imwrite(filename, annotated_frame)

                # 🎙️ POST-TRIGGER CAPTURE DELAY (CENTERING HOOK)
                post_trigger_duration = 1.5
                start_wait = time.time()
                try:
                    while time.time() - start_wait < post_trigger_duration:
                        raw_data_tail = stream.read(CHUNK, exception_on_overflow=False)
                        signal_samples_tail = np.frombuffer(raw_data_tail, dtype=np.int16)
                        audio_history_48k.extend(signal_samples_tail)
                except IOError:
                    pass

                audio_filename = f"/home/user/iot_project/BESTMODEL/audio_alerts/audio_breach_{timestamp_fs}.wav"
                native_buffer = np.array(audio_history_48k)

                if len(native_buffer) >= 144000:
                    audio_window = native_buffer[-144000:]
                else:
                    audio_window = native_buffer

                # Write out the perfectly centered 3-second audio asset
                # Downsample 48k -> 16k to keep the payload small, then save
                audio_window_16k = audio_window[::3]  # 48000/3 = 16000 Hz
                wav.write(audio_filename, 16000, audio_window_16k)

                # NEW: base64-encode the WAV so the dashboard can play it
                with open(audio_filename, "rb") as _af:
                    audio_b64_string = ("data:audio/wav;base64,"
                                        + base64.b64encode(_af.read()).decode("utf-8"))

                # 🟢 FIX: Explicitly evaluate completion time post-delay to prevent truncated cooldown windows
                last_capture_time = time.time()

            tripped_by = []
            if cond_camera: tripped_by.append("CAMERA")
            if cond_audio_spike: tripped_by.append("AUDIO_Z_SCORE")
            if cond_pir_decibel: tripped_by.append("PIR+DECIBEL")
            pathway_string = " + ".join(tripped_by)

            now = datetime.datetime.now()
            time_string = now.strftime('%d%m%y %H%M%S')

            # Build alert payload
            alert_payload = {
                "timestamp": time_string,
                "tripped_pathways": tripped_by,
                "audio": {
                    "z_score": round(float(z_score), 2),
                    "std_deviation": round(float(std_abnormal), 4),
                    "max_delta_db": round(float(current_db), 1),
                    "best_class_1": f"{top1_name.upper()} ({top1_prob:.1f}%)",
                    "best_class_2": f"{top2_name.upper()} ({top2_prob:.1f}%)",
                    "audio_b64": audio_b64_string
                },
                "pir_detected": bool(pir_active),
                "camera": {
                    "confidence_pct": round(cam_confidence, 1),
                    "aoi_breached": aoi_active,
                    # 🟢 CHANGED: Sending the payload string directly instead of a URL
                    "saved_snapshot": image_payload_string
                }
            }

            try:
                mqtt_queue.put_nowait((MQTT_ALARM_TOPIC, alert_payload))
            except queue.Full:
                print("\n⚠️ Alert Dropped: MQTT Pipeline saturated.")

            # 📋 CONSOLE DEBUGS
            print("\n\n================================================")
            print(f"- Time ({time_string}) [TRIPPED BY: {pathway_string}]")
            print(f"- AUDIO(confidence STD: {std_abnormal:.4f}, MAX delta decibel: {current_db:.1f}dB, best class 1: {top1_name.upper()} ({top1_prob:.1f}%), best class 2: {top2_name.upper()} ({top2_prob:.1f}%))")
            print(f"- PIR_DETECT? {pir_string}")
            print(f"- CAMERA Confidence: {cam_confidence:.1f}%")
            print("📡 Alarm Event Queued to Network Engine.")
            print("================================================\n")

            time.sleep(0.5) # Buzzer rings for half a second

            # Turn off hardware alerts
            GPIO.output(BUZZER_PIN, GPIO.LOW)
            GPIO.output(LED_PIN, GPIO.LOW)

            # 🧹 FLUSH INTERNAL HARDWARE AUDIO BUFFER
            try:
                while stream.get_read_available() > 0:
                    stream.read(CHUNK, exception_on_overflow=False)
            except IOError:
                pass

            audio_rolling_buffer.clear()
            time.sleep(1.0) # Post-alarm stabilization cooldown

        time.sleep(0.02)

except KeyboardInterrupt:
    print("\nShutting down engine cleanly.")
    GPIO.output(BUZZER_PIN, GPIO.LOW)
    GPIO.output(LED_PIN, GPIO.LOW)
    GPIO.cleanup()
    stream.stop_stream()
    stream.close()
    p.terminate()
    video_capture.release()
