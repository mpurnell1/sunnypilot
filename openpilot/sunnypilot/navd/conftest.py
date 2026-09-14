"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pytest

import openpilot.cereal.messaging as messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.prefix import OpenpilotPrefix


# every test gets its own params and msgq directory; the PubMaster creates every socket in
# it, since a SubMaster raises on a socket nobody has published to yet
@pytest.fixture(autouse=True)
def navd_prefix():
  with OpenpilotPrefix():
    pm = messaging.PubMaster(list(SERVICE_LIST.keys()))
    yield
    del pm
