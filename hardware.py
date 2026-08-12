from collections.abc import Callable

# BCM numbers, not physical header pin numbers. Each normally-open button is
# wired between its GPIO and ground, so gpiozero's internal pull-up is used.
BUTTON_PINS = {1: 17, 2: 27, 3: 22, 4: 23, 5: 24}
DEBOUNCE_SECONDS = 0.08
HOLD_SECONDS = 1.0


class HardwareButtons:
    """Tap plays, hold stops.

    The five buttons are the only input on the finished enclosure, so the stop
    control has to live on them too. A press is therefore acted on when the
    button is *released*: gpiozero fires when_held first on a long press, we
    mark that press as handled, and the release is then ignored. A tap costs
    only the few milliseconds the finger is down.
    """

    def __init__(
        self,
        on_tap: Callable[[int], None],
        on_hold: Callable[[int], None] | None = None,
        hold_seconds: float = HOLD_SECONDS,
    ):
        self._buttons = []
        self._handled: dict[int, bool] = {}
        hold = on_hold or on_tap
        try:
            from gpiozero import Button
        except (ImportError, OSError):
            return
        for number, pin in BUTTON_PINS.items():
            button = Button(
                pin,
                pull_up=True,
                bounce_time=DEBOUNCE_SECONDS,
                hold_time=hold_seconds,
            )
            button.when_pressed = lambda n=number: self._pressed(n)
            button.when_held = lambda n=number: self._held(n, hold)
            button.when_released = lambda n=number: self._released(n, on_tap)
            self._buttons.append(button)

    def _pressed(self, number):
        self._handled[number] = False

    def _held(self, number, on_hold):
        self._handled[number] = True
        on_hold(number)

    def _released(self, number, on_tap):
        if self._handled.pop(number, False):
            return
        on_tap(number)
