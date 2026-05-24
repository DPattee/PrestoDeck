from presto import Presto

class Colors:
    def __init__(self, display):
        self.WHITE = display.create_pen(255, 250, 240)
        self.GRAY = display.create_pen(128, 128, 128)
        self._BLACK = display.create_pen(10, 10, 10)
        self.BLACK = display.create_pen(0, 0, 0)

class BaseApp:
    def __init__(self, **presto_kwargs):
        self.presto = Presto(**presto_kwargs)
        self.display = self.presto.display
        self.touch = self.presto.touch

        self.layers = presto_kwargs.get("layers", 1)

        self.width, self.height = self.display.get_bounds()
        self.center_x, self.center_y = self.center = (self.width // 2, self.height // 2)

        self.colors = Colors(self.display)
        self.background_color = self.colors.BLACK

        self.clear()
    
    def clear(self, layer=None):
        layers = [layer] if layer is not None else range(self.layers)
        for i in layers:
            self.display.set_layer(i)
            self.display.set_pen(self.background_color)
            self.display.clear()
    
    def toggle_leds(self, value):
        if value:
            self.presto.auto_ambient_leds(True)
        else:
            self.presto.auto_ambient_leds(False)
            for i in range(7):
                self.presto.set_led_rgb(i, 0, 0, 0)

    def set_backlight(self, value):
        self.presto.set_backlight(max(0.1, min(1.0, value)))