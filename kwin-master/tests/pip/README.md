<!--
    SPDX-FileCopyrightText: None

    SPDX-License-Identifier: CC0-1.0
-->

# Picture-in-Picture demo

### How to build and run it

```
cmake -B build
cmake --build build --parallel
build/bin/piptest
```

Note that you also need to start `kwin_wayland` with the `KWIN_WAYLAND_SUPPORT_XX_PIP_V1=1` environment variable set.
