import unittest
from unittest.mock import Mock, patch

from monitorize.platform import kde_virtual_monitor


def output(
    name,
    output_id,
    x,
    y,
    width=1920,
    height=1080,
    scale=1,
    replication_source=0,
):
    return {
        "connected": True,
        "enabled": True,
        "currentModeId": "1",
        "id": output_id,
        "modes": [
            {
                "id": "1",
                "refreshRate": 60,
                "size": {"width": width, "height": height},
            }
        ],
        "name": name,
        "pos": {"x": x, "y": y},
        "replicationSource": replication_source,
        "scale": scale,
        "size": {"width": width, "height": height},
        "uuid": f"uuid-{output_id}",
    }


class KdeVirtualMonitorTest(unittest.TestCase):
    def test_active_output_modes_use_current_mode_and_ignore_disabled_outputs(self):
        physical = output(
            "eDP-2", 1, 0, 0, width=2560, height=1600, scale=1.75
        )
        virtual = output("Virtual-1", 2, 1463, 147, width=1024, height=768)
        disabled = output("HDMI-A-1", 3, 0, 0)
        disabled["enabled"] = False

        modes = kde_virtual_monitor.active_output_modes(
            [physical, virtual, disabled]
        )

        self.assertEqual(modes["eDP-2"]["width"], 2560)
        self.assertEqual(modes["eDP-2"]["height"], 1600)
        self.assertEqual(modes["Virtual-1"]["width"], 1024)
        self.assertNotIn("HDMI-A-1", modes)

    @patch("monitorize.platform.kde_virtual_monitor.subprocess.run")
    @patch("monitorize.platform.kde_virtual_monitor.os.path.isfile", return_value=True)
    def test_flatpak_kscreen_queries_run_on_host(self, _isfile, run):
        run.return_value = Mock(returncode=0, stdout='{"outputs": []}')

        self.assertEqual(kde_virtual_monitor.kde_outputs(), [])

        run.assert_called_once_with(
            [
                "flatpak-spawn",
                "--host",
                "--directory=/",
                "kscreen-doctor",
                "-j",
            ],
            capture_output=True,
            text=True,
            timeout=kde_virtual_monitor.KSCREEN_QUERY_TIMEOUT,
        )

    @patch("monitorize.platform.kde_virtual_monitor.kde_outputs")
    def test_host_inventory_retries_transient_empty_result(self, outputs):
        physical = output("eDP-1", 1, 0, 0)
        outputs.side_effect = [[], [], [physical]]

        found = kde_virtual_monitor.wait_for_kde_outputs(attempts=3, delay=0)

        self.assertEqual(found, [physical])
        self.assertEqual(outputs.call_count, 3)

    @patch("monitorize.platform.kde_virtual_monitor.kde_outputs")
    def test_portal_output_discovery_uses_new_runtime_id(self, outputs):
        physical = output("eDP-1", 1, 0, 0)
        portal = output("Virtual-virtual-xdp-kde-monitorize", 8, 1920, 0)
        outputs.return_value = [physical, portal]

        found = kde_virtual_monitor.wait_for_new_kde_output(
            [physical], "virtual-xdp-kde-monitorize", attempts=1, delay=0
        )

        self.assertEqual(found, (portal, [physical, portal]))

    @patch("monitorize.platform.kde_virtual_monitor.kde_outputs")
    @patch("monitorize.platform.kde_virtual_monitor._run_kscreen", return_value="")
    def test_portal_topology_repair_reports_initial_mirroring(
        self, run_kscreen, outputs
    ):
        physical = output("eDP-1", 1, 0, 0)
        mirrored = output(
            "Virtual-virtual-xdp-kde-monitorize",
            8,
            0,
            0,
            replication_source=1,
        )
        repaired = output("Virtual-virtual-xdp-kde-monitorize", 8, 1920, 0)
        outputs.return_value = [physical, repaired]

        ok, details, _message = kde_virtual_monitor.repair_kde_extended_topology(
            mirrored, [physical, mirrored], attempts=1, delay=0
        )

        self.assertTrue(ok)
        self.assertTrue(details["was_mirrored"])
        self.assertEqual(details["position"], (1920, 0))
        run_kscreen.assert_called_once_with(
            "output.8.mirror.none", "output.8.position.1920,0"
        )

    @patch("monitorize.platform.kde_virtual_monitor._run_kscreen", return_value="")
    @patch("monitorize.platform.kde_virtual_monitor._output_snapshot")
    @patch("monitorize.platform.kde_virtual_monitor.find_kde_output")
    def test_configure_clears_mirroring_and_separates_output(
        self, find_output, snapshot, run_kscreen
    ):
        physical = output("eDP-1", 1, 0, 0, width=2560, height=1600, scale=1.75)
        mirrored = output(
            "Virtual-Monitorize-1", 9, 0, 0, replication_source=1
        )
        repaired = output("Virtual-Monitorize-1", 9, 1463, 0)
        snapshot.return_value = mirrored, [physical, mirrored]
        find_output.return_value = repaired

        ok, details, message = kde_virtual_monitor.configure_native_virtual_output(
            "Virtual-Monitorize-1", 1920, 1080, 60, attempts=1, delay=0
        )

        self.assertTrue(ok)
        self.assertTrue(details["topology_repaired"])
        self.assertIn("restored an extended layout", message)
        run_kscreen.assert_called_once_with(
            "output.9.mirror.none",
            "output.9.mode.1",
            "output.9.position.1463,0",
        )

    @patch("monitorize.platform.kde_virtual_monitor._run_kscreen", return_value="")
    @patch("monitorize.platform.kde_virtual_monitor._output_snapshot")
    @patch("monitorize.platform.kde_virtual_monitor.find_kde_output")
    def test_configure_preserves_non_overlapping_position(
        self, find_output, snapshot, run_kscreen
    ):
        physical = output("eDP-1", 1, 0, 0)
        extended = output("Virtual-Monitorize-1", 9, 1920, 120)
        snapshot.return_value = extended, [physical, extended]
        find_output.return_value = extended

        ok, details, _message = kde_virtual_monitor.configure_native_virtual_output(
            "Virtual-Monitorize-1", 1920, 1080, 60, attempts=1, delay=0
        )

        self.assertTrue(ok)
        self.assertFalse(details["topology_repaired"])
        run_kscreen.assert_called_once_with(
            "output.9.mirror.none", "output.9.mode.1"
        )

    @patch("monitorize.platform.kde_virtual_monitor._run_kscreen", return_value="")
    @patch("monitorize.platform.kde_virtual_monitor._output_snapshot")
    @patch("monitorize.platform.kde_virtual_monitor.find_kde_output")
    def test_configure_separates_apparent_mirror_without_replication(
        self, find_output, snapshot, run_kscreen
    ):
        physical = output("eDP-1", 1, 0, 0)
        overlapping = output("Virtual-Monitorize-1", 9, 0, 0)
        repaired = output("Virtual-Monitorize-1", 9, 1920, 0)
        snapshot.return_value = overlapping, [physical, overlapping]
        find_output.return_value = repaired

        ok, details, _message = kde_virtual_monitor.configure_native_virtual_output(
            "Virtual-Monitorize-1", 1920, 1080, 60, attempts=1, delay=0
        )

        self.assertTrue(ok)
        self.assertTrue(details["topology_repaired"])
        run_kscreen.assert_called_once_with(
            "output.9.mirror.none",
            "output.9.mode.1",
            "output.9.position.1920,0",
        )

    @patch("monitorize.platform.kde_virtual_monitor._run_kscreen", return_value="")
    @patch("monitorize.platform.kde_virtual_monitor._output_snapshot")
    @patch("monitorize.platform.kde_virtual_monitor.find_kde_output")
    def test_configure_fails_if_kwin_keeps_replication_source(
        self, find_output, snapshot, _run_kscreen
    ):
        physical = output("eDP-1", 1, 0, 0)
        mirrored = output(
            "Virtual-Monitorize-1", 9, 0, 0, replication_source=1
        )
        snapshot.return_value = mirrored, [physical, mirrored]
        find_output.return_value = mirrored

        ok, details, message = kde_virtual_monitor.configure_native_virtual_output(
            "Virtual-Monitorize-1", 1920, 1080, 60, attempts=1, delay=0
        )

        self.assertFalse(ok)
        self.assertEqual(details, {})
        self.assertEqual(message, "KDE did not clear mirroring on the virtual output")

    @patch("monitorize.platform.kde_virtual_monitor._run_kscreen", return_value="")
    @patch("monitorize.platform.kde_virtual_monitor.find_kde_output")
    def test_existing_vkms_output_uses_nearest_advertised_60hz_mode(
        self, find_output, run_kscreen
    ):
        virtual = output("Virtual-7", 7, 1920, 0)
        virtual["modes"] = [
            {"id": "59", "refreshRate": 59.94,
             "size": {"width": 2560, "height": 1600}},
            {"id": "60", "refreshRate": 59.99,
             "size": {"width": 2560, "height": 1600}},
        ]
        active = dict(virtual, currentModeId="60")
        find_output.side_effect = [virtual, active]

        ok, details, message = kde_virtual_monitor.configure_existing_output_mode(
            "Virtual-7", 2560, 1600, attempts=1, delay=0
        )

        self.assertTrue(ok)
        self.assertEqual(details["mode_id"], "60")
        self.assertEqual(details["refresh_rate"], 59.99)
        self.assertIn("59.99Hz", message)
        run_kscreen.assert_called_once_with(
            "output.7.mirror.none", "output.7.mode.60"
        )

    @patch("monitorize.platform.kde_virtual_monitor._run_kscreen", return_value="")
    @patch("monitorize.platform.kde_virtual_monitor.find_kde_output")
    def test_existing_vkms_output_falls_back_to_preferred_mode(
        self, find_output, _run_kscreen
    ):
        virtual = output("Virtual-7", 7, 1920, 0, width=1024, height=768)
        virtual["preferredModeId"] = "1"
        find_output.return_value = virtual

        ok, details, message = kde_virtual_monitor.configure_existing_output_mode(
            "Virtual-7", 2560, 1440, attempts=1, delay=0
        )

        self.assertTrue(ok)
        self.assertTrue(details["fell_back"])
        self.assertEqual((details["width"], details["height"]), (1024, 768))
        self.assertIn("requested 2560x1440 is unavailable", message)


if __name__ == "__main__":
    unittest.main()
