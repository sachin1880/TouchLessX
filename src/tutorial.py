"""The guided tutorial on the Learn page: one step per gesture in the active profile,
each finished by the camera actually seeing the gesture done. Kept free of Tk so the
step logic can be tested without a display or a camera."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .action_controller import action_spec
from .gesture_engine import CUSTOM_PREFIX, GestureEvent

# The order gestures are taught in: the cursor first, so every later step has the
# basics of steering, then clicks, held poses, flicks and finally the two-hand ones.
TEACHING_ORDER = (
    "open_palm", "point", "pinch", "two_fingers", "three_fingers", "fist", "thumbs_up", "hand_open",
    "swipe_left", "swipe_right", "swipe_up", "swipe_down", "point_left", "point_right",
    "thumb_index", "two_hand_pinch", "both_palms",
)

# How to make each gesture, in words a first-time user can follow.
HOW = {
    "open_palm": "Raise one hand, fingers spread, palm facing the camera.",
    "point": "Raise only your index finger, the other fingers curled.",
    "pinch": "Touch the tips of your thumb and index finger together, then open them again.",
    "two_fingers": "Raise your index and middle fingers - a 'V' - and hold it for a moment.",
    "three_fingers": "Raise your index, middle and ring fingers, and hold them up.",
    "fist": "Close your hand into a fist and hold it for a moment.",
    "thumbs_up": "Make a fist with your thumb pointing straight up, and hold it for a moment.",
    "hand_open": "Make a fist, then open your hand wide within about a second.",
    "swipe_left": "Flick your open hand quickly to the left - a fast, short movement.",
    "swipe_right": "Flick your open hand quickly to the right - a fast, short movement.",
    "swipe_up": "Flick your open hand quickly upwards - a fast, short movement.",
    "swipe_down": "Flick your open hand quickly downwards - a fast, short movement.",
    "point_left": "Point your index finger sideways, to the left.",
    "point_right": "Point your index finger sideways, to the right.",
    "two_hand_pinch": "Raise both hands and pinch with both at the same time.",
    "both_palms": "Raise both hands, palms open towards the camera, and hold them still.",
    "thumb_index": "Make an L: index finger up, thumb out to the side, other fingers curled.",
}

# What to do while a held pose is active, for the continuous actions.
MOVE_HINT = {
    "move_cursor": "Keep the pose and move your hand around - the cursor follows it.",
    "scroll": "Keep the pose and move your hand up and down - the page follows it.",
    "volume": "Keep the pose and move your hand up (louder) and down (quieter).",
    "zoom": "Keep the pose and spread apart (zoom in), then bring together (zoom out).",
}
# How far the hand must travel, in frame widths, before a movement step counts.
MOVE_TARGET = {"move_cursor": 0.6, "scroll": 0.4, "volume": 0.4, "zoom": 0.25}
HOLD_TARGET = 1.5  # seconds a held pose with no movement action must be kept


@dataclass
class Step:
    key: str
    title: str
    how: str                 # what to do with the hand
    does: str = ""           # what it does in the active profile
    glyph: str = ""
    gestures: tuple[str, ...] = ()
    goal: str = "info"       # "info" | "trigger" | "double" | "hold" | "move"
    target: float = 1.0      # triggers, seconds or frame widths, depending on the goal
    note: str = ""


def build_steps(
    action_for: Callable[[str], str],
    label_for: Callable[[str], str],
    glyph_for: Callable[[str], str],
    name_for: Callable[[str], str],
    category_on: Callable[[str], bool],
    custom_gestures: list[str] = (),
    emergency_gesture: str = "none",
) -> list[Step]:
    """The lesson for the active profile: an introduction, one step per mapped gesture
    (plus double-click after the click), how to stop, and a closing step."""
    steps = [Step(
        "intro", "Get ready",
        "Sit 40-80 cm from the camera in reasonable light, with your hand where you can see it in the "
        "preview. Practice mode is on for the whole lesson: gestures are recognised, but nothing is "
        "clicked, typed or scrolled.",
        glyph="👋",
        note="Each step finishes by itself once the camera sees you do it. Skip any step you like.",
    )]

    order = list(TEACHING_ORDER) + [g for g in custom_gestures if g.startswith(CUSTOM_PREFIX)]
    for gesture in order:
        action = action_for(gesture)
        spec = action_spec(action)
        if spec is None or action == "none":
            continue
        how = HOW.get(gesture, "Make the pose you trained in the Training Studio.")
        note = ""
        if spec.category and not category_on(spec.category):
            note = "This feature is switched off on the dashboard, so it won't act until you switch it on."
        if action == "toggle_pause":
            note = "Do it twice: the first time pauses control, the second resumes it."
        if gesture == emergency_gesture:
            note = "This is also your emergency stop: it pauses control. The last step shows how to resume."
        base = dict(title=name_for(gesture), how=how, does=label_for(action), glyph=glyph_for(gesture),
                    gestures=(gesture,), note=note)
        if spec.kind == "continuous":
            if action in MOVE_TARGET:
                steps.append(Step(gesture, goal="move", target=MOVE_TARGET[action],
                                  **{**base, "how": f"{how} {MOVE_HINT[action]}"}))
            else:
                steps.append(Step(gesture, goal="hold", target=HOLD_TARGET, **base))
        else:
            steps.append(Step(gesture, goal="trigger", target=2 if action == "toggle_pause" else 1, **base))
        if action == "left_click":
            steps.append(Step(
                "double_click", "Double-click", "Pinch twice quickly. This is how you open a file or folder.",
                does="Double Click", glyph=glyph_for(gesture), gestures=(gesture,), goal="double",
            ))

    steps.append(Step(
        "stop", "How to stop",
        "Space or Esc pauses control while TouchlessX is in front. The Pause button in the header, the "
        "floating mini controller and the red EMERGENCY STOP button on the dashboard work from anywhere. "
        "Stop Camera turns tracking off completely.",
        glyph="⏸",
    ))
    steps.append(Step(
        "done", "You're ready",
        "That's every gesture in this profile. Leave practice mode to control your computer for real, "
        "or go through the lesson again. Switching profile changes the gestures - the lesson follows "
        "whichever profile is active.",
        glyph="🎉",
    ))
    return steps


class Lesson:
    """Walks through the steps and decides, from the gesture events, when one is done."""

    def __init__(self, steps: list[Step], double_click_seconds: float = 0.8) -> None:
        self.steps = steps
        self.double_click_seconds = double_click_seconds
        self.index = 0
        self.completed: set[str] = set()
        self._reset_progress()

    def _reset_progress(self) -> None:
        self.progress = 0.0          # 0..1 for the current step
        self._count = 0
        self._travel = 0.0
        self._last_trigger: float | None = None

    @property
    def step(self) -> Step:
        return self.steps[self.index]

    @property
    def is_last(self) -> bool:
        return self.index == len(self.steps) - 1

    def practice_steps(self) -> list[Step]:
        return [s for s in self.steps if s.goal != "info"]

    def go(self, index: int) -> None:
        self.index = max(0, min(len(self.steps) - 1, index))
        self._reset_progress()

    def next(self) -> None:
        self.go(self.index + 1)

    def back(self) -> None:
        self.go(self.index - 1)

    def feed(self, event: GestureEvent, now: float) -> bool:
        """Updates the current step's progress. True on the event that completes it."""
        step = self.step
        if step.goal == "info" or step.key in self.completed or event.name not in step.gestures:
            return False
        if step.goal == "trigger":
            if event.is_trigger:
                self._count += 1
            self.progress = self._count / step.target
        elif step.goal == "double":
            if event.is_trigger:
                if self._last_trigger is not None and now - self._last_trigger <= self.double_click_seconds:
                    self.progress = 1.0
                else:
                    self._last_trigger, self.progress = now, 0.5
        elif step.goal == "hold":
            self.progress = event.hold_time / step.target
        elif step.goal == "move":
            if event.motion_delta is not None:
                self._travel += abs(event.motion_delta[0]) + abs(event.motion_delta[1])
            self.progress = self._travel / step.target
        if self.progress >= 1.0:
            self.progress = 1.0
            self.completed.add(step.key)
            return True
        return False

    def missed(self, now: float) -> None:
        """Called when the hand leaves the gesture: a double-click needs its second pinch in time."""
        step = self.step
        if step.goal == "double" and step.key not in self.completed and self._last_trigger is not None \
                and now - self._last_trigger > self.double_click_seconds:
            self._last_trigger, self.progress = None, 0.0
