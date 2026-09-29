from openpilot.common.test import OpenpilotTestCase

from openpilot.cereal import log
from opendbc.car.structs import car
from opendbc.car.car_helpers import interfaces
from opendbc.car.subaru.values import CAR as SUBARU
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.car.helpers import convert_to_capnp
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.locationd.helpers import Pose
from openpilot.common.mock.generators import generate_deviceMotion
from openpilot.sunnypilot.selfdrive.car import interfaces as sunnypilot_interfaces


class TestTorqueRateLimit(OpenpilotTestCase):
  """The Subaru EPS slews at about 1300 counts/s (STEER_MAX 2047); the torque controller caps its
  command to that and freezes the integrator while capped, so a large error cannot be commanded
  into slew saturation. Other brands are unaffected."""

  def _controller(self, car_name):
    CarInterface = interfaces[car_name]
    CP = CarInterface.get_non_essential_params(car_name)
    CP_SP = CarInterface.get_non_essential_params_sp(CP, car_name)
    CI = CarInterface(CP, CP_SP)
    sunnypilot_interfaces.setup_interfaces(CI)
    # Subaru defaults to a non-torque tune off-device; force the torque union so the controller builds
    if CP.lateralTuning.which() != 'torque':
      CP.lateralTuning.init('torque')
      CP.lateralTuning.torque.latAccelFactor = 2.5
      CP.lateralTuning.torque.friction = 0.1
    lac = LatControlTorque(CP.as_reader(), convert_to_capnp(CP_SP).as_reader(), CI, DT_CTRL)
    return lac, VehicleModel(CP)

  def _step(self, lac, VM, desired_curv):
    CS = car.CarState.new_message()
    CS.vEgo = 15.6
    CS.steeringPressed = False
    pose = Pose.from_device_motion(generate_deviceMotion().deviceMotion)
    _out, _, _log = lac.update(True, CS, VM, log.VehicleParameters.new_message(), False, desired_curv, pose, False, 0.15)
    return _out

  def test_subaru_caps_the_rate_and_freezes_the_integrator(self):
    lac, VM = self._controller(SUBARU.SUBARU_FORESTER)
    cap = 1300.0 / 2047.0 * DT_CTRL
    prev, engaged = 0.0, False
    for _ in range(40):
      out = self._step(lac, VM, 0.05)  # a step the loop cannot satisfy in one frame
      assert abs(out - prev) <= cap + 1e-9, (out, prev, cap)
      prev = out
      engaged = engaged or lac.torque_rate_limited
    assert engaged, "the rate limit never engaged on a hard step"
    assert abs(lac.pid.i) <= abs(out) + 1e-6, "the integrator wound up past the capped command"

  def test_other_brands_are_not_capped(self):
    lac, VM = self._controller(TOYOTA.TOYOTA_RAV4)
    assert lac.torque_rate_limit == float("inf")
    for _ in range(10):
      self._step(lac, VM, 0.05)
    assert not lac.torque_rate_limited
