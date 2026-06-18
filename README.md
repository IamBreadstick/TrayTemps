TrayTemps is a lightweight Windows tray temperature monitor for CPU and GPU temperatures.

- TrayTemps works without PawnIO.
- GPU temperatures usually work without PawnIO.
- CPU temperatures may require PawnIO because LibreHardwareMonitor uses it for low-level CPU sensor access on many systems.
- TrayTemps checks whether CPU readings are available. If CPU temperature is unavailable and PawnIO is missing, TrayTemps prompts only when needed.
- You can continue without PawnIO; GPU temperatures remain available, but CPU temperatures will not be.

TrayTemps saves user-created files inside the app folder:

TrayTempsData/
sensor_dumps/ debug sensor dumps
graph_csv/ raw CSV exports
graph_files/ TrayTemps graph files (.ttgraph)
