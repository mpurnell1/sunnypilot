from openpilot.common.test import OpenpilotTestCase

from opendbc.car.car_helpers import interfaces
from opendbc.car.subaru.values import CAR as SUBARU
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.car.helpers import convert_to_capnp
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque, KP as KP_V1, KP_INTERP, KP_INTERP_SUBARU_LOW
from openpilot.sunnypilot.selfdrive.controls.lib.latcontrol_torque_v0 import LatControlTorque as LatControlTorqueV0, KP as KP_V0
from openpilot.sunnypilot.selfdrive.car import interfaces as sunnypilot_interfaces

# the last gain each controller's table ends on
KP_LAST: dict[type, float] = {LatControlTorque: KP_V1, LatControlTorqueV0: KP_V0}


class TestSubaruTorqueGains(OpenpilotTestCase):
  """Both torque controllers carry the Subaru low-speed gain schedule, since controlsd_ext picks V0 by
  the TorqueControlTune param."""

  CONTROLLERS = (LatControlTorque, LatControlTorqueV0)

  def _controller(self, car_name, cls):
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
    return cls(CP.as_reader(), convert_to_capnp(CP_SP).as_reader(), CI, DT_CTRL)

  def test_subaru_low_speed_gains_in_both_controllers(self):
    for cls in self.CONTROLLERS:
      speeds, gains = self._controller(SUBARU.SUBARU_FORESTER, cls).pid._k_p
      assert list(gains) == KP_INTERP_SUBARU_LOW + [KP_LAST[cls]], cls.__module__
      assert speeds[-1] == 30

  def test_other_brands_keep_the_stock_gains(self):
    for cls in self.CONTROLLERS:
      gains = self._controller(TOYOTA.TOYOTA_RAV4, cls).pid._k_p[1]
      assert list(gains) == KP_INTERP[:-1] + [KP_LAST[cls]], cls.__module__
