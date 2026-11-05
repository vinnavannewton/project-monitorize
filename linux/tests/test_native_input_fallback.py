"""Compile the production mouse fallback branch against a recording pointer sink."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

@unittest.skipUnless(shutil.which('g++'), 'requires a C++ compiler')
class NativeTouchFallbackTests(unittest.TestCase):
    def test_contact_ownership_drag_and_cancel(self):
        source = (ROOT / 'external/sunshine/src/platform/virtualhid_input.cpp').read_text()
        start = source.index('    if (!context.native_touch)', source.index('  void touch_update(client_context_t'))
        end = source.index('    if (!context.touch)', start)
        branch = source[start:end]
        harness = r'''
#include <optional>
#include <cstdint>
#include <cassert>
struct Client { bool native_touch=false; std::optional<std::uint32_t> emulated_touch; int *global; bool emulated_pen=false; };
struct Touch { int eventType; std::uint32_t pointerId; float x=.25f,y=.5f; };
struct Port {};
enum { LI_TOUCH_EVENT_DOWN, LI_TOUCH_EVENT_MOVE, LI_TOUCH_EVENT_UP, LI_TOUCH_EVENT_CANCEL, LI_TOUCH_EVENT_CANCEL_ALL };
int down=0,up=0,moves=0;
void button_mouse(int &, int, bool release) { if(release)++up; else ++down; }
void emulated_pointer(Client &, const Port &, float, float) { ++moves; }
void update(Client &context, const Port &touch_port, const Touch &touch) {
BRANCH
}
int main() {
 int global=0; Client c{false, {}, &global}; Port p;
 update(c,p,{LI_TOUCH_EVENT_DOWN,7});
 update(c,p,{LI_TOUCH_EVENT_DOWN,8});
 assert(down==1 && moves==1 && c.emulated_touch==7);
 update(c,p,{LI_TOUCH_EVENT_MOVE,8}); assert(moves==1);
 update(c,p,{LI_TOUCH_EVENT_MOVE,7}); assert(moves==2);
 update(c,p,{LI_TOUCH_EVENT_CANCEL_ALL,0}); assert(up==1 && !c.emulated_touch);
 update(c,p,{LI_TOUCH_EVENT_MOVE,8}); assert(moves==2 && down==1);
 update(c,p,{LI_TOUCH_EVENT_DOWN,9}); update(c,p,{LI_TOUCH_EVENT_UP,9});
 assert(up==2 && down==2 && !c.emulated_touch);
}
'''.replace('BRANCH', branch)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'test.cpp';path.write_text(harness)
            subprocess.run(['g++','-std=c++20',str(path),'-o',str(Path(d)/'test')],check=True,capture_output=True)
            subprocess.run([str(Path(d)/'test')],check=True)
