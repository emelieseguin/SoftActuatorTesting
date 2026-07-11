#!/usr/bin/env python3
"""
Main test GUI
- Load a calibration config file (JSON) produced by the separate calibration tool
- Connect to serial, read CSV-like lines, extract raw volts, apply calibration model
- Optional live plot if matplotlib is installed
Save as: main_gui.py
Requirements:
  pip install pyserial matplotlib opencv-python numpy   # optional pieces
Run: python main_gui.py
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import subprocess
import queue
import time
import json
import os
import serial
import serial.tools.list_ports
from collections import deque
from datetime import datetime


# Optional plotting
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except Exception:
    HAS_MATPLOTLIB = False

# Globals
BAUDRATE = 115200
SERIAL_TIMEOUT = 0.5
PLOT_MAX_POINTS = 2000
PLOT_UPDATE_MS = 200

line_q = queue.Queue()

# -----------------------
# Serial reader thread
# -----------------------
class SerialReader(threading.Thread):
    def __init__(self, ser):
        super().__init__(daemon=True)
        self.ser = ser
        self.running = True

    def run(self):
        try:
            while self.running:
                try:
                    raw = self.ser.readline()
                except Exception as e:
                    line_q.put((time.time(), f"__ERROR__ Serial read error: {e}"))
                    break
                if not raw:
                    continue
                try:
                    text = raw.decode('utf-8', errors='replace').strip()
                except Exception:
                    text = str(raw)
                line_q.put((time.time(), text))
        except Exception as e:
            line_q.put((time.time(), f"__ERROR__ {e}"))

    def stop(self):
        self.running = False

# -----------------------
# Calibration utilities
# -----------------------
def apply_calibration_model(model, raw):
    if model is None:
        return raw
    t = model.get('type', 'linear')
    coeffs = model.get('coeffs', [])
    try:
        if t == 'linear' and len(coeffs) >= 2:
            a, b = coeffs[0], coeffs[1]
            return a * raw + b
        elif t == 'quadratic' and len(coeffs) >= 3:
            a, b, c = coeffs[0], coeffs[1], coeffs[2]
            return a * raw * raw + b * raw + c
    except Exception:
        pass
    return raw

class FFmpegRecorder:
    def __init__(self, index, out_path, width=1920, height=1080, fps=30):
        self.index = index
        self.out_path = out_path
        self.width = width
        self.height = height
        self.fps = fps
        self.proc = None

    def start(self):
        import subprocess

        input_spec = f"video={self.index}"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-f", "dshow",
            "-rtbufsize", "512M",
            "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.fps),

            # Ask the capture card for MJPEG (supported at all your resolutions)
            "-vcodec", "mjpeg",

            "-i", input_spec,

            # Convert to a clean pixel format for QuickSync
            "-pix_fmt", "nv12",

            # Hardware encoding
            "-c:v", "h264_qsv",
            "-preset", "veryfast",
            "-global_quality", "23",

            self.out_path
        ]

        self.proc = subprocess.Popen(cmd)


    def stop(self):
        if not self.proc:
            return None

        self.proc.terminate()
        self.proc.wait()

        return self.out_path
    

# -----------------------
# Main App
# -----------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Main Test GUI")

        # Serial
        self.ser = None
        self.reader = None

        # Calibration state
        self.calibration_model = None
        self.calibration_enabled = False
        self.last_raw_voltage = None

        # Live plot data
        self.plot_times = deque(maxlen=PLOT_MAX_POINTS)
        self.plot_pressures = deque(maxlen=PLOT_MAX_POINTS)

        # Build UI
        self.build_ui()

        # schedule serial processing
        self._after_process_serial = None
        self._after_update_plot = None
        self.process_serial_queue()

    def build_ui(self):
        frm = ttk.Frame(self.root, padding=8)
        frm.pack(fill="both", expand=True)

        # Serial controls
        row = 0
        ttk.Label(frm, text="Serial Port").grid(row=row, column=0, sticky="w")
        self.port_cb = ttk.Combobox(frm, values=self.list_serial_ports(), width=20)
        self.port_cb.grid(row=row, column=1, sticky="w")
        self.port_cb.set(self.port_cb['values'][0] if self.port_cb['values'] else "")
        self.btn_refresh = ttk.Button(frm, text="Refresh", command=self.refresh_ports)
        self.btn_refresh.grid(row=row, column=2, padx=4)
        self.btn_connect = ttk.Button(frm, text="Connect", command=self.connect_serial)
        self.btn_connect.grid(row=row, column=3, padx=4)
        self.lbl_serial = ttk.Label(frm, text="Not connected")
        self.lbl_serial.grid(row=row, column=4, sticky="w")

        # Calibration Controls
        row += 1
        cal_frame = ttk.LabelFrame(frm, text="Calibration", padding=6)
        cal_frame.grid(row=row, column=0, columnspan=5, sticky="ew", pady=6)

        self.btn_calibrate = ttk.Button(cal_frame, text="Load Calibration JSON", command=self.load_calibration_file)
        self.btn_calibrate.grid(row=0, column=0, padx=4, pady=4)

        self.lbl_cal_file = ttk.Label(cal_frame, text="No calibration loaded")
        self.lbl_cal_file.grid(row=0, column=1, sticky="w")


        # Cycle Controls 
        row += 1
        cycle_frame = ttk.LabelFrame(frm, text="Cycle Controls", padding=6)
        cycle_frame.grid(row=row, column=0, columnspan=5, sticky="ew", pady=6)

        ttk.Label(cycle_frame, text="Total Cycles").grid(row=0, column=0)
        self.spin_cycles = tk.Spinbox(cycle_frame, from_=1, to=10000, width=6)
        self.spin_cycles.grid(row=0, column=1, padx=4)

        ttk.Label(cycle_frame, text="ON (s)").grid(row=0, column=2)
        self.entry_on = tk.Entry(cycle_frame, width=8)
        self.entry_on.grid(row=0, column=3, padx=4)

        ttk.Label(cycle_frame, text="OFF (s)").grid(row=0, column=4)
        self.entry_off = tk.Entry(cycle_frame, width=8)
        self.entry_off.grid(row=0, column=5, padx=4)

        self.spin_cycles.delete(0, "end")
        self.spin_cycles.insert(0, "20")

        self.entry_on.insert(0, "6")   # default ON time in seconds
        self.entry_off.insert(0, "5")  # default OFF time in seconds

        self.btn_set = ttk.Button(cycle_frame, text="Set Params", command=self.send_set_params)
        self.btn_set.grid(row=0, column=6, padx=6)

        self.btn_start = ttk.Button(cycle_frame, text="Start Run", command=self.send_start)
        self.btn_start.grid(row=0, column=7, padx=6)

        self.btn_stop = ttk.Button(cycle_frame, text="Stop Run", command=self.send_stop)
        self.btn_stop.grid(row=0, column=8, padx=6)

        row += 1

        # Status labels
        self.lbl_cycles = ttk.Label(cycle_frame, text="Cycles left: -")
        self.lbl_cycles.grid(row=1, column=0, sticky="w")
        self.lbl_time = ttk.Label(cycle_frame, text="Run time: 0.0 s")
        self.lbl_time.grid(row=1, column=1, sticky="w")
        self.lbl_rate = ttk.Label(cycle_frame, text="Sample rate: - Hz")
        self.lbl_rate.grid(row=1, column=2, sticky="w")

        # Camera Controls
        cam_frame = ttk.LabelFrame(frm, text="Camera", padding=6)
        cam_frame.grid(row=row, column=0, columnspan=5, sticky="ew", pady=6)

        self.cam_status = ttk.Label(cam_frame, text="Camera: Not connected")
        self.cam_status.grid(row=0, column=0, sticky="w")

        self.cam_index_cb = ttk.Combobox(cam_frame, values=self.detect_cameras(), width=30)
        self.cam_index_cb.grid(row=1, column=0, sticky="w")

        self.btn_cam_connect = ttk.Button(cam_frame, text="Connect Camera", command=self.connect_camera)
        self.btn_cam_connect.grid(row=2, column=0, sticky="w")

        self.btn_cam_disconnect = ttk.Button(cam_frame, text="Disconnect Camera", command=self.disconnect_camera)
        self.btn_cam_disconnect.grid(row=2, column=1, sticky="w")



        # Live raw and calibrated display
        row += 1
        ttk.Label(frm, text="Live raw volts").grid(row=row, column=0, sticky="w")
        self.lbl_raw = ttk.Label(frm, text="(no data)")
        self.lbl_raw.grid(row=row, column=1, sticky="w")
        ttk.Label(frm, text="Calibrated pressure kPa").grid(row=row, column=2, sticky="w")
        self.lbl_pressure = ttk.Label(frm, text="(no data)")
        self.lbl_pressure.grid(row=row, column=3, sticky="w")

        # Console
        row += 1
        ttk.Label(frm, text="Console").grid(row=row, column=0, sticky="w")
        row += 1
        self.console = tk.Text(frm, height=10, width=80, state="disabled")
        self.console.grid(row=row, column=0, columnspan=5, pady=6)

        # Plot area
        row += 1
        if HAS_MATPLOTLIB:
            self.fig, self.ax = plt.subplots(figsize=(6,3))
            self.ax.set_xlabel("Time")
            self.ax.set_ylabel("Pressure kPa")
            self.canvas = FigureCanvasTkAgg(self.fig, master=frm)
            self.canvas.get_tk_widget().grid(row=row, column=0, columnspan=5, sticky="nsew")
            self.schedule_plot_update()
        else:
            ttk.Label(frm, text="Install matplotlib to see live plot").grid(row=row, column=0, columnspan=5)

        
    def send_set_params(self):
        try:
            cycles = int(self.spin_cycles.get())
            on_s = float(self.entry_on.get())
            off_s = float(self.entry_off.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Cycles must be an integer and ON/OFF must be numeric seconds")
            return

        if cycles < 1:
            messagebox.showerror("Invalid input", "Total cycles must be at least 1")
            return

        on_ms = int(on_s * 1000)
        off_ms = int(off_s * 1000)

        self.send_serial(f"CMD:SET CYCLES {cycles}")
        self.send_serial(f"CMD:SET ON {on_ms}")
        self.send_serial(f"CMD:SET OFF {off_ms}")

        self.console_insert(f"Updated parameters → Cycles={cycles}, ON={on_s}s, OFF={off_s}s")

    def send_serial(self, msg):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write((msg + "\n").encode("utf-8"))
                self.console_insert(f"TX → {msg}")
            except Exception as e:
                messagebox.showerror("Serial Error", f"Failed to send command:\n{e}")
        else:
            messagebox.showwarning("Serial", "Not connected to a serial device")

    def detect_cameras(self):
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True
            )
            cams = []
            for line in result.stderr.splitlines():
                if "(video)" in line:
                    name = line.split('"')[1]
                    cams.append(name)
            return cams
        except:
            return []

    def connect_camera(self):
        cam = self.cam_index_cb.get()
        if not cam:
            messagebox.showwarning("Camera", "Select a camera first")
            return

        # Store selected camera
        self.selected_camera = cam
        self.cam_status.config(text=f"Camera connected: {cam}")

    def disconnect_camera(self):
        # Clear selected camera
        self.selected_camera = None
        self.cam_status.config(text="Camera disconnected")


    def gui_start_recording(self):
        cam = self.cam_index_cb.get()
        out_dir = "runs"
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(out_dir, f"run_{ts}.mkv")

        self._ffmpeg = FFmpegRecorder(index=cam, out_path=out_path, width=3840, height=2160, fps=60)
        self._ffmpeg.start()
        self.cam_status.config(text=f"Recording → {out_path}")

    def gui_stop_recording(self):
        if hasattr(self, "_ffmpeg") and self._ffmpeg:
            final = self._ffmpeg.stop()
            self.cam_status.config(text=f"Saved: {final}")
            self._ffmpeg = None


    def send_start(self):
        # Make a new folder for this run
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_run_dir = os.path.join("runs", f"run_{ts}")
        os.makedirs(self.current_run_dir, exist_ok=True)

        # Start camera recording automatically
        cam = getattr(self, "selected_camera", None)
        if cam:
            video_path = os.path.join(self.current_run_dir, "video.mkv")
            self._ffmpeg = FFmpegRecorder(
                index=cam,
                out_path=video_path,
                width=3840,
                height=2160,
                fps=60
            )
            self._ffmpeg.start()
            self.cam_status.config(text=f"Recording → {video_path}")

        # Start pneumatic cycle
        self.send_serial("CMD:START")

    def send_stop(self):
        self.send_serial("CMD:STOP")
        
    def list_serial_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        return ports

    def refresh_ports(self):
        self.port_cb['values'] = self.list_serial_ports()

    def connect_serial(self):
        if self.ser and self.ser.is_open:
            # disconnect
            try:
                if self.reader:
                    self.reader.stop()
                    self.reader = None
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            self.lbl_serial.config(text="Not connected")
            self.btn_connect.config(text="Connect")
            self.console_insert("Serial disconnected")
            return

        port = self.port_cb.get().strip()
        if not port:
            messagebox.showwarning("Serial", "Select a serial port")
            return
        try:
            self.ser = serial.Serial(port, BAUDRATE, timeout=SERIAL_TIMEOUT)
            self.reader = SerialReader(self.ser)
            self.reader.start()
            self.lbl_serial.config(text=f"Connected {port}")
            self.btn_connect.config(text="Disconnect")
            self.console_insert(f"Serial connected {port}")
        except Exception as e:
            messagebox.showerror("Serial", f"Failed to open {port}\n{e}")
            self.ser = None

    def load_calibration_file(self):
        path = filedialog.askopenfilename(
            title="Select calibration config",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            model = data.get("model")
            if not model or "coeffs" not in model:
                messagebox.showerror("Invalid file", "Calibration file missing model/coeffs")
                return
            self.calibration_model = model
            self.calibration_enabled = True
            self.lbl_cal_file.config(text=os.path.basename(path))
            self.console_insert(f"Loaded calibration from {path}")
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    def process_serial_queue(self):
        try:
            while True:
                ts, line = line_q.get_nowait()
                self.handle_serial_line(line)
        except queue.Empty:
            pass
        self._after_process_serial = self.root.after(100, self.process_serial_queue)

    def handle_serial_line(self, line):
  
        # -----------------------------
        # 1. Extract voltage correctly
        # -----------------------------
        # Arduino sends CSV like:  time,voltage,pressureRaw,...
        parts = line.split(',')
        val = None

        if len(parts) >= 2:
            try:
                val = float(parts[2])
            except:
                val = None

        # -----------------------------
        # 2. Update GUI + compute pressure
        # -----------------------------
        pressure = None

        if val is not None:
            self.last_raw_voltage = val
            self.lbl_raw.config(text=f"{val:.3f} V")

            if self.calibration_enabled and self.calibration_model:
                pressure = apply_calibration_model(self.calibration_model, val)
                self.lbl_pressure.config(text=f"{pressure:.3f} kPa")
            else:
                self.lbl_pressure.config(text="(no calibration)")

            # -----------------------------
            # 3. Append to plot
            # -----------------------------
            if pressure is not None:
                self.plot_times.append(datetime.now())
                self.plot_pressures.append(pressure)

            # -----------------------------
            # 4. Write CSV row
            # -----------------------------
            if self.run_active and self.run_file:
                t = time.time() - self.run_start_time_wall
                self.run_file.write(f"{t:.3f},{val:.6f},{pressure:.6f}\n")

        # Always print to console
        self.console_insert(line)

        # -----------------------------
        # 5. NEW RUN
        # -----------------------------
        if line.lower().startswith("--- new run ---"):
            self.run_active = True
            self.sample_count = 0
            self.run_start_time_wall = time.time()

            csv_path = os.path.join(self.current_run_dir, "data.csv")
            self.run_file = open(csv_path, "w")
            self.run_file.write("time_s,volts,pressure_kPa\n")
            return

        # -----------------------------
        # 6. END RUN
        # -----------------------------
        if line.lower().startswith("--- end run ---"):
            self.run_active = False

            if self.run_file:
                self.run_file.close()
                self.run_file = None

            if hasattr(self, "_ffmpeg") and self._ffmpeg:
                final = self._ffmpeg.stop()
                self.cam_status.config(text=f"Saved: {final}")
                self._ffmpeg = None

            return


    def console_insert(self, text):
        try:
            self.console.config(state="normal")
            t = datetime.now().strftime("%H:%M:%S")
            self.console.insert("end", f"[{t}] {text}\n")
            self.console.see("end")
            self.console.config(state="disabled")
        except Exception:
            pass

    def schedule_plot_update(self):
        if not HAS_MATPLOTLIB:
            return
        try:
            self._update_plot()
        except Exception:
            pass
        self._after_update_plot = self.root.after(PLOT_UPDATE_MS, self.schedule_plot_update)

    def _update_plot(self):
        if not self.plot_times:
            return
        try:
            self.ax.cla()
            self.ax.set_xlabel("Time")
            self.ax.set_ylabel("Pressure kPa")
            times = list(self.plot_times)
            pressures = list(self.plot_pressures)
            # convert times to seconds relative
            t0 = times[0]
            xs = [(t - t0).total_seconds() for t in times]
            self.ax.plot(xs, pressures, color='tab:blue')
            self.canvas.draw()
        except Exception:
            pass

    def shutdown(self):
        try:
            if getattr(self, "_after_process_serial", None):
                self.root.after_cancel(self._after_process_serial)
        except Exception:
            pass
        try:
            if getattr(self, "reader", None):
                self.reader.stop()
                self.reader = None
        except Exception:
            pass
        try:
            if getattr(self, "ser", None) and self.ser.is_open:
                self.ser.close()
                self.ser = None
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    try:
        root.protocol("WM_DELETE_WINDOW", app.shutdown)
        root.mainloop()
    except KeyboardInterrupt:
        app.shutdown()