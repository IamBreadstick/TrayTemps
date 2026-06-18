from __future__ import annotations

import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from typing import Optional

import pystray
from pystray import MenuItem as item

from .config import AppConfig, load_config, save_config
from .formatting import temp_label
from .graph_file import load_ttgraph, register_ttgraph_file_type
from .graph_window import TrayTempsWindow
from .models import TemperatureSnapshot
from .models import AppState
from .pawnio import install_pawnio_interactive, is_pawnio_installed, needs_pawnio_for_cpu, pawnio_status_text
from .sensors import LibreHardwareMonitorReader
from .startup import is_enabled as startup_is_enabled, is_startup_launch, set_enabled as startup_set_enabled
from .tray_icon import make_temp_icon

APP_NAME = "TrayTemps"


class TrayTempsApp:
    def __init__(self) -> None:
        self.config: AppConfig = load_config()
        self.config.start_with_windows = startup_is_enabled()
        self.state = AppState()
        self.reader = LibreHardwareMonitorReader()
        self.stop_event = threading.Event()
        self.cpu_icon: Optional[pystray.Icon] = None
        self.gpu_icon: Optional[pystray.Icon] = None
        self.window: Optional[TrayTempsWindow] = None
        self.root: Optional[tk.Tk] = None
        self._pawnio_prompt_open = False
        self._pawnio_prompt_shown_this_session = False

    def run(self) -> None:
        self.root = tk.Tk()
        # Register only TrayTemps' own graph extension. This does not change CSV associations.
        try:
            register_ttgraph_file_type()
        except Exception:
            pass
        self.window = TrayTempsWindow(self.root, self.state, self.config, self.on_config_changed, self.export_sensor_debug_dump)

        # Start visibly instead of silently hiding in the tray.
        # The first real sensor read happens on the polling thread so a cold backend
        # cannot make the app appear frozen on launch.
        snapshot = self.state.get_latest()

        self.cpu_icon = pystray.Icon(
            f"{APP_NAME} CPU",
            make_temp_icon(snapshot, self.config.unit, "cpu"),
            self._cpu_tooltip(snapshot),
            self._make_menu(),
        )
        self.gpu_icon = pystray.Icon(
            f"{APP_NAME} GPU",
            make_temp_icon(snapshot, self.config.unit, "gpu"),
            self._gpu_tooltip(snapshot),
            self._make_menu(),
        )

        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self._tray_loop, args=(self.cpu_icon,), daemon=True).start()
        threading.Thread(target=self._tray_loop, args=(self.gpu_icon,), daemon=True).start()

        if not is_startup_launch():
            self.root.after(100, self.window.show)
        self.root.mainloop()
        self.shutdown()

    def _tray_loop(self, tray_icon: Optional[pystray.Icon]) -> None:
        if tray_icon is not None:
            tray_icon.run()

    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            snapshot = self.reader.read()
            self.state.add_snapshot(snapshot)
            self._update_tray(snapshot)
            self._maybe_prompt_for_pawnio(snapshot)
            self.stop_event.wait(self.config.refresh_interval_seconds)

    def _update_tray(self, snapshot: Optional[TemperatureSnapshot] = None) -> None:
        if snapshot is None:
            snapshot = self.state.get_latest()
        try:
            if self.cpu_icon is not None:
                self.cpu_icon.icon = make_temp_icon(snapshot, self.config.unit, "cpu")
                self.cpu_icon.title = self._cpu_tooltip(snapshot)
            if self.gpu_icon is not None:
                self.gpu_icon.icon = make_temp_icon(snapshot, self.config.unit, "gpu")
                self.gpu_icon.title = self._gpu_tooltip(snapshot)
        except Exception:
            pass

    def _power_label(self, value: Optional[float]) -> str:
        if value is None:
            return "-- W"
        if value >= 100:
            return f"{value:.0f} W"
        if value >= 10:
            return f"{value:.1f} W"
        return f"{value:.2f} W"

    def _cpu_tooltip(self, snapshot: TemperatureSnapshot) -> str:
        return f"CPU: {temp_label(snapshot.cpu_c, self.config.unit)}, {self._power_label(snapshot.cpu_power_w)} — {snapshot.cpu.name}"

    def _gpu_tooltip(self, snapshot: TemperatureSnapshot) -> str:
        return f"GPU: {temp_label(snapshot.gpu_c, self.config.unit)}, {self._power_label(snapshot.gpu_power_w)} — {snapshot.gpu.name}"

    def _make_menu(self):
        return pystray.Menu(
            item("Open", self.open_window, default=True),
            pystray.Menu.SEPARATOR,
            item("Refresh rate", pystray.Menu(
                item("1 second", lambda: self.set_interval(1), checked=lambda _: self.config.refresh_interval_seconds == 1, radio=True),
                item("2 seconds", lambda: self.set_interval(2), checked=lambda _: self.config.refresh_interval_seconds == 2, radio=True),
                item("5 seconds", lambda: self.set_interval(5), checked=lambda _: self.config.refresh_interval_seconds == 5, radio=True),
            )),
            item("Temperature unit", pystray.Menu(
                item("Celsius", lambda: self.set_unit("C"), checked=lambda _: self.config.unit == "C", radio=True),
                item("Fahrenheit", lambda: self.set_unit("F"), checked=lambda _: self.config.unit == "F", radio=True),
            )),
            item("Start with Windows", self.toggle_startup, checked=lambda _: startup_is_enabled()),
            item("Install enhanced CPU sensor support", self.install_pawnio_from_menu, enabled=lambda _: not is_pawnio_installed()),
            item("Do not ask about enhanced CPU support", self.disable_pawnio_prompt, checked=lambda _: self.config.pawnio_prompt == "never"),
            pystray.Menu.SEPARATOR,
            item("Exit", self.exit_app),
        )


    def _maybe_prompt_for_pawnio(self, snapshot: TemperatureSnapshot) -> None:
        if self.root is None:
            return
        if self.config.pawnio_prompt == "never":
            return
        if self._pawnio_prompt_open or self._pawnio_prompt_shown_this_session:
            return
        need = needs_pawnio_for_cpu(snapshot.cpu_c, snapshot.cpu_status, snapshot.cpu_backend)
        if not need.needed:
            return
        self._pawnio_prompt_shown_this_session = True
        self._pawnio_prompt_open = True
        self.root.after(0, self._show_pawnio_prompt)

    def _show_pawnio_prompt(self) -> None:
        if self.root is None:
            self._pawnio_prompt_open = False
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("TrayTemps needs PawnIO")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = tk.Frame(dialog, padx=18, pady=16)
        frame.pack(fill="both", expand=True)

        title = tk.Label(frame, text="TrayTemps needs PawnIO", font=("Segoe UI", 10, "bold"), anchor="w", justify="left")
        title.pack(fill="x", pady=(0, 8))

        text = (
            "TrayTemps needs PawnIO to read CPU temperature data on this system.\n\n"
            "You can continue without it. GPU temperatures will be available, but CPU temperatures will not."
        )
        label = tk.Label(frame, text=text, wraplength=460, justify="left", anchor="w")
        label.pack(fill="x")

        buttons = tk.Frame(frame)
        buttons.pack(fill="x")

        def close() -> None:
            try:
                dialog.grab_release()
                dialog.destroy()
            except Exception:
                pass
            self._pawnio_prompt_open = False

        def install() -> None:
            started, message = install_pawnio_interactive()
            close()
            if not started:
                try:
                    messagebox.showerror("TrayTemps", message)
                except Exception:
                    pass

        def never() -> None:
            self.config.pawnio_prompt = "never"
            save_config(self.config)
            self._refresh_menus()
            close()

        tk.Button(buttons, text="Install PawnIO", command=install, width=18).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="Not now", command=close, width=12).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="Don’t ask again", command=never, width=16).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", close)
        try:
            dialog.update_idletasks()
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def install_pawnio_from_menu(self, icon=None, menu_item=None) -> None:
        if self.root is None:
            return
        self.root.after(0, self._show_pawnio_prompt)

    def disable_pawnio_prompt(self, icon=None, menu_item=None) -> None:
        self.config.pawnio_prompt = "ask" if self.config.pawnio_prompt == "never" else "never"
        save_config(self.config)
        self._refresh_menus()

    def open_window(self, icon=None, menu_item=None) -> None:
        if self.root is None or self.window is None:
            return
        self.root.after(0, self.window.show)

    def set_interval(self, seconds: int) -> None:
        if seconds not in (1, 2, 5):
            return
        self.config.refresh_interval_seconds = seconds
        save_config(self.config)
        self.on_config_changed()

    def set_unit(self, unit: str) -> None:
        if unit not in ("C", "F"):
            return
        self.config.unit = unit
        save_config(self.config)
        self.on_config_changed()
        self._update_tray()

    def export_sensor_debug_dump(self, icon=None, menu_item=None):
        try:
            return self.reader.export_sensor_debug_dump()
        except Exception:
            return None

    def toggle_startup(self, icon=None, menu_item=None) -> None:
        target = not startup_is_enabled()
        if startup_set_enabled(target):
            self.config.start_with_windows = target
            save_config(self.config)
            self._refresh_menus()

    def _refresh_menus(self) -> None:
        for tray_icon in (self.cpu_icon, self.gpu_icon):
            try:
                if tray_icon is not None:
                    tray_icon.update_menu()
            except Exception:
                pass

    def on_config_changed(self) -> None:
        save_config(self.config)
        self._update_tray()

    def exit_app(self, icon=None, menu_item=None) -> None:
        self.shutdown()

    def shutdown(self) -> None:
        self.stop_event.set()
        for tray_icon in (self.cpu_icon, self.gpu_icon):
            try:
                if tray_icon is not None:
                    tray_icon.stop()
            except Exception:
                pass
        try:
            self.reader.close()
        except Exception:
            pass
        try:
            if self.root is not None:
                self.root.quit()
                self.root.destroy()
        except Exception:
            pass


def _run_graph_viewer(path: Path) -> None:
    config = load_config()
    state, _metadata = load_ttgraph(path)
    root = tk.Tk()
    window = TrayTempsWindow(root, state, config, lambda: save_config(config), None, viewer_mode=True, source_path=path)
    root.after(100, window.show)
    root.mainloop()


def main() -> None:
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        if candidate.suffix.lower() == ".ttgraph" and candidate.exists():
            _run_graph_viewer(candidate)
            return
    app = TrayTempsApp()
    app.run()


if __name__ == "__main__":
    main()
