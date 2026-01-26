# Replay Overlay (Development)

> **Note:** This is the private development/testing repository. For the public release, see [replay-overlay](https://github.com/schylerchase/replay-overlay).

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
Download and run `ReplayOverlay_Setup.exe` from the [Releases](https://github.com/schylerchase/replay-overlay-interactive/releases) page.

### From Source
```bash
pip install -r requirements.txt
python replay_overlay_interactive.py
```

## Usage

1. Enable OBS WebSocket server (Tools > WebSocket Server Settings)
2. Launch the overlay
3. Press `F10` (default) to toggle the overlay visibility
4. Press `Num +` (default) to save a replay

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

Found a bug or have a feature request? Please open an issue on the [GitHub Issues](https://github.com/schylerchase/replay-overlay-interactive/issues) page.

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
