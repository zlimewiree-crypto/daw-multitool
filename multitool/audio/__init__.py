"""DAW Multitool's own audio device and the bridge that sits between your DAW and your speakers.

device.py installs and removes the "DAW Multitool" audio device (a Core Audio plug-in, native/audio_device/);
bridge.py runs the helper that passes what the DAW plays into it on to your speakers or interface, at
Varispeed's speed; build.py compiles both from native/ for source runs and the app build.
"""
