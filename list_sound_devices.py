#!/usr/bin/env python3

"""
List Sound Devices Utility
====================
This script lists all available audio hardware using the
python-sounddevice library. It handles ASIO environment setup and
automatically adjusts channel indexing based on the Host API.

Can run standalone at CLI for an on-screen report or as a function
reurning a dictionary with the following structure:

The get_devices_and_channels() function returns a dictionary where:
    - Key:   (int) The PortAudio Device ID.
    - Value: (dict) A sub-dictionary containing:
        {
            'name': (str) The cleaned device name,
            'hostapi': (str) The name of the host API (e.g., ASIO, WASAPI),
            'input_channels': (list) A list of valid input channel indices,
            'output_channels': (list) A list of valid output channel indices
        }

Indexing Logic:
---------------
- ASIO: Returns 0-based indices (e.g., [0, 1, 2...]) for hardware direct access.
- Non-ASIO (WASAPI/MME/etc.): Returns 1-based indices (e.g., [1, 2, 3...])
  to match standard mapping conventions.
"""

import os

# Enable ASIO build of PortAudio in python-sounddevice (Windows).
# This must be set before importing sounddevice.
os.environ["SD_ENABLE_ASIO"] = "1"

import sounddevice as sd


def clean_device_name(name: str) -> str:
    """Removes messy Windows driver paths and registry strings from device names."""
    if "@System32" in name or ".sys" in name:
        # Splits by semicolon and takes the last part, usually the readable name
        parts = name.split(";")
        clean = parts[-1].replace("%0", "").replace("%1", "").strip()
        return clean if clean else "Bluetooth Audio Device"
    return name


def get_devices_and_channels() -> dict:
    """
    Queries audio devices and returns a dict indexed by Device ID.
    Categorized by Host API on screen.
    ASIO uses 0-based channel indices; others use 1-based channel indices.
    """
    # Hard refresh PortAudio state to catch any recent device changes
    sd._terminate()
    sd._initialize()

    apis = sd.query_hostapis()
    devs = sd.query_devices()

    device_catalog = {}

    print("\n" + "=" * 45)
    print("           AUDIO DEVICE EXPLORER")
    print("=" * 45)

    for api_idx, a in enumerate(apis):
        api_name = a['name']
        print(f"\n--- HOST API: {api_name} ---")

        # Determine index base: ASIO is 0-based, others (WASAPI/MME) are 1-based
        is_asio = "ASIO" in api_name.upper()
        base_idx = 0 if is_asio else 1

        for dev_idx, d in enumerate(devs):
            if d['hostapi'] == api_idx:
                # Generate specific channel index lists
                max_in = d['max_input_channels']
                max_out = d['max_output_channels']

                in_ch_indices = list(range(base_idx, max_in + base_idx)) if max_in > 0 else []
                out_ch_indices = list(range(base_idx, max_out + base_idx)) if max_out > 0 else []

                # Clean up the driver string
                display_name = clean_device_name(d['name'])

                # Populate the return dictionary
                device_catalog[dev_idx] = {
                    'name': display_name,
                    'hostapi': api_name,
                    'input_channels': in_ch_indices,
                    'output_channels': out_ch_indices
                }

                # Consolidated ID and Name reporting
                print(f"  Device ID: {dev_idx} - {display_name}")
                if in_ch_indices:
                    print(f"      In Channel Indices:  {in_ch_indices}")
                if out_ch_indices:
                    print(f"      Out Channel Indices: {out_ch_indices}")

                # Line break between devices for readability
                print("")

    return device_catalog


if __name__ == "__main__":
    get_devices_and_channels()