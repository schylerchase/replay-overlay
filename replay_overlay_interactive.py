"""
Replay Overlay Interactive - Full Featured OBS Control + ShadowPlay-style Overlay
Combines OBS control panel with replay notifications and organize-by-game.
"""

import sys
import os
import json
import base64
import threading
import time
import ctypes
import subprocess
import logging
import re
import shlex
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QSystemTrayIcon, QMenu,
    QListWidget, QSlider, QCheckBox, QScrollArea, QLineEdit,
    QFileDialog, QSpinBox, QDoubleSpinBox, QDialog, QTabWidget, QFormLayout,
    QGroupBox, QComboBox, QWizard, QWizardPage
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from PySide6.QtGui import QPixmap, QIcon, QColor, QAction, QPainter, QFont

try:
    import obsws_python as obsws
    HAS_OBSWS = True
except ImportError:
    HAS_OBSWS = False

try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.flv', '.avi', '.mov', '.ts', '.m4v'}

# Store config in LocalAppData with secure permissions
APP_DATA_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home())) / "ReplayOverlay"
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APP_DATA_DIR / "config.json"
LOG_PATH = APP_DATA_DIR / "overlay.log"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
DEFAULT_CONFIG = {
    "obs_port": 4455,
    "obs_password": "",
    "toggle_hotkey": "f10",
    "save_hotkey": "f9",
    "hotkey_enabled": True,
    "watch_folder": str(Path.home() / "Videos"),
    "organize_by_game": True,
    "sync_obs_folder": True,
    "show_notifications": True,
    "notification_duration": 3.0,
    "notification_opacity": 25,
    "notification_message": "REPLAY SAVED",
    "show_rec_indicator": True,
    "rec_indicator_position": "top-left",
    "auto_launch_obs": False,
    "auto_launch_minimized": True,
    "auto_start_buffer": False,
    "obs_path": "C:/Program Files/obs-studio/bin/64bit/obs64.exe",
    "run_as_admin": False,
    "start_with_windows": False,
    "overlay_x": None,
    "overlay_y": None,
}

IGNORED_PROCESSES = {
    'explorer', 'searchhost', 'shellexperiencehost', 'applicationframehost',
    'systemsettings', 'textinputhost', 'dwm', 'csrss', 'winlogon',
    'chrome', 'firefox', 'msedge', 'opera', 'brave', 'discord', 'slack',
    'teams', 'zoom', 'spotify', 'code', 'devenv', 'obs64', 'obs32', 'obs',
}


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                loaded = json.load(f)
                config = DEFAULT_CONFIG.copy()
                config.update(loaded)
                return config
        except (IOError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to load config: {e}")
    return DEFAULT_CONFIG.copy()


def save_config(config):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        # Set restrictive permissions on config file (Windows)
        if sys.platform == 'win32':
            _set_file_permissions(CONFIG_PATH)
        logger.info("Config saved successfully")
    except IOError as e:
        logger.error(f"Failed to save config: {e}")


def _set_file_permissions(filepath):
    """Set restrictive file permissions on Windows (current user only).

    Security: Prevents other users from reading config with password.
    """
    try:
        import subprocess
        # Use icacls to set permissions: only current user has access
        # /inheritance:r removes inherited permissions
        # /grant:r gives explicit permission only to current user
        username = os.environ.get('USERNAME', '')
        if username:
            subprocess.run(
                ['icacls', str(filepath), '/inheritance:r', '/grant:r', f'{username}:(R,W)'],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"Could not set file permissions: {e}")


def get_foreground_process():
    """Get the name of the foreground process (case-preserved)."""
    if sys.platform != 'win32':
        return None
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).stem
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, ValueError) as e:
        logger.debug(f"Failed to get foreground process: {e}")
    return None


def is_process_ignored(process_name):
    """Check if process should be ignored (case-insensitive)."""
    if not process_name:
        return True
    return process_name.lower() in IGNORED_PROCESSES


def is_valid_obs_executable(obs_path):
    """Validate that obs_path points to a legitimate OBS executable.

    Security: Prevents arbitrary executable launch via config manipulation.
    """
    if not obs_path:
        return False

    try:
        path = Path(obs_path).resolve()

        # Must exist and be a file
        if not path.is_file():
            return False

        # Must be named obs64.exe or obs32.exe (case-insensitive)
        filename = path.name.lower()
        if filename not in ('obs64.exe', 'obs32.exe'):
            logger.warning(f"Invalid OBS executable name: {filename}")
            return False

        # Must be in a directory containing 'obs-studio' in the path
        path_str = str(path).lower()
        if 'obs-studio' not in path_str and 'obs studio' not in path_str:
            logger.warning(f"OBS path does not appear to be in OBS Studio directory: {obs_path}")
            return False

        return True
    except (OSError, ValueError) as e:
        logger.warning(f"Error validating OBS path: {e}")
        return False


def sanitize_profile_name(profile):
    """Sanitize OBS profile name to prevent path traversal.

    Security: Prevents directory traversal attacks via malicious profile names.
    """
    if not profile:
        return 'Untitled'

    # Remove any path traversal characters
    sanitized = re.sub(r'[\\/:*?"<>|]', '', profile)
    sanitized = sanitized.replace('..', '')

    # Limit length
    if len(sanitized) > 100:
        sanitized = sanitized[:100]

    return sanitized if sanitized else 'Untitled'


def is_admin():
    if sys.platform != 'win32':
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, ValueError):
        return False


def request_admin_restart():
    """Request admin restart with properly quoted arguments."""
    if sys.platform != 'win32':
        return False
    try:
        script = sys.argv[0]
        # Quote each argument individually to prevent injection
        quoted_params = ' '.join(f'"{arg}"' for arg in sys.argv[1:]) if sys.argv[1:] else ''
        full_params = f'"{script}" {quoted_params}'.strip()
        logger.info(f"Requesting admin restart with params: {full_params}")
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, full_params, None, 1)
        return result > 32  # ShellExecute returns >32 on success
    except (OSError, ValueError) as e:
        logger.error(f"Admin restart failed: {e}")
        return False


def set_windows_startup(enabled):
    """Add or remove app from Windows startup via registry."""
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "ReplayOverlayInteractive"

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)

        if enabled:
            # Get the path to run - use pythonw for no console window
            if getattr(sys, 'frozen', False):
                # Running as compiled exe
                exe_path = sys.executable
            else:
                # Running as script - use pythonw to hide console
                pythonw = Path(sys.executable).parent / "pythonw.exe"
                if pythonw.exists():
                    exe_path = f'"{pythonw}" "{Path(__file__).resolve()}"'
                else:
                    exe_path = f'"{sys.executable}" "{Path(__file__).resolve()}"'

            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass  # Already removed

        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"Startup registry error: {e}")
        return False


def get_windows_startup():
    """Check if app is set to start with Windows."""
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "ReplayOverlayInteractive"

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE)
        try:
            winreg.QueryValueEx(key, app_name)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except OSError:
        return False


def get_obs_replay_hotkey():
    """Read the replay buffer save hotkey from OBS config files."""
    if sys.platform != 'win32':
        return None
    try:
        # OBS config directory
        appdata = os.environ.get('APPDATA', '')
        obs_dir = Path(appdata) / 'obs-studio'
        if not obs_dir.exists():
            return None

        # Read global.ini to find active profile
        # OBS global.ini may not have section headers, so read manually
        global_ini = obs_dir / 'global.ini'
        if not global_ini.exists():
            return None

        profile = 'Untitled'
        with open(global_ini, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if line.startswith('Profile='):
                    profile = sanitize_profile_name(line.split('=', 1)[1])
                    break

        # Read profile's basic.ini for hotkeys
        profile_dir = obs_dir / 'basic' / 'profiles' / profile
        basic_ini = profile_dir / 'basic.ini'

        if not basic_ini.exists():
            return None

        with open(basic_ini, 'r', encoding='utf-8') as f:
            content = f.read()

        # Look for ReplayBuffer hotkey section
        # OBS stores hotkeys in format: ReplayBuffer={"ReplayBuffer.Save":[{"key":"OBS_KEY_..."}]}
        import re
        match = re.search(r'ReplayBuffer=\{"ReplayBuffer\.Save":(\[.*?\])\}', content)
        if not match:
            return None

        hotkey_json = match.group(1)
        hotkey_data = json.loads(hotkey_json)

        if not hotkey_data:
            return None

        # Parse the OBS key format
        key_entry = hotkey_data[0]
        obs_key = key_entry.get('key', '')

        # Map OBS key names to keyboard library format
        obs_to_keyboard = {
            'OBS_KEY_F1': 'f1', 'OBS_KEY_F2': 'f2', 'OBS_KEY_F3': 'f3',
            'OBS_KEY_F4': 'f4', 'OBS_KEY_F5': 'f5', 'OBS_KEY_F6': 'f6',
            'OBS_KEY_F7': 'f7', 'OBS_KEY_F8': 'f8', 'OBS_KEY_F9': 'f9',
            'OBS_KEY_F10': 'f10', 'OBS_KEY_F11': 'f11', 'OBS_KEY_F12': 'f12',
            'OBS_KEY_INSERT': 'insert', 'OBS_KEY_DELETE': 'delete',
            'OBS_KEY_HOME': 'home', 'OBS_KEY_END': 'end',
            'OBS_KEY_PAGEUP': 'page up', 'OBS_KEY_PAGEDOWN': 'page down',
            'OBS_KEY_NUMPAD0': 'num 0', 'OBS_KEY_NUMPAD1': 'num 1',
            'OBS_KEY_NUMPAD2': 'num 2', 'OBS_KEY_NUMPAD3': 'num 3',
            'OBS_KEY_NUMPAD4': 'num 4', 'OBS_KEY_NUMPAD5': 'num 5',
            'OBS_KEY_NUMPAD6': 'num 6', 'OBS_KEY_NUMPAD7': 'num 7',
            'OBS_KEY_NUMPAD8': 'num 8', 'OBS_KEY_NUMPAD9': 'num 9',
            'OBS_KEY_NUMPADADD': 'num add', 'OBS_KEY_NUMPADSUBTRACT': 'num subtract',
            'OBS_KEY_NUMPADMULTIPLY': 'num multiply', 'OBS_KEY_NUMPADDIVIDE': 'num divide',
            'OBS_KEY_NUMPLUS': 'num add', 'OBS_KEY_NUMMINUS': 'num subtract',
            'OBS_KEY_NUMASTERISK': 'num multiply', 'OBS_KEY_NUMSLASH': 'num divide',
        }

        # Build the hotkey string with modifiers
        parts = []
        if key_entry.get('control'):
            parts.append('ctrl')
        if key_entry.get('alt'):
            parts.append('alt')
        if key_entry.get('shift'):
            parts.append('shift')

        # Convert OBS key to keyboard format
        key_name = obs_to_keyboard.get(obs_key, '')
        if not key_name and obs_key.startswith('OBS_KEY_'):
            # Try direct mapping for letter/number keys
            key_name = obs_key.replace('OBS_KEY_', '').lower()

        if key_name:
            parts.append(key_name)
            return '+'.join(parts)

    except Exception as e:
        print(f"Error reading OBS hotkey: {e}")

    return None


class OBSController:
    def __init__(self, port=4455, password=""):
        self.port = port
        self.password = password
        self.client = None
        self._connected = False

    def connect(self):
        if not HAS_OBSWS:
            return False
        try:
            self.client = obsws.ReqClient(host='localhost', port=self.port, password=self.password, timeout=3)
            self._connected = True
            return True
        except Exception:
            self._connected = False
            return False

    @property
    def connected(self):
        return self._connected and self.client is not None

    def get_scenes(self):
        if not self.connected: return []
        try: return [s['sceneName'] for s in self.client.get_scene_list().scenes]
        except Exception: return []

    def get_current_scene(self):
        if not self.connected: return None
        try: return self.client.get_current_program_scene().scene_name
        except Exception: return None

    def set_scene(self, name):
        if not self.connected: return False
        try:
            self.client.set_current_program_scene(name)
            return True
        except Exception: return False

    def get_scene_items(self, scene):
        if not self.connected: return []
        try:
            return [{'id': i.get('sceneItemId'), 'name': i.get('sourceName'), 'visible': i.get('sceneItemEnabled', False)}
                    for i in self.client.get_scene_item_list(scene).scene_items]
        except Exception: return []

    def set_source_visible(self, scene, item_id, visible):
        if not self.connected:
            return False
        try:
            self.client.set_scene_item_enabled(scene, item_id, visible)
            return True
        except Exception:
            return False

    def get_audio_sources(self):
        if not self.connected: return []
        AUDIO_KINDS = {
            'wasapi_input_capture', 'wasapi_output_capture',
            'pulse_input_capture', 'pulse_output_capture',
            'coreaudio_input_capture', 'coreaudio_output_capture',
            'wasapi_process_output_capture',
        }
        try:
            sources = []
            for inp in self.client.get_input_list().inputs:
                kind = inp.get('inputKind', '')
                if kind in AUDIO_KINDS:
                    try:
                        vol = self.client.get_input_volume(inp['inputName'])
                        mute = self.client.get_input_mute(inp['inputName'])
                        sources.append({
                            'name': inp['inputName'],
                            'volume': vol.input_volume_mul if hasattr(vol, 'input_volume_mul') else 1.0,
                            'muted': mute.input_muted if hasattr(mute, 'input_muted') else False
                        })
                    except Exception: pass
            return sources
        except Exception: return []

    def set_input_volume(self, name, vol):
        if not self.connected: return False
        try:
            self.client.set_input_volume(name, vol_mul=vol)
            return True
        except Exception: return False

    def toggle_mute(self, name):
        if not self.connected: return False
        try:
            self.client.toggle_input_mute(name)
            return True
        except Exception: return False

    def get_screenshot(self, w=320, h=180):
        """Get screenshot with base64 size validation."""
        if not self.connected: return None
        MAX_BASE64_SIZE = 10 * 1024 * 1024  # 10MB max for decoded image
        try:
            scene = self.get_current_scene()
            if not scene: return None
            resp = self.client.get_source_screenshot(name=scene, img_format="png", width=w, height=h, quality=-1)
            data = resp.image_data.split(',')[1] if ',' in resp.image_data else resp.image_data
            # Validate base64 size before decoding
            if len(data) > MAX_BASE64_SIZE:
                logger.warning("Screenshot base64 data exceeds size limit")
                return None
            px = QPixmap()
            px.loadFromData(base64.b64decode(data))
            return px
        except Exception: return None

    def has_active_capture(self):
        if not self.connected: return None
        CAPTURE_TYPES = {'game_capture', 'window_capture', 'monitor_capture', 'display_capture', 'dshow_input'}
        try:
            scene = self.get_current_scene()
            if not scene: return None
            for item in self.client.get_scene_item_list(scene).scene_items:
                if item.get('inputKind') in CAPTURE_TYPES and item.get('sceneItemEnabled', False):
                    try:
                        if self.client.get_source_active(item.get('sourceName')).video_active:
                            return True
                    except Exception: pass
            return False
        except Exception: return None

    def get_stream_status(self):
        if not self.connected: return None
        try: return self.client.get_stream_status().output_active
        except Exception: return None

    def get_record_status(self):
        if not self.connected: return None
        try: return self.client.get_record_status().output_active
        except Exception: return None

    def get_buffer_status(self):
        if not self.connected: return None
        try: return self.client.get_replay_buffer_status().output_active
        except Exception: return None

    def get_virtualcam_status(self):
        if not self.connected: return None
        try: return self.client.get_virtual_cam_status().output_active
        except Exception: return None

    def toggle_stream(self):
        if self.connected:
            try: self.client.toggle_stream()
            except Exception: pass

    def toggle_record(self):
        if self.connected:
            try: self.client.toggle_record()
            except Exception: pass

    def toggle_buffer(self):
        if self.connected:
            try: self.client.toggle_replay_buffer()
            except Exception: pass

    def start_buffer(self):
        if self.connected:
            try: self.client.start_replay_buffer()
            except Exception: pass

    def stop_buffer(self):
        if self.connected:
            try: self.client.stop_replay_buffer()
            except Exception: pass

    def save_buffer(self):
        if self.connected:
            try:
                self.client.save_replay_buffer()
                return True
            except Exception: pass
        return False

    def toggle_virtualcam(self):
        if self.connected:
            try: self.client.toggle_virtual_cam()
            except Exception: pass

    def set_record_directory(self, path):
        if not self.connected: return False
        try:
            self.client.set_record_directory(path)
            return True
        except Exception: return False


class SignalBridge(QObject):
    toggle_overlay = Signal()
    save_replay = Signal()
    update_ui = Signal(dict)
    update_preview = Signal(QPixmap)
    show_notification = Signal(str, str)
    update_rec_indicator = Signal(bool)


class ReplayHandler(FileSystemEventHandler):
    def __init__(self, signals, config):
        self.signals = signals
        self.config = config
        self.recent = set()
        self.pending_game = None

    def on_created(self, event):
        if event.is_directory:
            return
        filepath = Path(event.src_path)
        if filepath.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        if filepath.name in self.recent:
            return

        self.recent.add(filepath.name)
        threading.Thread(target=lambda: self._cleanup(filepath.name), daemon=True).start()

        if self.pending_game and self.config.get('organize_by_game', False):
            game_folder = filepath.parent / self.pending_game
            dest = game_folder / filepath.name
            game_name = self.pending_game  # Capture for thread

            def move():
                # Wait for file to finish writing (size stops changing)
                if not self._wait_for_file_complete(filepath):
                    print(f"File never stabilized: {filepath}")
                    return
                try:
                    game_folder.mkdir(exist_ok=True)
                    if filepath.exists() and not dest.exists():
                        import shutil
                        shutil.move(str(filepath), str(dest))
                        print(f"Moved replay to: {dest}")
                except Exception as e:
                    print(f"Error moving file: {e}")

            threading.Thread(target=move, daemon=True).start()
            self.pending_game = None

    def _wait_for_file_complete(self, filepath, timeout=30):
        """Wait for file to finish writing by checking if size stops changing."""
        last_size = -1
        stable_count = 0
        start = time.time()

        while time.time() - start < timeout:
            try:
                if not filepath.exists():
                    return False
                current_size = filepath.stat().st_size
                if current_size == last_size and current_size > 0:
                    stable_count += 1
                    if stable_count >= 3:  # Size stable for 3 checks (1.5 sec)
                        return True
                else:
                    stable_count = 0
                last_size = current_size
            except OSError:
                pass
            time.sleep(0.5)

        return False

    def _cleanup(self, name):
        time.sleep(10)
        self.recent.discard(name)


class SourceWidget(QWidget):
    toggled = Signal(int, bool)

    def __init__(self, item_id, name, visible):
        super().__init__()
        self.item_id = item_id
        self.setFixedHeight(20)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self.cb = QCheckBox()
        self.cb.setChecked(visible)
        self.cb.stateChanged.connect(self._on_state_changed)
        layout.addWidget(self.cb)

        lbl = QLabel(name[:15])
        lbl.setStyleSheet("font-size: 10px;")
        layout.addWidget(lbl, 1)

    def _on_state_changed(self, state):
        checked = state == 2
        self.toggled.emit(self.item_id, checked)

    def update_visible(self, visible):
        self.cb.blockSignals(True)
        self.cb.setChecked(visible)
        self.cb.blockSignals(False)


class AudioWidget(QWidget):
    volume_changed = Signal(str, float)
    mute_toggled = Signal(str)

    def __init__(self, name, volume, muted):
        super().__init__()
        self.name = name
        self._muted = muted
        self.setFixedHeight(22)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 4, 0)
        layout.setSpacing(6)

        # Mute button - simple colored square
        self.mute_btn = QPushButton()
        self.mute_btn.setFixedSize(16, 16)
        self.mute_btn.setCheckable(True)
        self.mute_btn.setChecked(muted)
        self.mute_btn.clicked.connect(lambda: self.mute_toggled.emit(self.name))
        self._apply_mute_style(muted)
        layout.addWidget(self.mute_btn)

        # Name label
        lbl = QLabel(name[:10])
        lbl.setFixedWidth(65)
        lbl.setStyleSheet("font-size: 10px; color: #ccc;")
        layout.addWidget(lbl)

        # Volume slider
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(volume * 100))
        self.slider.setFixedHeight(14)
        self.slider.valueChanged.connect(lambda v: self.volume_changed.emit(self.name, v / 100.0))
        layout.addWidget(self.slider, 1)

    def _apply_mute_style(self, muted):
        if muted:
            self.mute_btn.setStyleSheet("background: #e94560; border: none; border-radius: 2px;")
        else:
            self.mute_btn.setStyleSheet("background: #4ecca3; border: none; border-radius: 2px;")

    def update_mute(self, muted):
        if muted == self._muted:
            return
        self._muted = muted
        self.mute_btn.blockSignals(True)
        self.mute_btn.setChecked(muted)
        self._apply_mute_style(muted)
        self.mute_btn.blockSignals(False)

    def update_volume(self, volume):
        self.slider.blockSignals(True)
        self.slider.setValue(int(volume * 100))
        self.slider.blockSignals(False)


class SetupWizard(QWizard):
    """First-run setup wizard for easy configuration."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Replay Overlay Setup")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setFixedSize(500, 400)

        self.addPage(self._create_welcome_page())
        self.addPage(self._create_obs_page())
        self.addPage(self._create_hotkey_page())
        self.addPage(self._create_folder_page())
        self.addPage(self._create_finish_page())

    def _create_welcome_page(self):
        page = QWizardPage()
        page.setTitle("Welcome to Replay Overlay")
        page.setSubTitle("This wizard will help you set up your replay buffer overlay.")

        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Replay Overlay gives you ShadowPlay-style controls for OBS:\n\n"
            "- Quick access to scenes, sources, and audio\n"
            "- Save replay buffer with a hotkey\n"
            "- Automatic organization by game\n"
            "- On-screen REC indicator\n\n"
            "Click Next to begin setup."
        ))
        layout.addStretch()
        return page

    def _create_obs_page(self):
        page = QWizardPage()
        page.setTitle("OBS Connection")
        page.setSubTitle("Configure how the overlay connects to OBS Studio.")

        layout = QFormLayout(page)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self.config.get('obs_port', 4455))
        layout.addRow("WebSocket Port:", self.port_spin)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setText(self.config.get('obs_password', ''))
        self.password_edit.setPlaceholderText("Leave empty if no password set")
        layout.addRow("Password:", self.password_edit)

        hint = QLabel("In OBS: Tools > WebSocket Server Settings")
        hint.setStyleSheet("color: gray; font-size: 10px;")
        layout.addRow("", hint)

        self.auto_launch_cb = QCheckBox("Auto-launch OBS when overlay starts")
        self.auto_launch_cb.setChecked(self.config.get('auto_launch_obs', False))
        layout.addRow("", self.auto_launch_cb)

        self.auto_buffer_cb = QCheckBox("Auto-start replay buffer on connect")
        self.auto_buffer_cb.setChecked(self.config.get('auto_start_buffer', False))
        layout.addRow("", self.auto_buffer_cb)

        return page

    def _create_hotkey_page(self):
        page = QWizardPage()
        page.setTitle("Hotkeys")
        page.setSubTitle("Set up global hotkeys for quick access.")

        layout = QFormLayout(page)

        self.toggle_edit = QLineEdit()
        self.toggle_edit.setText(self.config.get('toggle_hotkey', 'f10'))
        self.toggle_edit.setPlaceholderText("e.g., f10, ctrl+shift+o")
        layout.addRow("Toggle Overlay:", self.toggle_edit)

        self.save_edit = QLineEdit()
        self.save_edit.setText(self.config.get('save_hotkey', 'f9'))
        self.save_edit.setPlaceholderText("e.g., f9, num add")
        layout.addRow("Save Replay:", self.save_edit)

        sync_btn = QPushButton("Sync from OBS")
        sync_btn.clicked.connect(self._sync_from_obs)
        layout.addRow("", sync_btn)

        hint = QLabel("Tip: Use the same save hotkey as OBS for consistency")
        hint.setStyleSheet("color: gray; font-size: 10px;")
        layout.addRow("", hint)

        return page

    def _sync_from_obs(self):
        hotkey = get_obs_replay_hotkey()
        if hotkey:
            self.save_edit.setText(hotkey)

    def _create_folder_page(self):
        page = QWizardPage()
        page.setTitle("Recording Folder")
        page.setSubTitle("Choose where your replays are saved.")

        layout = QVBoxLayout(page)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setText(self.config.get('watch_folder', str(Path.home() / "Videos")))
        folder_row.addWidget(self.folder_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        layout.addLayout(folder_row)

        self.organize_cb = QCheckBox("Organize replays by game (creates subfolders)")
        self.organize_cb.setChecked(self.config.get('organize_by_game', True))
        layout.addWidget(self.organize_cb)

        self.sync_folder_cb = QCheckBox("Sync folder from OBS settings")
        self.sync_folder_cb.setChecked(self.config.get('sync_obs_folder', True))
        layout.addWidget(self.sync_folder_cb)

        layout.addStretch()
        return page

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _create_finish_page(self):
        page = QWizardPage()
        page.setTitle("Setup Complete")
        page.setSubTitle("You're all set!")

        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Configuration complete!\n\n"
            "Quick tips:\n"
            "- Press your toggle hotkey to show/hide the overlay\n"
            "- The REC indicator shows when replay buffer is active\n"
            "- Right-click the tray icon for more options\n"
            "- Access settings anytime from the overlay or tray\n\n"
            "Click Finish to start using Replay Overlay."
        ))

        self.startup_cb = QCheckBox("Start with Windows")
        self.startup_cb.setChecked(self.config.get('start_with_windows', False))
        layout.addWidget(self.startup_cb)

        layout.addStretch()
        return page

    def accept(self):
        # Save all settings
        self.config['obs_port'] = self.port_spin.value()
        self.config['obs_password'] = self.password_edit.text()
        self.config['auto_launch_obs'] = self.auto_launch_cb.isChecked()
        self.config['auto_start_buffer'] = self.auto_buffer_cb.isChecked()
        self.config['toggle_hotkey'] = self.toggle_edit.text() or 'f10'
        self.config['save_hotkey'] = self.save_edit.text() or 'f9'
        self.config['watch_folder'] = self.folder_edit.text()
        self.config['organize_by_game'] = self.organize_cb.isChecked()
        self.config['sync_obs_folder'] = self.sync_folder_cb.isChecked()
        self.config['start_with_windows'] = self.startup_cb.isChecked()

        if self.startup_cb.isChecked():
            set_windows_startup(True)

        save_config(self.config)
        super().accept()


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config.copy()
        self.setWindowTitle("Replay Overlay")
        self.setFixedWidth(380)
        self.setStyleSheet("""
            QDialog { background-color: #1a1a2e; }
            QLabel { color: #eaeaea; font-size: 11px; }
            QLabel#section { color: #e94560; font-size: 12px; font-weight: bold; margin-top: 8px; }
            QLineEdit, QSpinBox, QDoubleSpinBox {
                background-color: #16213e; border: 1px solid #2c3e50;
                border-radius: 4px; padding: 6px; color: #eaeaea;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #4ecca3; }
            QPushButton {
                background-color: #16213e; border: 1px solid #2c3e50;
                border-radius: 4px; padding: 6px 12px; color: #eaeaea;
            }
            QPushButton:hover { background-color: #0f3460; border-color: #4ecca3; }
            QPushButton#save { background-color: #e94560; border-color: #e94560; color: white; }
            QPushButton#save:hover { background-color: #ff5577; }
            QCheckBox { color: #eaeaea; font-size: 11px; }
            QCheckBox::indicator { width: 14px; height: 14px; }
            QScrollArea { border: none; background: transparent; }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(8)

        # Title
        title = QLabel("Replay Overlay")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #eaeaea; margin-bottom: 8px;")
        main_layout.addWidget(title)

        # Scroll area for settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_widget = QWidget()
        layout = QVBoxLayout(scroll_widget)
        layout.setSpacing(6)
        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll, 1)

        # === OVERLAY SECTION ===
        overlay_lbl = QLabel("Overlay")
        overlay_lbl.setObjectName("section")
        layout.addWidget(overlay_lbl)

        # Watch Folder
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Watch Folder"))
        self.folder_edit = QLineEdit(self.config.get('watch_folder', ''))
        folder_row.addWidget(self.folder_edit, 1)
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(32)
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        layout.addLayout(folder_row)

        self.sync_cb = QCheckBox("Sync folder with OBS (updates OBS recording path)")
        self.sync_cb.setChecked(self.config.get('sync_obs_folder', True))
        layout.addWidget(self.sync_cb)

        # Duration & Opacity
        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("Duration"))
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.5, 10.0)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setValue(self.config.get('notification_duration', 3.0))
        self.duration_spin.setFixedWidth(75)
        dur_row.addWidget(self.duration_spin)
        dur_row.addSpacing(12)
        dur_row.addWidget(QLabel("Opacity"))
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(10, 100)
        self.opacity_spin.setValue(self.config.get('notification_opacity', 25))
        self.opacity_spin.setSuffix(" %")
        self.opacity_spin.setFixedWidth(80)
        dur_row.addWidget(self.opacity_spin)
        dur_row.addStretch()
        layout.addLayout(dur_row)

        # Message
        msg_row = QHBoxLayout()
        msg_row.addWidget(QLabel("Message"))
        self.message_edit = QLineEdit(self.config.get('notification_message', 'REPLAY SAVED'))
        msg_row.addWidget(self.message_edit, 1)
        layout.addLayout(msg_row)

        self.rec_indicator_cb = QCheckBox("Show REC indicator when buffer active")
        self.rec_indicator_cb.setChecked(self.config.get('show_rec_indicator', True))
        layout.addWidget(self.rec_indicator_cb)

        # REC indicator position
        rec_pos_row = QHBoxLayout()
        rec_pos_row.addWidget(QLabel("REC Position"))
        self.rec_position_combo = QComboBox()
        self.rec_position_combo.addItems([
            "Top-Left", "Top-Center", "Top-Right",
            "Bottom-Left", "Bottom-Center", "Bottom-Right"
        ])
        # Map stored value to combo index
        pos_map = {
            "top-left": 0, "top-center": 1, "top-right": 2,
            "bottom-left": 3, "bottom-center": 4, "bottom-right": 5
        }
        current_pos = self.config.get('rec_indicator_position', 'top-left')
        self.rec_position_combo.setCurrentIndex(pos_map.get(current_pos, 0))
        self.rec_position_combo.setFixedWidth(120)
        rec_pos_row.addWidget(self.rec_position_combo)
        rec_pos_row.addStretch()
        layout.addLayout(rec_pos_row)

        # === OBS SECTION ===
        obs_lbl = QLabel("OBS")
        obs_lbl.setObjectName("section")
        layout.addWidget(obs_lbl)

        # OBS Path
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("OBS Path"))
        self.obs_path_edit = QLineEdit(self.config.get('obs_path', ''))
        path_row.addWidget(self.obs_path_edit, 1)
        browse_obs_btn = QPushButton("...")
        browse_obs_btn.setFixedWidth(32)
        browse_obs_btn.clicked.connect(self._browse_obs)
        path_row.addWidget(browse_obs_btn)
        layout.addLayout(path_row)

        # Auto-launch options
        launch_row = QHBoxLayout()
        self.auto_launch_cb = QCheckBox("Auto-launch")
        self.auto_launch_cb.setChecked(self.config.get('auto_launch_obs', False))
        launch_row.addWidget(self.auto_launch_cb)
        self.minimized_cb = QCheckBox("Minimized")
        self.minimized_cb.setChecked(self.config.get('auto_launch_minimized', True))
        launch_row.addWidget(self.minimized_cb)
        launch_row.addStretch()
        layout.addLayout(launch_row)

        self.organize_cb = QCheckBox("Organize replays by game (creates subfolders)")
        self.organize_cb.setChecked(self.config.get('organize_by_game', True))
        layout.addWidget(self.organize_cb)

        self.auto_buffer_cb = QCheckBox("Auto-start replay buffer on connect")
        self.auto_buffer_cb.setChecked(self.config.get('auto_start_buffer', False))
        layout.addWidget(self.auto_buffer_cb)

        # === HOTKEY SECTION ===
        hotkey_lbl = QLabel("Hotkey")
        hotkey_lbl.setObjectName("section")
        layout.addWidget(hotkey_lbl)

        hotkey_row = QHBoxLayout()
        self.hotkey_enabled_cb = QCheckBox("Enable")
        self.hotkey_enabled_cb.setChecked(self.config.get('hotkey_enabled', True))
        hotkey_row.addWidget(self.hotkey_enabled_cb)
        self.hotkey_edit = QLineEdit(self.config.get('save_hotkey', 'f9'))
        self.hotkey_edit.setFixedWidth(80)
        self.hotkey_edit.setPlaceholderText("Key")
        hotkey_row.addWidget(self.hotkey_edit)
        sync_btn = QPushButton("Sync from OBS")
        sync_btn.setFixedWidth(100)
        sync_btn.clicked.connect(self._sync_hotkey_from_obs)
        hotkey_row.addWidget(sync_btn)
        hotkey_row.addStretch()
        layout.addLayout(hotkey_row)

        # Toggle hotkey
        toggle_row = QHBoxLayout()
        toggle_row.addWidget(QLabel("Toggle Overlay:"))
        self.toggle_edit = QLineEdit(self.config.get('toggle_hotkey', 'f10'))
        self.toggle_edit.setFixedWidth(80)
        toggle_row.addWidget(self.toggle_edit)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        self.admin_cb = QCheckBox("Run as Administrator (required if OBS/games run as admin)")
        self.admin_cb.setChecked(self.config.get('run_as_admin', False))
        layout.addWidget(self.admin_cb)

        # === SYSTEM SECTION ===
        system_lbl = QLabel("System")
        system_lbl.setObjectName("section")
        layout.addWidget(system_lbl)

        self.startup_cb = QCheckBox("Start with Windows")
        self.startup_cb.setChecked(self.config.get('start_with_windows', False))
        layout.addWidget(self.startup_cb)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setObjectName("save")
        save_btn.setFixedWidth(100)
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(save_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        main_layout.addLayout(btn_layout)

    def _sync_hotkey_from_obs(self):
        hotkey = get_obs_replay_hotkey()
        if hotkey:
            self.hotkey_edit.setText(hotkey)
        else:
            # Show message that hotkey couldn't be read
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Sync Failed",
                "Could not read replay buffer hotkey from OBS.\n\n"
                "Make sure OBS has been run at least once and you have\n"
                "configured a hotkey for 'Save Replay Buffer'."
            )

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _browse_obs(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select OBS", self.obs_path_edit.text(), "Executable (*.exe)")
        if path:
            self.obs_path_edit.setText(path)

    def _save(self):
        # Overlay settings
        self.config['watch_folder'] = self.folder_edit.text()
        self.config['sync_obs_folder'] = self.sync_cb.isChecked()
        self.config['notification_duration'] = self.duration_spin.value()
        self.config['notification_opacity'] = self.opacity_spin.value()
        self.config['notification_message'] = self.message_edit.text()
        self.config['show_rec_indicator'] = self.rec_indicator_cb.isChecked()
        # Map combo index to position string
        pos_values = ["top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"]
        self.config['rec_indicator_position'] = pos_values[self.rec_position_combo.currentIndex()]

        # OBS settings
        self.config['obs_path'] = self.obs_path_edit.text()
        self.config['auto_launch_obs'] = self.auto_launch_cb.isChecked()
        self.config['auto_launch_minimized'] = self.minimized_cb.isChecked()
        self.config['organize_by_game'] = self.organize_cb.isChecked()
        self.config['auto_start_buffer'] = self.auto_buffer_cb.isChecked()

        # Hotkey settings
        self.config['hotkey_enabled'] = self.hotkey_enabled_cb.isChecked()
        self.config['save_hotkey'] = self.hotkey_edit.text()
        self.config['toggle_hotkey'] = self.toggle_edit.text()
        self.config['run_as_admin'] = self.admin_cb.isChecked()

        # System settings - handle Windows startup registry
        new_startup = self.startup_cb.isChecked()
        if new_startup != self.config.get('start_with_windows', False):
            set_windows_startup(new_startup)
        self.config['start_with_windows'] = new_startup

        self.accept()


class NotificationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(280, 60)

        panel = QWidget()
        panel.setStyleSheet("background-color: rgba(26, 26, 46, 240); border-radius: 8px;")
        self.setCentralWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 20px; font-weight: bold; padding: 15px;")
        layout.addWidget(self.label)

        self._timer = QTimer()
        self._timer.timeout.connect(self.hide)
        self._position()

    def _position(self):
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - self.width() - 20, 50)

    def show_message(self, text, color, duration=3000):
        self.label.setText(text)
        self.label.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: bold; padding: 15px;")
        self._timer.stop()
        self._timer.start(duration)
        self.show()


class RecIndicatorPanel(QWidget):
    """Custom painted panel for REC indicator."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(15, 15, 15, 220))
        painter.setPen(QColor(233, 69, 96, 100))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 11, 11)


class RecIndicatorWindow(QMainWindow):
    """Persistent REC indicator overlay - shows when replay buffer is active."""

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(58, 22)

        panel = RecIndicatorPanel()
        self.setCentralWidget(panel)

        layout = QHBoxLayout(panel)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(4)

        # Red dot - use unicode circle for reliable rendering
        self.dot = QLabel("\u2022")
        self.dot.setStyleSheet("color: #e94560; font-size: 14px; background: transparent;")
        self.dot.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.dot)

        # REC text
        self.label = QLabel("REC")
        self.label.setStyleSheet("color: #e94560; font-size: 9px; font-weight: bold; background: transparent; letter-spacing: 1px;")
        layout.addWidget(self.label)

        self._position()
        self._blink_timer = QTimer()
        self._blink_timer.timeout.connect(self._blink)
        self._blink_state = True

    def _position(self):
        screen = QApplication.primaryScreen().geometry()
        pos = self.config.get('rec_indicator_position', 'top-left')
        margin = 20
        w, h = self.width(), self.height()

        match pos:
            case 'top-left':
                x, y = margin, margin
            case 'top-center':
                x, y = (screen.width() - w) // 2, margin
            case 'top-right':
                x, y = screen.width() - w - margin, margin
            case 'bottom-left':
                x, y = margin, screen.height() - h - margin
            case 'bottom-center':
                x, y = (screen.width() - w) // 2, screen.height() - h - margin
            case 'bottom-right':
                x, y = screen.width() - w - margin, screen.height() - h - margin
            case _:
                x, y = margin, margin

        self.move(x, y)

    def _blink(self):
        self._blink_state = not self._blink_state
        if self._blink_state:
            self.dot.setStyleSheet("color: #e94560; font-size: 14px; background: transparent;")
            self.raise_()  # Keep on top during blink cycle
        else:
            self.dot.setStyleSheet("color: transparent; font-size: 14px; background: transparent;")

    def set_active(self, active):
        if not self.config.get('show_rec_indicator', True):
            self.hide()
            return

        if active:
            self._position()  # Reposition each time in case config changed
            self._blink_timer.start(500)
            self.show()
            self.raise_()  # Force to top of window stack
        else:
            self._blink_timer.stop()
            self.hide()


class OverlayPanel(QMainWindow):
    def __init__(self, config, obs, signals, app):
        super().__init__()
        self.config = config
        self.obs = obs
        self.signals = signals
        self.app = app
        self._visible = False
        self._current_scene = None
        self._source_widgets = {}
        self._audio_widgets = {}
        self._last_audio_names = set()
        self._last_game = None  # Track last game for organize-by-game

        self._setup_ui()
        self._connect_signals()
        self._start_timers()

    def _setup_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._position_window()

        # Main panel with solid background
        panel = QWidget()
        panel.setObjectName("mainPanel")
        panel.setStyleSheet("""
            QWidget#mainPanel {
                background-color: #1a1a2e;
                border: 1px solid #2c3e50;
                border-radius: 8px;
            }
            QLabel { color: #eaeaea; font-family: Segoe UI, Arial; }
            QLabel#title { font-size: 12px; font-weight: bold; color: #4ecca3; }
            QLabel#section { font-size: 9px; font-weight: bold; color: #7f8c8d; margin-top: 4px; }
            QPushButton {
                background-color: #16213e;
                border: 2px solid #2c3e50;
                border-radius: 4px;
                padding: 5px 8px;
                color: #7f8c8d;
                font-family: Segoe UI, Arial;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #0f3460; border-color: #4ecca3; color: #eaeaea; }
            QPushButton#active { background-color: #1a4a3a; border-color: #4ecca3; color: #4ecca3; font-weight: bold; }
            QPushButton#recording { background-color: #4a1a2a; border-color: #e94560; color: #e94560; font-weight: bold; }
            QPushButton#save { background-color: #16213e; border-color: #4ecca3; color: #4ecca3; }
            QListWidget {
                background-color: #16213e;
                border: 1px solid #2c3e50;
                border-radius: 4px;
                color: #eaeaea;
                font-size: 11px;
            }
            QListWidget::item { padding: 3px 6px; }
            QListWidget::item:selected { background-color: #0f3460; }
            QScrollArea { border: 1px solid #2c3e50; border-radius: 4px; background: #16213e; }
            QCheckBox { color: #eaeaea; }
            QSlider::groove:horizontal { background: #2c3e50; height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { background: #4ecca3; width: 10px; height: 10px; margin: -3px 0; border-radius: 5px; }
            QSlider::sub-page:horizontal { background: #4ecca3; border-radius: 2px; }
        """)
        self.setCentralWidget(panel)

        main = QVBoxLayout(panel)
        main.setContentsMargins(10, 8, 10, 8)
        main.setSpacing(6)

        # === HEADER ===
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("OBS CONTROL")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()

        self.capture_indicator = QLabel()
        self.capture_indicator.setStyleSheet("font-size: 9px; font-weight: bold;")
        self.capture_indicator.hide()
        header.addWidget(self.capture_indicator)

        self.status_label = QLabel("...")
        self.status_label.setStyleSheet("font-size: 9px; color: #7f8c8d;")
        header.addWidget(self.status_label)

        settings_btn = QPushButton("SET")
        settings_btn.setFixedSize(32, 18)
        settings_btn.setStyleSheet("font-size: 8px; padding: 0; border-color: #4ecca3; color: #4ecca3;")
        settings_btn.clicked.connect(self._open_settings)
        header.addWidget(settings_btn)

        close_btn = QPushButton("X")
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet("font-size: 10px; padding: 0; border-color: #e94560; color: #e94560;")
        close_btn.clicked.connect(self.hide_overlay)
        header.addWidget(close_btn)
        main.addLayout(header)

        # === PREVIEW (16:9 aspect ratio) ===
        preview_container = QWidget()
        preview_container.setFixedHeight(100)
        preview_container.setStyleSheet("background: #000; border: 1px solid #2c3e50; border-radius: 4px;")
        preview_layout = QHBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addStretch()
        self.preview_label = QLabel("No Preview")
        self.preview_label.setFixedSize(178, 100)  # 16:9 aspect ratio
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("color: #555;")
        preview_layout.addWidget(self.preview_label)
        preview_layout.addStretch()
        main.addWidget(preview_container)

        # === SCENES & SOURCES (side by side) ===
        lists = QHBoxLayout()
        lists.setSpacing(6)

        # Scenes
        scenes_col = QVBoxLayout()
        scenes_col.setSpacing(2)
        scenes_lbl = QLabel("SCENES")
        scenes_lbl.setObjectName("section")
        scenes_col.addWidget(scenes_lbl)
        self.scenes_list = QListWidget()
        self.scenes_list.setFixedHeight(70)
        self.scenes_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scenes_list.itemClicked.connect(lambda i: self.obs.set_scene(i.text()))
        scenes_col.addWidget(self.scenes_list)
        lists.addLayout(scenes_col, 1)

        # Sources
        sources_col = QVBoxLayout()
        sources_col.setSpacing(2)
        sources_lbl = QLabel("SOURCES")
        sources_lbl.setObjectName("section")
        sources_col.addWidget(sources_lbl)
        self.sources_scroll = QScrollArea()
        self.sources_scroll.setFixedHeight(70)
        self.sources_scroll.setWidgetResizable(True)
        self.sources_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sources_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.sources_widget = QWidget()
        self.sources_layout = QVBoxLayout(self.sources_widget)
        self.sources_layout.setContentsMargins(4, 2, 4, 2)
        self.sources_layout.setSpacing(1)
        self.sources_layout.addStretch()
        self.sources_scroll.setWidget(self.sources_widget)
        sources_col.addWidget(self.sources_scroll)
        lists.addLayout(sources_col, 1)
        main.addLayout(lists)

        # === AUDIO MIXER ===
        audio_lbl = QLabel("AUDIO")
        audio_lbl.setObjectName("section")
        main.addWidget(audio_lbl)

        self.audio_scroll = QScrollArea()
        self.audio_scroll.setFixedHeight(72)
        self.audio_scroll.setWidgetResizable(True)
        self.audio_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.audio_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.audio_widget = QWidget()
        self.audio_layout = QVBoxLayout(self.audio_widget)
        self.audio_layout.setContentsMargins(4, 2, 4, 2)
        self.audio_layout.setSpacing(2)
        self.audio_layout.addStretch()
        self.audio_scroll.setWidget(self.audio_widget)
        main.addWidget(self.audio_scroll)

        # === CONTROLS ===
        controls_lbl = QLabel("CONTROLS")
        controls_lbl.setObjectName("section")
        main.addWidget(controls_lbl)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self.stream_btn = QPushButton("Stream")
        self.stream_btn.setFixedHeight(26)
        self.stream_btn.clicked.connect(self.obs.toggle_stream)
        row1.addWidget(self.stream_btn)
        self.record_btn = QPushButton("Record")
        self.record_btn.setFixedHeight(26)
        self.record_btn.clicked.connect(self.obs.toggle_record)
        row1.addWidget(self.record_btn)
        main.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self.buffer_btn = QPushButton("Buffer")
        self.buffer_btn.setFixedHeight(26)
        self.buffer_btn.clicked.connect(self.obs.toggle_buffer)
        row2.addWidget(self.buffer_btn)
        self.save_btn = QPushButton("Save Replay")
        self.save_btn.setFixedHeight(26)
        self.save_btn.setObjectName("save")
        self.save_btn.clicked.connect(self._save_replay)
        row2.addWidget(self.save_btn)
        main.addLayout(row2)

        # === FOOTER ===
        footer = QLabel(f"{self.config.get('toggle_hotkey', 'F10').upper()} toggle | {self.config.get('save_hotkey', 'F9').upper()} save")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color: #555; font-size: 9px; margin-top: 4px;")
        main.addWidget(footer)

        # Set minimum size, allow resizing
        self.setMinimumSize(340, 456)
        self.resize(340, 456)

    def _position_window(self):
        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - 360
        y = 50
        self.move(x, y)

    def _connect_signals(self):
        # Use QueuedConnection to ensure slots run in main thread when signals come from other threads
        self.signals.toggle_overlay.connect(self.toggle_visibility, Qt.QueuedConnection)
        self.signals.save_replay.connect(self._save_replay, Qt.QueuedConnection)
        self.signals.update_ui.connect(self._update_ui, Qt.QueuedConnection)
        self.signals.update_preview.connect(self._set_preview)

    def _start_timers(self):
        self._preview_timer = QTimer()
        self._preview_timer.timeout.connect(self._fetch_preview)
        self._preview_timer.start(250)

        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._fetch_status)
        self._status_timer.start(1000)

    def _fetch_preview(self):
        if not self._visible: return
        def fetch():
            # Request 16:9 preview at higher res for quality, will scale down
            px = self.obs.get_screenshot(320, 180)
            if px: self.signals.update_preview.emit(px)
        threading.Thread(target=fetch, daemon=True).start()

    def _set_preview(self, px):
        if px and not px.isNull():
            # Scale to fit the 178x100 label (16:9 aspect ratio)
            self.preview_label.setPixmap(px.scaled(178, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _fetch_status(self):
        def fetch():
            data = {
                'connected': self.obs.connected,
                'scenes': self.obs.get_scenes(),
                'current_scene': self.obs.get_current_scene(),
                'sources': [],
                'audio': self.obs.get_audio_sources(),
                'streaming': self.obs.get_stream_status(),
                'recording': self.obs.get_record_status(),
                'buffer': self.obs.get_buffer_status(),
                'has_capture': self.obs.has_active_capture(),
            }
            if data['current_scene']:
                data['sources'] = self.obs.get_scene_items(data['current_scene'])
            self.signals.update_ui.emit(data)
        threading.Thread(target=fetch, daemon=True).start()

    def _update_ui(self, data):
        # Status
        if data['connected']:
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet("color: #4ecca3; font-size: 10px;")
        else:
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet("color: #e94560; font-size: 10px;")

        # Capture indicator
        buffer_on = data.get('buffer')
        has_capture = data.get('has_capture')
        if buffer_on:
            if has_capture:
                self.capture_indicator.setText("[REC]")
                self.capture_indicator.setStyleSheet("color: #e94560; font-size: 10px; font-weight: bold;")
            else:
                self.capture_indicator.setText("[IDLE]")
                self.capture_indicator.setStyleSheet("color: #f39c12; font-size: 10px; font-weight: bold;")
            self.capture_indicator.show()
        else:
            self.capture_indicator.hide()

        # Scenes
        scenes = data.get('scenes', [])
        current = data.get('current_scene')
        if scenes:
            items = [self.scenes_list.item(i).text() for i in range(self.scenes_list.count())]
            if scenes != items:
                self.scenes_list.clear()
                for s in scenes:
                    self.scenes_list.addItem(s)
            for i in range(self.scenes_list.count()):
                if self.scenes_list.item(i).text() == current:
                    self.scenes_list.setCurrentRow(i)
                    break

        # Sources - only rebuild if scene changed
        if current != self._current_scene:
            self._current_scene = current
            self._rebuild_sources(data.get('sources', []))
        else:
            # Update visibility of existing sources
            for src in data.get('sources', []):
                if src['id'] in self._source_widgets:
                    self._source_widgets[src['id']].update_visible(src['visible'])

        # Audio - update existing widgets, only add/remove as needed
        self._update_audio(data.get('audio', []))

        # Buttons
        streaming = data.get('streaming')
        recording = data.get('recording')
        buffer_active = data.get('buffer')

        self.stream_btn.setText("Stop Stream" if streaming else "Stream")
        self.stream_btn.setObjectName("recording" if streaming else "")
        self.stream_btn.style().unpolish(self.stream_btn)
        self.stream_btn.style().polish(self.stream_btn)

        self.record_btn.setText("Stop Rec" if recording else "Record")
        self.record_btn.setObjectName("recording" if recording else "")
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)

        self.buffer_btn.setText("Stop Buffer" if buffer_active else "Buffer")
        self.buffer_btn.setObjectName("active" if buffer_active else "")
        self.buffer_btn.style().unpolish(self.buffer_btn)
        self.buffer_btn.style().polish(self.buffer_btn)

    def _rebuild_sources(self, sources):
        # Clear existing
        while self.sources_layout.count() > 1:
            item = self.sources_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._source_widgets.clear()

        for src in sources:
            w = SourceWidget(src['id'], src['name'], src['visible'])
            # Use a helper to avoid lambda closure issues
            self._connect_source_toggle(w)
            self.sources_layout.insertWidget(self.sources_layout.count() - 1, w)
            self._source_widgets[src['id']] = w

    def _connect_source_toggle(self, widget):
        def on_toggle(item_id, visible):
            self.obs.set_source_visible(self._current_scene, item_id, visible)
        widget.toggled.connect(on_toggle)

    def _update_audio(self, audio):
        current_names = {a['name'] for a in audio}

        # IMPORTANT: Ignore empty results if we already have widgets
        # This prevents flashing from intermittent API failures
        if not current_names and self._audio_widgets:
            return

        # Only rebuild if the set of sources actually changed
        if current_names == self._last_audio_names:
            # Update mute states and volume levels
            for src in audio:
                if src['name'] in self._audio_widgets:
                    self._audio_widgets[src['name']].update_mute(src['muted'])
                    self._audio_widgets[src['name']].update_volume(src['volume'])
            return

        # Sources changed - rebuild
        self._last_audio_names = current_names.copy()

        # Disable updates during rebuild
        self.audio_widget.setUpdatesEnabled(False)

        # Remove widgets for sources that no longer exist
        for name in list(self._audio_widgets.keys()):
            if name not in current_names:
                w = self._audio_widgets.pop(name)
                self.audio_layout.removeWidget(w)
                w.deleteLater()

        # Add new widgets
        for src in audio:
            name = src['name']
            if name not in self._audio_widgets:
                w = AudioWidget(name, src['volume'], src['muted'])
                w.volume_changed.connect(lambda n, v: self.obs.set_input_volume(n, v))
                w.mute_toggled.connect(lambda n: self.obs.toggle_mute(n))
                self.audio_layout.insertWidget(self.audio_layout.count() - 1, w)
                self._audio_widgets[name] = w

        self.audio_widget.setUpdatesEnabled(True)

    def _save_replay(self):
        if self.config.get('organize_by_game', False):
            game = get_foreground_process()
            # If foreground is overlay or ignored, use last tracked game
            if not game or game.lower() in IGNORED_PROCESSES:
                game = getattr(self, '_last_game', None)
            if game and game.lower() not in IGNORED_PROCESSES:
                if self.app.replay_handler:
                    self.app.replay_handler.pending_game = game

        # Check capture status
        has_capture = self.obs.has_active_capture()
        if has_capture is False:
            self.signals.show_notification.emit("NO CAPTURE DETECTED", "#f39c12")
            return

        if self.obs.save_buffer():
            self.save_btn.setStyleSheet("background-color: #00FF00;")
            QTimer.singleShot(200, lambda: self.save_btn.setStyleSheet(""))
            # Show notification with custom message
            msg = self.config.get('notification_message', 'REPLAY SAVED')
            duration = int(self.config.get('notification_duration', 3.0) * 1000)
            self.signals.show_notification.emit(msg, "#00FF00")

    def _open_settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec():
            self.config.update(dialog.config)
            save_config(self.config)
            print(f"Settings saved. Config: toggle={self.config.get('toggle_hotkey')}, save={self.config.get('save_hotkey')}")
            # Notify app to update hotkeys and REC indicator
            if hasattr(self.app, '_register_hotkeys'):
                self.app._register_hotkeys()
            if hasattr(self.app, '_refresh_rec_indicator'):
                self.app._refresh_rec_indicator()
            # Also update file watcher if folder changed
            if hasattr(self.app, '_restart_file_watcher'):
                self.app._restart_file_watcher()

    def toggle_visibility(self):
        # Use actual visibility instead of internal flag for reliability
        if self.isVisible():
            self.hide_overlay()
        else:
            self.show_overlay()

    def show_overlay(self):
        # Capture the current game BEFORE showing overlay
        game = get_foreground_process()
        if game and game.lower() not in IGNORED_PROCESSES:
            self._last_game = game
        self._visible = True
        self._position_window()
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_overlay(self):
        self._visible = False
        self.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._resize_edge = self._get_resize_edge(event.position().toPoint())

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            if hasattr(self, '_resize_edge') and self._resize_edge:
                self._do_resize(event.globalPosition().toPoint())
            elif hasattr(self, '_drag_pos'):
                self.move(event.globalPosition().toPoint() - self._drag_pos)
        else:
            # Update cursor based on hover position
            edge = self._get_resize_edge(event.position().toPoint())
            match edge:
                case 'right' | 'left':
                    self.setCursor(Qt.SizeHorCursor)
                case 'bottom' | 'top':
                    self.setCursor(Qt.SizeVerCursor)
                case 'bottomright' | 'topleft':
                    self.setCursor(Qt.SizeFDiagCursor)
                case 'bottomleft' | 'topright':
                    self.setCursor(Qt.SizeBDiagCursor)
                case _:
                    self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._resize_edge = None

    def _get_resize_edge(self, pos):
        margin = 8
        rect = self.rect()
        x, y = pos.x(), pos.y()

        on_left = x < margin
        on_right = x > rect.width() - margin
        on_top = y < margin
        on_bottom = y > rect.height() - margin

        if on_bottom and on_right:
            return 'bottomright'
        if on_bottom and on_left:
            return 'bottomleft'
        if on_top and on_right:
            return 'topright'
        if on_top and on_left:
            return 'topleft'
        if on_right:
            return 'right'
        if on_left:
            return 'left'
        if on_bottom:
            return 'bottom'
        if on_top:
            return 'top'
        return None

    def _do_resize(self, global_pos):
        geo = self.frameGeometry()
        min_w, min_h = self.minimumWidth(), self.minimumHeight()

        if 'right' in self._resize_edge:
            new_w = max(min_w, global_pos.x() - geo.left())
            geo.setWidth(new_w)
        if 'left' in self._resize_edge:
            new_w = max(min_w, geo.right() - global_pos.x())
            geo.setLeft(geo.right() - new_w)
        if 'bottom' in self._resize_edge:
            new_h = max(min_h, global_pos.y() - geo.top())
            geo.setHeight(new_h)
        if 'top' in self._resize_edge:
            new_h = max(min_h, geo.bottom() - global_pos.y())
            geo.setTop(geo.bottom() - new_h)

        self.setGeometry(geo)


class App:
    def __init__(self):
        self.config = load_config()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # Show setup wizard on first run (no config file exists)
        if not CONFIG_PATH.exists():
            wizard = SetupWizard(self.config)
            if wizard.exec():
                self.config = load_config()  # Reload after wizard saves
            else:
                sys.exit(0)  # User cancelled wizard

        self._sync_obs_hotkey()  # Try to read hotkey from OBS config
        self.signals = SignalBridge()
        self.obs = OBSController(
            port=self.config.get('obs_port', 4455),
            password=self.config.get('obs_password', '')
        )

        self.notification = NotificationWindow()
        self.rec_indicator = RecIndicatorWindow(self.config)
        self.overlay = OverlayPanel(self.config, self.obs, self.signals, self)
        self.replay_handler = None
        self.observer = None
        self._hotkey_hooks = []
        self._last_buffer_status = None

        self._setup_tray()
        self._connect_obs()
        self._register_hotkeys()
        self._start_file_watcher()
        self._start_buffer_monitor()

        self.signals.show_notification.connect(self.notification.show_message)
        self.signals.update_rec_indicator.connect(self.rec_indicator.set_active)

    def _sync_obs_hotkey(self):
        """Try to read the save replay hotkey from OBS config on startup."""
        hotkey = get_obs_replay_hotkey()
        if hotkey:
            # Only update if different from current
            if hotkey != self.config.get('save_hotkey', 'f9'):
                self.config['save_hotkey'] = hotkey
                save_config(self.config)
                print(f"Synced save hotkey from OBS: {hotkey}")

    def _setup_tray(self):
        self.tray = QSystemTrayIcon()
        px = QPixmap(32, 32)
        px.fill(QColor("#e94560"))
        self.tray.setIcon(QIcon(px))
        self.tray.setToolTip("Replay Overlay")

        menu = QMenu()
        menu.addAction("Show Overlay", self.overlay.show_overlay)
        menu.addSeparator()
        menu.addAction("Save Replay", self._save_replay_from_tray)
        menu.addAction("Start Buffer", lambda: self.obs.start_buffer())
        menu.addAction("Stop Buffer", lambda: self.obs.stop_buffer())
        menu.addSeparator()
        menu.addAction("Settings", self._open_settings)
        menu.addAction("Open Library", self._open_library)
        menu.addSeparator()
        menu.addAction("Restart", self._restart)
        menu.addAction("Exit", self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.overlay.toggle_visibility() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _connect_obs(self):
        if self.config.get('auto_launch_obs', False):
            obs_path = self.config.get('obs_path', '')
            if obs_path and is_valid_obs_executable(obs_path):
                # Check if OBS is already running
                if not self._is_obs_running():
                    try:
                        # Must set cwd to OBS directory so it can find locale files
                        obs_dir = Path(obs_path).parent
                        subprocess.Popen([obs_path, '--minimize-to-tray'], cwd=str(obs_dir))
                        logger.info(f"Launched OBS from: {obs_path}")
                    except (OSError, ValueError) as e:
                        logger.error(f"Failed to launch OBS: {e}")
            elif obs_path:
                logger.warning(f"Skipping OBS auto-launch: invalid path {obs_path}")

        def connect():
            for _ in range(10):
                if self.obs.connect():
                    print("Connected to OBS")
                    if self.config.get('sync_obs_folder', True):
                        folder = self.config.get('watch_folder', '')
                        if folder:
                            self.obs.set_record_directory(folder)
                    # Auto-start replay buffer if configured
                    if self.config.get('auto_start_buffer', False):
                        time.sleep(0.5)  # Brief delay to ensure OBS is ready
                        if not self.obs.get_buffer_status():
                            self.obs.start_buffer()
                            print("Auto-started replay buffer")
                    return
                time.sleep(2)
            print("Could not connect to OBS")

        threading.Thread(target=connect, daemon=True).start()

    def _is_obs_running(self):
        """Check if OBS is already running."""
        try:
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq obs64.exe', '/NH'],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            if 'obs64.exe' in result.stdout.lower():
                return True
            # Also check obs32.exe for 32-bit systems
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq obs32.exe', '/NH'],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            return 'obs32.exe' in result.stdout.lower()
        except (OSError, subprocess.SubprocessError):
            return False

    def _register_hotkeys(self):
        if not HAS_KEYBOARD:
            print("Keyboard library not available")
            return

        # Remove existing hotkeys by key string
        for key in self._hotkey_hooks:
            try:
                keyboard.remove_hotkey(key)
            except (KeyError, ValueError): pass
        self._hotkey_hooks.clear()

        if not self.config.get('hotkey_enabled', True):
            print("Hotkeys disabled in settings")
            return

        try:
            toggle_key = self.config.get('toggle_hotkey', 'f10') or 'f10'
            save_key = self.config.get('save_hotkey', 'f9') or 'f9'

            # Validate hotkeys - must be at least 2 chars and not just modifiers
            if len(toggle_key) >= 2 and toggle_key not in ('+', 'ctrl', 'alt', 'shift'):
                keyboard.add_hotkey(toggle_key, self._on_toggle_hotkey, suppress=False)
                self._hotkey_hooks.append(toggle_key)
                print(f"Toggle hotkey registered: {toggle_key.upper()}")
            else:
                print(f"Invalid toggle hotkey: '{toggle_key}', using F10")
                keyboard.add_hotkey('f10', self._on_toggle_hotkey, suppress=False)
                self._hotkey_hooks.append('f10')

            if len(save_key) >= 2 and save_key not in ('+', 'ctrl', 'alt', 'shift'):
                keyboard.add_hotkey(save_key, self._on_save_hotkey, suppress=False)
                self._hotkey_hooks.append(save_key)
                print(f"Save hotkey registered: {save_key.upper()}")
            else:
                print(f"Invalid save hotkey: '{save_key}', using F9")
                keyboard.add_hotkey('f9', self._on_save_hotkey, suppress=False)
                self._hotkey_hooks.append('f9')
        except Exception as e:
            print(f"Hotkey error: {e}")

    def _on_toggle_hotkey(self):
        """Handle toggle hotkey press - emits signal for thread safety."""
        print("F10 pressed - toggling overlay")
        self.signals.toggle_overlay.emit()

    def _on_save_hotkey(self):
        """Handle save hotkey press - emits signal for thread safety."""
        self.signals.save_replay.emit()

    def _start_file_watcher(self):
        if not HAS_WATCHDOG: return
        folder = Path(self.config.get('watch_folder', ''))
        if not folder.exists(): return

        self.replay_handler = ReplayHandler(self.signals, self.config)
        self.observer = Observer()
        self.observer.schedule(self.replay_handler, str(folder), recursive=False)
        self.observer.start()
        print(f"Watching: {folder}")

    def _restart_file_watcher(self):
        """Restart file watcher with updated config (e.g., new watch folder)."""
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2)
            self.observer = None
        self._start_file_watcher()

    def _start_buffer_monitor(self):
        """Monitor buffer status and update REC indicator."""
        def check():
            while True:
                time.sleep(1)
                if not self.obs.connected:
                    continue
                status = self.obs.get_buffer_status()
                if status != self._last_buffer_status:
                    self._last_buffer_status = status
                    self.signals.update_rec_indicator.emit(status or False)

        threading.Thread(target=check, daemon=True).start()

    def _refresh_rec_indicator(self):
        """Refresh REC indicator position and visibility after settings change."""
        # Update the config reference
        self.rec_indicator.config = self.config
        # Reposition if currently visible
        if self.rec_indicator.isVisible():
            self.rec_indicator._position()
        # Re-apply visibility based on current buffer status
        if self._last_buffer_status:
            self.rec_indicator.set_active(True)

    def _save_replay_from_tray(self):
        if self.config.get('organize_by_game', False):
            game = get_foreground_process()
            if game and game.lower() not in IGNORED_PROCESSES:
                if self.replay_handler:
                    self.replay_handler.pending_game = game
        if self.obs.save_buffer():
            msg = self.config.get('notification_message', 'REPLAY SAVED')
            self.signals.show_notification.emit(msg, "#00FF00")

    def _open_settings(self):
        dialog = SettingsDialog(self.config)
        if dialog.exec():
            old_hotkeys = (self.config.get('toggle_hotkey'), self.config.get('save_hotkey'))
            old_folder = self.config.get('watch_folder', '')
            self.config.update(dialog.config)
            save_config(self.config)
            print(f"Settings saved from tray. Folder: {self.config.get('watch_folder')}")

            # Re-register hotkeys if changed
            new_hotkeys = (self.config.get('toggle_hotkey'), self.config.get('save_hotkey'))
            if old_hotkeys != new_hotkeys:
                self._register_hotkeys()

            # Refresh REC indicator position
            self._refresh_rec_indicator()

            # Restart file watcher if folder changed
            if old_folder != self.config.get('watch_folder', ''):
                self._restart_file_watcher()

            # Sync folder with OBS
            if self.config.get('sync_obs_folder') and self.obs.connected:
                self.obs.set_record_directory(self.config.get('watch_folder', ''))

    def _open_library(self):
        folder = self.config.get('watch_folder', '')
        if folder and Path(folder).exists():
            os.startfile(folder)

    def _restart(self):
        self.quit()
        python = sys.executable
        os.execv(python, [python] + sys.argv)

    def run(self):
        print("Replay Overlay Interactive")
        print(f"Admin: {is_admin()}")
        sys.exit(self.app.exec())

    def quit(self):
        if self.observer:
            self.observer.stop()
        save_config(self.config)
        self.app.quit()


def main():
    # Check if admin elevation needed
    config = load_config()
    if config.get('run_as_admin', False) and not is_admin():
        print("Requesting admin privileges...")
        if request_admin_restart():
            sys.exit(0)

    app = App()
    app.run()


if __name__ == "__main__":
    main()
