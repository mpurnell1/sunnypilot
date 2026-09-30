from opendbc.car import DT_CTRL
from opendbc.car.structs import car
from opendbc.car.subaru.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.car.car_events import CarEvents, SUBARU_CUT_FADE_FRAMES, SUBARU_CUT_ANGLE_DEG

EventName = log.OnroadEvent.EventName
STEER_EVENTS = (EventName.steerTempUnavailable, EventName.steerTempUnavailableSilent)
UNPRESSED_FRAMES = int(1.5 / DT_CTRL)


def car_state(**fields):
  return car.CarState.new_message(**{'vEgo': 5., 'steeringAngleDeg': SUBARU_CUT_ANGLE_DEG + 10., **fields})


class TestSubaruHighAngleCut:
  def setup_method(self):
    self.car_events = CarEvents(car.CarParams.new_message(brand='subaru', carFingerprint=CAR.SUBARU_FORESTER))
    self.CS_prev = car_state()

  def run(self, CS, frames):
    seen = set()
    for _ in range(frames):
      names = self.car_events.update(CS, self.CS_prev, car.CarControl.new_message()).names
      seen |= {n for n in names if n in STEER_EVENTS}
      self.CS_prev = CS
    return seen

  def test_empty_wheel_alerts_after_the_fade(self):
    self.run(car_state(), UNPRESSED_FRAMES)
    assert self.run(car_state(steerFaultTemporary=True), SUBARU_CUT_FADE_FRAMES) == set()
    assert self.run(car_state(steerFaultTemporary=True), 1) == {EventName.steerTempUnavailable}

  def test_hand_on_the_wheel_stays_quiet(self):
    self.run(car_state(), UNPRESSED_FRAMES)
    assert self.run(car_state(steerFaultTemporary=True, steeringPressed=True), 5 * SUBARU_CUT_FADE_FRAMES) == set()

  def test_hand_released_mid_cut_gets_the_stock_grace(self):
    self.run(car_state(steerFaultTemporary=True, steeringPressed=True), 5 * SUBARU_CUT_FADE_FRAMES)
    assert self.run(car_state(steerFaultTemporary=True), UNPRESSED_FRAMES - 1) == set()
    assert self.run(car_state(steerFaultTemporary=True), 1) == {EventName.steerTempUnavailable}

  def test_standstill_stays_quiet(self):
    self.run(car_state(), UNPRESSED_FRAMES)
    assert self.run(car_state(steerFaultTemporary=True, standstill=True), 5 * SUBARU_CUT_FADE_FRAMES) == set()

  def test_fault_outside_the_envelope_keeps_the_stock_alert(self):
    self.run(car_state(vEgo=20., steeringAngleDeg=10.), UNPRESSED_FRAMES)
    assert self.run(car_state(vEgo=20., steeringAngleDeg=10., steerFaultTemporary=True), 1) == {EventName.steerTempUnavailable}
