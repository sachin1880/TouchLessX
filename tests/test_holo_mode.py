"""Tests for full-screen Holo mode: hands -> pointers and pinches, pressing and moving
panels, and the hologram - turning, flick-spin, two-hand zoom and twist, explode,
models, the energy field - plus leaving and the rendered frame. No window or camera."""
from __future__ import annotations

import math

import numpy as np

from src.holo_scene import (ACTIVE, BLAST_COOLDOWN, CHARGE_FULL, EXIT_HOLD, IDLE_SPIN, MODELS, PUSH_RADIUS,
                            SCALE_RANGE, TITLE_H, TRAIL_SECONDS, Hand, HandInput, Hologram, HoloScene, Particles,
                            build_model, shatter)

W, H = 1440, 900


def _landmarks(nx: float, ny: float, pinched: bool) -> np.ndarray:
    """A hand whose pinch point is at (nx, ny) in camera space, pinched or wide open."""
    lm = np.zeros((21, 3), np.float32)
    lm[:, 0], lm[:, 1] = nx, ny + 0.15
    lm[0, :2] = (nx, ny + 0.25)          # wrist
    lm[9, :2] = (nx, ny + 0.15)          # middle finger base: palm length 0.1
    half = 0.005 if pinched else 0.04    # thumb-index gap 0.1 or 0.8 palm lengths
    lm[4, :2] = (nx - half, ny)
    lm[8, :2] = (nx + half, ny)
    return lm


def _camera_point(x: float, y: float) -> tuple[float, float]:
    left, top, right, bottom = ACTIVE
    return left + x / W * (right - left), top + y / H * (bottom - top)


def _hand_at(x: float, y: float, pinching: bool, second: Hand | None = None) -> Hand:
    return Hand(True, x, y, pinching, [(x, y)] * 21, (x, y), second)


def _scene() -> HoloScene:
    return HoloScene(W, H, ["Safari", "Notes"])


def _centre(scene, panel_id, button_id):
    panel = scene.panels[panel_id]
    button = next(b for b in panel.buttons if b.id == button_id)
    bx, by, bw, bh = button.rect
    return panel.x + bx + bw / 2, panel.y + by + bh / 2


def _empty_space(scene):
    """Beside the hologram, not on it: pinching here turns it."""
    cx, cy, size = scene.stage()
    return cx + size * 0.85, cy


def _on_hologram(scene):
    cx, cy, _size = scene.center()
    return cx, cy


# ------------------------------------------------------------------ hand input
def test_the_pointer_is_the_pinch_point_mapped_to_the_screen():
    hand = HandInput(W, H).update(_landmarks(*_camera_point(700, 400), pinched=False), 0.0)
    assert hand.present and not hand.pinching
    assert abs(hand.x - 700) < 1 and abs(hand.y - 400) < 1
    assert len(hand.skeleton) == 21 and hand.second is None


def test_the_other_hand_is_tracked_too():
    hands = HandInput(W, H)
    hand = hands.update(_landmarks(0.3, 0.5, True), 0.0, _landmarks(0.7, 0.5, False))
    assert hand.pinching and hand.second is not None and not hand.second.pinching
    assert hand.second.x > hand.x


def test_the_pointer_stays_on_the_screen():
    hand = HandInput(W, H).update(_landmarks(0.0, 0.0, pinched=False), 0.0)
    assert (hand.x, hand.y) == (0.0, 0.0)


def test_pinch_has_hysteresis():
    hands = HandInput(W, H, pinch_threshold=0.4)
    lm = _landmarks(0.5, 0.5, pinched=True)
    assert hands.update(lm, 0.0).pinching
    lm[4, 0], lm[8, 0] = 0.5 - 0.022, 0.5 + 0.022   # gap 0.44: above the threshold, inside the release band
    assert hands.update(lm, 0.03).pinching
    lm[4, 0], lm[8, 0] = 0.5 - 0.03, 0.5 + 0.03     # gap 0.6: released
    assert not hands.update(lm, 0.06).pinching


def test_a_lost_hand_lets_go():
    hands = HandInput(W, H)
    assert hands.update(_landmarks(0.5, 0.5, True), 0.0).pinching
    assert not hands.update(None, 0.03).present
    assert not hands.update(_landmarks(0.5, 0.5, False), 0.06).pinching


# ------------------------------------------------------------------ panels
def test_pinching_a_button_presses_it_once():
    scene = _scene()
    x, y = _centre(scene, "music", "play")
    assert scene.handle(_hand_at(x, y, False), 0.0) == []
    assert scene.handle(_hand_at(x, y, True), 0.1) == ["media:play_pause"]
    assert scene.handle(_hand_at(x, y, True), 0.2) == []        # held: no repeat
    scene.handle(_hand_at(x, y, False), 0.3)
    assert scene.handle(_hand_at(x, y, True), 0.4) == ["media:play_pause"]


def test_the_dock_opens_apps_and_exits():
    scene = _scene()
    assert scene.handle(_hand_at(*_centre(scene, "dock", "app0"), True), 0.0) == ["app:Safari"]
    scene.handle(_hand_at(0, 0, False), 0.1)
    assert scene.handle(_hand_at(*_centre(scene, "dock", "exit"), True), 0.2) == ["exit"]


def test_a_panel_is_moved_by_its_title_bar():
    scene = _scene()
    clock = scene.panels["clock"]
    start = (clock.x, clock.y)
    grab = (clock.x + 40, clock.y + TITLE_H / 2)
    scene.handle(_hand_at(*grab, True), 0.0)
    scene.handle(_hand_at(grab[0] + 300, grab[1] + 120, True), 0.1)
    scene.handle(_hand_at(grab[0] + 300, grab[1] + 120, False), 0.2)
    assert (clock.x, clock.y) == (start[0] + 300, start[1] + 120)
    assert scene.layout_changed


def test_the_layout_is_remembered_and_leaves_the_middle_free():
    scene = _scene()
    cx, cy, size = scene.stage()
    for panel in scene.panels.values():
        if panel.id != "dock":
            assert not panel.contains(cx, cy)      # the hologram's stage is clear
    scene.panels["clock"].x = W / 2
    assert HoloScene(W, H, [], scene.layout()).panels["clock"].x == W / 2


# ------------------------------------------------------------------ the hologram
def test_pinching_empty_space_turns_the_hologram_with_the_hand():
    scene = _scene()
    x, y = _empty_space(scene)
    yaw = scene.holo.yaw
    scene.handle(_hand_at(x, y, True), 0.0)
    assert scene.holo.grabbed and scene.mode == "TURNING"
    scene.handle(_hand_at(x + 100, y, True), 0.1)
    assert math.isclose(scene.holo.yaw - yaw, 100 * 0.008, rel_tol=0.05)


def test_a_flick_keeps_it_spinning_then_it_slows():
    scene = _scene()
    x, y = _empty_space(scene)
    scene.handle(_hand_at(x, y, True), 0.0)
    for i in range(1, 6):
        scene.handle(_hand_at(x + i * 60, y, True), i * 0.033)       # a fast swipe
    scene.handle(_hand_at(x + 300, y, False), 0.2)                   # let go mid-move
    fast = scene.holo.spin[0]
    assert fast > IDLE_SPIN * 5
    yaw = scene.holo.yaw
    for i in range(30):
        scene.idle(0.2 + (i + 1) * 0.033)
    assert scene.holo.yaw > yaw + 0.5                                # still turning on its own
    assert scene.holo.spin[0] < fast                                 # and slowing down


def test_left_alone_it_settles_into_a_slow_turn():
    holo = Hologram()
    holo.spin[0] = 6.0
    for _ in range(600):
        holo.step(1 / 30)
    assert math.isclose(float(holo.spin[0]), IDLE_SPIN, rel_tol=0.1)


def test_two_hands_zoom_and_twist():
    scene = _scene()
    x, y = _empty_space(scene)
    left, right = _hand_at(x - 100, y, True), _hand_at(x + 100, y, True)
    left.second = right
    scene.handle(left, 0.0)
    assert scene.mode == "ZOOM"
    wider = _hand_at(x - 200, y, True, _hand_at(x + 200, y, True))
    scene.handle(wider, 0.1)
    assert math.isclose(scene.holo.scale, 2.0, rel_tol=0.01)
    huge = _hand_at(x - 2000, y, True, _hand_at(x + 2000, y, True))
    scene.handle(huge, 0.2)
    assert scene.holo.scale == SCALE_RANGE[1]                        # capped
    tilted = _hand_at(x - 200, y + 200, True, _hand_at(x + 200, y - 200, True))
    scene.handle(tilted, 0.3)
    assert abs(scene.holo.roll) > 0.5                                # twisted


def test_one_hand_pinching_with_the_other_open_still_turns():
    scene = _scene()
    x, y = _empty_space(scene)
    scene.handle(_hand_at(x, y, True, _hand_at(x + 300, y, False)), 0.0)
    assert scene.mode == "TURNING"


def test_a_fist_explodes_it_and_an_open_hand_rebuilds_it():
    scene = _scene()
    t = 0.0
    for _ in range(30):
        t += 0.033
        scene.handle(_hand_at(100, 100, False), t, "fist")
    assert scene.holo.explode == 1.0 and scene.mode == "EXPLODED"
    for _ in range(30):
        t += 0.033
        scene.handle(_hand_at(100, 100, False), t, "open_palm")
    assert scene.holo.explode == 0.0


def test_a_passing_fist_does_not_explode_it():
    scene = _scene()
    scene.handle(Hand(), 0.0, "fist")
    scene.handle(Hand(), 0.05, "open_palm")
    assert scene.holo.explode_target == 0.0


def test_the_model_buttons_switch_models_and_reset_restores_it():
    scene = _scene()
    scene.handle(_hand_at(*_centre(scene, "models", "dna"), True), 0.0)
    assert scene.holo.model == "dna"
    scene.holo.scale, scene.holo.roll = 2.0, 1.0
    scene.handle(_hand_at(0, 0, False), 0.1)
    scene.handle(_hand_at(*_centre(scene, "models", "reset"), True), 0.2)
    assert (scene.holo.scale, scene.holo.roll) == (1.0, 0.0)


def test_every_model_builds_and_reassembles_exactly():
    for name in MODELS:
        lines = build_model(name)
        assert lines and all(line.shape[1] == 3 for line in lines)
        assert max(float(np.abs(line).max()) for line in lines) <= 1.4    # fits its stage
        pieces, directions = shatter(lines)
        assert len(pieces) == len(directions)
        assert sum(len(p) - 1 for p in pieces) == sum(len(line) - 1 for line in lines)   # no segment lost


def test_the_fingertip_pushes_particles_away():
    particles = Particles(count=50)
    resting = particles.step(0.0, 500, 400, 200, 1.0, None)
    target = resting[0]
    pushed = particles.step(0.0, 500, 400, 200, 1.0, (float(target[0]) + 5, float(target[1])))
    assert np.linalg.norm(pushed[0] - target) > 5
    far = particles.step(0.0, 500, 400, 200, 1.0, (-5000.0, -5000.0))
    assert len(far) == 50


def test_pushed_particles_drift_back():
    particles = Particles(count=10)
    particles.offset[:] = PUSH_RADIUS
    for _ in range(90):
        particles.step(1 / 30, 500, 400, 200, 1.0, None)
    assert float(np.abs(particles.offset).max()) < PUSH_RADIUS * 0.2


# ------------------------------------------------------------------ leaving and drawing
def test_both_palms_held_exits():
    scene = _scene()
    assert scene.handle(Hand(), 0.0, "both_palms") == []
    assert scene.handle(Hand(), EXIT_HOLD / 2, "both_palms") == []
    assert scene.handle(Hand(), EXIT_HOLD + 0.01, "both_palms") == ["exit"]


def test_a_brief_both_palms_does_not_exit():
    scene = _scene()
    scene.handle(Hand(), 0.0, "both_palms")
    scene.handle(Hand(), 0.3, "open_palm")
    assert scene.handle(Hand(), EXIT_HOLD + 0.1, "both_palms") == []


def test_render_makes_a_full_screen_frame_for_every_model():
    scene = _scene()
    scene.notice = "Starting the camera…"
    frame = np.full((360, 640, 3), 128, np.uint8)
    two = _hand_at(600, 400, True, _hand_at(900, 400, True))
    for i, name in enumerate(MODELS):
        scene.holo.set_model(name)
        image = scene.render(frame, two, i * 0.1)
        assert image.shape == (H, W, 3) and image.dtype == np.uint8
    assert scene.render(None, Hand(), 1.0).shape == (H, W, 3)   # no camera yet


def test_buttons_take_their_own_height():
    """Regression: button() fixed height=32 and a caller's height= crashed the Holo Desk page."""
    import tkinter as tk

    import pytest
    customtkinter = pytest.importorskip("customtkinter")
    from src.ui_widgets import button
    try:
        root = customtkinter.CTk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        assert button(root, "Tall", lambda: None, "primary", 170, height=40).cget("height") == 40
        assert button(root, "Default", lambda: None).cget("height") == 32
    finally:
        root.destroy()


# ------------------------------------------------------------------ carry and throw
def test_pinching_the_hologram_carries_it():
    scene = _scene()
    x, y = _on_hologram(scene)
    scene.handle(_hand_at(x, y, True), 0.0)
    assert scene.mode == "CARRIED" and "grab" in scene.sounds
    scene.handle(_hand_at(x - 200, y + 50, True), 0.1)
    cx, cy, _size = scene.center()
    assert (round(cx), round(cy)) == (round(x - 200), round(y + 50))


def test_a_fast_release_throws_it_and_it_bounces_then_comes_home():
    scene = _scene()
    x, y = _on_hologram(scene)
    scene.handle(_hand_at(x, y, True), 0.0)
    for i in range(1, 5):
        scene.handle(_hand_at(x + i * 80, y, True), i * 0.033)       # ~2400 px/s to the right
    scene.handle(_hand_at(x + 320, y, False), 0.17)
    assert scene.holo.velocity[0] > 1000 and "throw" in scene.sounds
    t, bounced = 0.17, False
    for _ in range(90):
        t += 0.033
        scene.sounds.clear()
        scene.idle(t)
        bounced = bounced or "bounce" in scene.sounds
        cx, _cy, _size = scene.center()
        assert cx <= W                                               # never leaves the screen
    assert bounced
    for _ in range(300):
        t += 0.033
        scene.idle(t)
    assert float(np.linalg.norm(scene.holo.offset)) < 5             # floated back home


# ------------------------------------------------------------------ repulsor
def _palm(x=400.0, y=500.0) -> Hand:
    hand = _hand_at(x, y, False)
    hand.skeleton = [(x, y)] * 21
    return hand


def test_a_still_open_palm_charges_and_fires_the_repulsor():
    scene = _scene()
    t = 0.0
    scene.handle(_palm(), t, "open_palm")
    while scene.charge == 0 and t < 1.0:
        t += 0.033
        scene.handle(_palm(), t, "open_palm")
    assert 0 < scene.charge < 1 and scene.mode == "CHARGING" and "charge" in scene.sounds
    while not scene.shockwaves and t < 2.0:
        t += 0.033
        scene.handle(_palm(), t, "open_palm")
    assert scene.shockwaves and scene.flash > 0.8 and "blast" in scene.sounds
    assert t >= CHARGE_FULL - 0.05
    assert abs(scene.holo.spin[0]) > 5                               # knocked spinning
    assert float(np.abs(scene.particles.offset).max()) > 50          # particles blown away


def test_the_repulsor_has_a_cooldown():
    scene = _scene()
    t = 0.0
    while not scene.shockwaves:
        t += 0.033
        scene.handle(_palm(), t, "open_palm")
    fired = t
    while t < fired + BLAST_COOLDOWN - 0.1:
        t += 0.033
        scene.handle(_palm(), t, "open_palm")
    assert len(scene.shockwaves) == 1


def test_moving_or_closing_the_palm_cancels_the_charge():
    scene = _scene()
    t = 0.0
    while scene.charge == 0:
        t += 0.033
        scene.handle(_palm(), t, "open_palm")
    scene.sounds.clear()
    scene.handle(_palm(900, 500), t + 0.033, "open_palm")            # a big, fast move
    assert scene.charge == 0 and "charge_stop" in scene.sounds
    while scene.charge == 0:
        t += 0.033
        scene.handle(_palm(900, 500), t + 0.066, "open_palm")
    scene.handle(_palm(900, 500), t + 0.1, "fist")
    assert scene.charge == 0 and not scene.shockwaves


def test_two_open_hands_make_lightning_not_a_repulsor():
    scene = _scene()
    t = 0.0
    for _ in range(60):
        t += 0.033
        scene.handle(_hand_at(300, 400, False, _hand_at(900, 400, False)), t, "open_palm")
    assert scene.lightning and scene.charge == 0 and not scene.shockwaves
    scene.handle(_hand_at(300, 400, True, _hand_at(900, 400, False)), t + 0.033)
    assert not scene.lightning                                       # a pinch ends it


# ------------------------------------------------------------------ trails, swipes, boot, toggles
def test_fingertips_leave_short_trails():
    scene = _scene()
    for i in range(30):
        scene.handle(_hand_at(100 + i * 10, 300, False), i * 0.033)
    trail = scene._trails[0]
    assert len(trail) > 3 and trail[-1][2] - trail[0][2] <= TRAIL_SECONDS
    scene.handle(Hand(), 30 * 0.033 + TRAIL_SECONDS + 0.1)
    assert not scene._trails[0]                                      # fades away once the hand goes


def test_a_swipe_changes_the_model_and_it_assembles():
    scene = _scene()
    first = scene.holo.model
    scene.handle(_hand_at(100, 100, False), 1.0, "swipe_left")
    assert scene.holo.model != first and scene.holo.explode == 1.0 and "whoosh" in scene.sounds
    scene.handle(_hand_at(100, 100, False), 1.1, "swipe_right")      # too soon: ignored
    second = scene.holo.model
    scene.handle(_hand_at(100, 100, False), 2.0, "swipe_right")
    assert scene.holo.model == first != second


def test_it_boots_once_with_a_sound():
    scene = _scene()
    scene.render(None, Hand(), 5.0)
    scene.render(None, Hand(), 5.1)
    assert scene.sounds.count("boot") == 1
    assert scene.holo.explode == 1.0                                 # starts in pieces, assembles


def test_sound_and_trails_switch_off():
    scene = _scene()
    scene.handle(_hand_at(*_centre(scene, "models", "sound"), True), 0.0)
    assert not scene.sound_on
    scene.sounds.clear()
    scene.handle(_hand_at(0, 0, False), 0.1)
    scene.handle(_hand_at(*_centre(scene, "models", "trails"), True), 0.2)
    assert not scene.trails_on and scene.sounds == []
    scene.handle(_hand_at(0, 0, False), 0.3)
    for i in range(10):
        scene.handle(_hand_at(100 + i * 10, 300, False), 0.4 + i * 0.033)
    assert not scene._trails[0]


def test_every_effect_renders():
    scene = _scene()
    frame = np.full((360, 640, 3), 90, np.uint8)
    scene.render(frame, Hand(), 0.0)                                 # boot
    t = 0.0
    while not scene.shockwaves:
        t += 0.033
        scene.handle(_palm(), t, "open_palm")
    two = _hand_at(300, 400, False, _hand_at(900, 400, False))
    scene.handle(two, t + 0.04, "open_palm")
    image = scene.render(frame, two, t + 0.05)
    assert image.shape == (H, W, 3) and scene.lightning and scene.flash > 0


def test_the_sounds_are_synthesized():
    from src.holo_sounds import synthesize
    sounds = synthesize()
    for name in ("click", "boot", "whoosh", "grab", "throw", "bounce", "explode", "charge", "blast", "zap"):
        assert name in sounds and len(sounds[name]) > 1000
        assert float(np.abs(sounds[name]).max()) <= 1.0
