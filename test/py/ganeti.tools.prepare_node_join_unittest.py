#!/usr/bin/python
#

# Copyright (C) 2012 Google Inc.
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


"""Script for testing ganeti.tools.prepare_node_join"""

import unittest
import shutil
import tempfile
import os.path
import OpenSSL

from ganeti import errors
from ganeti import constants
from ganeti import serializer
from ganeti import pathutils
from ganeti import compat
from ganeti import utils
from ganeti.tools import prepare_node_join

import testutils


_JoinError = prepare_node_join.JoinError


class TestLoadData(unittest.TestCase):
  def testNoJson(self):
    self.assertRaises(errors.ParseError, prepare_node_join.LoadData, "")
    self.assertRaises(errors.ParseError, prepare_node_join.LoadData, "}")

  def testInvalidDataStructure(self):
    raw = serializer.DumpJson({
      "some other thing": False,
      })
    self.assertRaises(errors.ParseError, prepare_node_join.LoadData, raw)

    raw = serializer.DumpJson([])
    self.assertRaises(errors.ParseError, prepare_node_join.LoadData, raw)

  def testValidData(self):
    raw = serializer.DumpJson({})
    self.assertEqual(prepare_node_join.LoadData(raw), {})


class TestVerifyCertificate(testutils.GanetiTestCase):
  def setUp(self):
    testutils.GanetiTestCase.setUp(self)
    self.tmpdir = tempfile.mkdtemp()

  def tearDown(self):
    testutils.GanetiTestCase.tearDown(self)
    shutil.rmtree(self.tmpdir)

  def testNoCert(self):
    prepare_node_join.VerifyCertificate({}, _verify_fn=NotImplemented)

  def testGivenPrivateKey(self):
    cert_filename = testutils.TestDataFilename("cert2.pem")
    cert_pem = utils.ReadFile(cert_filename)

    self.assertRaises(_JoinError, prepare_node_join._VerifyCertificate,
                      cert_pem, _check_fn=NotImplemented)

  def testInvalidCertificate(self):
    self.assertRaises(errors.X509CertError,
                      prepare_node_join._VerifyCertificate,
                      "Something that's not a certificate",
                      _check_fn=NotImplemented)

  @staticmethod
  def _Check(cert):
    assert cert.get_subject()

  def testSuccessfulCheck(self):
    cert_filename = testutils.TestDataFilename("cert1.pem")
    cert_pem = utils.ReadFile(cert_filename)
    prepare_node_join._VerifyCertificate(cert_pem, _check_fn=self._Check)


class TestVerifyClusterName(unittest.TestCase):
  def setUp(self):
    unittest.TestCase.setUp(self)
    self.tmpdir = tempfile.mkdtemp()

  def tearDown(self):
    unittest.TestCase.tearDown(self)
    shutil.rmtree(self.tmpdir)

  def testNoName(self):
    self.assertRaises(_JoinError, prepare_node_join.VerifyClusterName,
                      {}, _verify_fn=NotImplemented)

  @staticmethod
  def _FailingVerify(name):
    assert name == "cluster.example.com"
    raise errors.GenericError()

  def testFailingVerification(self):
    data = {
      constants.SSHS_CLUSTER_NAME: "cluster.example.com",
      }

    self.assertRaises(errors.GenericError, prepare_node_join.VerifyClusterName,
                      data, _verify_fn=self._FailingVerify)


class TestUpdateSshDaemon(unittest.TestCase):
  def setUp(self):
    unittest.TestCase.setUp(self)
    self.tmpdir = tempfile.mkdtemp()

    self.keyfiles = {
      constants.SSHK_RSA:
        (utils.PathJoin(self.tmpdir, "rsa.private"),
         utils.PathJoin(self.tmpdir, "rsa.public")),
      constants.SSHK_DSA:
        (utils.PathJoin(self.tmpdir, "dsa.private"),
         utils.PathJoin(self.tmpdir, "dsa.public")),
      }

  def tearDown(self):
    unittest.TestCase.tearDown(self)
    shutil.rmtree(self.tmpdir)

  def testNoKeys(self):
    data_empty_keys = {
      constants.SSHS_SSH_HOST_KEY: [],
      }

    for data in [{}, data_empty_keys]:
      for dry_run in [False, True]:
        prepare_node_join.UpdateSshDaemon(data, dry_run,
                                          _runcmd_fn=NotImplemented,
                                          _keyfiles=NotImplemented)
    self.assertEqual(os.listdir(self.tmpdir), [])

  def _TestDryRun(self, data):
    prepare_node_join.UpdateSshDaemon(data, True, _runcmd_fn=NotImplemented,
                                      _keyfiles=self.keyfiles)
    self.assertEqual(os.listdir(self.tmpdir), [])

  def testDryRunRsa(self):
    self._TestDryRun({
      constants.SSHS_SSH_HOST_KEY: [
        (constants.SSHK_RSA, "rsapriv", "rsapub"),
        ],
      })

  def testDryRunDsa(self):
    self._TestDryRun({
      constants.SSHS_SSH_HOST_KEY: [
        (constants.SSHK_DSA, "dsapriv", "dsapub"),
        ],
      })

  def _RunCmd(self, fail, cmd, interactive=NotImplemented):
    self.assertTrue(interactive)
    self.assertEqual(cmd, [pathutils.DAEMON_UTIL, "reload-ssh-keys"])
    if fail:
      exit_code = constants.EXIT_FAILURE
    else:
      exit_code = constants.EXIT_SUCCESS
    return utils.RunResult(exit_code, None, "stdout", "stderr",
                           utils.ShellQuoteArgs(cmd),
                           NotImplemented, NotImplemented)

  def _TestUpdate(self, failcmd):
    data = {
      constants.SSHS_SSH_HOST_KEY: [
        (constants.SSHK_DSA, "dsapriv", "dsapub"),
        (constants.SSHK_RSA, "rsapriv", "rsapub"),
        ],
      }
    runcmd_fn = compat.partial(self._RunCmd, failcmd)
    if failcmd:
      self.assertRaises(_JoinError, prepare_node_join.UpdateSshDaemon,
                        data, False, _runcmd_fn=runcmd_fn,
                        _keyfiles=self.keyfiles)
    else:
      prepare_node_join.UpdateSshDaemon(data, False, _runcmd_fn=runcmd_fn,
                                        _keyfiles=self.keyfiles)
    self.assertEqual(sorted(os.listdir(self.tmpdir)), sorted([
      "rsa.public", "rsa.private",
      "dsa.public", "dsa.private",
      ]))
    self.assertEqual(utils.ReadFile(utils.PathJoin(self.tmpdir, "rsa.public")),
                     "rsapub")
    self.assertEqual(utils.ReadFile(utils.PathJoin(self.tmpdir, "rsa.private")),
                     "rsapriv")
    self.assertEqual(utils.ReadFile(utils.PathJoin(self.tmpdir, "dsa.public")),
                     "dsapub")
    self.assertEqual(utils.ReadFile(utils.PathJoin(self.tmpdir, "dsa.private")),
                     "dsapriv")

  def testSuccess(self):
    self._TestUpdate(False)

  def testFailure(self):
    self._TestUpdate(True)


class TestUpdateSshRoot(unittest.TestCase):
  def setUp(self):
    unittest.TestCase.setUp(self)
    self.tmpdir = tempfile.mkdtemp()
    self.sshdir = utils.PathJoin(self.tmpdir, ".ssh")

  def tearDown(self):
    unittest.TestCase.tearDown(self)
    shutil.rmtree(self.tmpdir)

  def _GetHomeDir(self, user):
    self.assertEqual(user, constants.SSH_LOGIN_USER)
    return self.tmpdir

  def testNoKeys(self):
    data_empty_keys = {
      constants.SSHS_SSH_ROOT_KEY: [],
      }

    for data in [{}, data_empty_keys]:
      for dry_run in [False, True]:
        prepare_node_join.UpdateSshRoot(data, dry_run,
                                        _homedir_fn=NotImplemented)
    self.assertEqual(os.listdir(self.tmpdir), [])

  def testDryRun(self):
    data = {
      constants.SSHS_SSH_ROOT_KEY: [
        (constants.SSHK_RSA, "aaa", "bbb"),
        ]
      }

    prepare_node_join.UpdateSshRoot(data, True,
                                    _homedir_fn=self._GetHomeDir)
    self.assertEqual(os.listdir(self.tmpdir), [".ssh"])
    self.assertEqual(os.listdir(self.sshdir), [])

  def testUpdate(self):
    data = {
      constants.SSHS_SSH_ROOT_KEY: [
        (constants.SSHK_DSA, "privatedsa", "ssh-dss pubdsa"),
        ]
      }

    prepare_node_join.UpdateSshRoot(data, False,
                                    _homedir_fn=self._GetHomeDir)
    self.assertEqual(os.listdir(self.tmpdir), [".ssh"])
    self.assertEqual(sorted(os.listdir(self.sshdir)),
                     sorted(["authorized_keys", "id_dsa", "id_dsa.pub"]))
    self.assertEqual(utils.ReadFile(utils.PathJoin(self.sshdir, "id_dsa")),
                     "privatedsa")
    self.assertEqual(utils.ReadFile(utils.PathJoin(self.sshdir, "id_dsa.pub")),
                     "ssh-dss pubdsa")
    self.assertEqual(utils.ReadFile(utils.PathJoin(self.sshdir,
                                                   "authorized_keys")),
                     "ssh-dss pubdsa\n")


if __name__ == "__main__":
  testutils.GanetiTestProgram()
