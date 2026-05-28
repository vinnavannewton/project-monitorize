import re

with open("/home/vinnavan/user/MegaProjects/Monitorize/linux/pipeline_builder.py", "r") as f:
    content = f.read()

# find udpsink
# sink = f"rtph264pay config-interval=-1 pt=96 ! udpsink host={host} port=7112 sync=false buffer-size=2000000"
old_sink = '        sink = f"rtph264pay config-interval=-1 pt=96 ! udpsink host={host} port=7112 sync=false buffer-size=2000000"'

# To properly pace, we set max-bitrate slightly higher than video bitrate to allow RTP headers overhead.
# udpsink max-bitrate takes bits/sec. 
# bitrate is in kbps (e.g. 30000). So 30000 * 1000 = 30000000. Plus 20% overhead = bitrate * 1200.
new_sink = '        sink = f"rtph264pay config-interval=-1 pt=96 mtu=1400 ! udpsink host={host} port=7112 sync=false buffer-size=2000000 max-bitrate={bitrate * 1200}"'

content = content.replace(old_sink, new_sink)

with open("/home/vinnavan/user/MegaProjects/Monitorize/linux/pipeline_builder.py", "w") as f:
    f.write(content)
print("Linux pipeline updated with max-bitrate pacing.")
