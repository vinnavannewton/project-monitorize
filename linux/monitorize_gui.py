
"""
Monitorize GUI — PyQt6 control panel
Run from the linux/ directory:  python3 monitorize_gui.py

This file is a thin launcher that delegates to the gui/ package.
"""

import sys
import os


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main_window import main

if __name__ == "__main__":
    main()
