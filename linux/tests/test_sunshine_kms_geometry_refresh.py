"""Exercise the production KMS factory with a fake DRM/layout backend.

Compile just the factory so this regression can run without DRM privileges,
displays or the complete Sunshine dependency graph.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'external/sunshine/src/platform/linux/kmsgrab.cpp'


@unittest.skipUnless(shutil.which('g++') and SOURCE.exists(), 'requires g++ and Sunshine sources')
class KmsGeometryRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        source = SOURCE.read_text()
        start = source.index('  std::shared_ptr<display_t> kms_display(')
        end = source.index('\n  /**', start)
        factory = source[start:end]
        harness = r'''
#include <memory>
#include <string>
#include <vector>
namespace video { struct config_t {}; }
namespace platf {
enum class mem_type_e { system, vaapi, cuda, vulkan };
struct display_t { virtual ~display_t() = default; int origin = -1; };
int live_origin = 1463, cached_origin = -1, enumerations = 0;
bool available = true, fail_vram = false, fail_ram = false;
mem_type_e enumerated_type = mem_type_e::system;
std::vector<std::string> opened;
std::vector<std::string> kms_display_names(mem_type_e type) {
  ++enumerations;
  enumerated_type = type;
  cached_origin = live_origin;
  return available ? std::vector<std::string>{"Virtual-1", "eDP-1"}
                   : std::vector<std::string>{"eDP-1"};
}
namespace kms {
struct display_ram_t : display_t {
  explicit display_ram_t(mem_type_e) {}
  int init(const std::string &name, const video::config_t &) {
    opened.push_back(name);
    origin = cached_origin;
    return fail_ram || !available || name != "Virtual-1";
  }
};
struct display_vram_t : display_ram_t {
  explicit display_vram_t(mem_type_e type) : display_ram_t(type) {}
  int init(const std::string &name, const video::config_t &config) {
    return display_ram_t::init(name, config) || fail_vram;
  }
};
}
FACTORY
}
int main(int, char **argv) {
  using namespace platf;
  const std::string scenario = argv[1];
  auto type = scenario == "system" ? mem_type_e::system :
              scenario == "cuda" ? mem_type_e::cuda :
              scenario == "vulkan" ? mem_type_e::vulkan : mem_type_e::vaapi;
  auto first = kms_display(type, "Virtual-1", {});
  if (!first || first->origin != 1463 || enumerations != 1) return 1;
  // Disabling the physical screen rebases KWin's active screen to zero.
  live_origin = 0;
  available = scenario != "missing";
  fail_vram = scenario == "ram_fallback" || scenario == "both_fail";
  fail_ram = scenario == "both_fail";
  auto second = kms_display(type, "Virtual-1", {});
  if (enumerations != 2 || enumerated_type != type) return 2;
  if (scenario == "missing" || scenario == "both_fail") {
    if (second) return 3;
  } else if (!second || second->origin != 0) return 4;
  for (const auto &name : opened) if (name != "Virtual-1") return 5;
  if (fail_vram && opened.size() != 3) return 6;
  return 0;
}
'''.replace('FACTORY', factory)
        path = Path(cls.directory.name)
        (path / 'regression.cpp').write_text(harness)
        cls.binary = path / 'regression'
        result = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra',
                                 str(path / 'regression.cpp'), '-o', str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def test_recreated_capture_refreshes_geometry_for_each_memory_type(self):
        for scenario in ('system', 'vaapi', 'cuda', 'vulkan'):
            with self.subTest(memory=scenario):
                subprocess.run([str(self.binary), scenario], check=True)

    def test_missing_owned_display_does_not_capture_physical_display(self):
        subprocess.run([str(self.binary), 'missing'], check=True)

    def test_ram_fallback_uses_refreshed_geometry(self):
        subprocess.run([str(self.binary), 'ram_fallback'], check=True)

    def test_both_capture_paths_failing_returns_no_display(self):
        subprocess.run([str(self.binary), 'both_fail'], check=True)
