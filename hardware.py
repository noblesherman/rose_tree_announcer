from collections.abc import Callable

# BCM numbers, not physical header pin numbers. Each normally-open button is
# wired between its GPIO and ground, so gpiozero's internal pull-up is used.
BUTTON_PINS = {1: 17, 2: 27, 3: 22, 4: 23, 5: 24}
DEBOUNCE_SECONDS = 0.08

class HardwareButtons:
    def __init__(self, on_press: Callable[[int], None]):
        self._buttons = []
        try:
            from gpiozero import Button
        except (ImportError, OSError):
            return
        for number, pin in BUTTON_PINS.items():
            button = Button(pin, pull_up=True, bounce_time=DEBOUNCE_SECONDS)
            button.when_pressed = lambda n=number: on_press(n)
            self._buttons.append(button)
