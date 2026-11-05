# Client display protocol delivery

The Python app and bundled Sunshine must be delivered together. The native source
installer, Arch package preparation, RPM preparation (Fedora and Tumbleweed), and
DEB build run `check-client-display-protocol.py` before compiling Sunshine. It
rejects old or incomplete source trees missing protocol v1 and its session hooks.
The installed app also checks the binary capability marker before starting a
client-controlled listener. These checks do not replace capture or live-client QA.

Before publishing this feature:

1. Commit and publish the paired `external/sunshine` changes.
2. Update the parent repository's Sunshine gitlink and all active native pins:
   `packaging/arch/sources.conf`, both RPM specs, and each
   `packaging/deb/*/sunshine.mk`.
3. Run the existing clean-submodule, pin equality, and package preflight checks.
   Native builders archive committed `HEAD`; they cannot deliver uncommitted
   development files.
4. Build and test the app and host together. Keep manual streaming as well as
   client-requested display launch in the package smoke tests.

Do not substitute the old pinned commit or invent a future commit hash. Until
the paired changes are committed and published, native release builds are
intentionally blocked. Flatpak support for this workflow remains deferred.

The new Python runtime module is included by the existing setuptools
`monitorize*` package discovery; it needs no separate installed helper entrypoint.

Guarded KDE layouts also require the binary marker `MONITORIZE_LAYOUT_RECOVERY=1`
and first-frame `stream_ready` hook. The installed Python packages include
`platform/session_layout.py` and `streaming/recovery_guardian.py` through existing
package discovery. The guardian runs as the desktop user and uses existing
`kscreen-doctor`; it requires no new root entrypoint. Its session journal and lock
must not be removed while recovery owns a live output. Full host rebuild and live
layout/capture/input/recovery QA are required; source/unit proof is insufficient.


Streaming Extend sessions now always launch listeners first. Each slot creates
its display only after an authenticated prepare request. `use_client_display_settings`
selects the mode source: true uses the request, false uses the saved app mode.
Both use the same readiness, disconnect grace, cleanup/recreation and retry path.
Display-only (`streaming_backend=none`) still creates immediately without Sunshine;
Mirror uses its existing screen. Listeners require Passwordless Display Creation to be enabled for display creation.
Codec negotiation now also requires `MONITORIZE_CODEC_NEGOTIATION=1`; rebuild
the paired Sunshine fork after updating source.


## Listener codec negotiation

Before opening discovery/listening sockets, the Linux engine tests encoding SDR
black frames in memory through existing software/VAAPI/NVENC upload devices.
No virtual display is created and no desktop is captured. Auto advertises only
codecs that passed on its selected encoder; explicit H.264/HEVC/AV1 is enforced
at RTSP negotiation, rejecting mismatches rather than substituting codecs.
H.264 remains the protocol baseline bit even when an advanced codec is forced;
a client selecting that baseline is rejected by the explicit-codec policy.
After authenticated display preparation, normal capture-dependent encoder probing
runs again; the synthetic probe never leaves a cached capture encoder selected.
Discovery advertises SDR 4:2:0 only; HDR and 4:4:4 are not claimed for listeners.
Vulkan-only encoders cannot currently use the RAM preflight path and are rejected
rather than advertised without testing. Startup probing has a 90-second app
readiness window. Driver/GPU changes are rechecked on actual launch; reconnect or
restart listening to refresh discovery capabilities after hardware changes.

Source checks: 132 focused Python tests, isolated C++ codec-policy assertions,
and compiler syntax checks of video/main/nvhttp/rtsp. A full engine rebuild and
real GPU/client negotiation remain required. Test explicit and Auto on both sides,
unsupported codecs, two independent listeners, disconnect/reconnect, and confirm
the actual codec in the client statistics. No codec switch occurs after decoding
has begun.
