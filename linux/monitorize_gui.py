
"""
Monitorize GUI — PyQt6 control panel
Run from the linux/ directory:  python3 monitorize_gui.py

This file is a thin launcher that delegates to the gui/ package.
"""

from gui import streaming_controller
from gui.main_window import main

if __name__ == "__main__":
    print(
        f"[Monitorize] Loaded streaming controller: "
        f"{streaming_controller.__file__}",
        flush=True,
    )
    main()
