# Replay Overlay Interactive

A ShadowPlay-style overlay for OBS Studio that provides quick access to replay buffer controls, scene switching, and audio mixing.

## Features

- **Replay Buffer Controls** - Start/stop buffer, save replays with hotkey
- **Scene Switching** - Quick access to all OBS scenes
- **Source Toggling** - Show/hide sources in current scene
- **Audio Mixer** - Volume sliders for all audio sources
- **REC Indicator** - Configurable on-screen indicator when buffer is active
- **Organize by Game** - Automatically sorts replays into folders by active window title
- **Hotkey Sync** - Reads save hotkey directly from OBS config
- **System Tray** - Runs in background with tray icon
- **Auto-launch OBS** - Optionally starts OBS when overlay launches

## Requirements

- Windows 10/11
- OBS Studio with WebSocket server enabled (Tools > WebSocket Server Settings)
- Python 3.10+ (for running from source)

## Installation

### From Executable
Download `ReplayOverlay.exe` and run it.

### From Source
```bash
pip install -r requirements.txt
python replay_overlay_interactive.py
```

## Usage

1. Enable OBS WebSocket server (Tools > WebSocket Server Settings)
2. Launch the overlay
3. Press `F10` (default) to toggle the overlay
4. Press `Num +` (default) to save a replay

## Configuration

Settings are stored in `config.json` in the same directory as the executable/script.

| Setting | Description | Default |
|---------|-------------|---------|
| `toggle_hotkey` | Hotkey to show/hide overlay | `f10` |
| `save_hotkey` | Hotkey to save replay | `num add` |
| `watch_folder` | Folder to watch for new recordings | OBS output path |
| `organize_by_game` | Sort replays into game folders | `true` |
| `show_rec_indicator` | Show REC indicator when buffer active | `true` |
| `rec_indicator_position` | Position: top-left, top-center, top-right, bottom-left, bottom-center, bottom-right | `top-left` |
| `auto_launch_obs` | Start OBS if not running | `true` |
| `auto_start_buffer` | Auto-start replay buffer on connect | `true` |
| `obs_port` | OBS WebSocket port | `4455` |
| `obs_password` | OBS WebSocket password | `""` |

## License

MIT
