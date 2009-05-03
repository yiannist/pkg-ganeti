#
#

# Copyright (C) 2006, 2007 Google Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.


"""Module implementing a fake ConfigWriter"""

import socket
from ganeti import utils

class FakeConfig:
    """Fake configuration object"""

    def IsCluster(self):
        return True

    def GetNodeList(self):
        return ["a", "b", "c"]

    def GetMaster(self):
        return utils.HostInfo().name


class FakeSStore:
    """Fake simplestore object"""

    def GetClusterName(self):
        return "test.cluster"

    def GetMasterNode(self):
        return utils.HostInfo().name


class FakeProc:
    """Fake processor object"""

    def LogWarning(self, msg):
        pass

    def LogInfo(self, msg):
        pass
