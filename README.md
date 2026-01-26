# Replay Overlay

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

### From Installer
Download and run `ReplayOverlay_Setup.exe` from the [Releases](https://github.com/schylerchase/replay-overlay/releases) page.

### From Source
```bash
pip install -r requirements.txt
python replay_overlay_interactive.py
```

## OBS Setup

### Enable WebSocket Server
1. Open OBS Studio
2. Go to **Tools > WebSocket Server Settings**
3. Check **Enable WebSocket Server**
4. Note the port (default: 4455)
5. Optionally set a password

### Enable Replay Buffer
1. Go to **Settings > Output > Replay Buffer**
2. Check **Enable Replay Buffer**
3. Set your desired buffer length (e.g., 30-120 seconds)
4. Optionally set a hotkey in **Settings > Hotkeys > Replay Buffer > Save Replay**

## Usage

1. Launch the overlay after OBS is running
2. Press `F10` (default) to toggle the overlay visibility
3. Press your configured hotkey to save a replay
4. Right-click the tray icon for quick access to settings

## Configuration

Settings are stored in `%LOCALAPPDATA%\ReplayOverlay\config.json`.

| Setting | Description | Default |
|---------|-------------|---------|
| `toggle_hotkey` | Hotkey to show/hide overlay | `f10` |
| `save_hotkey` | Hotkey to save replay | `num add` |
| `watch_folder` | Folder to watch for new recordings | OBS output path |
| `organize_by_game` | Sort replays into game folders | `true` |
| `show_rec_indicator` | Show REC indicator when buffer active | `true` |
| `rec_indicator_position` | Position: top-left, top-center, top-right, bottom-left, bottom-center, bottom-right | `top-right` |
| `auto_launch_obs` | Start OBS if not running | `false` |
| `auto_start_buffer` | Auto-start replay buffer on connect | `false` |
| `obs_port` | OBS WebSocket port | `4455` |
| `obs_password` | OBS WebSocket password | `""` |

## Issues & Support

Found a bug or have a feature request? Please open an issue on the [GitHub Issues](https://github.com/schylerchase/replay-overlay/issues) page.

When reporting bugs, please include:
- Your Windows version
- Your OBS Studio version
- Steps to reproduce the issue
- Any error messages you see

## AI Disclosure

This project was developed with assistance from AI tools (Claude). The core architecture, feature implementation, and code refinements were created collaboratively with AI assistance. All code has been reviewed and tested by the author.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Disclaimer

This project is not affiliated with, endorsed by, or sponsored by OBS Project or Streamlabs. OBS Studio is a trademark of the OBS Project.
