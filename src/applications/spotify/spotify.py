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

class State:
    """Tracks the current state of the Spotify app including playback and UI controls."""
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
        self.devices = []
        self.exit = False

        self.latest_fetch = None
    
    def copy(self):
        state = State()
        state.toggle_leds = self.toggle_leds
        state.backlight = round(self.backlight, 2)
        state.is_playing = self.is_playing
        state.repeat = self.repeat
        state.shuffle = self.shuffle
        state.show_controls = self.show_controls
        state.show_settings = self.show_settings
        state.show_device_picker = self.show_device_picker
        state.device_name = self.device_name
        state.waiting = self.waiting
        state.screen_asleep = self.screen_asleep
        state.exit = self.exit
        state.track = {'id': self.track['id']} if self.track else None # only care about track id
        return state
    
    def __eq__(self, other):
        if not isinstance(other, State) or other is None:
            return False
        return (
            self.toggle_leds == other.toggle_leds and
            round(self.backlight, 2) == round(other.backlight, 2) and
            self.is_playing == other.is_playing and
            self.repeat == other.repeat and
            self.shuffle == other.shuffle and
            self.show_controls == other.show_controls and
            self.show_settings == other.show_settings and
            self.show_device_picker == other.show_device_picker and
            self.device_name == other.device_name and
            self.waiting == other.waiting and
            self.screen_asleep == other.screen_asleep and
            self.exit == other.exit and
            (self.track or {}).get('id') == (other.track or {}).get('id')
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
    PANEL = (60, 60, 360, 370)
    SLIDER = (80, 370, 320, 40)
    DEVICE_BUTTON = (80, 95, 260, 36)
    DEVICE_PICKER = (50, 90, 380, 300)
    PICKER_ROW_HEIGHT = 44
    LED_TOGGLE = (300, 160, 60, 60)
    SHUFFLE_TOGGLE = (300, 220, 60, 60)
    REPEAT_TOGGLE = (300, 280, 60, 60)
    CLOSE = (370, 75, 40, 40)

    def __init__(self, app):
        self.app = app
        self.display = app.display
        self.colors = app.colors

        self.led_icons = self._load_icons(("light_on.png", "light_off.png"))
        self.shuffle_icons = self._load_icons(("shuffle_on.png", "shuffle_off.png"))
        self.repeat_icons = self._load_icons(("repeat_on.png", "repeat_off.png"))

        self.close_icon = pngdec.PNG(self.display)
        self.close_icon.open_file("applications/spotify/icons/exit.png")
        self._slider_dragging = False

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

        self.display.set_pen(self.colors.WHITE)
        self.display.set_thickness(2)
        self.display.text("Settings", px + 20, py + 20, scale=1.2)

        self.display.text("Device", px + 20, py + 55, scale=0.9)
        self._draw_device_button(state)

        self.display.text("Ambient LEDs", px + 20, py + 115, scale=0.9)
        self._draw_toggle_icon(
            self.led_icons["light_on.png" if state.toggle_leds else "light_off.png"],
            self.LED_TOGGLE,
        )

        self.display.text("Shuffle", px + 20, py + 175, scale=0.9)
        self._draw_toggle_icon(
            self.shuffle_icons["shuffle_on.png" if state.shuffle else "shuffle_off.png"],
            self.SHUFFLE_TOGGLE,
        )

        self.display.text("Repeat", px + 20, py + 235, scale=0.9)
        self._draw_toggle_icon(
            self.repeat_icons["repeat_on.png" if state.repeat else "repeat_off.png"],
            self.REPEAT_TOGGLE,
        )

        self.display.text("Backlight", px + 20, py + 295, scale=0.9)
        self._draw_slider(state.backlight)

        cx, cy, cw, ch = self.CLOSE
        close_w, close_h = self.close_icon.get_width(), self.close_icon.get_height()
        self.close_icon.decode(cx + (cw - close_w) // 2, cy + (ch - close_h) // 2)

        if state.show_device_picker:
            self._draw_device_picker(state)

    def _truncate(self, text, max_len=24):
        text = ''.join(i if ord(i) < 128 else ' ' for i in (text or ""))
        if len(text) > max_len:
            return text[:max_len] + "..."
        return text

    def _draw_device_button(self, state):
        bx, by, bw, bh = self.DEVICE_BUTTON
        self.display.set_pen(self.colors._BLACK)
        self.display.rectangle(bx, by, bw, bh)
        self.display.set_pen(self.colors.WHITE)
        self.display.rectangle(bx + 2, by + 2, bw - 4, bh - 4)
        self.display.set_pen(self.colors._BLACK)
        label = self._truncate(state.device_name, 20)
        self.display.text(label, bx + 10, by + 10, scale=0.8)

    def _draw_device_picker(self, state):
        px, py, pw, ph = self.DEVICE_PICKER
        self.display.set_pen(self.colors._BLACK)
        self.display.rectangle(0, 0, self.app.width, self.app.height)
        self.display.set_pen(self.colors.GRAY)
        self.display.rectangle(px, py, pw, ph)
        self.display.set_pen(self.colors.WHITE)
        self.display.text("Select Device", px + 20, py + 15, scale=1.0)

        if state.devices_loading:
            self.display.text("Loading...", px + 20, py + 60, scale=0.9)
            return

        if not state.devices:
            self.display.text("No devices found", px + 20, py + 60, scale=0.9)
            self.display.text("Open Spotify on a device", px + 20, py + 85, scale=0.7)
            return

        current_id = self.app.spotify_client.session.device_id
        max_rows = min(len(state.devices), 5)
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

            name = device.get("name", "Unknown")
            if not device.get("available", True):
                name = name + " [Offline]"
            name = self._truncate(name, 22)
            device_type = self._truncate(device.get("type", ""), 12)
            self.display.text(name, rx + 8, ry + 6, scale=0.8)
            self.display.text(device_type, rx + 8, ry + 24, scale=0.6)

    def _picker_row_bounds(self, index):
        px, py, pw, _ = self.DEVICE_PICKER
        row_y = py + 50 + index * self.PICKER_ROW_HEIGHT
        return (px + 10, row_y, pw - 20, self.PICKER_ROW_HEIGHT - 4)

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
            return True
        return False

    def handle_release(self):
        if self._slider_dragging:
            self.app.save_runtime_settings()
        self._slider_dragging = False

    def _handle_device_picker_touch(self, touch, state):
        px, py, pw, ph = self.DEVICE_PICKER
        if not self._in_bounds(touch.x, touch.y, self.DEVICE_PICKER):
            state.show_device_picker = False
            return True

        if state.devices_loading or not state.devices:
            return True

        for i, device in enumerate(state.devices[:5]):
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

    @staticmethod
    def _in_bounds(x, y, bounds):
        bx, by, bw, bh = bounds
        return bx <= x <= bx + bw and by <= y <= by + bh

class Spotify(BaseApp):
    """Main Spotify app managing playback controls, track display, and UI interactions."""
    WAITING_SLEEP_SECONDS = 300

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
        self.state.device_name = saved_device_name or self.state.device_name
        self.known_devices = self._load_known_devices()
        if self.has_saved_device:
            self._remember_device({
                "id": saved_device_id,
                "name": saved_device_name,
                "type": "",
            })
        self.waiting_since = None
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
        try:
            with open("device_id.txt", "r") as f:
                return f.read().strip()
        except OSError:
            return None

    def _save_device_id(self, device_id):
        try:
            with open("device_id.txt", "w") as f:
                f.write(device_id)
        except OSError as e:
            print("Failed to save device id:", e)

    def _load_saved_device_name(self):
        try:
            with open("device_name.txt", "r") as f:
                return f.read().strip()
        except OSError:
            return None

    def _save_device_name(self, device_name):
        try:
            with open("device_name.txt", "w") as f:
                f.write(device_name)
        except OSError as e:
            print("Failed to save device name:", e)

    def load_runtime_settings(self):
        try:
            with open("runtime_settings.json", "r") as f:
                settings = json.loads(f.read())
                return settings if isinstance(settings, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_runtime_settings(self):
        settings = {
            "toggle_leds": self.state.toggle_leds,
            "backlight": round(self.state.backlight, 2),
        }
        try:
            with open("runtime_settings.json", "w") as f:
                f.write(json.dumps(settings))
        except OSError as e:
            print("Failed to save runtime settings:", e)

    def set_ambient_leds(self, value):
        self.state.toggle_leds = value
        self.toggle_leds(value)
        self.save_runtime_settings()

    def set_backlight_setting(self, value):
        self.state.backlight = max(0.1, min(1.0, value))
        self.set_backlight(self.state.backlight)

    def _load_known_devices(self):
        try:
            with open("known_devices.json", "r") as f:
                devices = json.loads(f.read())
                return devices if isinstance(devices, list) else []
        except (OSError, ValueError):
            return []

    def _save_known_devices(self):
        try:
            with open("known_devices.json", "w") as f:
                f.write(json.dumps(self.known_devices))
        except OSError as e:
            print("Failed to save known devices:", e)

    def _remember_device(self, device):
        device_id = device.get("id")
        if not device_id:
            return

        saved = {
            "id": device_id,
            "name": device.get("name", "Device"),
            "type": device.get("type", ""),
        }
        for i, known in enumerate(self.known_devices):
            if known.get("id") == device_id:
                self.known_devices[i] = saved
                return
        self.known_devices.append(saved)

    def _merge_devices(self, live_devices):
        live_ids = {}
        merged = []

        for device in live_devices:
            device_id = device.get("id")
            if not device_id:
                continue
            live_ids[device_id] = True
            self._remember_device(device)
            merged.append({
                "id": device_id,
                "name": device.get("name", "Device"),
                "type": device.get("type", ""),
                "available": True,
            })

        for device in self.known_devices:
            device_id = device.get("id")
            if device_id and device_id not in live_ids:
                merged.append({
                    "id": device_id,
                    "name": device.get("name", "Device"),
                    "type": device.get("type", ""),
                    "available": False,
                })

        self._save_known_devices()
        return merged

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
            print("Failed to fetch devices:", e)
            self.state.devices = self._merge_devices([])
        self.state.devices_loading = False
        self._sync_device_name()

    def _sync_device_name(self):
        device_id = self.spotify_client.session.device_id
        for device in self.state.devices:
            if device.get("id") == device_id:
                self.state.device_name = device.get("name", "Device")
                self._save_device_name(self.state.device_name)
                return
        if device_id and not self.state.device_name:
            self.state.device_name = "Device"

    def select_device(self, device_id, device_name):
        if not device_id:
            return
        try:
            self.spotify_client.transfer_playback(device_id, play=False)
        except Exception as e:
            print("Failed to transfer playback:", e)
        self.spotify_client.session.device_id = device_id
        secrets.SPOTIFY_CREDENTIALS['device_id'] = device_id
        self.state.device_name = device_name or "Device"
        self.has_saved_device = True
        self._save_device_id(device_id)
        self._save_device_name(self.state.device_name)
        self.state.latest_fetch = None
        
    def setup_buttons(self):
        """Initializes control buttons and their behavior."""
        # --- Shared update functions ---
        def update_show_controls(state, button):
            button.enabled = state.show_controls and not state.show_settings

        def update_always_enabled(state, button):
            button.enabled = not state.show_settings

        def update_play_pause(state, button):
            button.enabled = state.show_controls and not state.show_settings
            button.icon = "pause.png" if state.is_playing else "play.png"

        def update_settings(state, button):
            button.enabled = state.show_controls and not state.show_settings
            button.icon = "settings.png"

        # --- On-press handlers ---
        def exit_app(self):
            self.state.exit = True

        def toggle_controls(self):
            self.state.show_controls = not self.state.show_controls

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
            ("Next", ["next.png"], (self.center_x + 120, self.height - 100, 80, 100), next_track, update_show_controls),
            ("Previous", ["previous.png"], (self.center_x - 200, self.height - 100, 80, 100), previous_track, update_show_controls),
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
                    self.draw_overlay()
                while self.touch.state:
                    self.touch.poll()
                    if self.settings.handle_drag(self.touch, self.state):
                        self.draw_overlay()
                self.settings.handle_release()
                await asyncio.sleep_ms(1)
                continue

            for button in self.buttons:
                button.update(self.state, button)
                if button.is_pressed(self.state):
                    print(f"{button.name} pressed")
                    try:
                        button.on_press(self)
                    except Exception as e:
                        print(f"Failed to execute on_press: {e}")
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

    def show_image(self, img, minimized=False):
        """Displays an album cover image on the screen."""
        if not img:
            print("No image data to display.")
            return

        try:
            self.j.open_RAM(memoryview(img))

            img_width, img_height = self.j.get_width(), self.j.get_height()
            img_x, img_y = (self.width - img_width) // 2, (self.height - img_height) // 2

            self.clear(0)
            self.j.decode(img_x, img_y, jpegdec.JPEG_SCALE_FULL, dither=True)

        except Exception as e:
            print("Failed to load image:", e)
        
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
                button.draw(self.state)
            self.write_track()

        self.presto.update()

    async def display_loop(self):
        """Periodically updates the display with the latest track info and controls."""
        INTERVAL = 10
        prev_state = None

        while not self.state.exit:
            if not self.state.latest_fetch or time.time() - self.state.latest_fetch > INTERVAL:
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
                    print("Failed to fetch playback state:", e)

            self._manage_waiting_screen()

            await asyncio.sleep(0)

            layer0_changed = (
                not prev_state
                or prev_state.waiting != self.state.waiting
                or (prev_state.track or {}).get('id') != (self.state.track or {}).get("id")
            )
            if layer0_changed:
                if self.state.waiting:
                    self.show_waiting()
                elif self.state.track:
                    img = get_album_cover(self.state.track)
                    self.show_image(img)

            await asyncio.sleep(0)

            # update display if state changes
            if prev_state != self.state:
                self.draw_overlay()
                prev_state = self.state.copy()
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
        print("Got current playing track: " + current_track.get("name"))
        return device_id, current_track, is_playing, shuffle, repeat

    return None

def get_album_cover(track):
    """Fetches and resizes the album cover image for the given track."""
    images = (track.get("album") or {}).get("images") or []
    if not images:
        print("Track has no album images.")
        return None

    image = images[1] if len(images) > 1 else images[0]
    img_url = image.get("url")
    if not img_url:
        print("Album image has no URL.")
        return None
    
    img = None
    resize_url = f"https://wsrv.nl/?url={img_url}&w=480&h=480"
    response = None
    try:
        response = requests.get(resize_url)
        if response.status_code == 200:
            img = response.content
        else:
            print("Failed to fetch image:", response.status_code)
    except Exception as e:
        print("Fetch image error:", e)
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