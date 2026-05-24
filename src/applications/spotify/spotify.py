import gc
import time
import jpegdec
import pngdec
import uasyncio as asyncio
import urequests as requests
import ujson as json

from touch import Button

from applications.spotify.spotify_client import Session, SpotifyWebApiClient
from base import BaseApp
import secrets

DEBUG = False

def debug_print(*args):
    if DEBUG:
        print(*args)

class State:
    """Tracks the current state of the Spotify app including playback and UI controls."""
    WAITING_INDEX = 8
    TRACK_ID_INDEX = 11

    def __init__(self):
        self.toggle_leds = True
        self.backlight = 1.0
        self.is_playing = False
        self.repeat = False
        self.shuffle = False
        self.track = None
        self.waiting = False
        self.screen_asleep = False
        self.show_controls = False
        self.show_settings = False
        self.show_device_picker = False
        self.devices_loading = False
        self.device_name = "Device"
        self.device_label = "Device"
        self.devices = []
        self.exit = False

        self.latest_fetch = None

    def snapshot(self):
        track_id = (self.track or {}).get('id')
        return (
            self.toggle_leds,
            round(self.backlight, 2),
            self.is_playing,
            self.repeat,
            self.shuffle,
            self.show_controls,
            self.show_settings,
            self.show_device_picker,
            self.waiting,
            self.screen_asleep,
            self.exit,
            track_id,
            self.device_name,
        )

class ControlButton():
    """Represents a control button with an icon and touch area."""
    def __init__(self, display, name, icons, bounds, on_press=None, update=None):
        self.name = name
        self.enabled = False
        self.icon = icons[0] if icons else None
        self.pngs = {}
        if icons:
            for icon in icons:
                png = pngdec.PNG(display)
                png.open_file("applications/spotify/icons/" + icon)
                self.pngs[icon] = png

        self.button = Button(*bounds)
        self.on_press = on_press
        self.update = update

    def is_pressed(self, state):
        """Checks if the button is enabled and currently pressed."""
        return self.enabled and self.button.is_pressed()

    def draw(self, state):
        """Draws the button icon if enabled."""
        if self.enabled and self.icon:
            self.draw_icon()

    def draw_icon(self):
        """Renders the button's icon centered inside its bounds."""
        png = self.pngs[self.icon]
        x, y, width, height = self.button.bounds
        png_width, png_height = png.get_width(), png.get_height()
        x_offset = (width-png_width)//2
        y_offset = (height-png_height)//2

        png.decode(x+x_offset, y+y_offset)

class SettingsPanel:
    """Modal settings window with playback options and display controls."""
    PANEL = (12, 12, 456, 456)
    SLIDER = (190, 356, 230, 40)
    DEVICE_BUTTON = (190, 92, 230, 40)
    DEVICE_PICKER = (12, 12, 456, 456)
    PICKER_ROW_HEIGHT = 44
    LED_TOGGLE = (340, 148, 60, 60)
    SHUFFLE_TOGGLE = (340, 214, 60, 60)
    REPEAT_TOGGLE = (340, 280, 60, 60)
    CLOSE = (404, 18, 52, 40)
    TITLE_BAR_HEIGHT = 52

    def __init__(self, app):
        self.app = app
        self.display = app.display
        self.colors = app.colors

        self.led_icons = self._load_icons(("light_on.png", "light_off.png"))
        self.shuffle_icons = self._load_icons(("shuffle_on.png", "shuffle_off.png"))
        self.repeat_icons = self._load_icons(("repeat_on.png", "repeat_off.png"))

        self._slider_dragging = False
        self._slider_dirty = False

    def _load_icons(self, names):
        icons = {}
        for name in names:
            png = pngdec.PNG(self.display)
            png.open_file("applications/spotify/icons/" + name)
            icons[name] = png
        return icons

    def draw(self, state):
        px, py, pw, ph = self.PANEL
        self.display.set_pen(self.colors._BLACK)
        self.display.rectangle(px - 2, py - 2, pw + 4, ph + 4)
        self.display.set_pen(self.colors.GRAY)
        self.display.rectangle(px, py, pw, ph)

        self._draw_title_bar("Settings", self.PANEL)

        self.display.text("Device", px + 20, 112, scale=0.9)
        self._draw_device_button(state)

        self.display.text("Ambient LEDs", px + 20, 178, scale=0.9)
        self._draw_toggle_icon(
            self.led_icons["light_on.png" if state.toggle_leds else "light_off.png"],
            self.LED_TOGGLE,
        )

        self.display.text("Shuffle", px + 20, 244, scale=0.9)
        self._draw_toggle_icon(
            self.shuffle_icons["shuffle_on.png" if state.shuffle else "shuffle_off.png"],
            self.SHUFFLE_TOGGLE,
        )

        self.display.text("Repeat", px + 20, 310, scale=0.9)
        self._draw_toggle_icon(
            self.repeat_icons["repeat_on.png" if state.repeat else "repeat_off.png"],
            self.REPEAT_TOGGLE,
        )

        self.display.text("Backlight", px + 20, 376, scale=0.9)
        self._draw_slider(state.backlight)

        self._draw_close_button()

        if state.show_device_picker:
            self._draw_device_picker(state)

    def _truncate(self, text, max_len=24):
        text = ''.join(i if ord(i) < 128 else ' ' for i in (text or ""))
        if len(text) > max_len:
            return text[:max_len] + "..."
        return text

    def _draw_title_bar(self, title, bounds):
        px, py, pw, _ = bounds
        self.display.set_pen(self.colors.DARK_GRAY)
        self.display.rectangle(px, py, pw, self.TITLE_BAR_HEIGHT)
        self.display.set_pen(self.colors._BLACK)
        self.display.rectangle(px, py + self.TITLE_BAR_HEIGHT - 2, pw, 2)
        self.display.set_pen(self.colors.WHITE)
        self.display.set_thickness(2)
        self.display.text(title, px + 20, py + 25, scale=1.2)

    def _draw_close_button(self):
        cx, cy, cw, ch = self.CLOSE
        x_padding = 16
        y_padding = 10

        self.display.set_pen(self.colors._BLACK)
        self.display.rectangle(cx, cy, cw, ch)
        self.display.set_pen(self.colors.DARK_GRAY)
        self.display.rectangle(cx + 2, cy + 2, cw - 4, ch - 4)

        self.display.set_pen(self.colors._BLACK)
        self.display.line(
            cx + x_padding,
            cy + y_padding,
            cx + cw - x_padding,
            cy + ch - y_padding,
            8,
        )
        self.display.line(
            cx + cw - x_padding,
            cy + y_padding,
            cx + x_padding,
            cy + ch - y_padding,
            8,
        )

        self.display.set_pen(self.colors.WHITE)
        self.display.line(
            cx + x_padding,
            cy + y_padding,
            cx + cw - x_padding,
            cy + ch - y_padding,
            4,
        )
        self.display.line(
            cx + cw - x_padding,
            cy + y_padding,
            cx + x_padding,
            cy + ch - y_padding,
            4,
        )

    def _draw_device_button(self, state):
        bx, by, bw, bh = self.DEVICE_BUTTON
        self.display.set_pen(self.colors._BLACK)
        self.display.rectangle(bx, by, bw, bh)
        self.display.set_pen(self.colors.WHITE)
        self.display.rectangle(bx + 2, by + 2, bw - 4, bh - 4)
        self.display.set_pen(self.colors._BLACK)
        self.display.text(state.device_label, bx + 10, by + bh // 2, scale=0.8)

    def _draw_device_picker(self, state):
        px, py, pw, ph = self.DEVICE_PICKER
        self.display.set_pen(self.colors._BLACK)
        self.display.rectangle(px - 2, py - 2, pw + 4, ph + 4)
        self.display.set_pen(self.colors.GRAY)
        self.display.rectangle(px, py, pw, ph)
        self._draw_title_bar("Select Device", self.DEVICE_PICKER)
        self._draw_close_button()

        if state.devices_loading:
            self.display.text("Loading...", px + 20, py + 60, scale=0.9)
            return

        if not state.devices:
            self.display.text("No devices found", px + 20, py + 60, scale=0.9)
            self.display.text("Open Spotify on a device", px + 20, py + 85, scale=0.7)
            return

        current_id = self.app.spotify_client.session.device_id
        max_rows = min(len(state.devices), self._max_picker_rows())
        for i in range(max_rows):
            device = state.devices[i]
            row = self._picker_row_bounds(i)
            rx, ry, rw, rh = row
            if device.get("id") == current_id:
                self.display.set_pen(self.colors.GREEN)
                self.display.rectangle(rx, ry, rw, rh)
                self.display.set_pen(self.colors.WHITE)
            else:
                self.display.set_pen(self.colors._BLACK)
                self.display.rectangle(rx, ry, rw, rh)
                self.display.set_pen(self.colors.WHITE)

            self.display.text(device.get("display_name", "Unknown"), rx + 8, ry + rh // 2, scale=0.8)

    def _picker_row_bounds(self, index):
        px, py, pw, _ = self.DEVICE_PICKER
        row_y = py + self.TITLE_BAR_HEIGHT + 10 + index * self.PICKER_ROW_HEIGHT
        return (px + 10, row_y, pw - 20, self.PICKER_ROW_HEIGHT - 4)

    def _max_picker_rows(self):
        _, _, _, ph = self.DEVICE_PICKER
        return (ph - self.TITLE_BAR_HEIGHT - 20) // self.PICKER_ROW_HEIGHT

    def _draw_toggle_icon(self, icon, bounds):
        lx, ly, lw, lh = bounds
        icon_w, icon_h = icon.get_width(), icon.get_height()
        icon.decode(lx + (lw - icon_w) // 2, ly + (lh - icon_h) // 2)

    def _draw_slider(self, value):
        sx, sy, sw, sh = self.SLIDER
        self.display.set_pen(self.colors._BLACK)
        self.display.rectangle(sx, sy + sh // 2 - 4, sw, 8)
        self.display.set_pen(self.colors.WHITE)
        self.display.rectangle(sx + 2, sy + sh // 2 - 2, sw - 4, 4)

        normalized = (value - 0.1) / 0.9
        thumb_x = sx + int(normalized * (sw - 1))
        self.display.set_pen(self.colors.WHITE)
        self.display.circle(thumb_x, sy + sh // 2, 12)

    def redraw_slider(self, state):
        sx, sy, sw, sh = self.SLIDER
        x = max(0, sx - 14)
        y = sy
        width = min(self.app.width - x, sw + 28)

        self.display.set_layer(1)
        self.display.set_pen(self.colors.GRAY)
        self.display.rectangle(x, y, width, sh)
        self._draw_slider(state.backlight)
        self.app.presto.update()
        self._slider_dirty = False

    def consume_slider_dirty(self):
        if not self._slider_dirty:
            return False
        self._slider_dirty = False
        return True

    def handle_touch(self, touch, state):
        if not touch.state:
            return False

        if state.show_device_picker:
            return self._handle_device_picker_touch(touch, state)

        if self._in_bounds(touch.x, touch.y, self.CLOSE):
            state.show_settings = False
            return True

        if self._in_bounds(touch.x, touch.y, self.DEVICE_BUTTON):
            state.show_device_picker = True
            self.app.refresh_devices()
            return True

        if self._in_bounds(touch.x, touch.y, self.LED_TOGGLE):
            self.app.set_ambient_leds(not state.toggle_leds)
            return True

        if self._in_bounds(touch.x, touch.y, self.SHUFFLE_TOGGLE):
            state.shuffle = not state.shuffle
            self.app.spotify_client.toggle_shuffle(state.shuffle)
            return True

        if self._in_bounds(touch.x, touch.y, self.REPEAT_TOGGLE):
            state.repeat = not state.repeat
            self.app.spotify_client.toggle_repeat(state.repeat)
            return True

        if self._in_bounds(touch.x, touch.y, self.SLIDER):
            self._slider_dragging = True
            self._set_backlight_from_x(touch.x, state)
            return True

        return False

    def handle_drag(self, touch, state):
        if state.show_device_picker:
            return False
        if self._slider_dragging and touch.state:
            self._set_backlight_from_x(touch.x, state)
            return self._slider_dirty
        return False

    def handle_release(self):
        if self._slider_dragging:
            self.app.save_runtime_settings()
        self._slider_dragging = False

    def _handle_device_picker_touch(self, touch, state):
        px, py, pw, ph = self.DEVICE_PICKER
        if self._in_bounds(touch.x, touch.y, self.CLOSE):
            state.show_device_picker = False
            return True

        if not self._in_bounds(touch.x, touch.y, self.DEVICE_PICKER):
            state.show_device_picker = False
            return True

        if state.devices_loading or not state.devices:
            return True

        for i, device in enumerate(state.devices[:self._max_picker_rows()]):
            if self._in_bounds(touch.x, touch.y, self._picker_row_bounds(i)):
                self.app.select_device(device.get("id"), device.get("name", "Device"))
                state.show_device_picker = False
                return True

        return True

    def _set_backlight_from_x(self, x, state):
        sx, _, sw, _ = self.SLIDER
        value = max(0.0, min(1.0, (x - sx) / sw))
        value = 0.1 + 0.9 * value
        if abs(value - state.backlight) > 0.01:
            self.app.set_backlight_setting(value)
            self._slider_dirty = True

    @staticmethod
    def _in_bounds(x, y, bounds):
        bx, by, bw, bh = bounds
        return bx <= x <= bx + bw and by <= y <= by + bh

class Spotify(BaseApp):
    """Main Spotify app managing playback controls, track display, and UI interactions."""
    PLAYBACK_POLL_INTERVAL_SECONDS = 10
    ASLEEP_POLL_INTERVAL_SECONDS = 60
    WAITING_SLEEP_SECONDS = 300
    CONTROLS_TIMEOUT_SECONDS = 30
    MAX_KNOWN_DEVICES = 10

    def __init__(self):
        super().__init__(ambient_light=True, full_res=True, layers=2)

        self.display.set_layer(0)
        icon = pngdec.PNG(self.display)
        icon.open_file("applications/spotify/icon.png")
        icon.decode(self.center_x - icon.get_width()//2, self.center_y - icon.get_height()//2 - 20)
        self.presto.update()

        self.display.set_font("sans")
        self.display.set_layer(1)
        self.display_text("Connecting to WIFI", (90, self.height - 80), thickness=2)
        self.presto.update()

        self.presto.connect()
        while not self.presto.wifi.isconnected():
            self.clear(1)
            self.display_text("Failed to connect to WIFI", (40, self.height - 80), thickness=2)
            time.sleep(2)

        self.clear(1)
        self.display_text("Instantiating Spotify Client", (35, self.height - 80), thickness=2)
        self.spotify_client = self.get_spotify_client()
        self.clear(1)
        self.presto.update()

        # JPEG decoder
        self.j = jpegdec.JPEG(self.display)

        self.waiting_icon = pngdec.PNG(self.display)
        self.waiting_icon.open_file("applications/spotify/icon_small.png")

        self.state = State()
        runtime_settings = self.load_runtime_settings()
        if "toggle_leds" in runtime_settings:
            self.state.toggle_leds = runtime_settings["toggle_leds"]
        if "backlight" in runtime_settings:
            self.state.backlight = max(0.1, min(1.0, runtime_settings["backlight"]))
        saved_device_id = self._load_saved_device_id()
        saved_device_name = self._load_saved_device_name()
        self.has_saved_device = bool(saved_device_id and saved_device_name)
        self.set_device_name(saved_device_name or self.state.device_name)
        self.known_devices = self._load_known_devices()
        if self.has_saved_device:
            self._remember_device({
                "id": saved_device_id,
                "name": saved_device_name,
            })
            self.save_runtime_settings()
        self.waiting_since = None
        self.controls_visible_since = None
        self.set_backlight(self.state.backlight)
        self.toggle_leds(self.state.toggle_leds)
        self.settings = SettingsPanel(self)
        self.setup_buttons()

    def display_text(self, text, position, color=65535, scale=1, thickness=None):
        if thickness:
            self.display.set_thickness(2)
        x,y = position
        self.display.set_pen(color)
        self.display.text(text, x, y, scale=scale)
        self.presto.update()

    def get_spotify_client(self):
        if not hasattr(secrets, 'SPOTIFY_CREDENTIALS') or not secrets.SPOTIFY_CREDENTIALS:
            while True:
                self.clear(1)
                self.display.set_pen(self.colors.WHITE)
                self.display.text("Spotify credentials not found", 40, self.height - 80, scale=.9)
                self.presto.update()
                time.sleep(2)

        session = Session(secrets.SPOTIFY_CREDENTIALS)
        client = SpotifyWebApiClient(session)
        saved_device_id = self._load_saved_device_id()
        if saved_device_id:
            client.session.device_id = saved_device_id
            secrets.SPOTIFY_CREDENTIALS['device_id'] = saved_device_id
        return client

    def _load_saved_device_id(self):
        settings = self.load_runtime_settings()
        return settings.get("device_id")

    def _save_device_id(self, device_id):
        self.save_runtime_settings()

    def _load_saved_device_name(self):
        settings = self.load_runtime_settings()
        return settings.get("device_name")

    def _save_device_name(self, device_name):
        self.save_runtime_settings()

    def _make_device_label(self, name, available=True, max_len=22):
        name = ''.join(i if ord(i) < 128 else ' ' for i in (name or "Device"))
        if not available:
            name = name + " [Offline]"
        if len(name) > max_len:
            return name[:max_len] + "..."
        return name

    def set_device_name(self, device_name):
        self.state.device_name = device_name or "Device"
        self.state.device_label = self._make_device_label(self.state.device_name, True, 20)

    def load_runtime_settings(self):
        try:
            with open("spotify_settings.json", "r") as f:
                settings = json.loads(f.read())
                return settings if isinstance(settings, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_runtime_settings(self):
        settings = {
            "toggle_leds": self.state.toggle_leds,
            "backlight": round(self.state.backlight, 2),
        }
        if hasattr(self, "spotify_client") and self.spotify_client.session.device_id:
            settings["device_id"] = self.spotify_client.session.device_id
        if getattr(self, "has_saved_device", False) and self.state.device_name:
            settings["device_name"] = self.state.device_name

        try:
            with open("spotify_settings.json", "w") as f:
                f.write(json.dumps(settings))
        except OSError as e:
            debug_print("Failed to save runtime settings:", e)

    def set_ambient_leds(self, value):
        self.state.toggle_leds = value
        self.toggle_leds(value)
        self.save_runtime_settings()

    def set_backlight_setting(self, value):
        self.state.backlight = max(0.1, min(1.0, value))
        self.set_backlight(self.state.backlight)

    def _load_known_devices(self):
        try:
            with open("spotify_devices.json", "r") as f:
                devices = json.loads(f.read())
                return devices if isinstance(devices, list) else []
        except (OSError, ValueError):
            return []

    def _save_known_devices(self):
        try:
            with open("spotify_devices.json", "w") as f:
                self._trim_known_devices()
                f.write(json.dumps(self.known_devices))
        except OSError as e:
            debug_print("Failed to save known devices:", e)

    def _trim_known_devices(self):
        device_id = self.spotify_client.session.device_id
        selected = []
        others = []
        seen = {}

        for device in self.known_devices:
            known_id = device.get("id")
            if not known_id or known_id in seen:
                continue
            seen[known_id] = True
            if known_id == device_id:
                selected.append(device)
            else:
                others.append(device)

        self.known_devices = (selected + others)[:self.MAX_KNOWN_DEVICES]

    def _remember_device(self, device, promote=False):
        device_id = device.get("id")
        if not device_id:
            return

        saved = {
            "id": device_id,
            "name": device.get("name", "Device"),
        }
        for i, known in enumerate(self.known_devices):
            if known.get("id") == device_id:
                del self.known_devices[i]
                if promote:
                    self.known_devices.insert(0, saved)
                else:
                    self.known_devices.insert(i, saved)
                self._trim_known_devices()
                return
        if promote:
            self.known_devices.insert(0, saved)
        else:
            self.known_devices.append(saved)
        self._trim_known_devices()

    def _merge_devices(self, live_devices):
        live_ids = {}
        merged = []

        for device in live_devices:
            device_id = device.get("id")
            if not device_id:
                continue
            live_ids[device_id] = True
            self._remember_device(device)
            name = device.get("name", "Device")
            merged.append({
                "id": device_id,
                "name": name,
                "available": True,
                "display_name": self._make_device_label(name, True),
            })

        for device in self.known_devices:
            device_id = device.get("id")
            if device_id and device_id not in live_ids:
                name = device.get("name", "Device")
                merged.append({
                    "id": device_id,
                    "name": name,
                    "available": False,
                    "display_name": self._make_device_label(name, False),
                })

        self._save_known_devices()
        return self._prioritize_devices(merged)

    def _prioritize_devices(self, devices):
        selected_id = self.spotify_client.session.device_id
        selected = []
        online = []
        offline = []

        for device in devices:
            if device.get("id") == selected_id:
                selected.append(device)
            elif device.get("available", True):
                online.append(device)
            else:
                offline.append(device)

        return selected + online + offline

    def refresh_devices(self):
        self.state.devices_loading = True
        self.state.devices = []
        try:
            resp = self.spotify_client.devices()
            if resp:
                self.state.devices = self._merge_devices(resp.get("devices", []))
            else:
                self.state.devices = self._merge_devices([])
        except Exception as e:
            debug_print("Failed to fetch devices:", e)
            self.state.devices = self._merge_devices([])
        self.state.devices_loading = False
        self._sync_device_name()

    def _sync_device_name(self):
        device_id = self.spotify_client.session.device_id
        for device in self.state.devices:
            if device.get("id") == device_id:
                device_name = device.get("name", "Device")
                if device_name != self.state.device_name:
                    self.set_device_name(device_name)
                    self._save_device_name(self.state.device_name)
                return
        if device_id and not self.state.device_name:
            self.set_device_name("Device")

    def select_device(self, device_id, device_name):
        if not device_id:
            return
        if device_id == self.spotify_client.session.device_id:
            self.set_device_name(device_name or self.state.device_name)
            self.has_saved_device = True
            self._save_device_name(self.state.device_name)
            self._remember_device({
                "id": device_id,
                "name": self.state.device_name,
            }, promote=True)
            self._save_known_devices()
            return

        try:
            self.spotify_client.transfer_playback(device_id, play=False)
        except Exception as e:
            debug_print("Failed to transfer playback:", e)
        self.spotify_client.session.device_id = device_id
        secrets.SPOTIFY_CREDENTIALS['device_id'] = device_id
        self.set_device_name(device_name or "Device")
        self.has_saved_device = True
        self._remember_device({
            "id": device_id,
            "name": self.state.device_name,
        }, promote=True)
        self._save_device_id(device_id)
        self._save_device_name(self.state.device_name)
        self._save_known_devices()
        self.state.latest_fetch = None

    def setup_buttons(self):
        """Initializes control buttons and their behavior."""
        # --- Shared update functions ---
        def update_show_controls(state, button):
            button.enabled = state.show_controls and not state.show_settings

        def update_playback_controls(state, button):
            button.enabled = state.show_controls and not state.show_settings and not state.waiting

        def update_always_enabled(state, button):
            button.enabled = not state.show_settings

        def update_play_pause(state, button):
            button.enabled = state.show_controls and not state.show_settings and not state.waiting
            button.icon = "pause.png" if state.is_playing else "play.png"

        def update_settings(state, button):
            button.enabled = state.show_controls and not state.show_settings
            button.icon = "settings.png"

        # --- On-press handlers ---
        def exit_app(self):
            self.state.exit = True

        def toggle_controls(self):
            self.state.show_controls = not self.state.show_controls
            self.controls_visible_since = time.time() if self.state.show_controls else None

        def play_pause(self):
            if self.state.is_playing:
                self.spotify_client.pause()
            else:
                self.spotify_client.play()
            self.state.is_playing = not self.state.is_playing

        def next_track(self):
            self.spotify_client.next()
            self.state.latest_fetch = None

        def previous_track(self):
            self.spotify_client.previous()
            self.state.latest_fetch = None

        def open_settings(self):
            self.state.show_settings = True
            self.draw_overlay()

        # --- Button configurations ---
        buttons_config = [
            ("Exit", ["exit.png"], (0, 0, 80, 80), exit_app, update_show_controls),
            ("Next", ["next.png"], (self.center_x + 120, self.height - 100, 80, 100), next_track, update_playback_controls),
            ("Previous", ["previous.png"], (self.center_x - 200, self.height - 100, 80, 100), previous_track, update_playback_controls),
            ("Play", ["play.png", "pause.png"], (self.center_x - 40, self.height - 100, 80, 100), play_pause, update_play_pause),
            ("Settings", ["settings.png"], (self.width - 100, 0, 100, 80), open_settings, update_settings),
            ("Toggle Controls", None, (0, 0, self.width, self.height), toggle_controls, update_always_enabled),
        ]

        # --- Create ControlButton instances ---
        self.buttons = [
            ControlButton(self.display, name, icons, bounds, on_press, update)
            for name, icons, bounds, on_press, update in buttons_config
        ]

    def run(self):
        """Starts the app's event loops."""
        loop = asyncio.get_event_loop()
        loop.create_task(self.touch_handler_loop())
        loop.create_task(self.display_loop())
        loop.run_forever()

    async def touch_handler_loop(self):
        """Handles touch input events and button presses."""
        while not self.state.exit:
            self.touch.poll()

            if self.state.screen_asleep and self.touch.state:
                self._wake_screen()
                while self.touch.state:
                    self.touch.poll()
                await asyncio.sleep_ms(1)
                continue

            if self.state.show_settings:
                if self.settings.handle_touch(self.touch, self.state):
                    if self.settings.consume_slider_dirty():
                        self.settings.redraw_slider(self.state)
                    else:
                        self.draw_overlay()
                while self.touch.state:
                    self.touch.poll()
                    if self.settings.handle_drag(self.touch, self.state):
                        self.settings.redraw_slider(self.state)
                self.settings.handle_release()
                await asyncio.sleep_ms(1)
                continue

            for button in self.buttons:
                button.update(self.state, button)
                if button.is_pressed(self.state):
                    debug_print(f"{button.name} pressed")
                    try:
                        button.on_press(self)
                        if self.state.show_controls:
                            self.controls_visible_since = time.time()
                    except Exception as e:
                        debug_print(f"Failed to execute on_press: {e}")
                    break

            # Wait here until the user stops touching the screen
            while self.touch.state:
                self.touch.poll()

            await asyncio.sleep_ms(1)

    def display_centered_text(self, text, y, scale=1):
        """Draws text centered horizontally on the display."""
        try:
            text_width = self.display.measure_text(text, scale)
        except Exception:
            text_width = len(text) * 12 * scale
        self.display.text(text, int((self.width - text_width) // 2), y, scale=scale)

    def show_waiting(self):
        """Displays a black screen with the Spotify icon and waiting message."""
        self.display.set_layer(0)
        self.clear(0)

        icon_w, icon_h = self.waiting_icon.get_width(), self.waiting_icon.get_height()
        icon_x = self.center_x - icon_w // 2
        icon_y = self.center_y - icon_h // 2 - 20
        self.waiting_icon.decode(icon_x, icon_y)

        self.display.set_font("sans")
        self.display.set_thickness(2)
        self.display.set_pen(self.colors.WHITE)
        if not self.has_saved_device:
            self.display_centered_text("Select a device", icon_y + icon_h + 34, scale=0.8)
            self.display_centered_text("to control...", icon_y + icon_h + 60, scale=0.8)
            return

        device_name = ''.join(i if ord(i) < 128 else ' ' for i in self.state.device_name)
        if len(device_name) > 22:
            device_name = device_name[:22] + "..."
        self.display_centered_text("waiting for", icon_y + icon_h + 34, scale=0.85)
        self.display_centered_text(device_name, icon_y + icon_h + 62, scale=0.75)

    def _wake_screen(self):
        """Turns the screen back on after sleep and redraws the current view."""
        self.state.screen_asleep = False
        self.state.latest_fetch = None
        self.turn_screen_on(self.state.backlight)
        if self.state.waiting:
            self.waiting_since = time.time()
            self.show_waiting()
        self.draw_overlay()

    def _manage_waiting_screen(self):
        """Turn the screen off after prolonged waiting; restore it when playback returns."""
        if self.state.waiting:
            if self.waiting_since is None:
                self.waiting_since = time.time()
            elif (not self.state.screen_asleep
                  and time.time() - self.waiting_since >= self.WAITING_SLEEP_SECONDS):
                self.state.screen_asleep = True
                self.turn_screen_off()
        else:
            if self.state.screen_asleep:
                self.turn_screen_on(self.state.backlight)
                self.state.screen_asleep = False
            self.waiting_since = None

    def _manage_controls_timeout(self):
        """Hide controls after inactivity, pausing while modal windows are open."""
        if not self.state.show_controls:
            self.controls_visible_since = None
            return

        if self.state.show_settings or self.state.show_device_picker:
            self.controls_visible_since = time.time()
            return

        if self.controls_visible_since is None:
            self.controls_visible_since = time.time()
            return

        if time.time() - self.controls_visible_since >= self.CONTROLS_TIMEOUT_SECONDS:
            self.state.show_controls = False
            self.controls_visible_since = None

    def show_image(self, img, minimized=False):
        """Displays an album cover image on the screen."""
        if not img:
            debug_print("No image data to display.")
            return

        try:
            self.j.open_RAM(memoryview(img))

            img_width, img_height = self.j.get_width(), self.j.get_height()
            img_x, img_y = (self.width - img_width) // 2, (self.height - img_height) // 2

            self.clear(0)
            self.j.decode(img_x, img_y, jpegdec.JPEG_SCALE_FULL, dither=True)

        except Exception as e:
            debug_print("Failed to load image:", e)

    def write_track(self):
        """Writes the track name and artists on the screen."""
        if self.state.show_controls and self.state.track:
            self.display.set_thickness(3)

            track_name = self.state.track.get("name")
            # strip non-ascii characters
            track_name = ''.join(i if ord(i) < 128 else ' ' for i in track_name)
            if len(track_name) > 20:
                track_name = track_name[:20] + " ..."
            # shadow effect
            self.display.set_pen(self.colors._BLACK)
            self.display.text(track_name, 20, self.height - 137, scale=1.1)

            self.display.set_pen(self.colors.WHITE)
            self.display.text(track_name, 18, self.height - 140, scale=1.1)

            artists = ", ".join([artist.get("name") for artist in self.state.track.get("artists")])
            # strip non-ascii characters
            artists = ''.join(i if ord(i) < 128 else ' ' for i in artists)
            if len(artists) > 35:
                artists = artists[:35] + " ..."
            self.display.set_thickness(2)
            # shadow effect
            self.display.set_pen(self.colors._BLACK)
            self.display.text(artists, 20, self.height - 108, scale=0.7)

            self.display.set_pen(self.colors.WHITE)
            self.display.text(artists, 18, self.height - 111, scale=0.7)

    def draw_overlay(self):
        """Draws the control overlay or settings panel on layer 1."""
        self.display.set_layer(1)
        self.clear(1)

        if self.state.show_settings:
            self.settings.draw(self.state)
        elif self.state.show_controls:
            for button in self.buttons:
                button.update(self.state, button)
                button.draw(self.state)
            self.write_track()

        self.presto.update()

    async def display_loop(self):
        """Periodically updates the display with the latest track info and controls."""
        prev_state = None

        while not self.state.exit:
            poll_interval = (
                self.ASLEEP_POLL_INTERVAL_SECONDS
                if self.state.screen_asleep
                else self.PLAYBACK_POLL_INTERVAL_SECONDS
            )
            if not self.state.latest_fetch or time.time() - self.state.latest_fetch > poll_interval:
                self.state.latest_fetch = time.time()
                try:
                    result = fetch_state(self.spotify_client)
                    if result:
                        device_id, self.state.track, self.state.is_playing, self.state.shuffle, self.state.repeat = result
                        self.state.waiting = False
                        if device_id:
                            self.spotify_client.session.device_id = device_id
                    else:
                        self.state.waiting = True
                        self.state.track = None
                        self.state.is_playing = False
                except Exception as e:
                    debug_print("Failed to fetch playback state:", e)

            self._manage_waiting_screen()
            self._manage_controls_timeout()

            await asyncio.sleep(0)

            current_state = self.state.snapshot()
            layer0_changed = (
                not prev_state
                or prev_state[State.WAITING_INDEX] != current_state[State.WAITING_INDEX]
                or prev_state[State.TRACK_ID_INDEX] != current_state[State.TRACK_ID_INDEX]
            )
            if layer0_changed:
                if self.state.waiting:
                    self.show_waiting()
                elif self.state.track:
                    img = get_album_cover(self.state.track)
                    self.show_image(img)

            await asyncio.sleep(0)

            # update display if state changes
            if prev_state != current_state:
                self.draw_overlay()
                prev_state = current_state
            gc.collect()
            await asyncio.sleep_ms(200)

def fetch_state(spotify_client):
    """Fetches the current playback state from Spotify."""

    current_track = None
    is_playing = False
    shuffle = False
    repeat = False
    device_id = None
    resp = spotify_client.current_playing()
    if resp and resp.get("item"):
        current_track = resp["item"]
        is_playing = resp.get("is_playing")
        shuffle = resp.get("shuffle_state")
        repeat = resp.get("repeat_state", "off") != "off"
        device_id = resp["device"]["id"]
        debug_print("Got current playing track: " + current_track.get("name"))
        return device_id, current_track, is_playing, shuffle, repeat

    return None

def get_album_cover(track):
    """Fetches and resizes the album cover image for the given track."""
    images = (track.get("album") or {}).get("images") or []
    if not images:
        debug_print("Track has no album images.")
        return None

    image = images[1] if len(images) > 1 else images[0]
    img_url = image.get("url")
    if not img_url:
        debug_print("Album image has no URL.")
        return None

    img = None
    resize_url = f"https://wsrv.nl/?url={img_url}&w=480&h=480"
    response = None
    try:
        response = requests.get(resize_url)
        if response.status_code == 200:
            img = response.content
        else:
            debug_print("Failed to fetch image:", response.status_code)
    except Exception as e:
        debug_print("Fetch image error:", e)
    finally:
        if response:
            response.close()

    return img

def launch():
    """Launches the Spotify app and starts the event loop."""
    app = Spotify()
    app.run()

    app.clear()
    del app
    gc.collect()
