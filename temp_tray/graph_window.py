from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Tuple

from .config import AppConfig, save_config
from .formatting import convert_temp, temp_label
from .graph_file import register_ttgraph_file_type, save_ttgraph
from .models import AppState, TemperatureSnapshot
from .storage import export_dir, open_file


def _windows_prefers_dark() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
    except Exception:
        return False


LIGHT = {
    "bg": "#f3f3f3",
    "panel": "#ffffff",
    "fg": "#111111",
    "muted": "#5f6368",
    "grid": "#e8eaed",
    "axis": "#5f6368",
    "border": "#dadce0",
    "button": "#f8f9fa",
    "button_active": "#e8eaed",
}

DARK = {
    "bg": "#202124",
    "panel": "#26272a",
    "fg": "#f1f3f4",
    "muted": "#bdc1c6",
    "grid": "#3c4043",
    "axis": "#bdc1c6",
    "border": "#4a4d51",
    "button": "#303134",
    "button_active": "#3c4043",
}


class TrayTempsWindow:
    def __init__(
        self,
        root: tk.Tk,
        state: AppState,
        config: AppConfig,
        on_config_changed: Callable[[], None],
        on_export_sensor_dump: Optional[Callable[[], Optional[Path]]] = None,
        viewer_mode: bool = False,
        source_path: Optional[Path] = None,
    ) -> None:
        self.root = root
        self.state = state
        self.config = config
        self.on_config_changed = on_config_changed
        self.on_export_sensor_dump = on_export_sensor_dump
        self.viewer_mode = viewer_mode
        self.source_path = source_path

        self.root.title("TrayTemps Graph Viewer" if viewer_mode else "TrayTemps")
        self.root.geometry("820x560")
        self.root.minsize(720, 460)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy if viewer_mode else self.hide)

        self.cpu_var = tk.StringVar(value="CPU: --")
        self.gpu_var = tk.StringVar(value="GPU: --")
        self.session_var = tk.StringVar(value="Session: --")
        self.refresh_var = tk.StringVar(value="Refresh rate: 2 seconds")
        self.zoom_var = tk.StringVar(value="View: full session")
        self.appearance_var = tk.StringVar(value=self.config.appearance.capitalize())
        self.interval_combo_var = tk.StringVar(value=self._interval_display(self.config.refresh_interval_seconds))
        self.unit_combo_var = tk.StringVar(value=self._unit_display(self.config.unit))

        # None means show the full session. Any value means show the newest N seconds.
        self._view_seconds: Optional[float] = None
        self._current_theme_name = ""
        self._dropdown_menus: list[tk.Menu] = []
        self._hover_points: list[dict] = []

        self.container = tk.Frame(root, padx=14, pady=12)
        self.container.pack(fill=tk.BOTH, expand=True)
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(3, weight=1)
        self._options_visible = False

        self.header = tk.Frame(self.container)
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.columnconfigure(0, weight=1)
        self.title_label = tk.Label(self.header, text="TrayTemps Graph Viewer" if viewer_mode else "TrayTemps", font=("Segoe UI", 18, "bold"))
        self.title_label.grid(row=0, column=0, sticky="w")
        self.options_button = tk.Button(self.header, text="Options", command=self._toggle_options, padx=10, pady=3)
        self.options_button.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.reset_zoom_button = tk.Button(self.header, text="Reset zoom", command=self._reset_zoom, padx=10, pady=3)
        self.reset_zoom_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
        self.clear_button = tk.Button(self.header, text="Clear graph", command=self._clear_graph, padx=10, pady=3)
        if not viewer_mode:
            self.clear_button.grid(row=0, column=3, sticky="e")

        self.status = tk.Frame(self.container)
        self.status.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        self.status.columnconfigure(0, weight=1)
        self.status.columnconfigure(1, weight=0, minsize=170)
        self.cpu_label = tk.Label(self.status, textvariable=self.cpu_var, font=("Segoe UI", 10))
        self.cpu_label.grid(row=0, column=0, sticky="w")
        self.gpu_label = tk.Label(self.status, textvariable=self.gpu_var, font=("Segoe UI", 10))
        self.gpu_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.session_label = tk.Label(self.status, textvariable=self.session_var, font=("Segoe UI", 9))
        self.session_label.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.refresh_label = tk.Label(self.status, textvariable=self.refresh_var, font=("Segoe UI", 9))
        self.refresh_label.grid(row=1, column=1, sticky="e", padx=(12, 0), pady=(2, 0))
        self.zoom_label = tk.Label(self.status, textvariable=self.zoom_var, font=("Segoe UI", 9))
        self.zoom_label.grid(row=2, column=1, sticky="e", padx=(12, 0), pady=(2, 0))

        self.controls = tk.Frame(self.container)
        self.controls.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.controls.columnconfigure(0, weight=0)
        self.controls.columnconfigure(1, weight=0)
        self.controls.columnconfigure(2, weight=0)
        self.controls.columnconfigure(3, weight=0)
        self.controls.columnconfigure(4, weight=0)
        self.controls.columnconfigure(5, weight=1)

        self.refresh_text = tk.Label(self.controls, text="Refresh")
        self.refresh_text.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 8))
        self.interval_combo = self._make_dropdown(
            self.controls,
            self.interval_combo_var,
            ["1 second", "2 seconds", "5 seconds"],
            self._set_interval,
            width=12,
        )
        self.interval_combo.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=(0, 8))

        self.unit_text = tk.Label(self.controls, text="Unit")
        self.unit_text.grid(row=0, column=2, sticky="w", padx=(0, 6), pady=(0, 8))
        self.unit_combo = self._make_dropdown(
            self.controls,
            self.unit_combo_var,
            ["Celsius (°C)", "Fahrenheit (°F)"],
            self._set_unit,
            width=18,
        )
        self.unit_combo.grid(row=0, column=3, sticky="w", padx=(0, 16), pady=(0, 8))

        self.appearance_text = tk.Label(self.controls, text="Appearance")
        self.appearance_text.grid(row=0, column=4, sticky="w", padx=(0, 6), pady=(0, 8))
        self.appearance_combo = self._make_dropdown(
            self.controls,
            self.appearance_var,
            ["System", "Light", "Dark"],
            self._set_appearance,
            width=11,
        )
        self.appearance_combo.grid(row=0, column=5, sticky="w", pady=(0, 8))

        self.save_graph_button = tk.Button(self.controls, text="Save graph", command=self._save_graph_file, padx=10, pady=2)
        self.save_graph_button.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 0))
        self.debug_dump_button = tk.Button(self.controls, text="Create sensor dump", command=self._create_sensor_dump, padx=10, pady=2)
        if not viewer_mode:
            self.debug_dump_button.grid(row=1, column=2, columnspan=3, sticky="w", padx=(0, 0), pady=(0, 0))
        if viewer_mode:
            self.interval_combo.configure(state=tk.DISABLED)
            self.refresh_text.configure(text="Saved")
        self.controls.grid_remove()

        self.canvas = tk.Canvas(self.container, highlightthickness=1)
        self.canvas.grid(row=3, column=0, sticky="nsew")
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)
        self.canvas.bind("<Configure>", lambda _event: self._redraw_graph())
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", lambda _event: self._clear_hover())

        self.legend = tk.Frame(self.container)
        self.legend.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.legend.columnconfigure(4, weight=1)
        self.cpu_swatch = tk.Label(self.legend, text="", width=2, height=1)
        self.cpu_swatch.grid(row=0, column=0, sticky="w")
        self.cpu_legend_label = tk.Label(self.legend, text="CPU", font=("Segoe UI", 9))
        self.cpu_legend_label.grid(row=0, column=1, sticky="w", padx=(6, 18))
        self.gpu_swatch = tk.Label(self.legend, text="", width=2, height=1)
        self.gpu_swatch.grid(row=0, column=2, sticky="w")
        self.gpu_legend_label = tk.Label(self.legend, text="GPU", font=("Segoe UI", 9))
        self.gpu_legend_label.grid(row=0, column=3, sticky="w", padx=(6, 0))

        self._apply_theme()
        if not viewer_mode:
            self.hide()
        self._tick()

    def _make_dropdown(
        self,
        parent: tk.Widget,
        variable: tk.StringVar,
        values: list[str],
        command: Callable[[], None],
        width: int,
    ) -> tk.Menubutton:
        button = tk.Menubutton(
            parent,
            textvariable=variable,
            indicatoron=False,
            width=width,
            anchor="w",
            padx=7,
            pady=2,
            relief=tk.FLAT,
            borderwidth=1,
            highlightthickness=1,
        )
        menu = tk.Menu(button, tearoff=False, borderwidth=0, activeborderwidth=0)
        self._dropdown_menus.append(menu)

        def choose(value: str) -> None:
            variable.set(value)
            command()
            # Return focus to the root so the control does not remain visually selected.
            self.root.focus_set()

        for value in values:
            menu.add_command(label=value, command=lambda v=value: choose(v))
        button.configure(menu=menu)
        return button

    def _interval_display(self, seconds: int) -> str:
        return "1 second" if seconds == 1 else f"{seconds} seconds"

    def _unit_display(self, unit: str) -> str:
        return "Fahrenheit (°F)" if unit == "F" else "Celsius (°C)"

    def _theme_name(self) -> str:
        if self.config.appearance == "dark":
            return "dark"
        if self.config.appearance == "light":
            return "light"
        return "dark" if _windows_prefers_dark() else "light"

    def _palette(self):
        return DARK if self._theme_name() == "dark" else LIGHT

    def _apply_theme(self) -> None:
        theme_name = self._theme_name()
        if theme_name == self._current_theme_name:
            return
        self._current_theme_name = theme_name
        p = self._palette()
        self.root.configure(bg=p["bg"])
        for frame in (self.container, self.header, self.status, self.controls, self.legend):
            frame.configure(bg=p["bg"])
        for label in (
            self.title_label,
            self.cpu_label,
            self.gpu_label,
            self.session_label,
            self.refresh_label,
            self.zoom_label,
            self.refresh_text,
            self.cpu_legend_label,
            self.gpu_legend_label,
            self.unit_text,
            self.appearance_text,
        ):
            label.configure(bg=p["bg"], fg=p["fg"])
        for button in (self.options_button, self.reset_zoom_button, self.clear_button, self.save_graph_button, self.debug_dump_button):
            button.configure(
                bg=p["button"],
                fg=p["fg"],
                activebackground=p["button_active"],
                activeforeground=p["fg"],
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=p["border"],
                highlightcolor=p["border"],
            )
        for dropdown in (self.interval_combo, self.unit_combo, self.appearance_combo):
            dropdown.configure(
                bg=p["button"],
                fg=p["fg"],
                activebackground=p["button_active"],
                activeforeground=p["fg"],
                disabledforeground=p["muted"],
                relief=tk.FLAT,
                highlightbackground=p["border"],
                highlightcolor=p["border"],
            )
        for menu in self._dropdown_menus:
            menu.configure(
                bg=p["button"],
                fg=p["fg"],
                activebackground=p["button_active"],
                activeforeground=p["fg"],
                disabledforeground=p["muted"],
                relief=tk.FLAT,
            )
        self.canvas.configure(bg=p["panel"], highlightbackground=p["border"])

    def show(self) -> None:
        self._apply_theme()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        self.root.withdraw()

    def toggle(self) -> None:
        if self.root.state() == "withdrawn":
            self.show()
        else:
            self.hide()

    def _toggle_options(self) -> None:
        self._options_visible = not self._options_visible
        if self._options_visible:
            self.controls.grid()
        else:
            self.controls.grid_remove()

    def _set_interval(self, _event=None) -> None:
        text = self.interval_combo_var.get()
        seconds = 2
        if text.startswith("1"):
            seconds = 1
        elif text.startswith("5"):
            seconds = 5
        self.config.refresh_interval_seconds = seconds
        save_config(self.config)
        self.on_config_changed()

    def _set_unit(self, _event=None) -> None:
        text = self.unit_combo_var.get()
        self.config.unit = "F" if "Fahrenheit" in text else "C"
        save_config(self.config)
        self.on_config_changed()
        self._redraw_graph()

    def _set_appearance(self, _event=None) -> None:
        value = self.appearance_var.get().strip().lower()
        if value not in ("system", "light", "dark"):
            value = "system"
        self.config.appearance = value
        save_config(self.config)
        self._current_theme_name = ""
        self._apply_theme()
        self._redraw_graph()

    def _clear_graph(self) -> None:
        self.state.clear_history()
        self._reset_zoom(redraw=False)
        self._redraw_graph()

    def _create_sensor_dump(self) -> None:
        if self.on_export_sensor_dump is None:
            return
        try:
            path = self.on_export_sensor_dump()
            if path is not None:
                messagebox.showinfo("TrayTemps", f"Sensor dump saved:\n{path}")
        except Exception as exc:
            messagebox.showerror("TrayTemps", f"Failed to create sensor dump:\n{type(exc).__name__}: {exc}")

    def _save_graph_file(self) -> None:
        history = self.state.get_history()
        if not history:
            messagebox.showinfo("TrayTemps", "No graph data to save yet.")
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = export_dir("graph_files") / f"TrayTemps-graph-{stamp}.ttgraph"
        try:
            register_ttgraph_file_type()
            save_ttgraph(path, history, self.state.session_started)
            open_file(path)
            messagebox.showinfo("TrayTemps", f"TrayTemps graph saved:\n{path}")
        except Exception as exc:
            messagebox.showerror("TrayTemps", f"Failed to save TrayTemps graph:\n{type(exc).__name__}: {exc}")

    def _reset_zoom(self, redraw: bool = True) -> None:
        self._view_seconds = None
        if redraw:
            self._redraw_graph()

    def _tick(self) -> None:
        self._apply_theme()
        self._update_labels()
        self._redraw_graph()
        self.root.after(1000, self._tick)

    def _update_labels(self) -> None:
        latest = self.state.get_latest()
        unit = self.config.unit
        self.cpu_var.set(self._device_line("CPU", latest.cpu_c, latest.cpu.name, latest.cpu_backend, latest.cpu_status, unit))
        self.gpu_var.set(self._device_line("GPU", latest.gpu_c, latest.gpu.name, latest.gpu_backend, latest.gpu_status, unit))
        self.cpu_swatch.configure(bg=_hex(latest.cpu.color))
        self.gpu_swatch.configure(bg=_hex(latest.gpu.color))

        history = self.state.get_history()
        if self.viewer_mode and history:
            elapsed = max(timedelta(), history[-1].timestamp - self.state.session_started)
        else:
            elapsed = max(timedelta(), datetime.now() - self.state.session_started)
        total_seconds = int(elapsed.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        self.session_var.set(f"Session: {hours:02d}:{minutes:02d}:{seconds:02d}")
        if self.viewer_mode:
            self.refresh_var.set("Saved graph")
        else:
            self.refresh_var.set(f"Refresh rate: {self.config.refresh_interval_seconds} seconds")

        unit_display = self._unit_display(unit)
        if self.unit_combo_var.get() != unit_display:
            self.unit_combo_var.set(unit_display)
        interval_display = self._interval_display(self.config.refresh_interval_seconds)
        if self.interval_combo_var.get() != interval_display:
            self.interval_combo_var.set(interval_display)
        appearance_display = self.config.appearance.capitalize()
        if self.appearance_var.get() != appearance_display:
            self.appearance_var.set(appearance_display)


    def _device_line(self, label: str, temp_c, name: str, backend: str, status: str, unit: str) -> str:
        if self.viewer_mode:
            return f"{label}: {name}"
        latest = self.state.get_latest()
        power = latest.cpu_power_w if label == "CPU" else latest.gpu_power_w
        return f"{label}: {temp_label(temp_c, unit)}, {power_label(power)} — {name}"
    def _on_canvas_motion(self, event) -> None:
        if not self._hover_points:
            self._clear_hover()
            return
        nearest = min(
            self._hover_points,
            key=lambda p: (p["screen_x"] - event.x) ** 2 + (p["screen_y"] - event.y) ** 2,
        )
        distance_sq = (nearest["screen_x"] - event.x) ** 2 + (nearest["screen_y"] - event.y) ** 2
        if distance_sq > 18 ** 2:
            self._clear_hover()
            return
        self._draw_hover(nearest)

    def _clear_hover(self) -> None:
        try:
            self.canvas.delete("hover")
        except Exception:
            pass

    def _draw_hover(self, point: dict) -> None:
        self.canvas.delete("hover")
        p = self._palette()
        x = float(point["screen_x"])
        y = float(point["screen_y"])
        unit = self.config.unit
        power = power_label(point.get("power_w"))
        time_text = point["timestamp"].strftime("%H:%M:%S") if hasattr(point.get("timestamp"), "strftime") else ""
        value_text = f"{point['value']:.1f} °{unit}"
        label = f"{point['label']}  {value_text}  {power}  {time_text}"

        self.canvas.create_line(x, 34, x, max(self.canvas.winfo_height() - 38, 34), fill=p["axis"], dash=(3, 3), tags="hover")
        self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=_hex(point.get("color", (255, 255, 255))), outline=p["panel"], width=1, tags="hover")

        text_id = self.canvas.create_text(x + 10, y - 10, text=label, anchor="sw", fill=p["fg"], font=("Segoe UI", 9), tags="hover")
        bbox = self.canvas.bbox(text_id)
        if not bbox:
            return
        pad = 6
        x1, y1, x2, y2 = bbox
        width = self.canvas.winfo_width()
        dx = 0
        if x2 + pad > width - 8:
            dx = (width - 8) - (x2 + pad)
        if x1 + dx - pad < 8:
            dx += 8 - (x1 + dx - pad)
        if dx:
            self.canvas.move(text_id, dx, 0)
            bbox = self.canvas.bbox(text_id)
            if not bbox:
                return
            x1, y1, x2, y2 = bbox
        rect = self.canvas.create_rectangle(x1 - pad, y1 - pad, x2 + pad, y2 + pad, fill=p["panel"], outline=p["border"], tags="hover")
        self.canvas.tag_raise(text_id, rect)

    def _on_mousewheel(self, event) -> None:
        history = self.state.get_history()
        if len(history) < 2:
            return

        width = max(self.canvas.winfo_width(), 10)
        margin_left, margin_right = 56, 20
        plot_w = max(1, width - margin_left - margin_right)
        if event.x < margin_left or event.x > margin_left + plot_w:
            return

        full_start = history[0].timestamp
        full_end = history[-1].timestamp
        full_seconds = max(1.0, (full_end - full_start).total_seconds())
        view_start, view_end = self._current_view_bounds(history)
        view_seconds = max(1.0, (view_end - view_start).total_seconds())

        if getattr(event, "num", None) == 4:
            zoom_in = True
        elif getattr(event, "num", None) == 5:
            zoom_in = False
        else:
            zoom_in = getattr(event, "delta", 0) > 0

        if zoom_in:
            self._view_seconds = max(20.0, view_seconds * 0.75)
        else:
            new_seconds = view_seconds / 0.75
            if new_seconds >= full_seconds * 0.98:
                self._reset_zoom()
                return
            self._view_seconds = new_seconds
        self._redraw_graph()

    def _current_view_bounds(self, history: list[TemperatureSnapshot]) -> Tuple[datetime, datetime]:
        full_start = history[0].timestamp
        full_end = history[-1].timestamp
        full_seconds = max(1.0, (full_end - full_start).total_seconds())
        if self._view_seconds is None or self._view_seconds >= full_seconds * 0.98:
            return full_start, full_end
        view_seconds = max(1.0, self._view_seconds)
        view_end = full_end
        view_start = max(full_start, view_end - timedelta(seconds=view_seconds))
        return view_start, view_end

    def _redraw_graph(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        p = self._palette()
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)

        margin_left = 56
        margin_right = 20
        margin_top = 34
        margin_bottom = 38
        plot_w = max(1, width - margin_left - margin_right)
        plot_h = max(1, height - margin_top - margin_bottom)

        history = self.state.get_history()
        unit = self.config.unit

        canvas.create_text(width // 2, 10, text=f"Temperature °{unit}", anchor="n", fill=p["fg"], font=("Segoe UI", 9))
        canvas.create_text(margin_left, 10, text="Scroll to zoom", anchor="nw", fill=p["muted"], font=("Segoe UI", 8))
        canvas.create_line(margin_left, margin_top, margin_left, margin_top + plot_h, fill=p["axis"])
        canvas.create_line(margin_left, margin_top + plot_h, margin_left + plot_w, margin_top + plot_h, fill=p["axis"])

        points_cpu = []
        points_gpu = []
        all_values = []
        view_start = view_end = None
        if history:
            view_start, view_end = self._current_view_bounds(history)
            view_seconds = max(1.0, (view_end - view_start).total_seconds())
            visible_history = [s for s in history if view_start <= s.timestamp <= view_end]
            if not visible_history:
                visible_history = [history[-1]]
            for snap in visible_history:
                x_ratio = (snap.timestamp - view_start).total_seconds() / view_seconds
                x = margin_left + max(0.0, min(1.0, x_ratio)) * plot_w
                cpu_v = convert_temp(snap.cpu_c, unit)
                gpu_v = convert_temp(snap.gpu_c, unit)
                if cpu_v is not None:
                    points_cpu.append({
                        "x": x,
                        "value": cpu_v,
                        "label": "CPU",
                        "timestamp": snap.timestamp,
                        "power_w": snap.cpu_power_w,
                        "color": snap.cpu.color,
                    })
                    all_values.append(cpu_v)
                if gpu_v is not None:
                    points_gpu.append({
                        "x": x,
                        "value": gpu_v,
                        "label": "GPU",
                        "timestamp": snap.timestamp,
                        "power_w": snap.gpu_power_w,
                        "color": snap.gpu.color,
                    })
                    all_values.append(gpu_v)

        if not all_values:
            self.zoom_var.set("View: full session")
            canvas.create_text(width // 2, height // 2, text="Waiting for temperature data...", fill=p["muted"], font=("Segoe UI", 11))
            return

        min_v = min(all_values)
        max_v = max(all_values)
        if max_v - min_v < 10:
            mid = (max_v + min_v) / 2
            min_v = mid - 5
            max_v = mid + 5
        else:
            pad = (max_v - min_v) * 0.12
            min_v -= pad
            max_v += pad

        def to_xy(point):
            x = point["x"]
            v = point["value"]
            y_ratio = (v - min_v) / (max_v - min_v)
            y = margin_top + plot_h - y_ratio * plot_h
            return x, y

        for i in range(5):
            ratio = i / 4
            v = max_v - ratio * (max_v - min_v)
            y = margin_top + ratio * plot_h
            canvas.create_line(margin_left, y, margin_left + plot_w, y, fill=p["grid"])
            canvas.create_text(margin_left - 10, y, text=str(int(round(v))), anchor="e", fill=p["muted"], font=("Segoe UI", 8))

        self._hover_points = []

        def draw_series(points, color):
            if len(points) == 1:
                x, y = to_xy(points[0])
                canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=_hex(color), outline="")
                self._hover_points.append({**points[0], "screen_x": x, "screen_y": y})
                return
            coords = []
            for point in points:
                x, y = to_xy(point)
                coords.extend((x, y))
                self._hover_points.append({**point, "screen_x": x, "screen_y": y})
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=_hex(color), width=2, smooth=True)

        latest = self.state.get_latest()
        draw_series(points_cpu, latest.cpu.color)
        draw_series(points_gpu, latest.gpu.color)

        if history and view_start is not None and view_end is not None:
            full_start = history[0].timestamp
            full_end = history[-1].timestamp
            full_seconds = max(1.0, (full_end - full_start).total_seconds())
            view_seconds = max(1.0, (view_end - view_start).total_seconds())
            if view_seconds >= full_seconds * 0.98:
                self.zoom_var.set("View: full session")
            else:
                self.zoom_var.set(f"View: last {format_duration(view_seconds)}")
            canvas.create_text(margin_left, height - 14, text=view_start.strftime("%H:%M:%S"), anchor="w", fill=p["muted"], font=("Segoe UI", 8))
            canvas.create_text(margin_left + plot_w, height - 14, text=view_end.strftime("%H:%M:%S"), anchor="e", fill=p["muted"], font=("Segoe UI", 8))


# Backwards-compatible alias for existing imports.
TempTrayWindow = TrayTempsWindow


def power_label(value: Optional[float]) -> str:
    if value is None:
        return "-- W"
    if value >= 100:
        return f"{value:.0f} W"
    if value >= 10:
        return f"{value:.1f} W"
    return f"{value:.2f} W"


def format_duration(seconds: float) -> str:
    seconds_i = int(seconds)
    if seconds_i < 60:
        return f"{seconds_i}s"
    minutes = seconds_i // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h {minutes % 60}m"


def _hex(color) -> str:
    return "#%02x%02x%02x" % color
