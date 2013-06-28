#!/usr/bin/python
#

# Copyright (C) 2006, 2007, 2010, 2012, 2013 Google Inc.
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


"""Script for unittesting the bdev module"""


import os
import random
import unittest

from ganeti import bdev
from ganeti import compat
from ganeti import constants
from ganeti import errors
from ganeti import objects
from ganeti import utils

import testutils


class TestBaseDRBD(testutils.GanetiTestCase):
  def testGetVersion(self):
    data = [
      ["version: 8.0.12 (api:76/proto:86-91)"],
      ["version: 8.2.7 (api:88/proto:0-100)"],
      ["version: 8.3.7.49 (api:188/proto:13-191)"],
    ]
    result = [
      {
      "k_major": 8,
      "k_minor": 0,
      "k_point": 12,
      "api": 76,
      "proto": 86,
      "proto2": "91",
      },
      {
      "k_major": 8,
      "k_minor": 2,
      "k_point": 7,
      "api": 88,
      "proto": 0,
      "proto2": "100",
      },
      {
      "k_major": 8,
      "k_minor": 3,
      "k_point": 7,
      "api": 188,
      "proto": 13,
      "proto2": "191",
      }
    ]
    for d,r in zip(data, result):
      self.assertEqual(bdev.BaseDRBD._GetVersion(d), r)


class TestDRBD8Runner(testutils.GanetiTestCase):
  """Testing case for DRBD8"""

  @staticmethod
  def _has_disk(data, dname, mname):
    """Check local disk corectness"""
    retval = (
      "local_dev" in data and
      data["local_dev"] == dname and
      "meta_dev" in data and
      data["meta_dev"] == mname and
      "meta_index" in data and
      data["meta_index"] == 0
      )
    return retval

  @staticmethod
  def _has_net(data, local, remote):
    """Check network connection parameters"""
    retval = (
      "local_addr" in data and
      data["local_addr"] == local and
      "remote_addr" in data and
      data["remote_addr"] == remote
      )
    return retval

  def testParserCreation(self):
    """Test drbdsetup show parser creation"""
    bdev.DRBD8._GetShowParser()

  def testParser80(self):
    """Test drbdsetup show parser for disk and network version 8.0"""
    data = testutils.ReadTestData("bdev-drbd-8.0.txt")
    result = bdev.DRBD8._GetDevInfo(data)
    self.failUnless(self._has_disk(result, "/dev/xenvg/test.data",
                                   "/dev/xenvg/test.meta"),
                    "Wrong local disk info")
    self.failUnless(self._has_net(result, ("192.0.2.1", 11000),
                                  ("192.0.2.2", 11000)),
                    "Wrong network info (8.0.x)")

  def testParser83(self):
    """Test drbdsetup show parser for disk and network version 8.3"""
    data = testutils.ReadTestData("bdev-drbd-8.3.txt")
    result = bdev.DRBD8._GetDevInfo(data)
    self.failUnless(self._has_disk(result, "/dev/xenvg/test.data",
                                   "/dev/xenvg/test.meta"),
                    "Wrong local disk info")
    self.failUnless(self._has_net(result, ("192.0.2.1", 11000),
                                  ("192.0.2.2", 11000)),
                    "Wrong network info (8.0.x)")

  def testParserNetIP4(self):
    """Test drbdsetup show parser for IPv4 network"""
    data = testutils.ReadTestData("bdev-drbd-net-ip4.txt")
    result = bdev.DRBD8._GetDevInfo(data)
    self.failUnless(("local_dev" not in result and
                     "meta_dev" not in result and
                     "meta_index" not in result),
                    "Should not find local disk info")
    self.failUnless(self._has_net(result, ("192.0.2.1", 11002),
                                  ("192.0.2.2", 11002)),
                    "Wrong network info (IPv4)")

  def testParserNetIP6(self):
    """Test drbdsetup show parser for IPv6 network"""
    data = testutils.ReadTestData("bdev-drbd-net-ip6.txt")
    result = bdev.DRBD8._GetDevInfo(data)
    self.failUnless(("local_dev" not in result and
                     "meta_dev" not in result and
                     "meta_index" not in result),
                    "Should not find local disk info")
    self.failUnless(self._has_net(result, ("2001:db8:65::1", 11048),
                                  ("2001:db8:66::1", 11048)),
                    "Wrong network info (IPv6)")

  def testParserDisk(self):
    """Test drbdsetup show parser for disk"""
    data = testutils.ReadTestData("bdev-drbd-disk.txt")
    result = bdev.DRBD8._GetDevInfo(data)
    self.failUnless(self._has_disk(result, "/dev/xenvg/test.data",
                                   "/dev/xenvg/test.meta"),
                    "Wrong local disk info")
    self.failUnless(("local_addr" not in result and
                     "remote_addr" not in result),
                    "Should not find network info")

  def testBarriersOptions(self):
    """Test class method that generates drbdsetup options for disk barriers"""
    # Tests that should fail because of wrong version/options combinations
    should_fail = [
      (8, 0, 12, "bfd", True),
      (8, 0, 12, "fd", False),
      (8, 0, 12, "b", True),
      (8, 2, 7, "bfd", True),
      (8, 2, 7, "b", True)
    ]

    for vmaj, vmin, vrel, opts, meta in should_fail:
      self.assertRaises(errors.BlockDeviceError,
                        bdev.DRBD8._ComputeDiskBarrierArgs,
                        vmaj, vmin, vrel, opts, meta)

    # get the valid options from the frozenset(frozenset()) in constants.
    valid_options = [list(x)[0] for x in constants.DRBD_VALID_BARRIER_OPT]

    # Versions that do not support anything
    for vmaj, vmin, vrel in ((8, 0, 0), (8, 0, 11), (8, 2, 6)):
      for opts in valid_options:
        self.assertRaises(errors.BlockDeviceError,
                          bdev.DRBD8._ComputeDiskBarrierArgs,
                          vmaj, vmin, vrel, opts, True)

    # Versions with partial support (testing only options that are supported)
    tests = [
      (8, 0, 12, "n", False, []),
      (8, 0, 12, "n", True, ["--no-md-flushes"]),
      (8, 2, 7, "n", False, []),
      (8, 2, 7, "fd", False, ["--no-disk-flushes", "--no-disk-drain"]),
      (8, 0, 12, "n", True, ["--no-md-flushes"]),
      ]

    # Versions that support everything
    for vmaj, vmin, vrel in ((8, 3, 0), (8, 3, 12)):
      tests.append((vmaj, vmin, vrel, "bfd", True,
                    ["--no-disk-barrier", "--no-disk-drain",
                     "--no-disk-flushes", "--no-md-flushes"]))
      tests.append((vmaj, vmin, vrel, "n", False, []))
      tests.append((vmaj, vmin, vrel, "b", True,
                    ["--no-disk-barrier", "--no-md-flushes"]))
      tests.append((vmaj, vmin, vrel, "fd", False,
                    ["--no-disk-flushes", "--no-disk-drain"]))
      tests.append((vmaj, vmin, vrel, "n", True, ["--no-md-flushes"]))

    # Test execution
    for test in tests:
      vmaj, vmin, vrel, disabled_barriers, disable_meta_flush, expected = test
      args = \
        bdev.DRBD8._ComputeDiskBarrierArgs(vmaj, vmin, vrel,
                                           disabled_barriers,
                                           disable_meta_flush)
      self.failUnless(set(args) == set(expected),
                      "For test %s, got wrong results %s" % (test, args))

    # Unsupported or invalid versions
    for vmaj, vmin, vrel in ((0, 7, 25), (9, 0, 0), (7, 0, 0), (8, 4, 0)):
      self.assertRaises(errors.BlockDeviceError,
                        bdev.DRBD8._ComputeDiskBarrierArgs,
                        vmaj, vmin, vrel, "n", True)

    # Invalid options
    for option in ("", "c", "whatever", "nbdfc", "nf"):
      self.assertRaises(errors.BlockDeviceError,
                        bdev.DRBD8._ComputeDiskBarrierArgs,
                        8, 3, 11, option, True)


class TestDRBD8Status(testutils.GanetiTestCase):
  """Testing case for DRBD8 /proc status"""

  def setUp(self):
    """Read in txt data"""
    testutils.GanetiTestCase.setUp(self)
    proc_data = testutils.TestDataFilename("proc_drbd8.txt")
    proc80e_data = testutils.TestDataFilename("proc_drbd80-emptyline.txt")
    proc83_data = testutils.TestDataFilename("proc_drbd83.txt")
    proc83_sync_data = testutils.TestDataFilename("proc_drbd83_sync.txt")
    proc83_sync_krnl_data = \
      testutils.TestDataFilename("proc_drbd83_sync_krnl2.6.39.txt")
    self.proc_data = bdev.DRBD8._GetProcData(filename=proc_data)
    self.proc80e_data = bdev.DRBD8._GetProcData(filename=proc80e_data)
    self.proc83_data = bdev.DRBD8._GetProcData(filename=proc83_data)
    self.proc83_sync_data = bdev.DRBD8._GetProcData(filename=proc83_sync_data)
    self.proc83_sync_krnl_data = \
      bdev.DRBD8._GetProcData(filename=proc83_sync_krnl_data)
    self.mass_data = bdev.DRBD8._MassageProcData(self.proc_data)
    self.mass80e_data = bdev.DRBD8._MassageProcData(self.proc80e_data)
    self.mass83_data = bdev.DRBD8._MassageProcData(self.proc83_data)
    self.mass83_sync_data = bdev.DRBD8._MassageProcData(self.proc83_sync_data)
    self.mass83_sync_krnl_data = \
      bdev.DRBD8._MassageProcData(self.proc83_sync_krnl_data)

  def testIOErrors(self):
    """Test handling of errors while reading the proc file."""
    temp_file = self._CreateTempFile()
    os.unlink(temp_file)
    self.failUnlessRaises(errors.BlockDeviceError,
                          bdev.DRBD8._GetProcData, filename=temp_file)

  def testHelper(self):
    """Test reading usermode_helper in /sys."""
    sys_drbd_helper = testutils.TestDataFilename("sys_drbd_usermode_helper.txt")
    drbd_helper = bdev.DRBD8.GetUsermodeHelper(filename=sys_drbd_helper)
    self.failUnlessEqual(drbd_helper, "/bin/true")

  def testHelperIOErrors(self):
    """Test handling of errors while reading usermode_helper in /sys."""
    temp_file = self._CreateTempFile()
    os.unlink(temp_file)
    self.failUnlessRaises(errors.BlockDeviceError,
                          bdev.DRBD8.GetUsermodeHelper, filename=temp_file)

  def testMinorNotFound(self):
    """Test not-found-minor in /proc"""
    self.failUnless(9 not in self.mass_data)
    self.failUnless(9 not in self.mass83_data)
    self.failUnless(3 not in self.mass80e_data)

  def testLineNotMatch(self):
    """Test wrong line passed to DRBD8Status"""
    self.assertRaises(errors.BlockDeviceError, bdev.DRBD8Status, "foo")

  def testMinor0(self):
    """Test connected, primary device"""
    for data in [self.mass_data, self.mass83_data]:
      stats = bdev.DRBD8Status(data[0])
      self.failUnless(stats.is_in_use)
      self.failUnless(stats.is_connected and stats.is_primary and
                      stats.peer_secondary and stats.is_disk_uptodate)

  def testMinor1(self):
    """Test connected, secondary device"""
    for data in [self.mass_data, self.mass83_data]:
      stats = bdev.DRBD8Status(data[1])
      self.failUnless(stats.is_in_use)
      self.failUnless(stats.is_connected and stats.is_secondary and
                      stats.peer_primary and stats.is_disk_uptodate)

  def testMinor2(self):
    """Test unconfigured device"""
    for data in [self.mass_data, self.mass83_data, self.mass80e_data]:
      stats = bdev.DRBD8Status(data[2])
      self.failIf(stats.is_in_use)

  def testMinor4(self):
    """Test WFconn device"""
    for data in [self.mass_data, self.mass83_data]:
      stats = bdev.DRBD8Status(data[4])
      self.failUnless(stats.is_in_use)
      self.failUnless(stats.is_wfconn and stats.is_primary and
                      stats.rrole == "Unknown" and
                      stats.is_disk_uptodate)

  def testMinor6(self):
    """Test diskless device"""
    for data in [self.mass_data, self.mass83_data]:
      stats = bdev.DRBD8Status(data[6])
      self.failUnless(stats.is_in_use)
      self.failUnless(stats.is_connected and stats.is_secondary and
                      stats.peer_primary and stats.is_diskless)

  def testMinor8(self):
    """Test standalone device"""
    for data in [self.mass_data, self.mass83_data]:
      stats = bdev.DRBD8Status(data[8])
      self.failUnless(stats.is_in_use)
      self.failUnless(stats.is_standalone and
                      stats.rrole == "Unknown" and
                      stats.is_disk_uptodate)

  def testDRBD83SyncFine(self):
    stats = bdev.DRBD8Status(self.mass83_sync_data[3])
    self.failUnless(stats.is_in_resync)
    self.failUnless(stats.sync_percent is not None)

  def testDRBD83SyncBroken(self):
    stats = bdev.DRBD8Status(self.mass83_sync_krnl_data[3])
    self.failUnless(stats.is_in_resync)
    self.failUnless(stats.sync_percent is not None)


class TestRADOSBlockDevice(testutils.GanetiTestCase):
  def setUp(self):
    """Set up input data"""
    testutils.GanetiTestCase.setUp(self)

    self.plain_output_old_ok = \
      testutils.ReadTestData("bdev-rbd/plain_output_old_ok.txt")
    self.plain_output_old_no_matches = \
      testutils.ReadTestData("bdev-rbd/plain_output_old_no_matches.txt")
    self.plain_output_old_extra_matches = \
      testutils.ReadTestData("bdev-rbd/plain_output_old_extra_matches.txt")
    self.plain_output_old_empty = \
      testutils.ReadTestData("bdev-rbd/plain_output_old_empty.txt")
    self.plain_output_new_ok = \
      testutils.ReadTestData("bdev-rbd/plain_output_new_ok.txt")
    self.plain_output_new_no_matches = \
      testutils.ReadTestData("bdev-rbd/plain_output_new_no_matches.txt")
    self.plain_output_new_extra_matches = \
      testutils.ReadTestData("bdev-rbd/plain_output_new_extra_matches.txt")
    # This file is completely empty, and as such it's not shipped.
    self.plain_output_new_empty = ""
    self.json_output_ok = testutils.ReadTestData("bdev-rbd/json_output_ok.txt")
    self.json_output_no_matches = \
      testutils.ReadTestData("bdev-rbd/json_output_no_matches.txt")
    self.json_output_extra_matches = \
      testutils.ReadTestData("bdev-rbd/json_output_extra_matches.txt")
    self.json_output_empty = \
      testutils.ReadTestData("bdev-rbd/json_output_empty.txt")
    self.output_invalid = testutils.ReadTestData("bdev-rbd/output_invalid.txt")

    self.volume_name = "d7ab910a-4933-4ffe-88d0-faf2ce31390a.rbd.disk0"

  def test_ParseRbdShowmappedJson(self):
    parse_function = bdev.RADOSBlockDevice._ParseRbdShowmappedJson

    self.assertEqual(parse_function(self.json_output_ok, self.volume_name),
                     "/dev/rbd3")
    self.assertEqual(parse_function(self.json_output_empty, self.volume_name),
                     None)
    self.assertEqual(parse_function(self.json_output_no_matches,
                     self.volume_name), None)
    self.assertRaises(errors.BlockDeviceError, parse_function,
                      self.json_output_extra_matches, self.volume_name)
    self.assertRaises(errors.BlockDeviceError, parse_function,
                      self.output_invalid, self.volume_name)

  def test_ParseRbdShowmappedPlain(self):
    parse_function = bdev.RADOSBlockDevice._ParseRbdShowmappedPlain

    self.assertEqual(parse_function(self.plain_output_new_ok,
                     self.volume_name), "/dev/rbd3")
    self.assertEqual(parse_function(self.plain_output_old_ok,
                     self.volume_name), "/dev/rbd3")
    self.assertEqual(parse_function(self.plain_output_new_empty,
                     self.volume_name), None)
    self.assertEqual(parse_function(self.plain_output_old_empty,
                     self.volume_name), None)
    self.assertEqual(parse_function(self.plain_output_new_no_matches,
                     self.volume_name), None)
    self.assertEqual(parse_function(self.plain_output_old_no_matches,
                     self.volume_name), None)
    self.assertRaises(errors.BlockDeviceError, parse_function,
                      self.plain_output_new_extra_matches, self.volume_name)
    self.assertRaises(errors.BlockDeviceError, parse_function,
                      self.plain_output_old_extra_matches, self.volume_name)
    self.assertRaises(errors.BlockDeviceError, parse_function,
                      self.output_invalid, self.volume_name)

class TestComputeWrongFileStoragePathsInternal(unittest.TestCase):
  def testPaths(self):
    paths = bdev._GetForbiddenFileStoragePaths()

    for path in ["/bin", "/usr/local/sbin", "/lib64", "/etc", "/sys"]:
      self.assertTrue(path in paths)

    self.assertEqual(set(map(os.path.normpath, paths)), paths)

  def test(self):
    vfsp = bdev._ComputeWrongFileStoragePaths
    self.assertEqual(vfsp([]), [])
    self.assertEqual(vfsp(["/tmp"]), [])
    self.assertEqual(vfsp(["/bin/ls"]), ["/bin/ls"])
    self.assertEqual(vfsp(["/bin"]), ["/bin"])
    self.assertEqual(vfsp(["/usr/sbin/vim", "/srv/file-storage"]),
                     ["/usr/sbin/vim"])


class TestComputeWrongFileStoragePaths(testutils.GanetiTestCase):
  def test(self):
    tmpfile = self._CreateTempFile()

    utils.WriteFile(tmpfile, data="""
      /tmp
      x/y///z/relative
      # This is a test file
      /srv/storage
      /bin
      /usr/local/lib32/
      relative/path
      """)

    self.assertEqual(bdev.ComputeWrongFileStoragePaths(_filename=tmpfile), [
      "/bin",
      "/usr/local/lib32",
      "relative/path",
      "x/y/z/relative",
      ])


class TestCheckFileStoragePathInternal(unittest.TestCase):
  def testNonAbsolute(self):
    for i in ["", "tmp", "foo/bar/baz"]:
      self.assertRaises(errors.FileStoragePathError,
                        bdev._CheckFileStoragePath, i, ["/tmp"])

    self.assertRaises(errors.FileStoragePathError,
                      bdev._CheckFileStoragePath, "/tmp", ["tmp", "xyz"])

  def testNoAllowed(self):
    self.assertRaises(errors.FileStoragePathError,
                      bdev._CheckFileStoragePath, "/tmp", [])

  def testNoAdditionalPathComponent(self):
    self.assertRaises(errors.FileStoragePathError,
                      bdev._CheckFileStoragePath, "/tmp/foo", ["/tmp/foo"])

  def testAllowed(self):
    bdev._CheckFileStoragePath("/tmp/foo/a", ["/tmp/foo"])
    bdev._CheckFileStoragePath("/tmp/foo/a/x", ["/tmp/foo"])


class TestCheckFileStoragePath(testutils.GanetiTestCase):
  def testNonExistantFile(self):
    filename = "/tmp/this/file/does/not/exist"
    assert not os.path.exists(filename)
    self.assertRaises(errors.FileStoragePathError,
                      bdev.CheckFileStoragePath, "/bin/", _filename=filename)
    self.assertRaises(errors.FileStoragePathError,
                      bdev.CheckFileStoragePath, "/srv/file-storage",
                      _filename=filename)

  def testAllowedPath(self):
    tmpfile = self._CreateTempFile()

    utils.WriteFile(tmpfile, data="""
      /srv/storage
      """)

    bdev.CheckFileStoragePath("/srv/storage/inst1", _filename=tmpfile)

    # No additional path component
    self.assertRaises(errors.FileStoragePathError,
                      bdev.CheckFileStoragePath, "/srv/storage",
                      _filename=tmpfile)

    # Forbidden path
    self.assertRaises(errors.FileStoragePathError,
                      bdev.CheckFileStoragePath, "/usr/lib64/xyz",
                      _filename=tmpfile)


class TestLoadAllowedFileStoragePaths(testutils.GanetiTestCase):
  def testDevNull(self):
    self.assertEqual(bdev._LoadAllowedFileStoragePaths("/dev/null"), [])

  def testNonExistantFile(self):
    filename = "/tmp/this/file/does/not/exist"
    assert not os.path.exists(filename)
    self.assertEqual(bdev._LoadAllowedFileStoragePaths(filename), [])

  def test(self):
    tmpfile = self._CreateTempFile()

    utils.WriteFile(tmpfile, data="""
      # This is a test file
      /tmp
      /srv/storage
      relative/path
      """)

    self.assertEqual(bdev._LoadAllowedFileStoragePaths(tmpfile), [
      "/tmp",
      "/srv/storage",
      "relative/path",
      ])


class TestExclusiveStoragePvs(unittest.TestCase):
  """Test cases for functions dealing with LVM PV and exclusive storage"""
  # Allowance for rounding
  _EPS = 1e-4
  _MARGIN = constants.PART_MARGIN + constants.PART_RESERVED + _EPS

  @staticmethod
  def _GenerateRandomPvInfo(rnd, name, vg):
    # Granularity is .01 MiB
    size = rnd.randint(1024 * 100, 10 * 1024 * 1024 * 100)
    if rnd.choice([False, True]):
      free = float(rnd.randint(0, size)) / 100.0
    else:
      free = float(size) / 100.0
    size = float(size) / 100.0
    attr = "a-"
    return objects.LvmPvInfo(name=name, vg_name=vg, size=size, free=free,
                             attributes=attr)

  def testGetStdPvSize(self):
    """Test cases for bdev.LogicalVolume._GetStdPvSize()"""
    rnd = random.Random(9517)
    for _ in range(0, 50):
      # Identical volumes
      pvi = self._GenerateRandomPvInfo(rnd, "disk", "myvg")
      onesize = bdev.LogicalVolume._GetStdPvSize([pvi])
      self.assertTrue(onesize <= pvi.size)
      self.assertTrue(onesize > pvi.size * (1 - self._MARGIN))
      for length in range(2, 10):
        n_size = bdev.LogicalVolume._GetStdPvSize([pvi] * length)
        self.assertEqual(onesize, n_size)

      # Mixed volumes
      for length in range(1, 10):
        pvlist = [self._GenerateRandomPvInfo(rnd, "disk", "myvg")
                  for _ in range(0, length)]
        std_size = bdev.LogicalVolume._GetStdPvSize(pvlist)
        self.assertTrue(compat.all(std_size <= pvi.size for pvi in pvlist))
        self.assertTrue(compat.any(std_size > pvi.size * (1 - self._MARGIN)
                                   for pvi in pvlist))
        pvlist.append(pvlist[0])
        p1_size = bdev.LogicalVolume._GetStdPvSize(pvlist)
        self.assertEqual(std_size, p1_size)

  def testComputeNumPvs(self):
    """Test cases for bdev.LogicalVolume._ComputeNumPvs()"""
    rnd = random.Random(8067)
    for _ in range(0, 1000):
      pvlist = [self._GenerateRandomPvInfo(rnd, "disk", "myvg")]
      lv_size = float(rnd.randint(10 * 100, 1024 * 1024 * 100)) / 100.0
      num_pv = bdev.LogicalVolume._ComputeNumPvs(lv_size, pvlist)
      std_size = bdev.LogicalVolume._GetStdPvSize(pvlist)
      self.assertTrue(num_pv >= 1)
      self.assertTrue(num_pv * std_size >= lv_size)
      self.assertTrue((num_pv - 1) * std_size < lv_size * (1 + self._EPS))

  def testGetEmptyPvNames(self):
    """Test cases for bdev.LogicalVolume._GetEmptyPvNames()"""
    rnd = random.Random(21126)
    for _ in range(0, 100):
      num_pvs = rnd.randint(1, 20)
      pvlist = [self._GenerateRandomPvInfo(rnd, "disk%d" % n, "myvg")
                for n in range(0, num_pvs)]
      for num_req in range(1, num_pvs + 2):
        epvs = bdev.LogicalVolume._GetEmptyPvNames(pvlist, num_req)
        epvs_set = compat.UniqueFrozenset(epvs)
        if len(epvs) > 1:
          self.assertEqual(len(epvs), len(epvs_set))
        for pvi in pvlist:
          if pvi.name in epvs_set:
            self.assertEqual(pvi.size, pvi.free)
          else:
            # There should be no remaining empty PV when less than the
            # requeste number of PVs has been returned
            self.assertTrue(len(epvs) == num_req or pvi.free != pvi.size)


if __name__ == "__main__":
  testutils.GanetiTestProgram()
