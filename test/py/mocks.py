#
#

# Copyright (C) 2006, 2007, 2010, 2011 Google Inc.
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


import os

from ganeti import netutils


FAKE_CLUSTER_KEY = ("AAAAB3NzaC1yc2EAAAABIwAAAQEAsuGLw70et3eApJ/ZEJkAVZogIrm"
                    "EYPQJvb1ll52Ti0nr80Wztxibaa8bYGzY22rQIAloIlePeTGcJceAYK"
                    "PZgm0I/Mp2EUGg2NVsQZIzasz6cW0vYuiUbF9GkVlROmvOAykT58RfM"
                    "L8RhPrjrQxZc+NXgZtgDugYSZcXHDLUyWM1xKUoYy0MqYG6ZXCC/Zno"
                    "RThhmjOJgEmvwrMcTWQjmzH3NeJAxaBsEHR8tiVZ/Y23C/ULWLyNT6R"
                    "fB+DE7IovsMQaS+83AK1Teg7RWNyQczachatf/JT8VjUqFYjJepPjMb"
                    "vYdB2nQds7/+Bf40C/OpbvnAxna1kVtgFHAo18cQ==")


class FakeConfig(object):
  """Fake configuration object"""

  def IsCluster(self):
    return True

  def GetNodeList(self):
    return ["a", "b", "c"]

  def GetRsaHostKey(self):
    return FAKE_CLUSTER_KEY

  def GetDsaHostKey(self):
    return FAKE_CLUSTER_KEY

  def GetClusterName(self):
    return "test.cluster"

  def GetMasterNode(self):
    return "a"

  def GetMasterNodeName(self):
    return netutils.Hostname.GetSysName()

  def GetDefaultIAllocator(self):
    return "testallocator"

  def GetNodeName(self, node_uuid):
    if node_uuid in self.GetNodeList():
      return "node_%s.example.com" % (node_uuid,)
    else:
      return None

  def GetNodeNames(self, node_uuids):
    return map(self.GetNodeName, node_uuids)


class FakeProc(object):
  """Fake processor object"""

  def Log(self, msg, *args, **kwargs):
    pass

  def LogWarning(self, msg, *args, **kwargs):
    pass

  def LogInfo(self, msg, *args, **kwargs):
    pass

  def LogStep(self, current, total, message):
    pass


class FakeGLM(object):
  """Fake global lock manager object"""

  def list_owned(self, _):
    return set()


class FakeContext(object):
  """Fake context object"""

  def __init__(self):
    self.cfg = FakeConfig()
    self.glm = FakeGLM()


class FakeGetentResolver:
  """Fake runtime.GetentResolver"""

  def __init__(self):
    # As we nomally don't run under root we use our own uid/gid for all
    # fields. This way we don't run into permission denied problems.
    uid = os.getuid()
    gid = os.getgid()

    self.masterd_uid = uid
    self.masterd_gid = gid
    self.confd_uid = uid
    self.confd_gid = gid
    self.rapi_uid = uid
    self.rapi_gid = gid
    self.noded_uid = uid
    self.noded_gid = gid

    self.daemons_gid = gid
    self.admin_gid = gid

  def LookupUid(self, uid):
    return "user%s" % uid

  def LookupGid(self, gid):
    return "group%s" % gid
