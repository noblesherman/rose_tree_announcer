from collections.abc import Callable
BUTTON_PINS = {1:5, 2:6, 3:16, 4:20, 5:21}

class HardwareButtons:
    def __init__(self, on_press: Callable[[int], None]):
        self._buttons = []
        try:
            from gpiozero import Button
        except (ImportError, OSError):
            return
        for number, pin in BUTTON_PINS.items():
            button = Button(pin, pull_up=True, bounce_time=0.08)
            button.when_pressed = lambda n=number: on_press(n)
            self._buttons.append(button)
