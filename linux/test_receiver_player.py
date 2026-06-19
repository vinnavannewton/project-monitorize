import unittest

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

import receiver_player


class ReceiverPlayerTest(unittest.TestCase):
    def test_rotation_changes_while_playing(self):
        Gst.init(None)
        built = receiver_player.build_pipeline("127.0.0.1", 7110, 0, "fakesink")
        self.assertIsNotNone(built.get_by_name("rotate"))
        pipeline = Gst.parse_launch(
            "videotestsrc is-live=true ! videoflip name=rotate ! fakesink sync=false"
        )
        rotate = pipeline.get_by_name("rotate")
        try:
            pipeline.set_state(Gst.State.PLAYING)
            pipeline.get_state(Gst.SECOND)
            rotate.set_property("video-direction", 3)
            self.assertEqual(rotate.get_property("video-direction"), 3)
        finally:
            pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    unittest.main()
