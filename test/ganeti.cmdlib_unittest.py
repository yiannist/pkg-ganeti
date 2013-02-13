#!/usr/bin/python
#

# Copyright (C) 2008, 2011, 2012 Google Inc.
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


"""Script for unittesting the cmdlib module"""


import os
import unittest
import time
import tempfile
import shutil
import operator
import itertools
import copy

from ganeti import constants
from ganeti import mcpu
from ganeti import cmdlib
from ganeti import opcodes
from ganeti import errors
from ganeti import utils
from ganeti import luxi
from ganeti import ht
from ganeti import objects
from ganeti import compat
from ganeti import rpc
from ganeti.hypervisor import hv_xen

import testutils
import mocks


class TestCertVerification(testutils.GanetiTestCase):
  def setUp(self):
    testutils.GanetiTestCase.setUp(self)

    self.tmpdir = tempfile.mkdtemp()

  def tearDown(self):
    shutil.rmtree(self.tmpdir)

  def testVerifyCertificate(self):
    cmdlib._VerifyCertificate(self._TestDataFilename("cert1.pem"))

    nonexist_filename = os.path.join(self.tmpdir, "does-not-exist")

    (errcode, msg) = cmdlib._VerifyCertificate(nonexist_filename)
    self.assertEqual(errcode, cmdlib.LUClusterVerifyConfig.ETYPE_ERROR)

    # Try to load non-certificate file
    invalid_cert = self._TestDataFilename("bdev-net.txt")
    (errcode, msg) = cmdlib._VerifyCertificate(invalid_cert)
    self.assertEqual(errcode, cmdlib.LUClusterVerifyConfig.ETYPE_ERROR)


class TestOpcodeParams(testutils.GanetiTestCase):
  def testParamsStructures(self):
    for op in sorted(mcpu.Processor.DISPATCH_TABLE):
      lu = mcpu.Processor.DISPATCH_TABLE[op]
      lu_name = lu.__name__
      self.failIf(hasattr(lu, "_OP_REQP"),
                  msg=("LU '%s' has old-style _OP_REQP" % lu_name))
      self.failIf(hasattr(lu, "_OP_DEFS"),
                  msg=("LU '%s' has old-style _OP_DEFS" % lu_name))
      self.failIf(hasattr(lu, "_OP_PARAMS"),
                  msg=("LU '%s' has old-style _OP_PARAMS" % lu_name))


class TestIAllocatorChecks(testutils.GanetiTestCase):
  def testFunction(self):
    class TestLU(object):
      def __init__(self, opcode):
        self.cfg = mocks.FakeConfig()
        self.op = opcode

    class OpTest(opcodes.OpCode):
       OP_PARAMS = [
        ("iallocator", None, ht.NoType, None),
        ("node", None, ht.NoType, None),
        ]

    default_iallocator = mocks.FakeConfig().GetDefaultIAllocator()
    other_iallocator = default_iallocator + "_not"

    op = OpTest()
    lu = TestLU(op)

    c_i = lambda: cmdlib._CheckIAllocatorOrNode(lu, "iallocator", "node")

    # Neither node nor iallocator given
    op.iallocator = None
    op.node = None
    c_i()
    self.assertEqual(lu.op.iallocator, default_iallocator)
    self.assertEqual(lu.op.node, None)

    # Both, iallocator and node given
    op.iallocator = "test"
    op.node = "test"
    self.assertRaises(errors.OpPrereqError, c_i)

    # Only iallocator given
    op.iallocator = other_iallocator
    op.node = None
    c_i()
    self.assertEqual(lu.op.iallocator, other_iallocator)
    self.assertEqual(lu.op.node, None)

    # Only node given
    op.iallocator = None
    op.node = "node"
    c_i()
    self.assertEqual(lu.op.iallocator, None)
    self.assertEqual(lu.op.node, "node")

    # No node, iallocator or default iallocator
    op.iallocator = None
    op.node = None
    lu.cfg.GetDefaultIAllocator = lambda: None
    self.assertRaises(errors.OpPrereqError, c_i)


class TestLUTestJqueue(unittest.TestCase):
  def test(self):
    self.assert_(cmdlib.LUTestJqueue._CLIENT_CONNECT_TIMEOUT <
                 (luxi.WFJC_TIMEOUT * 0.75),
                 msg=("Client timeout too high, might not notice bugs"
                      " in WaitForJobChange"))


class TestLUQuery(unittest.TestCase):
  def test(self):
    self.assertEqual(sorted(cmdlib._QUERY_IMPL.keys()),
                     sorted(constants.QR_VIA_OP))

    assert constants.QR_NODE in constants.QR_VIA_OP
    assert constants.QR_INSTANCE in constants.QR_VIA_OP

    for i in constants.QR_VIA_OP:
      self.assert_(cmdlib._GetQueryImplementation(i))

    self.assertRaises(errors.OpPrereqError, cmdlib._GetQueryImplementation, "")
    self.assertRaises(errors.OpPrereqError, cmdlib._GetQueryImplementation,
                      "xyz")


class TestLUGroupAssignNodes(unittest.TestCase):

  def testCheckAssignmentForSplitInstances(self):
    node_data = dict((name, objects.Node(name=name, group=group))
                     for (name, group) in [("n1a", "g1"), ("n1b", "g1"),
                                           ("n2a", "g2"), ("n2b", "g2"),
                                           ("n3a", "g3"), ("n3b", "g3"),
                                           ("n3c", "g3"),
                                           ])

    def Instance(name, pnode, snode):
      if snode is None:
        disks = []
        disk_template = constants.DT_DISKLESS
      else:
        disks = [objects.Disk(dev_type=constants.LD_DRBD8,
                              logical_id=[pnode, snode, 1, 17, 17])]
        disk_template = constants.DT_DRBD8

      return objects.Instance(name=name, primary_node=pnode, disks=disks,
                              disk_template=disk_template)

    instance_data = dict((name, Instance(name, pnode, snode))
                         for name, pnode, snode in [("inst1a", "n1a", "n1b"),
                                                    ("inst1b", "n1b", "n1a"),
                                                    ("inst2a", "n2a", "n2b"),
                                                    ("inst3a", "n3a", None),
                                                    ("inst3b", "n3b", "n1b"),
                                                    ("inst3c", "n3b", "n2b"),
                                                    ])

    # Test first with the existing state.
    (new, prev) = \
      cmdlib.LUGroupAssignNodes.CheckAssignmentForSplitInstances([],
                                                                 node_data,
                                                                 instance_data)

    self.assertEqual([], new)
    self.assertEqual(set(["inst3b", "inst3c"]), set(prev))

    # And now some changes.
    (new, prev) = \
      cmdlib.LUGroupAssignNodes.CheckAssignmentForSplitInstances([("n1b",
                                                                   "g3")],
                                                                 node_data,
                                                                 instance_data)

    self.assertEqual(set(["inst1a", "inst1b"]), set(new))
    self.assertEqual(set(["inst3c"]), set(prev))


class TestClusterVerifySsh(unittest.TestCase):
  def testMultipleGroups(self):
    fn = cmdlib.LUClusterVerifyGroup._SelectSshCheckNodes
    mygroupnodes = [
      objects.Node(name="node20", group="my", offline=False),
      objects.Node(name="node21", group="my", offline=False),
      objects.Node(name="node22", group="my", offline=False),
      objects.Node(name="node23", group="my", offline=False),
      objects.Node(name="node24", group="my", offline=False),
      objects.Node(name="node25", group="my", offline=False),
      objects.Node(name="node26", group="my", offline=True),
      ]
    nodes = [
      objects.Node(name="node1", group="g1", offline=True),
      objects.Node(name="node2", group="g1", offline=False),
      objects.Node(name="node3", group="g1", offline=False),
      objects.Node(name="node4", group="g1", offline=True),
      objects.Node(name="node5", group="g1", offline=False),
      objects.Node(name="node10", group="xyz", offline=False),
      objects.Node(name="node11", group="xyz", offline=False),
      objects.Node(name="node40", group="alloff", offline=True),
      objects.Node(name="node41", group="alloff", offline=True),
      objects.Node(name="node50", group="aaa", offline=False),
      ] + mygroupnodes
    assert not utils.FindDuplicates(map(operator.attrgetter("name"), nodes))

    (online, perhost) = fn(mygroupnodes, "my", nodes)
    self.assertEqual(online, ["node%s" % i for i in range(20, 26)])
    self.assertEqual(set(perhost.keys()), set(online))

    self.assertEqual(perhost, {
      "node20": ["node10", "node2", "node50"],
      "node21": ["node11", "node3", "node50"],
      "node22": ["node10", "node5", "node50"],
      "node23": ["node11", "node2", "node50"],
      "node24": ["node10", "node3", "node50"],
      "node25": ["node11", "node5", "node50"],
      })

  def testSingleGroup(self):
    fn = cmdlib.LUClusterVerifyGroup._SelectSshCheckNodes
    nodes = [
      objects.Node(name="node1", group="default", offline=True),
      objects.Node(name="node2", group="default", offline=False),
      objects.Node(name="node3", group="default", offline=False),
      objects.Node(name="node4", group="default", offline=True),
      ]
    assert not utils.FindDuplicates(map(operator.attrgetter("name"), nodes))

    (online, perhost) = fn(nodes, "default", nodes)
    self.assertEqual(online, ["node2", "node3"])
    self.assertEqual(set(perhost.keys()), set(online))

    self.assertEqual(perhost, {
      "node2": [],
      "node3": [],
      })


class TestClusterVerifyFiles(unittest.TestCase):
  @staticmethod
  def _FakeErrorIf(errors, cond, ecode, item, msg, *args, **kwargs):
    assert ((ecode == constants.CV_ENODEFILECHECK and
             ht.TNonEmptyString(item)) or
            (ecode == constants.CV_ECLUSTERFILECHECK and
             item is None))

    if args:
      msg = msg % args

    if cond:
      errors.append((item, msg))

  _VerifyFiles = cmdlib.LUClusterVerifyGroup._VerifyFiles

  def test(self):
    errors = []
    master_name = "master.example.com"
    nodeinfo = [
      objects.Node(name=master_name, offline=False, vm_capable=True),
      objects.Node(name="node2.example.com", offline=False, vm_capable=True),
      objects.Node(name="node3.example.com", master_candidate=True,
                   vm_capable=False),
      objects.Node(name="node4.example.com", offline=False, vm_capable=True),
      objects.Node(name="nodata.example.com", offline=False, vm_capable=True),
      objects.Node(name="offline.example.com", offline=True),
      ]
    cluster = objects.Cluster(modify_etc_hosts=True,
                              enabled_hypervisors=[constants.HT_XEN_HVM])
    files_all = set([
      constants.CLUSTER_DOMAIN_SECRET_FILE,
      constants.RAPI_CERT_FILE,
      constants.RAPI_USERS_FILE,
      ])
    files_opt = set([
      constants.RAPI_USERS_FILE,
      hv_xen.XL_CONFIG_FILE,
      constants.VNC_PASSWORD_FILE,
      ])
    files_mc = set([
      constants.CLUSTER_CONF_FILE,
      ])
    files_vm = set([
      hv_xen.XEND_CONFIG_FILE,
      hv_xen.XL_CONFIG_FILE,
      constants.VNC_PASSWORD_FILE,
      ])
    nvinfo = {
      master_name: rpc.RpcResult(data=(True, {
        constants.NV_FILELIST: {
          constants.CLUSTER_CONF_FILE: "82314f897f38b35f9dab2f7c6b1593e0",
          constants.RAPI_CERT_FILE: "babbce8f387bc082228e544a2146fee4",
          constants.CLUSTER_DOMAIN_SECRET_FILE: "cds-47b5b3f19202936bb4",
          hv_xen.XEND_CONFIG_FILE: "b4a8a824ab3cac3d88839a9adeadf310",
          hv_xen.XL_CONFIG_FILE: "77935cee92afd26d162f9e525e3d49b9"
        }})),
      "node2.example.com": rpc.RpcResult(data=(True, {
        constants.NV_FILELIST: {
          constants.RAPI_CERT_FILE: "97f0356500e866387f4b84233848cc4a",
          hv_xen.XEND_CONFIG_FILE: "b4a8a824ab3cac3d88839a9adeadf310",
          }
        })),
      "node3.example.com": rpc.RpcResult(data=(True, {
        constants.NV_FILELIST: {
          constants.RAPI_CERT_FILE: "97f0356500e866387f4b84233848cc4a",
          constants.CLUSTER_DOMAIN_SECRET_FILE: "cds-47b5b3f19202936bb4",
          }
        })),
      "node4.example.com": rpc.RpcResult(data=(True, {
        constants.NV_FILELIST: {
          constants.RAPI_CERT_FILE: "97f0356500e866387f4b84233848cc4a",
          constants.CLUSTER_CONF_FILE: "conf-a6d4b13e407867f7a7b4f0f232a8f527",
          constants.CLUSTER_DOMAIN_SECRET_FILE: "cds-47b5b3f19202936bb4",
          constants.RAPI_USERS_FILE: "rapiusers-ea3271e8d810ef3",
          hv_xen.XL_CONFIG_FILE: "77935cee92afd26d162f9e525e3d49b9"
          }
        })),
      "nodata.example.com": rpc.RpcResult(data=(True, {})),
      "offline.example.com": rpc.RpcResult(offline=True),
      }
    assert set(nvinfo.keys()) == set(map(operator.attrgetter("name"), nodeinfo))

    self._VerifyFiles(compat.partial(self._FakeErrorIf, errors), nodeinfo,
                      master_name, nvinfo,
                      (files_all, files_opt, files_mc, files_vm))
    self.assertEqual(sorted(errors), sorted([
      (None, ("File %s found with 2 different checksums (variant 1 on"
              " node2.example.com, node3.example.com, node4.example.com;"
              " variant 2 on master.example.com)" % constants.RAPI_CERT_FILE)),
      (None, ("File %s is missing from node(s) node2.example.com" %
              constants.CLUSTER_DOMAIN_SECRET_FILE)),
      (None, ("File %s should not exist on node(s) node4.example.com" %
              constants.CLUSTER_CONF_FILE)),
      (None, ("File %s is missing from node(s) node4.example.com" %
              hv_xen.XEND_CONFIG_FILE)),
      (None, ("File %s is missing from node(s) node3.example.com" %
              constants.CLUSTER_CONF_FILE)),
      (None, ("File %s found with 2 different checksums (variant 1 on"
              " master.example.com; variant 2 on node4.example.com)" %
              constants.CLUSTER_CONF_FILE)),
      (None, ("File %s is optional, but it must exist on all or no nodes (not"
              " found on master.example.com, node2.example.com,"
              " node3.example.com)" % constants.RAPI_USERS_FILE)),
      (None, ("File %s is optional, but it must exist on all or no nodes (not"
              " found on node2.example.com)" % hv_xen.XL_CONFIG_FILE)),
      ("nodata.example.com", "Node did not return file checksum data"),
      ]))


class _FakeLU:
  def __init__(self, cfg=NotImplemented, proc=NotImplemented):
    self.warning_log = []
    self.info_log = []
    self.cfg = cfg
    self.proc = proc

  def LogWarning(self, text, *args):
    self.warning_log.append((text, args))

  def LogInfo(self, text, *args):
    self.info_log.append((text, args))


class TestLoadNodeEvacResult(unittest.TestCase):
  def testSuccess(self):
    for moved in [[], [
      ("inst20153.example.com", "grp2", ["nodeA4509", "nodeB2912"]),
      ]]:
      for early_release in [False, True]:
        for use_nodes in [False, True]:
          jobs = [
            [opcodes.OpInstanceReplaceDisks().__getstate__()],
            [opcodes.OpInstanceMigrate().__getstate__()],
            ]

          alloc_result = (moved, [], jobs)
          assert cmdlib.IAllocator._NEVAC_RESULT(alloc_result)

          lu = _FakeLU()
          result = cmdlib._LoadNodeEvacResult(lu, alloc_result,
                                              early_release, use_nodes)

          if moved:
            (_, (info_args, )) = lu.info_log.pop(0)
            for (instname, instgroup, instnodes) in moved:
              self.assertTrue(instname in info_args)
              if use_nodes:
                for i in instnodes:
                  self.assertTrue(i in info_args)
              else:
                self.assertTrue(instgroup in info_args)

          self.assertFalse(lu.info_log)
          self.assertFalse(lu.warning_log)

          for op in itertools.chain(*result):
            if hasattr(op.__class__, "early_release"):
              self.assertEqual(op.early_release, early_release)
            else:
              self.assertFalse(hasattr(op, "early_release"))

  def testFailed(self):
    alloc_result = ([], [
      ("inst5191.example.com", "errormsg21178"),
      ], [])
    assert cmdlib.IAllocator._NEVAC_RESULT(alloc_result)

    lu = _FakeLU()
    self.assertRaises(errors.OpExecError, cmdlib._LoadNodeEvacResult,
                      lu, alloc_result, False, False)
    self.assertFalse(lu.info_log)
    (_, (args, )) = lu.warning_log.pop(0)
    self.assertTrue("inst5191.example.com" in args)
    self.assertTrue("errormsg21178" in args)
    self.assertFalse(lu.warning_log)


class TestUpdateAndVerifySubDict(unittest.TestCase):
  def setUp(self):
    self.type_check = {
        "a": constants.VTYPE_INT,
        "b": constants.VTYPE_STRING,
        "c": constants.VTYPE_BOOL,
        "d": constants.VTYPE_STRING,
        }

  def test(self):
    old_test = {
      "foo": {
        "d": "blubb",
        "a": 321,
        },
      "baz": {
        "a": 678,
        "b": "678",
        "c": True,
        },
      }
    test = {
      "foo": {
        "a": 123,
        "b": "123",
        "c": True,
        },
      "bar": {
        "a": 321,
        "b": "321",
        "c": False,
        },
      }

    mv = {
      "foo": {
        "a": 123,
        "b": "123",
        "c": True,
        "d": "blubb"
        },
      "bar": {
        "a": 321,
        "b": "321",
        "c": False,
        },
      "baz": {
        "a": 678,
        "b": "678",
        "c": True,
        },
      }

    verified = cmdlib._UpdateAndVerifySubDict(old_test, test, self.type_check)
    self.assertEqual(verified, mv)

  def testWrong(self):
    test = {
      "foo": {
        "a": "blubb",
        "b": "123",
        "c": True,
        },
      "bar": {
        "a": 321,
        "b": "321",
        "c": False,
        },
      }

    self.assertRaises(errors.TypeEnforcementError,
                      cmdlib._UpdateAndVerifySubDict, {}, test, self.type_check)


class TestHvStateHelper(unittest.TestCase):
  def testWithoutOpData(self):
    self.assertEqual(cmdlib._MergeAndVerifyHvState(None, NotImplemented), None)

  def testWithoutOldData(self):
    new = {
      constants.HT_XEN_PVM: {
        constants.HVST_MEMORY_TOTAL: 4096,
        },
      }
    self.assertEqual(cmdlib._MergeAndVerifyHvState(new, None), new)

  def testWithWrongHv(self):
    new = {
      "i-dont-exist": {
        constants.HVST_MEMORY_TOTAL: 4096,
        },
      }
    self.assertRaises(errors.OpPrereqError, cmdlib._MergeAndVerifyHvState, new,
                      None)

class TestDiskStateHelper(unittest.TestCase):
  def testWithoutOpData(self):
    self.assertEqual(cmdlib._MergeAndVerifyDiskState(None, NotImplemented),
                     None)

  def testWithoutOldData(self):
    new = {
      constants.LD_LV: {
        "xenvg": {
          constants.DS_DISK_RESERVED: 1024,
          },
        },
      }
    self.assertEqual(cmdlib._MergeAndVerifyDiskState(new, None), new)

  def testWithWrongStorageType(self):
    new = {
      "i-dont-exist": {
        "xenvg": {
          constants.DS_DISK_RESERVED: 1024,
          },
        },
      }
    self.assertRaises(errors.OpPrereqError, cmdlib._MergeAndVerifyDiskState,
                      new, None)


class TestComputeMinMaxSpec(unittest.TestCase):
  def setUp(self):
    self.ipolicy = {
      constants.ISPECS_MAX: {
        constants.ISPEC_MEM_SIZE: 512,
        constants.ISPEC_DISK_SIZE: 1024,
        },
      constants.ISPECS_MIN: {
        constants.ISPEC_MEM_SIZE: 128,
        constants.ISPEC_DISK_COUNT: 1,
        },
      }

  def testNoneValue(self):
    self.assertTrue(cmdlib._ComputeMinMaxSpec(constants.ISPEC_MEM_SIZE, None,
                                              self.ipolicy, None) is None)

  def testAutoValue(self):
    self.assertTrue(cmdlib._ComputeMinMaxSpec(constants.ISPEC_MEM_SIZE, None,
                                              self.ipolicy,
                                              constants.VALUE_AUTO) is None)

  def testNotDefined(self):
    self.assertTrue(cmdlib._ComputeMinMaxSpec(constants.ISPEC_NIC_COUNT, None,
                                              self.ipolicy, 3) is None)

  def testNoMinDefined(self):
    self.assertTrue(cmdlib._ComputeMinMaxSpec(constants.ISPEC_DISK_SIZE, None,
                                              self.ipolicy, 128) is None)

  def testNoMaxDefined(self):
    self.assertTrue(cmdlib._ComputeMinMaxSpec(constants.ISPEC_DISK_COUNT, None,
                                                self.ipolicy, 16) is None)

  def testOutOfRange(self):
    for (name, val) in ((constants.ISPEC_MEM_SIZE, 64),
                        (constants.ISPEC_MEM_SIZE, 768),
                        (constants.ISPEC_DISK_SIZE, 4096),
                        (constants.ISPEC_DISK_COUNT, 0)):
      min_v = self.ipolicy[constants.ISPECS_MIN].get(name, val)
      max_v = self.ipolicy[constants.ISPECS_MAX].get(name, val)
      self.assertEqual(cmdlib._ComputeMinMaxSpec(name, None,
                                                 self.ipolicy, val),
                       "%s value %s is not in range [%s, %s]" %
                       (name, val,min_v, max_v))
      self.assertEqual(cmdlib._ComputeMinMaxSpec(name, "1",
                                                 self.ipolicy, val),
                       "%s/1 value %s is not in range [%s, %s]" %
                       (name, val,min_v, max_v))

  def test(self):
    for (name, val) in ((constants.ISPEC_MEM_SIZE, 256),
                        (constants.ISPEC_MEM_SIZE, 128),
                        (constants.ISPEC_MEM_SIZE, 512),
                        (constants.ISPEC_DISK_SIZE, 1024),
                        (constants.ISPEC_DISK_SIZE, 0),
                        (constants.ISPEC_DISK_COUNT, 1),
                        (constants.ISPEC_DISK_COUNT, 5)):
      self.assertTrue(cmdlib._ComputeMinMaxSpec(name, None, self.ipolicy, val)
                      is None)


def _ValidateComputeMinMaxSpec(name, *_):
  assert name in constants.ISPECS_PARAMETERS
  return None


class _SpecWrapper:
  def __init__(self, spec):
    self.spec = spec

  def ComputeMinMaxSpec(self, *args):
    return self.spec.pop(0)


class TestComputeIPolicySpecViolation(unittest.TestCase):
  def test(self):
    compute_fn = _ValidateComputeMinMaxSpec
    ret = cmdlib._ComputeIPolicySpecViolation(NotImplemented, 1024, 1, 1, 1,
                                              [1024], 1, _compute_fn=compute_fn)
    self.assertEqual(ret, [])

  def testInvalidArguments(self):
    self.assertRaises(AssertionError, cmdlib._ComputeIPolicySpecViolation,
                      NotImplemented, 1024, 1, 1, 1, [], 1)

  def testInvalidSpec(self):
    spec = _SpecWrapper([None, False, "foo", None, "bar", None])
    compute_fn = spec.ComputeMinMaxSpec
    ret = cmdlib._ComputeIPolicySpecViolation(NotImplemented, 1024, 1, 1, 1,
                                              [1024], 1, _compute_fn=compute_fn)
    self.assertEqual(ret, ["foo", "bar"])
    self.assertFalse(spec.spec)


class _StubComputeIPolicySpecViolation:
  def __init__(self, mem_size, cpu_count, disk_count, nic_count, disk_sizes,
               spindle_use):
    self.mem_size = mem_size
    self.cpu_count = cpu_count
    self.disk_count = disk_count
    self.nic_count = nic_count
    self.disk_sizes = disk_sizes
    self.spindle_use = spindle_use

  def __call__(self, _, mem_size, cpu_count, disk_count, nic_count, disk_sizes,
               spindle_use):
    assert self.mem_size == mem_size
    assert self.cpu_count == cpu_count
    assert self.disk_count == disk_count
    assert self.nic_count == nic_count
    assert self.disk_sizes == disk_sizes
    assert self.spindle_use == spindle_use

    return []


class TestComputeIPolicyInstanceViolation(unittest.TestCase):
  def test(self):
    beparams = {
      constants.BE_MAXMEM: 2048,
      constants.BE_VCPUS: 2,
      constants.BE_SPINDLE_USE: 4,
      }
    disks = [objects.Disk(size=512)]
    instance = objects.Instance(beparams=beparams, disks=disks, nics=[])
    stub = _StubComputeIPolicySpecViolation(2048, 2, 1, 0, [512], 4)
    ret = cmdlib._ComputeIPolicyInstanceViolation(NotImplemented, instance,
                                                  _compute_fn=stub)
    self.assertEqual(ret, [])


class TestComputeIPolicyInstanceSpecViolation(unittest.TestCase):
  def test(self):
    ispec = {
      constants.ISPEC_MEM_SIZE: 2048,
      constants.ISPEC_CPU_COUNT: 2,
      constants.ISPEC_DISK_COUNT: 1,
      constants.ISPEC_DISK_SIZE: [512],
      constants.ISPEC_NIC_COUNT: 0,
      constants.ISPEC_SPINDLE_USE: 1,
      }
    stub = _StubComputeIPolicySpecViolation(2048, 2, 1, 0, [512], 1)
    ret = cmdlib._ComputeIPolicyInstanceSpecViolation(NotImplemented, ispec,
                                                      _compute_fn=stub)
    self.assertEqual(ret, [])


class _CallRecorder:
  def __init__(self, return_value=None):
    self.called = False
    self.return_value = return_value

  def __call__(self, *args):
    self.called = True
    return self.return_value


class TestComputeIPolicyNodeViolation(unittest.TestCase):
  def setUp(self):
    self.recorder = _CallRecorder(return_value=[])

  def testSameGroup(self):
    ret = cmdlib._ComputeIPolicyNodeViolation(NotImplemented, NotImplemented,
                                              "foo", "foo",
                                              _compute_fn=self.recorder)
    self.assertFalse(self.recorder.called)
    self.assertEqual(ret, [])

  def testDifferentGroup(self):
    ret = cmdlib._ComputeIPolicyNodeViolation(NotImplemented, NotImplemented,
                                              "foo", "bar",
                                              _compute_fn=self.recorder)
    self.assertTrue(self.recorder.called)
    self.assertEqual(ret, [])


class _FakeConfigForTargetNodeIPolicy:
  def __init__(self, node_info=NotImplemented):
    self._node_info = node_info

  def GetNodeInfo(self, _):
    return self._node_info


class TestCheckTargetNodeIPolicy(unittest.TestCase):
  def setUp(self):
    self.instance = objects.Instance(primary_node="blubb")
    self.target_node = objects.Node(group="bar")
    node_info = objects.Node(group="foo")
    fake_cfg = _FakeConfigForTargetNodeIPolicy(node_info=node_info)
    self.lu = _FakeLU(cfg=fake_cfg)

  def testNoViolation(self):
    compute_recoder = _CallRecorder(return_value=[])
    cmdlib._CheckTargetNodeIPolicy(self.lu, NotImplemented, self.instance,
                                   self.target_node,
                                   _compute_fn=compute_recoder)
    self.assertTrue(compute_recoder.called)
    self.assertEqual(self.lu.warning_log, [])

  def testNoIgnore(self):
    compute_recoder = _CallRecorder(return_value=["mem_size not in range"])
    self.assertRaises(errors.OpPrereqError, cmdlib._CheckTargetNodeIPolicy,
                      self.lu, NotImplemented, self.instance, self.target_node,
                      _compute_fn=compute_recoder)
    self.assertTrue(compute_recoder.called)
    self.assertEqual(self.lu.warning_log, [])

  def testIgnoreViolation(self):
    compute_recoder = _CallRecorder(return_value=["mem_size not in range"])
    cmdlib._CheckTargetNodeIPolicy(self.lu, NotImplemented, self.instance,
                                   self.target_node, ignore=True,
                                   _compute_fn=compute_recoder)
    self.assertTrue(compute_recoder.called)
    msg = ("Instance does not meet target node group's (bar) instance policy:"
           " mem_size not in range")
    self.assertEqual(self.lu.warning_log, [(msg, ())])


class TestApplyContainerMods(unittest.TestCase):
  def testEmptyContainer(self):
    container = []
    chgdesc = []
    cmdlib.ApplyContainerMods("test", container, chgdesc, [], None, None, None)
    self.assertEqual(container, [])
    self.assertEqual(chgdesc, [])

  def testAdd(self):
    container = []
    chgdesc = []
    mods = cmdlib.PrepareContainerMods([
      (constants.DDM_ADD, -1, "Hello"),
      (constants.DDM_ADD, -1, "World"),
      (constants.DDM_ADD, 0, "Start"),
      (constants.DDM_ADD, -1, "End"),
      ], None)
    cmdlib.ApplyContainerMods("test", container, chgdesc, mods,
                              None, None, None)
    self.assertEqual(container, ["Start", "Hello", "World", "End"])
    self.assertEqual(chgdesc, [])

    mods = cmdlib.PrepareContainerMods([
      (constants.DDM_ADD, 0, "zero"),
      (constants.DDM_ADD, 3, "Added"),
      (constants.DDM_ADD, 5, "four"),
      (constants.DDM_ADD, 7, "xyz"),
      ], None)
    cmdlib.ApplyContainerMods("test", container, chgdesc, mods,
                              None, None, None)
    self.assertEqual(container,
                     ["zero", "Start", "Hello", "Added", "World", "four",
                      "End", "xyz"])
    self.assertEqual(chgdesc, [])

    for idx in [-2, len(container) + 1]:
      mods = cmdlib.PrepareContainerMods([
        (constants.DDM_ADD, idx, "error"),
        ], None)
      self.assertRaises(IndexError, cmdlib.ApplyContainerMods,
                        "test", container, None, mods, None, None, None)

  def testRemoveError(self):
    for idx in [0, 1, 2, 100, -1, -4]:
      mods = cmdlib.PrepareContainerMods([
        (constants.DDM_REMOVE, idx, None),
        ], None)
      self.assertRaises(IndexError, cmdlib.ApplyContainerMods,
                        "test", [], None, mods, None, None, None)

    mods = cmdlib.PrepareContainerMods([
      (constants.DDM_REMOVE, 0, object()),
      ], None)
    self.assertRaises(AssertionError, cmdlib.ApplyContainerMods,
                      "test", [""], None, mods, None, None, None)

  def testAddError(self):
    for idx in range(-100, -1) + [100]:
      mods = cmdlib.PrepareContainerMods([
        (constants.DDM_ADD, idx, None),
        ], None)
      self.assertRaises(IndexError, cmdlib.ApplyContainerMods,
                        "test", [], None, mods, None, None, None)

  def testRemove(self):
    container = ["item 1", "item 2"]
    mods = cmdlib.PrepareContainerMods([
      (constants.DDM_ADD, -1, "aaa"),
      (constants.DDM_REMOVE, -1, None),
      (constants.DDM_ADD, -1, "bbb"),
      ], None)
    chgdesc = []
    cmdlib.ApplyContainerMods("test", container, chgdesc, mods,
                              None, None, None)
    self.assertEqual(container, ["item 1", "item 2", "bbb"])
    self.assertEqual(chgdesc, [
      ("test/2", "remove"),
      ])

  def testModify(self):
    container = ["item 1", "item 2"]
    mods = cmdlib.PrepareContainerMods([
      (constants.DDM_MODIFY, -1, "a"),
      (constants.DDM_MODIFY, 0, "b"),
      (constants.DDM_MODIFY, 1, "c"),
      ], None)
    chgdesc = []
    cmdlib.ApplyContainerMods("test", container, chgdesc, mods,
                              None, None, None)
    self.assertEqual(container, ["item 1", "item 2"])
    self.assertEqual(chgdesc, [])

    for idx in [-2, len(container) + 1]:
      mods = cmdlib.PrepareContainerMods([
        (constants.DDM_MODIFY, idx, "error"),
        ], None)
      self.assertRaises(IndexError, cmdlib.ApplyContainerMods,
                        "test", container, None, mods, None, None, None)

  class _PrivateData:
    def __init__(self):
      self.data = None

  @staticmethod
  def _CreateTestFn(idx, params, private):
    private.data = ("add", idx, params)
    return ((100 * idx, params), [
      ("test/%s" % idx, hex(idx)),
      ])

  @staticmethod
  def _ModifyTestFn(idx, item, params, private):
    private.data = ("modify", idx, params)
    return [
      ("test/%s" % idx, "modify %s" % params),
      ]

  @staticmethod
  def _RemoveTestFn(idx, item, private):
    private.data = ("remove", idx, item)

  def testAddWithCreateFunction(self):
    container = []
    chgdesc = []
    mods = cmdlib.PrepareContainerMods([
      (constants.DDM_ADD, -1, "Hello"),
      (constants.DDM_ADD, -1, "World"),
      (constants.DDM_ADD, 0, "Start"),
      (constants.DDM_ADD, -1, "End"),
      (constants.DDM_REMOVE, 2, None),
      (constants.DDM_MODIFY, -1, "foobar"),
      (constants.DDM_REMOVE, 2, None),
      (constants.DDM_ADD, 1, "More"),
      ], self._PrivateData)
    cmdlib.ApplyContainerMods("test", container, chgdesc, mods,
      self._CreateTestFn, self._ModifyTestFn, self._RemoveTestFn)
    self.assertEqual(container, [
      (000, "Start"),
      (100, "More"),
      (000, "Hello"),
      ])
    self.assertEqual(chgdesc, [
      ("test/0", "0x0"),
      ("test/1", "0x1"),
      ("test/0", "0x0"),
      ("test/3", "0x3"),
      ("test/2", "remove"),
      ("test/2", "modify foobar"),
      ("test/2", "remove"),
      ("test/1", "0x1")
      ])
    self.assertTrue(compat.all(op == private.data[0]
                               for (op, _, _, private) in mods))
    self.assertEqual([private.data for (op, _, _, private) in mods], [
      ("add", 0, "Hello"),
      ("add", 1, "World"),
      ("add", 0, "Start"),
      ("add", 3, "End"),
      ("remove", 2, (100, "World")),
      ("modify", 2, "foobar"),
      ("remove", 2, (300, "End")),
      ("add", 1, "More"),
      ])


class _FakeConfigForGenDiskTemplate:
  def __init__(self):
    self._unique_id = itertools.count()
    self._drbd_minor = itertools.count(20)
    self._port = itertools.count(constants.FIRST_DRBD_PORT)
    self._secret = itertools.count()

  def GetVGName(self):
    return "testvg"

  def GenerateUniqueID(self, ec_id):
    return "ec%s-uq%s" % (ec_id, self._unique_id.next())

  def AllocateDRBDMinor(self, nodes, instance):
    return [self._drbd_minor.next()
            for _ in nodes]

  def AllocatePort(self):
    return self._port.next()

  def GenerateDRBDSecret(self, ec_id):
    return "ec%s-secret%s" % (ec_id, self._secret.next())

  def GetInstanceInfo(self, _):
    return "foobar"


class _FakeProcForGenDiskTemplate:
  def GetECId(self):
    return 0


class TestGenerateDiskTemplate(unittest.TestCase):
  def setUp(self):
    nodegroup = objects.NodeGroup(name="ng")
    nodegroup.UpgradeConfig()

    cfg = _FakeConfigForGenDiskTemplate()
    proc = _FakeProcForGenDiskTemplate()

    self.lu = _FakeLU(cfg=cfg, proc=proc)
    self.nodegroup = nodegroup

  @staticmethod
  def GetDiskParams():
    return copy.deepcopy(constants.DISK_DT_DEFAULTS)

  def testWrongDiskTemplate(self):
    gdt = cmdlib._GenerateDiskTemplate
    disk_template = "##unknown##"

    assert disk_template not in constants.DISK_TEMPLATES

    self.assertRaises(errors.ProgrammerError, gdt, self.lu, disk_template,
                      "inst26831.example.com", "node30113.example.com", [], [],
                      NotImplemented, NotImplemented, 0, self.lu.LogInfo,
                      self.GetDiskParams())

  def testDiskless(self):
    gdt = cmdlib._GenerateDiskTemplate

    result = gdt(self.lu, constants.DT_DISKLESS, "inst27734.example.com",
                 "node30113.example.com", [], [],
                 NotImplemented, NotImplemented, 0, self.lu.LogInfo,
                 self.GetDiskParams())
    self.assertEqual(result, [])

  def _TestTrivialDisk(self, template, disk_info, base_index, exp_dev_type,
                       file_storage_dir=NotImplemented,
                       file_driver=NotImplemented,
                       req_file_storage=NotImplemented,
                       req_shr_file_storage=NotImplemented):
    gdt = cmdlib._GenerateDiskTemplate

    map(lambda params: utils.ForceDictType(params,
                                           constants.IDISK_PARAMS_TYPES),
        disk_info)

    # Check if non-empty list of secondaries is rejected
    self.assertRaises(errors.ProgrammerError, gdt, self.lu,
                      template, "inst25088.example.com",
                      "node185.example.com", ["node323.example.com"], [],
                      NotImplemented, NotImplemented, base_index,
                      self.lu.LogInfo, self.GetDiskParams(),
                      _req_file_storage=req_file_storage,
                      _req_shr_file_storage=req_shr_file_storage)

    result = gdt(self.lu, template, "inst21662.example.com",
                 "node21741.example.com", [],
                 disk_info, file_storage_dir, file_driver, base_index,
                 self.lu.LogInfo, self.GetDiskParams(),
                 _req_file_storage=req_file_storage,
                 _req_shr_file_storage=req_shr_file_storage)

    for (idx, disk) in enumerate(result):
      self.assertTrue(isinstance(disk, objects.Disk))
      self.assertEqual(disk.dev_type, exp_dev_type)
      self.assertEqual(disk.size, disk_info[idx][constants.IDISK_SIZE])
      self.assertEqual(disk.mode, disk_info[idx][constants.IDISK_MODE])
      self.assertTrue(disk.children is None)

    self._CheckIvNames(result, base_index, base_index + len(disk_info))
    cmdlib._UpdateIvNames(base_index, result)
    self._CheckIvNames(result, base_index, base_index + len(disk_info))

    return result

  def _CheckIvNames(self, disks, base_index, end_index):
    self.assertEqual(map(operator.attrgetter("iv_name"), disks),
                     ["disk/%s" % i for i in range(base_index, end_index)])

  def testPlain(self):
    disk_info = [{
      constants.IDISK_SIZE: 1024,
      constants.IDISK_MODE: constants.DISK_RDWR,
      }, {
      constants.IDISK_SIZE: 4096,
      constants.IDISK_VG: "othervg",
      constants.IDISK_MODE: constants.DISK_RDWR,
      }]

    result = self._TestTrivialDisk(constants.DT_PLAIN, disk_info, 3,
                                   constants.LD_LV)

    self.assertEqual(map(operator.attrgetter("logical_id"), result), [
      ("testvg", "ec0-uq0.disk3"),
      ("othervg", "ec0-uq1.disk4"),
      ])

  @staticmethod
  def _AllowFileStorage():
    pass

  @staticmethod
  def _ForbidFileStorage():
    raise errors.OpPrereqError("Disallowed in test")

  def testFile(self):
    self.assertRaises(errors.OpPrereqError, self._TestTrivialDisk,
                      constants.DT_FILE, [], 0, NotImplemented,
                      req_file_storage=self._ForbidFileStorage)
    self.assertRaises(errors.OpPrereqError, self._TestTrivialDisk,
                      constants.DT_SHARED_FILE, [], 0, NotImplemented,
                      req_shr_file_storage=self._ForbidFileStorage)

    for disk_template in [constants.DT_FILE, constants.DT_SHARED_FILE]:
      disk_info = [{
        constants.IDISK_SIZE: 80 * 1024,
        constants.IDISK_MODE: constants.DISK_RDONLY,
        }, {
        constants.IDISK_SIZE: 4096,
        constants.IDISK_MODE: constants.DISK_RDWR,
        }, {
        constants.IDISK_SIZE: 6 * 1024,
        constants.IDISK_MODE: constants.DISK_RDWR,
        }]

      result = self._TestTrivialDisk(disk_template, disk_info, 2,
        constants.LD_FILE, file_storage_dir="/tmp",
        file_driver=constants.FD_BLKTAP,
        req_file_storage=self._AllowFileStorage,
        req_shr_file_storage=self._AllowFileStorage)

      self.assertEqual(map(operator.attrgetter("logical_id"), result), [
        (constants.FD_BLKTAP, "/tmp/disk2"),
        (constants.FD_BLKTAP, "/tmp/disk3"),
        (constants.FD_BLKTAP, "/tmp/disk4"),
        ])

  def testBlock(self):
    disk_info = [{
      constants.IDISK_SIZE: 8 * 1024,
      constants.IDISK_MODE: constants.DISK_RDWR,
      constants.IDISK_ADOPT: "/tmp/some/block/dev",
      }]

    result = self._TestTrivialDisk(constants.DT_BLOCK, disk_info, 10,
                                   constants.LD_BLOCKDEV)

    self.assertEqual(map(operator.attrgetter("logical_id"), result), [
      (constants.BLOCKDEV_DRIVER_MANUAL, "/tmp/some/block/dev"),
      ])

  def testRbd(self):
    disk_info = [{
      constants.IDISK_SIZE: 8 * 1024,
      constants.IDISK_MODE: constants.DISK_RDONLY,
      }, {
      constants.IDISK_SIZE: 100 * 1024,
      constants.IDISK_MODE: constants.DISK_RDWR,
      }]

    result = self._TestTrivialDisk(constants.DT_RBD, disk_info, 0,
                                   constants.LD_RBD)

    self.assertEqual(map(operator.attrgetter("logical_id"), result), [
      ("rbd", "ec0-uq0.rbd.disk0"),
      ("rbd", "ec0-uq1.rbd.disk1"),
      ])

  def testDrbd8(self):
    gdt = cmdlib._GenerateDiskTemplate
    drbd8_defaults = constants.DISK_LD_DEFAULTS[constants.LD_DRBD8]
    drbd8_default_metavg = drbd8_defaults[constants.LDP_DEFAULT_METAVG]

    disk_info = [{
      constants.IDISK_SIZE: 1024,
      constants.IDISK_MODE: constants.DISK_RDWR,
      }, {
      constants.IDISK_SIZE: 100 * 1024,
      constants.IDISK_MODE: constants.DISK_RDONLY,
      constants.IDISK_METAVG: "metavg",
      }, {
      constants.IDISK_SIZE: 4096,
      constants.IDISK_MODE: constants.DISK_RDWR,
      constants.IDISK_VG: "vgxyz",
      },
      ]

    exp_logical_ids = [[
      (self.lu.cfg.GetVGName(), "ec0-uq0.disk0_data"),
      (drbd8_default_metavg, "ec0-uq0.disk0_meta"),
      ], [
      (self.lu.cfg.GetVGName(), "ec0-uq1.disk1_data"),
      ("metavg", "ec0-uq1.disk1_meta"),
      ], [
      ("vgxyz", "ec0-uq2.disk2_data"),
      (drbd8_default_metavg, "ec0-uq2.disk2_meta"),
      ]]

    assert len(exp_logical_ids) == len(disk_info)

    map(lambda params: utils.ForceDictType(params,
                                           constants.IDISK_PARAMS_TYPES),
        disk_info)

    # Check if empty list of secondaries is rejected
    self.assertRaises(errors.ProgrammerError, gdt, self.lu, constants.DT_DRBD8,
                      "inst827.example.com", "node1334.example.com", [],
                      disk_info, NotImplemented, NotImplemented, 0,
                      self.lu.LogInfo, self.GetDiskParams())

    result = gdt(self.lu, constants.DT_DRBD8, "inst827.example.com",
                 "node1334.example.com", ["node12272.example.com"],
                 disk_info, NotImplemented, NotImplemented, 0, self.lu.LogInfo,
                 self.GetDiskParams())

    for (idx, disk) in enumerate(result):
      self.assertTrue(isinstance(disk, objects.Disk))
      self.assertEqual(disk.dev_type, constants.LD_DRBD8)
      self.assertEqual(disk.size, disk_info[idx][constants.IDISK_SIZE])
      self.assertEqual(disk.mode, disk_info[idx][constants.IDISK_MODE])

      for child in disk.children:
        self.assertTrue(isinstance(disk, objects.Disk))
        self.assertEqual(child.dev_type, constants.LD_LV)
        self.assertTrue(child.children is None)

      self.assertEqual(map(operator.attrgetter("logical_id"), disk.children),
                       exp_logical_ids[idx])

      self.assertEqual(len(disk.children), 2)
      self.assertEqual(disk.children[0].size, disk.size)
      self.assertEqual(disk.children[1].size, cmdlib.DRBD_META_SIZE)

    self._CheckIvNames(result, 0, len(disk_info))
    cmdlib._UpdateIvNames(0, result)
    self._CheckIvNames(result, 0, len(disk_info))

    self.assertEqual(map(operator.attrgetter("logical_id"), result), [
      ("node1334.example.com", "node12272.example.com",
       constants.FIRST_DRBD_PORT, 20, 21, "ec0-secret0"),
      ("node1334.example.com", "node12272.example.com",
       constants.FIRST_DRBD_PORT + 1, 22, 23, "ec0-secret1"),
      ("node1334.example.com", "node12272.example.com",
       constants.FIRST_DRBD_PORT + 2, 24, 25, "ec0-secret2"),
      ])


if __name__ == "__main__":
  testutils.GanetiTestProgram()
