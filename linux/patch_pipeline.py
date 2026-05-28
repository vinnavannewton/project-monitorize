import re

with open("/home/vinnavan/user/MegaProjects/Monitorize/linux/pipeline_builder.py", "r") as f:
    content = f.read()

# Update build_pipeline
old_sink = '''    # Mux + sink
    parse = "h264parse config-interval=-1"
    caps_out = "video/x-h264,stream-format=byte-stream,alignment=au"
    if server_mode:
        # In server mode, launch_with_fallback will pass a custom file descriptor
        # representing the accepted client socket. We use fdsink.
        sink = f"fdsink fd={port} sync=false"  # Here `port` is actually the passed fd
    else:
        sink = f"tcpclientsink host={host} port={port} sync=false"

    pipeline = (
        f"gst-launch-1.0 -e "
        f"{src} ! {framerate} ! {queue} ! {convert} ! "
        f"{encoder} ! {parse} ! {caps_out} ! {sink}"
    )'''

new_sink = '''    # Mux + sink
    parse = "h264parse config-interval=-1"
    if server_mode:
        sink = f"rtph264pay config-interval=-1 pt=96 ! udpsink host={host} port=7112 sync=false buffer-size=2000000"
        pipeline = (
            f"gst-launch-1.0 -e "
            f"{src} ! {framerate} ! {queue} ! {convert} ! "
            f"{encoder} ! {parse} ! {sink}"
        )
    else:
        caps_out = "video/x-h264,stream-format=byte-stream,alignment=au"
        sink = f"{caps_out} ! tcpclientsink host={host} port={port} sync=false"
        pipeline = (
            f"gst-launch-1.0 -e "
            f"{src} ! {framerate} ! {queue} ! {convert} ! "
            f"{encoder} ! {parse} ! {caps_out} ! {sink}"
        )'''

content = content.replace(old_sink, new_sink)

# Update launch_with_fallback
old_launch = '''        conn_sock, addr = server_sock.accept()
        print(f"[Streamer] ✅ Accepted connection from Android client at {addr}")
        
        # When passing fd to GStreamer, we inject it into pass_fds
        fd = conn_sock.fileno()
        pass_fds = pass_fds + (fd,) if pass_fds else (fd,)
        # In build_pipeline, if server_mode=True, we hijack the `port` argument as the `fd`
        pipeline_port_arg = fd
    else:
        pipeline_port_arg = port

    pipeline = build_pipeline(
        pw_fd=pw_fd, node_id=node_id,
        width=width, height=height, fps=fps, bitrate=bitrate, port=pipeline_port_arg,
        hw_encoder=hw_encoder, host=host, server_mode=server_mode,
    )'''

new_launch = '''        conn_sock, addr = server_sock.accept()
        print(f"[Streamer] ✅ Accepted connection from Android client at {addr}")
        client_ip = addr[0]
        
        pipeline_port_arg = port
        host_arg = client_ip
    else:
        pipeline_port_arg = port
        host_arg = host

    pipeline = build_pipeline(
        pw_fd=pw_fd, node_id=node_id,
        width=width, height=height, fps=fps, bitrate=bitrate, port=pipeline_port_arg,
        hw_encoder=hw_encoder, host=host_arg, server_mode=server_mode,
    )'''

content = content.replace(old_launch, new_launch)

# Fix the fallback block where it re-accepts
old_reaccept = '''            if server_mode and server_sock:
                print("[Streamer] Re-waiting for Android client for fallback pipeline...")
                if conn_sock: conn_sock.close()
                conn_sock, addr = server_sock.accept()
                print(f"[Streamer] ✅ Re-accepted connection from {addr}")
                fd = conn_sock.fileno()
                pass_fds = tuple(f for f in pass_fds if f != pipeline_port_arg) + (fd,)
                kwargs["pass_fds"] = pass_fds
                pipeline_port_arg = fd
            
            pipeline = build_pipeline(
                pw_fd=pw_fd, node_id=node_id,
                width=width, height=height, fps=fps, bitrate=bitrate, port=pipeline_port_arg,
                hw_encoder=None, host=host, server_mode=server_mode,
            )'''

new_reaccept = '''            if server_mode and server_sock:
                print("[Streamer] Re-waiting for Android client for fallback pipeline...")
                if conn_sock: conn_sock.close()
                conn_sock, addr = server_sock.accept()
                print(f"[Streamer] ✅ Re-accepted connection from {addr}")
                client_ip = addr[0]
                host_arg = client_ip
            
            pipeline = build_pipeline(
                pw_fd=pw_fd, node_id=node_id,
                width=width, height=height, fps=fps, bitrate=bitrate, port=pipeline_port_arg,
                hw_encoder=None, host=host_arg, server_mode=server_mode,
            )'''

content = content.replace(old_reaccept, new_reaccept)

with open("/home/vinnavan/user/MegaProjects/Monitorize/linux/pipeline_builder.py", "w") as f:
    f.write(content)
print("pipeline_builder.py patched.")
