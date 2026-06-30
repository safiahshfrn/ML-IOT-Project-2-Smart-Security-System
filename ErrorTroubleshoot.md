# MLIOT_PROJECT2_INTRUDERALERT
Intruder alert using Passive Infrared Sensor, Camera and a Microphone

# 🛡️ IoT ML Sensor Fusion Intruder Alert System

This repository contains the deployment files, model configurations, and automation architecture for an edge-computing security system run on a Raspberry Pi (`MLIOTPROJECT2`). 

---

## 📋 Prerequisites & Environment Setup

### 1. Local Machine Setup
Ensure your host laptop has the following environment ready:
* **Visual Studio Code (VS Code):** Installed for remote workspace management.
* **VS Code Remote - SSH Extension:** Required to securely modify and monitor scripts directly on the Raspberry Pi environment (`user@MLIOTPROJECT2.local`).

### 2. Core Broker & Global Environment Initialization
Open your computer's terminal or Windows PowerShell and establish an SSH session:
```bash
ssh user@MLIOTPROJECT2.local
```
Enter Password, **Password won't be displayed**, meaning that you have to blindly **type the password without the visual aid**.

## 🌐 Infrastructure & Connection Troubleshooting (SKIP THIS IF PASSWORD SAFELY ENTERED)

Beyond the sensor fusion logic, deploying code to an edge device like the Raspberry Pi over a local network introduced several critical infrastructure challenges. Below is how we resolved them:

### 1. Dynamic IP Address Shifting & `.local` Resolution
* **The Problem:** The Raspberry Pi's IP address frequently changed depending on the local network router's DHCP assignment. Hardcoding an IP address in the terminal meant the connection broke every time the Pi rebooted or hopped networks.
* **The Solution:** We utilized **mDNS (Multicast DNS)** to reference the hostname directly via `MLIOTPROJECT2.local`. This allows the host laptop to discover the Pi's network location dynamically without needing to look up the numeric IP address every session.


Once connected inside the Pi Terminal, execute the following system update and MQTT broker configurations:
```bash
# Update local package index
sudo apt update  

# Install MQTT Broker and testing clients
sudo apt install mosquitto mosquitto-clients -y 

# Enable background broker system process on boot
sudo systemctl enable mosquitto
sudo systemctl start mosquitto

# Verify broker status (should show 'active (running)')
sudo systemctl status mosquitto

# Install underlying ALSA sound architecture dependencies for Python
sudo apt-get install python3-pyaudio -y
```
### 3. VS Code Remote Environment Packages
Once inside your VS Code SSH workspace connection, install the behavioral libraries using the integrated terminal panel:
```bash
pip install numpy paho-mqtt gpiozero
```


# AFTER CODING, HOW TO ALLOW RASPBERRY PI TO RUN CODE UPON REBOOT (AUTOSTART) WITHOUT SSH CONNECTION

File Manifest Location
```bash
nano ~/.config/autostart/iot_project.desktop
```

Configuration Source Profile:
```TOML
[Desktop Entry]
Type=Application
Name=IoT ML Fusion Engine
Exec=lxterminal --geometry=110x30 -e "bash -c 'python3 /home/user/iot_project/BESTMODEL/FINAL_BestLogic.py; exec bash'"
Terminal=true
Icon=utilities-terminal
Categories=Utility;
```

# IMPORTANT TO NOT DAMAGE YOUR PI HARDWARE

```bash
# Safely restart the hardware system to apply changes
sudo reboot
```
Upon reboot, **Wait 1-2 minutes** for the pi to reboot and autostart its python code. Troubleshooting tips below if the buzzer/LED does not give any output. Please reconnect via `ssh user@MLIOTPROJECT2.local` and enter your password again, reestablishing your connection to the raspberry pi.

```bash
# Safely power down the device fully
sudo poweroff
```
*EXPECTED*: **RED LIGHT PERSIST, No green light, but you cannot connect via ssh anymore**, this means the pi has been turned off and you can safely cut the power.

## 🌐 Infrastructure & Connection Troubleshooting (SKIP THIS IF THE CODE RUNS WITHOUT SSH CONNECTION)
### 1. MAKE SURE THE PATH IS CONFIGURED CORRECTLY in the TOML file (`nano ~/.config/autostart/iot_project.desktop`)
* Pay attention to `'` and `"` symbols being used, check the file path

### 2. Execution Permissions (`chmod`) for Autostart Scripts
* **The Problem:** When configuring the pipeline to run autonomously on boot via the `.desktop` shortcut wrapper, the script failed to launch. The terminal logs threw a permission error because the raw script lacked execution rights.
* **The Solution:** We explicit granted binary execution rights to the target script using the change mode command:
  ```bash
  find ~ -name "*.desktop" 2>/dev/null
  ```
* A file path will display, we can use the chmod command to tell the PI that this path is executable.
  ```bash
  chmod +x /home/user/iot_project/BESTMODEL/FINAL_BestLogic.py
  ```
* Expected Result: There won't be a success notification, but there shouldn't be any error notifications.

### * 3. SSH Connection Rejected (Connection Refused / Host Key Verification Failed)
The Problem: When changing accounts, wiping devices, or reconnecting after a clean OS install, the host laptop would flat-out reject the SSH handshake, throwing an absolute block error. This happens because the host computer detects that the cryptographic identity signature of the Pi changed, assuming a "Man-in-the-Middle" security threat.

The Solution: We manually flushed the cached credentials on the host laptop's SSH register using the terminal:

```bash
ssh-keygen -f "~/.ssh/known_hosts" -R "MLIOTPROJECT2.local"
```
Clearing the old key allowed the laptop to safely accept the new identity token upon the next connection attempt.


# ALSO, CHECK IF THERE ARE BACKGROUND RUNNING PROCESSES IN PI
```bash
# List all active Python processes with their full paths:
ps aux | grep python

# Identify which process ID (PID) is actively locking the camera node:
sudo lsof /dev/video0

# List all system-assigned V4L2 video devices:
v4l2-ctl --list-devices

# Force kill all lingering Python executions cleanly
sudo killall -9 python python3

# Hard reset the Linux UVC (USB Video Class) driver stack to clear frozen hardware states
sudo rmmod uvcvideo && sudo modprobe uvcvideo
```
### Direct Manual Execution Loop
To run the updated real-time multithreaded architecture manually with clean console logs, use the absolute target binary path:
```bash
/usr/bin/python /home/user/iot_project/BESTMODEL/Optimized_ContinuousPublish.py
```

