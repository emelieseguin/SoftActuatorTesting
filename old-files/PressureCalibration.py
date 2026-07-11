#!/usr/bin/env python3
"""
Standalone Calibration Tool
- Connect to serial, record known pressure vs volts samples
- Fit linear or quadratic model
- Save calibration JSON file for the main GUI to load
Save as: calibration_tool.py
Requirements:
  pip install pyserial numpy matplotlib   # numpy and matplotlib optional but recommended
Run: python calibration_tool.py
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import queue
import time
import json
import os
import serial
import serial.tools.list_ports
from datetime import datetime

# Optional plotting and numpy
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except Exception:
    HAS_MATPLOTLIB = False

try:
    import numpy as np
    HAS_NUMPY = True
except Exception:
    HAS_NUMPY = False

line_q = queue.Queue()

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

# Simple linear fit
def linear_fit(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    a = num / den
    b = mean_y - a * mean_x
    return (a, b)

class CalibrationApp:
    def __init__(self, root):
        self.root = root
        root.title("Calibration Tool")

        self.ser = None
        self.reader = None

        self.samples = []  # list of (known_kPa, volts)
        self.model = None  # dict {'type':..., 'coeffs':[...]}

        self.last_raw_voltage = None

        self.build_ui()
        self.process_serial_queue()

    def build_ui(self):
        frm = ttk.Frame(self.root, padding=8)
        frm.pack(fill="both", expand=True)

        row = 0
        ttk.Label(frm, text="Serial Port").grid(row=row, column=0, sticky="w")
        self.port_cb = ttk.Combobox(frm, values=self.list_serial_ports(), width=20)
        self.port_cb.grid(row=row, column=1, sticky="w")
        self.btn_refresh = ttk.Button(frm, text="Refresh", command=self.refresh_ports)
        self.btn_refresh.grid(row=row, column=2, padx=4)
        self.btn_connect = ttk.Button(frm, text="Connect", command=self.connect_serial)
        self.btn_connect.grid(row=row, column=3, padx=4)
        self.lbl_serial = ttk.Label(frm, text="Not connected")
        self.lbl_serial.grid(row=row, column=4, sticky="w")

        row += 1
        ttk.Label(frm, text="Known pressure kPa").grid(row=row, column=0, sticky="w")
        self.entry_known = ttk.Entry(frm, width=12)
        self.entry_known.grid(row=row, column=1, sticky="w")
        self.btn_record = ttk.Button(frm, text="Record Sample", command=self.record_sample)
        self.btn_record.grid(row=row, column=2, padx=4)
        self.btn_request = ttk.Button(frm, text="Request Sample", command=self.request_sample)
        self.btn_request.grid(row=row, column=3, padx=4)

        row += 1
        ttk.Label(frm, text="Live raw volts").grid(row=row, column=0, sticky="w")
        self.lbl_live = ttk.Label(frm, text="(no data)")
        self.lbl_live.grid(row=row, column=1, sticky="w")

        row += 1
        self.samples_list = tk.Listbox(frm, height=8, width=60)
        self.samples_list.grid(row=row, column=0, columnspan=5, pady=6)

        row += 1
        self.btn_remove = ttk.Button(frm, text="Remove Selected", command=self.remove_selected)
        self.btn_remove.grid(row=row, column=0, sticky="w")
        self.btn_clear = ttk.Button(frm, text="Clear All", command=self.clear_all)
        self.btn_clear.grid(row=row, column=1, sticky="w", padx=4)

        row += 1
        ttk.Label(frm, text="Fit type").grid(row=row, column=0, sticky="w")
        self.fit_type_cb = ttk.Combobox(frm, values=["linear", "quadratic"], width=12)
        self.fit_type_cb.grid(row=row, column=1, sticky="w")
        self.fit_type_cb.set("linear")
        self.btn_fit = ttk.Button(frm, text="Fit Model", command=self.fit_model)
        self.btn_fit.grid(row=row, column=2, padx=4)
        self.lbl_fit = ttk.Label(frm, text="No model")
        self.lbl_fit.grid(row=row, column=3, sticky="w")

        row += 1
        self.btn_save = ttk.Button(frm, text="Save Calibration", command=self.save_calibration)
        self.btn_save.grid(row=row, column=0, sticky="w")
        self.btn_load = ttk.Button(frm, text="Load Calibration", command=self.load_calibration)
        self.btn_load.grid(row=row, column=1, sticky="w", padx=4)

        row += 1
        if HAS_MATPLOTLIB:
            plot_frame = ttk.LabelFrame(frm, text="Calibration Plot", padding=6)
            plot_frame.grid(row=row, column=0, columnspan=5, sticky="nsew", pady=6)
            self.fig, self.ax = plt.subplots(figsize=(6,3))
            self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            self.ax.set_xlabel("Volts (V)")
            self.ax.set_ylabel("Known pressure (kPa)")
            self.ax.grid(True)
        else:
            ttk.Label(frm, text="Install matplotlib to see calibration plot").grid(row=row, column=0, columnspan=5)

    def list_serial_ports(self):
        return [p.device for p in serial.tools.list_ports.comports()]

    def refresh_ports(self):
        self.port_cb['values'] = self.list_serial_ports()

    def connect_serial(self):
        if self.ser and self.ser.is_open:
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
            return

        port = self.port_cb.get().strip()
        if not port:
            messagebox.showwarning("Serial", "Select a serial port")
            return
        try:
            self.ser = serial.Serial(port, 115200, timeout=0.5)
            self.reader = SerialReader(self.ser)
            self.reader.start()
            self.lbl_serial.config(text=f"Connected {port}")
            self.btn_connect.config(text="Disconnect")
        except Exception as e:
            messagebox.showerror("Serial", f"Failed to open {port}\n{e}")
            self.ser = None

    def process_serial_queue(self):
        try:
            while True:
                ts, line = line_q.get_nowait()
                self.handle_serial_line(line)
        except queue.Empty:
            pass
        self.root.after(100, self.process_serial_queue)

    def handle_serial_line(self, line):
        # Try to extract a float voltage from the line
        import re
        m = re.search(r"([-+]?\d*\.\d+|\d+)", line)
        if m:
            try:
                val = float(m.group(0))
            except Exception:
                val = None
        else:
            val = None
        if val is not None:
            self.last_raw_voltage = val
            self.lbl_live.config(text=f"{val:.3f} V")
            self.plot_model()
        # also print to console via window title briefly
        # (keeps UI responsive without a large console widget)
        # self.root.title(f"Calibration Tool - last: {line}")

    def record_sample(self):
        try:
            known = float(self.entry_known.get())
        except Exception:
            messagebox.showerror("Invalid", "Enter numeric known pressure (kPa)")
            return
        if self.last_raw_voltage is None:
            messagebox.showwarning("No data", "No raw voltage available to record")
            return
        self.samples.append((known, self.last_raw_voltage))
        self.refresh_samples_list()
        self.entry_known.delete(0, 'end')

    def request_sample(self):
        # If device supports a CAL_ON/CAL_OFF command, user can implement it on Arduino.
        # Here we simply wait a short time and capture the last_raw_voltage.
        if not (self.ser and self.ser.is_open):
            messagebox.showwarning("Serial", "Serial not connected")
            return
        # Optionally send a command to Arduino to enable streaming for a short window
        try:
            self.ser.write(b"CMD:CAL_ON\n")
        except Exception:
            pass
        # schedule capture after 300 ms
        def capture():
            try:
                if self.ser:
                    try:
                        self.ser.write(b"CMD:CAL_OFF\n")
                    except Exception:
                        pass
                v = self.last_raw_voltage
                if v is None:
                    messagebox.showwarning("No reading", "No voltage reading received during request window.")
                    return
                # if user entered known value, save automatically
                try:
                    known_text = self.entry_known.get().strip()
                    known = float(known_text) if known_text != "" else None
                except Exception:
                    known = None
                if known is None:
                    messagebox.showinfo("Sample captured", f"Volts: {v:.3f} V\nEnter known pressure and press Record Sample to save.")
                    return
                self.samples.append((known, v))
                self.refresh_samples_list()
                self.entry_known.delete(0, 'end')
            except Exception as e:
                messagebox.showerror("Error", str(e))
        self.root.after(300, capture)

    def refresh_samples_list(self):
        self.samples_list.delete(0, 'end')
        for k, v in self.samples:
            self.samples_list.insert('end', f"Known: {k:.3f} kPa  Volts: {v:.3f} V")
        self.plot_model()

    def remove_selected(self):
        sel = self.samples_list.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.samples[idx]
        self.refresh_samples_list()

    def clear_all(self):
        if messagebox.askyesno("Clear", "Clear all samples?"):
            self.samples = []
            self.model = None
            self.refresh_samples_list()
            self.update_model_label()

    def fit_model(self):
        if len(self.samples) < 2:
            messagebox.showwarning("Not enough samples", "Need at least 2 samples to fit")
            return
        xs = [v for (k, v) in self.samples]  # volts
        ys = [k for (k, v) in self.samples]  # known pressures
        fit_type = self.fit_type_cb.get()
        if fit_type == "linear":
            res = linear_fit(xs, ys)
            if res is None:
                messagebox.showerror("Fit failed", "Linear fit failed")
                return
            a, b = res
            self.model = {'type': 'linear', 'coeffs': [a, b]}
            self.update_model_label()
            self.plot_model()
            messagebox.showinfo("Fit complete", f"Linear fit: pressure_kPa = {a:.6g}*volts + {b:.6g}")
            return
        elif fit_type == "quadratic":
            if not HAS_NUMPY:
                messagebox.showerror("Numpy required", "Quadratic fit requires numpy")
                return
            coeffs = np.polyfit(xs, ys, 2)
            self.model = {'type': 'quadratic', 'coeffs': [float(coeffs[0]), float(coeffs[1]), float(coeffs[2])]}
            self.update_model_label()
            self.plot_model()
            messagebox.showinfo("Fit complete", f"Quadratic coeffs: {self.model['coeffs']}")
            return
        else:
            messagebox.showerror("Unknown fit", "Unsupported fit type")

    def update_model_label(self):
        if not self.model:
            self.lbl_fit.config(text="No model")
            return
        t = self.model.get('type', 'linear')
        coeffs = self.model.get('coeffs', [])
        if t == 'linear' and len(coeffs) >= 2:
            a, b = coeffs[0], coeffs[1]
            self.lbl_fit.config(text=f"Linear: pressure_kPa = {a:.6g}*volts + {b:.6g}")
        elif t == 'quadratic' and len(coeffs) >= 3:
            a, b, c = coeffs[0], coeffs[1], coeffs[2]
            self.lbl_fit.config(text=f"Quad: {a:.6g}*volts^2 + {b:.6g}*volts + {c:.6g}")
        else:
            self.lbl_fit.config(text="Model present")

    def plot_model(self):
        if not HAS_MATPLOTLIB:
            return
        try:
            self.ax.cla()
            self.ax.set_xlabel("Volts (V)")
            self.ax.set_ylabel("Known pressure (kPa)")
            self.ax.grid(True)
            if self.samples:
                xs = [v for (k, v) in self.samples]
                ys = [k for (k, v) in self.samples]
                self.ax.scatter(xs, ys, color='blue', label='samples')
                xmin = min(xs); xmax = max(xs)
                rng = xmax - xmin if xmax > xmin else 1.0
                xs_plot = [xmin - 0.1*rng + i*(1.2*rng)/200.0 for i in range(201)]
                if self.model:
                    # apply model
                    def apply_model(m, x):
                        t = m.get('type', 'linear')
                        coeffs = m.get('coeffs', [])
                        try:
                            if t == 'linear' and len(coeffs) >= 2:
                                a, b = coeffs[0], coeffs[1]
                                return a * x + b
                            elif t == 'quadratic' and len(coeffs) >= 3:
                                a, b, c = coeffs[0], coeffs[1], coeffs[2]
                                return a * x * x + b * x + c
                        except Exception:
                            pass
                        return None
                    ys_plot = [apply_model(self.model, x) for x in xs_plot]
                    self.ax.plot(xs_plot, ys_plot, color='red', label='fit')
                self.ax.legend()
            self.canvas.draw()
        except Exception:
            pass

    def save_calibration(self):
        if not self.model:
            if not messagebox.askyesno("No model", "No model fitted. Save samples only?"):
                return
        path = filedialog.asksaveasfilename(
            title="Save calibration JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        data = {'model': self.model, 'samples': self.samples}
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Saved", f"Calibration saved to {path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def load_calibration(self):
        path = filedialog.askopenfilename(
            title="Load calibration JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            self.model = data.get('model')
            self.samples = data.get('samples', [])
            self.refresh_samples_list()
            self.update_model_label()
            messagebox.showinfo("Loaded", f"Loaded calibration from {path}")
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    def shutdown(self):
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
    app = CalibrationApp(root)
    try:
        root.protocol("WM_DELETE_WINDOW", app.shutdown)
        root.mainloop()
    except KeyboardInterrupt:
        app.shutdown()