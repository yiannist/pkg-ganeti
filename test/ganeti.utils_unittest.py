#!/usr/bin/python
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


"""Script for unittesting the utils module"""

import unittest
import os
import time
import tempfile
import os.path
import os
import md5
import signal
import socket
import shutil
import re
import select
import string

import ganeti
import testutils
from ganeti import constants
from ganeti import utils
from ganeti import errors
from ganeti.utils import IsProcessAlive, RunCmd, \
     RemoveFile, MatchNameComponent, FormatUnit, \
     ParseUnit, AddAuthorizedKey, RemoveAuthorizedKey, \
     ShellQuote, ShellQuoteArgs, TcpPing, ListVisibleFiles, \
     SetEtcHostsEntry, RemoveEtcHostsEntry, FirstFree, OwnIpAddress, \
     TailFile, ForceDictType, SafeEncode, IsNormAbsPath, FormatTime

from ganeti.errors import LockError, UnitParseError, GenericError, \
     ProgrammerError


class TestIsProcessAlive(unittest.TestCase):
  """Testing case for IsProcessAlive"""

  def testExists(self):
    mypid = os.getpid()
    self.assert_(IsProcessAlive(mypid),
                 "can't find myself running")

  def testNotExisting(self):
    pid_non_existing = os.fork()
    if pid_non_existing == 0:
      os._exit(0)
    elif pid_non_existing < 0:
      raise SystemError("can't fork")
    os.waitpid(pid_non_existing, 0)
    self.assert_(not IsProcessAlive(pid_non_existing),
                 "nonexisting process detected")


class TestPidFileFunctions(unittest.TestCase):
  """Tests for WritePidFile, RemovePidFile and ReadPidFile"""

  def setUp(self):
    self.dir = tempfile.mkdtemp()
    self.f_dpn = lambda name: os.path.join(self.dir, "%s.pid" % name)
    utils.DaemonPidFileName = self.f_dpn

  def testPidFileFunctions(self):
    pid_file = self.f_dpn('test')
    utils.WritePidFile('test')
    self.failUnless(os.path.exists(pid_file),
                    "PID file should have been created")
    read_pid = utils.ReadPidFile(pid_file)
    self.failUnlessEqual(read_pid, os.getpid())
    self.failUnless(utils.IsProcessAlive(read_pid))
    self.failUnlessRaises(GenericError, utils.WritePidFile, 'test')
    utils.RemovePidFile('test')
    self.failIf(os.path.exists(pid_file),
                "PID file should not exist anymore")
    self.failUnlessEqual(utils.ReadPidFile(pid_file), 0,
                         "ReadPidFile should return 0 for missing pid file")
    fh = open(pid_file, "w")
    fh.write("blah\n")
    fh.close()
    self.failUnlessEqual(utils.ReadPidFile(pid_file), 0,
                         "ReadPidFile should return 0 for invalid pid file")
    utils.RemovePidFile('test')
    self.failIf(os.path.exists(pid_file),
                "PID file should not exist anymore")

  def testKill(self):
    pid_file = self.f_dpn('child')
    r_fd, w_fd = os.pipe()
    new_pid = os.fork()
    if new_pid == 0: #child
      utils.WritePidFile('child')
      os.write(w_fd, 'a')
      signal.pause()
      os._exit(0)
      return
    # else we are in the parent
    # wait until the child has written the pid file
    os.read(r_fd, 1)
    read_pid = utils.ReadPidFile(pid_file)
    self.failUnlessEqual(read_pid, new_pid)
    self.failUnless(utils.IsProcessAlive(new_pid))
    utils.KillProcess(new_pid, waitpid=True)
    self.failIf(utils.IsProcessAlive(new_pid))
    utils.RemovePidFile('child')
    self.failUnlessRaises(ProgrammerError, utils.KillProcess, 0)

  def tearDown(self):
    for name in os.listdir(self.dir):
      os.unlink(os.path.join(self.dir, name))
    os.rmdir(self.dir)


class TestRunCmd(testutils.GanetiTestCase):
  """Testing case for the RunCmd function"""

  def setUp(self):
    testutils.GanetiTestCase.setUp(self)
    self.magic = time.ctime() + " ganeti test"
    self.fname = self._CreateTempFile()

  def testOk(self):
    """Test successful exit code"""
    result = RunCmd("/bin/sh -c 'exit 0'")
    self.assertEqual(result.exit_code, 0)
    self.assertEqual(result.output, "")

  def testFail(self):
    """Test fail exit code"""
    result = RunCmd("/bin/sh -c 'exit 1'")
    self.assertEqual(result.exit_code, 1)
    self.assertEqual(result.output, "")

  def testStdout(self):
    """Test standard output"""
    cmd = 'echo -n "%s"' % self.magic
    result = RunCmd("/bin/sh -c '%s'" % cmd)
    self.assertEqual(result.stdout, self.magic)
    result = RunCmd("/bin/sh -c '%s'" % cmd, output=self.fname)
    self.assertEqual(result.output, "")
    self.assertFileContent(self.fname, self.magic)

  def testStderr(self):
    """Test standard error"""
    cmd = 'echo -n "%s"' % self.magic
    result = RunCmd("/bin/sh -c '%s' 1>&2" % cmd)
    self.assertEqual(result.stderr, self.magic)
    result = RunCmd("/bin/sh -c '%s' 1>&2" % cmd, output=self.fname)
    self.assertEqual(result.output, "")
    self.assertFileContent(self.fname, self.magic)

  def testCombined(self):
    """Test combined output"""
    cmd = 'echo -n "A%s"; echo -n "B%s" 1>&2' % (self.magic, self.magic)
    expected = "A" + self.magic + "B" + self.magic
    result = RunCmd("/bin/sh -c '%s'" % cmd)
    self.assertEqual(result.output, expected)
    result = RunCmd("/bin/sh -c '%s'" % cmd, output=self.fname)
    self.assertEqual(result.output, "")
    self.assertFileContent(self.fname, expected)

  def testSignal(self):
    """Test signal"""
    result = RunCmd(["python", "-c", "import os; os.kill(os.getpid(), 15)"])
    self.assertEqual(result.signal, 15)
    self.assertEqual(result.output, "")

  def testListRun(self):
    """Test list runs"""
    result = RunCmd(["true"])
    self.assertEqual(result.signal, None)
    self.assertEqual(result.exit_code, 0)
    result = RunCmd(["/bin/sh", "-c", "exit 1"])
    self.assertEqual(result.signal, None)
    self.assertEqual(result.exit_code, 1)
    result = RunCmd(["echo", "-n", self.magic])
    self.assertEqual(result.signal, None)
    self.assertEqual(result.exit_code, 0)
    self.assertEqual(result.stdout, self.magic)

  def testFileEmptyOutput(self):
    """Test file output"""
    result = RunCmd(["true"], output=self.fname)
    self.assertEqual(result.signal, None)
    self.assertEqual(result.exit_code, 0)
    self.assertFileContent(self.fname, "")

  def testLang(self):
    """Test locale environment"""
    old_env = os.environ.copy()
    try:
      os.environ["LANG"] = "en_US.UTF-8"
      os.environ["LC_ALL"] = "en_US.UTF-8"
      result = RunCmd(["locale"])
      for line in result.output.splitlines():
        key, value = line.split("=", 1)
        # Ignore these variables, they're overridden by LC_ALL
        if key == "LANG" or key == "LANGUAGE":
          continue
        self.failIf(value and value != "C" and value != '"C"',
            "Variable %s is set to the invalid value '%s'" % (key, value))
    finally:
      os.environ = old_env

  def testDefaultCwd(self):
    """Test default working directory"""
    self.failUnlessEqual(RunCmd(["pwd"]).stdout.strip(), "/")

  def testCwd(self):
    """Test default working directory"""
    self.failUnlessEqual(RunCmd(["pwd"], cwd="/").stdout.strip(), "/")
    self.failUnlessEqual(RunCmd(["pwd"], cwd="/tmp").stdout.strip(), "/tmp")
    cwd = os.getcwd()
    self.failUnlessEqual(RunCmd(["pwd"], cwd=cwd).stdout.strip(), cwd)


class TestRemoveFile(unittest.TestCase):
  """Test case for the RemoveFile function"""

  def setUp(self):
    """Create a temp dir and file for each case"""
    self.tmpdir = tempfile.mkdtemp('', 'ganeti-unittest-')
    fd, self.tmpfile = tempfile.mkstemp('', '', self.tmpdir)
    os.close(fd)

  def tearDown(self):
    if os.path.exists(self.tmpfile):
      os.unlink(self.tmpfile)
    os.rmdir(self.tmpdir)


  def testIgnoreDirs(self):
    """Test that RemoveFile() ignores directories"""
    self.assertEqual(None, RemoveFile(self.tmpdir))


  def testIgnoreNotExisting(self):
    """Test that RemoveFile() ignores non-existing files"""
    RemoveFile(self.tmpfile)
    RemoveFile(self.tmpfile)


  def testRemoveFile(self):
    """Test that RemoveFile does remove a file"""
    RemoveFile(self.tmpfile)
    if os.path.exists(self.tmpfile):
      self.fail("File '%s' not removed" % self.tmpfile)


  def testRemoveSymlink(self):
    """Test that RemoveFile does remove symlinks"""
    symlink = self.tmpdir + "/symlink"
    os.symlink("no-such-file", symlink)
    RemoveFile(symlink)
    if os.path.exists(symlink):
      self.fail("File '%s' not removed" % symlink)
    os.symlink(self.tmpfile, symlink)
    RemoveFile(symlink)
    if os.path.exists(symlink):
      self.fail("File '%s' not removed" % symlink)


class TestRename(unittest.TestCase):
  """Test case for RenameFile"""

  def setUp(self):
    """Create a temporary directory"""
    self.tmpdir = tempfile.mkdtemp()
    self.tmpfile = os.path.join(self.tmpdir, "test1")

    # Touch the file
    open(self.tmpfile, "w").close()

  def tearDown(self):
    """Remove temporary directory"""
    shutil.rmtree(self.tmpdir)

  def testSimpleRename1(self):
    """Simple rename 1"""
    utils.RenameFile(self.tmpfile, os.path.join(self.tmpdir, "xyz"))
    self.assert_(os.path.isfile(os.path.join(self.tmpdir, "xyz")))

  def testSimpleRename2(self):
    """Simple rename 2"""
    utils.RenameFile(self.tmpfile, os.path.join(self.tmpdir, "xyz"),
                     mkdir=True)
    self.assert_(os.path.isfile(os.path.join(self.tmpdir, "xyz")))

  def testRenameMkdir(self):
    """Rename with mkdir"""
    utils.RenameFile(self.tmpfile, os.path.join(self.tmpdir, "test/xyz"),
                     mkdir=True)
    self.assert_(os.path.isdir(os.path.join(self.tmpdir, "test")))
    self.assert_(os.path.isfile(os.path.join(self.tmpdir, "test/xyz")))

    utils.RenameFile(os.path.join(self.tmpdir, "test/xyz"),
                     os.path.join(self.tmpdir, "test/foo/bar/baz"),
                     mkdir=True)
    self.assert_(os.path.isdir(os.path.join(self.tmpdir, "test")))
    self.assert_(os.path.isdir(os.path.join(self.tmpdir, "test/foo/bar")))
    self.assert_(os.path.isfile(os.path.join(self.tmpdir, "test/foo/bar/baz")))


class TestMatchNameComponent(unittest.TestCase):
  """Test case for the MatchNameComponent function"""

  def testEmptyList(self):
    """Test that there is no match against an empty list"""

    self.failUnlessEqual(MatchNameComponent("", []), None)
    self.failUnlessEqual(MatchNameComponent("test", []), None)

  def testSingleMatch(self):
    """Test that a single match is performed correctly"""
    mlist = ["test1.example.com", "test2.example.com", "test3.example.com"]
    for key in "test2", "test2.example", "test2.example.com":
      self.failUnlessEqual(MatchNameComponent(key, mlist), mlist[1])

  def testMultipleMatches(self):
    """Test that a multiple match is returned as None"""
    mlist = ["test1.example.com", "test1.example.org", "test1.example.net"]
    for key in "test1", "test1.example":
      self.failUnlessEqual(MatchNameComponent(key, mlist), None)

  def testFullMatch(self):
    """Test that a full match is returned correctly"""
    key1 = "test1"
    key2 = "test1.example"
    mlist = [key2, key2 + ".com"]
    self.failUnlessEqual(MatchNameComponent(key1, mlist), None)
    self.failUnlessEqual(MatchNameComponent(key2, mlist), key2)

  def testCaseInsensitivePartialMatch(self):
    """Test for the case_insensitive keyword"""
    mlist = ["test1.example.com", "test2.example.net"]
    self.assertEqual(MatchNameComponent("test2", mlist, case_sensitive=False),
                     "test2.example.net")
    self.assertEqual(MatchNameComponent("Test2", mlist, case_sensitive=False),
                     "test2.example.net")
    self.assertEqual(MatchNameComponent("teSt2", mlist, case_sensitive=False),
                     "test2.example.net")
    self.assertEqual(MatchNameComponent("TeSt2", mlist, case_sensitive=False),
                     "test2.example.net")


  def testCaseInsensitiveFullMatch(self):
    mlist = ["ts1.ex", "ts1.ex.org", "ts2.ex", "Ts2.ex"]
    # Between the two ts1 a full string match non-case insensitive should work
    self.assertEqual(MatchNameComponent("Ts1", mlist, case_sensitive=False),
                     None)
    self.assertEqual(MatchNameComponent("Ts1.ex", mlist, case_sensitive=False),
                     "ts1.ex")
    self.assertEqual(MatchNameComponent("ts1.ex", mlist, case_sensitive=False),
                     "ts1.ex")
    # Between the two ts2 only case differs, so only case-match works
    self.assertEqual(MatchNameComponent("ts2.ex", mlist, case_sensitive=False),
                     "ts2.ex")
    self.assertEqual(MatchNameComponent("Ts2.ex", mlist, case_sensitive=False),
                     "Ts2.ex")
    self.assertEqual(MatchNameComponent("TS2.ex", mlist, case_sensitive=False),
                     None)


class TestFormatUnit(unittest.TestCase):
  """Test case for the FormatUnit function"""

  def testMiB(self):
    self.assertEqual(FormatUnit(1, 'h'), '1M')
    self.assertEqual(FormatUnit(100, 'h'), '100M')
    self.assertEqual(FormatUnit(1023, 'h'), '1023M')

    self.assertEqual(FormatUnit(1, 'm'), '1')
    self.assertEqual(FormatUnit(100, 'm'), '100')
    self.assertEqual(FormatUnit(1023, 'm'), '1023')

    self.assertEqual(FormatUnit(1024, 'm'), '1024')
    self.assertEqual(FormatUnit(1536, 'm'), '1536')
    self.assertEqual(FormatUnit(17133, 'm'), '17133')
    self.assertEqual(FormatUnit(1024 * 1024 - 1, 'm'), '1048575')

  def testGiB(self):
    self.assertEqual(FormatUnit(1024, 'h'), '1.0G')
    self.assertEqual(FormatUnit(1536, 'h'), '1.5G')
    self.assertEqual(FormatUnit(17133, 'h'), '16.7G')
    self.assertEqual(FormatUnit(1024 * 1024 - 1, 'h'), '1024.0G')

    self.assertEqual(FormatUnit(1024, 'g'), '1.0')
    self.assertEqual(FormatUnit(1536, 'g'), '1.5')
    self.assertEqual(FormatUnit(17133, 'g'), '16.7')
    self.assertEqual(FormatUnit(1024 * 1024 - 1, 'g'), '1024.0')

    self.assertEqual(FormatUnit(1024 * 1024, 'g'), '1024.0')
    self.assertEqual(FormatUnit(5120 * 1024, 'g'), '5120.0')
    self.assertEqual(FormatUnit(29829 * 1024, 'g'), '29829.0')

  def testTiB(self):
    self.assertEqual(FormatUnit(1024 * 1024, 'h'), '1.0T')
    self.assertEqual(FormatUnit(5120 * 1024, 'h'), '5.0T')
    self.assertEqual(FormatUnit(29829 * 1024, 'h'), '29.1T')

    self.assertEqual(FormatUnit(1024 * 1024, 't'), '1.0')
    self.assertEqual(FormatUnit(5120 * 1024, 't'), '5.0')
    self.assertEqual(FormatUnit(29829 * 1024, 't'), '29.1')

class TestParseUnit(unittest.TestCase):
  """Test case for the ParseUnit function"""

  SCALES = (('', 1),
            ('M', 1), ('G', 1024), ('T', 1024 * 1024),
            ('MB', 1), ('GB', 1024), ('TB', 1024 * 1024),
            ('MiB', 1), ('GiB', 1024), ('TiB', 1024 * 1024))

  def testRounding(self):
    self.assertEqual(ParseUnit('0'), 0)
    self.assertEqual(ParseUnit('1'), 4)
    self.assertEqual(ParseUnit('2'), 4)
    self.assertEqual(ParseUnit('3'), 4)

    self.assertEqual(ParseUnit('124'), 124)
    self.assertEqual(ParseUnit('125'), 128)
    self.assertEqual(ParseUnit('126'), 128)
    self.assertEqual(ParseUnit('127'), 128)
    self.assertEqual(ParseUnit('128'), 128)
    self.assertEqual(ParseUnit('129'), 132)
    self.assertEqual(ParseUnit('130'), 132)

  def testFloating(self):
    self.assertEqual(ParseUnit('0'), 0)
    self.assertEqual(ParseUnit('0.5'), 4)
    self.assertEqual(ParseUnit('1.75'), 4)
    self.assertEqual(ParseUnit('1.99'), 4)
    self.assertEqual(ParseUnit('2.00'), 4)
    self.assertEqual(ParseUnit('2.01'), 4)
    self.assertEqual(ParseUnit('3.99'), 4)
    self.assertEqual(ParseUnit('4.00'), 4)
    self.assertEqual(ParseUnit('4.01'), 8)
    self.assertEqual(ParseUnit('1.5G'), 1536)
    self.assertEqual(ParseUnit('1.8G'), 1844)
    self.assertEqual(ParseUnit('8.28T'), 8682212)

  def testSuffixes(self):
    for sep in ('', ' ', '   ', "\t", "\t "):
      for suffix, scale in TestParseUnit.SCALES:
        for func in (lambda x: x, str.lower, str.upper):
          self.assertEqual(ParseUnit('1024' + sep + func(suffix)),
                           1024 * scale)

  def testInvalidInput(self):
    for sep in ('-', '_', ',', 'a'):
      for suffix, _ in TestParseUnit.SCALES:
        self.assertRaises(UnitParseError, ParseUnit, '1' + sep + suffix)

    for suffix, _ in TestParseUnit.SCALES:
      self.assertRaises(UnitParseError, ParseUnit, '1,3' + suffix)


class TestSshKeys(testutils.GanetiTestCase):
  """Test case for the AddAuthorizedKey function"""

  KEY_A = 'ssh-dss AAAAB3NzaC1w5256closdj32mZaQU root@key-a'
  KEY_B = ('command="/usr/bin/fooserver -t --verbose",from="1.2.3.4" '
           'ssh-dss AAAAB3NzaC1w520smc01ms0jfJs22 root@key-b')

  def setUp(self):
    testutils.GanetiTestCase.setUp(self)
    self.tmpname = self._CreateTempFile()
    handle = open(self.tmpname, 'w')
    try:
      handle.write("%s\n" % TestSshKeys.KEY_A)
      handle.write("%s\n" % TestSshKeys.KEY_B)
    finally:
      handle.close()

  def testAddingNewKey(self):
    AddAuthorizedKey(self.tmpname, 'ssh-dss AAAAB3NzaC1kc3MAAACB root@test')

    self.assertFileContent(self.tmpname,
      "ssh-dss AAAAB3NzaC1w5256closdj32mZaQU root@key-a\n"
      'command="/usr/bin/fooserver -t --verbose",from="1.2.3.4"'
      " ssh-dss AAAAB3NzaC1w520smc01ms0jfJs22 root@key-b\n"
      "ssh-dss AAAAB3NzaC1kc3MAAACB root@test\n")

  def testAddingAlmostButNotCompletelyTheSameKey(self):
    AddAuthorizedKey(self.tmpname,
        'ssh-dss AAAAB3NzaC1w5256closdj32mZaQU root@test')

    self.assertFileContent(self.tmpname,
      "ssh-dss AAAAB3NzaC1w5256closdj32mZaQU root@key-a\n"
      'command="/usr/bin/fooserver -t --verbose",from="1.2.3.4"'
      " ssh-dss AAAAB3NzaC1w520smc01ms0jfJs22 root@key-b\n"
      "ssh-dss AAAAB3NzaC1w5256closdj32mZaQU root@test\n")

  def testAddingExistingKeyWithSomeMoreSpaces(self):
    AddAuthorizedKey(self.tmpname,
        'ssh-dss  AAAAB3NzaC1w5256closdj32mZaQU   root@key-a')

    self.assertFileContent(self.tmpname,
      "ssh-dss AAAAB3NzaC1w5256closdj32mZaQU root@key-a\n"
      'command="/usr/bin/fooserver -t --verbose",from="1.2.3.4"'
      " ssh-dss AAAAB3NzaC1w520smc01ms0jfJs22 root@key-b\n")

  def testRemovingExistingKeyWithSomeMoreSpaces(self):
    RemoveAuthorizedKey(self.tmpname,
        'ssh-dss  AAAAB3NzaC1w5256closdj32mZaQU   root@key-a')

    self.assertFileContent(self.tmpname,
      'command="/usr/bin/fooserver -t --verbose",from="1.2.3.4"'
      " ssh-dss AAAAB3NzaC1w520smc01ms0jfJs22 root@key-b\n")

  def testRemovingNonExistingKey(self):
    RemoveAuthorizedKey(self.tmpname,
        'ssh-dss  AAAAB3Nsdfj230xxjxJjsjwjsjdjU   root@test')

    self.assertFileContent(self.tmpname,
      "ssh-dss AAAAB3NzaC1w5256closdj32mZaQU root@key-a\n"
      'command="/usr/bin/fooserver -t --verbose",from="1.2.3.4"'
      " ssh-dss AAAAB3NzaC1w520smc01ms0jfJs22 root@key-b\n")


class TestEtcHosts(testutils.GanetiTestCase):
  """Test functions modifying /etc/hosts"""

  def setUp(self):
    testutils.GanetiTestCase.setUp(self)
    self.tmpname = self._CreateTempFile()
    handle = open(self.tmpname, 'w')
    try:
      handle.write('# This is a test file for /etc/hosts\n')
      handle.write('127.0.0.1\tlocalhost\n')
      handle.write('192.168.1.1 router gw\n')
    finally:
      handle.close()

  def testSettingNewIp(self):
    SetEtcHostsEntry(self.tmpname, '1.2.3.4', 'myhost.domain.tld', ['myhost'])

    self.assertFileContent(self.tmpname,
      "# This is a test file for /etc/hosts\n"
      "127.0.0.1\tlocalhost\n"
      "192.168.1.1 router gw\n"
      "1.2.3.4\tmyhost.domain.tld myhost\n")
    self.assertFileMode(self.tmpname, 0644)

  def testSettingExistingIp(self):
    SetEtcHostsEntry(self.tmpname, '192.168.1.1', 'myhost.domain.tld',
                     ['myhost'])

    self.assertFileContent(self.tmpname,
      "# This is a test file for /etc/hosts\n"
      "127.0.0.1\tlocalhost\n"
      "192.168.1.1\tmyhost.domain.tld myhost\n")
    self.assertFileMode(self.tmpname, 0644)

  def testSettingDuplicateName(self):
    SetEtcHostsEntry(self.tmpname, '1.2.3.4', 'myhost', ['myhost'])

    self.assertFileContent(self.tmpname,
      "# This is a test file for /etc/hosts\n"
      "127.0.0.1\tlocalhost\n"
      "192.168.1.1 router gw\n"
      "1.2.3.4\tmyhost\n")
    self.assertFileMode(self.tmpname, 0644)

  def testRemovingExistingHost(self):
    RemoveEtcHostsEntry(self.tmpname, 'router')

    self.assertFileContent(self.tmpname,
      "# This is a test file for /etc/hosts\n"
      "127.0.0.1\tlocalhost\n"
      "192.168.1.1 gw\n")
    self.assertFileMode(self.tmpname, 0644)

  def testRemovingSingleExistingHost(self):
    RemoveEtcHostsEntry(self.tmpname, 'localhost')

    self.assertFileContent(self.tmpname,
      "# This is a test file for /etc/hosts\n"
      "192.168.1.1 router gw\n")
    self.assertFileMode(self.tmpname, 0644)

  def testRemovingNonExistingHost(self):
    RemoveEtcHostsEntry(self.tmpname, 'myhost')

    self.assertFileContent(self.tmpname,
      "# This is a test file for /etc/hosts\n"
      "127.0.0.1\tlocalhost\n"
      "192.168.1.1 router gw\n")
    self.assertFileMode(self.tmpname, 0644)

  def testRemovingAlias(self):
    RemoveEtcHostsEntry(self.tmpname, 'gw')

    self.assertFileContent(self.tmpname,
      "# This is a test file for /etc/hosts\n"
      "127.0.0.1\tlocalhost\n"
      "192.168.1.1 router\n")
    self.assertFileMode(self.tmpname, 0644)


class TestShellQuoting(unittest.TestCase):
  """Test case for shell quoting functions"""

  def testShellQuote(self):
    self.assertEqual(ShellQuote('abc'), "abc")
    self.assertEqual(ShellQuote('ab"c'), "'ab\"c'")
    self.assertEqual(ShellQuote("a'bc"), "'a'\\''bc'")
    self.assertEqual(ShellQuote("a b c"), "'a b c'")
    self.assertEqual(ShellQuote("a b\\ c"), "'a b\\ c'")

  def testShellQuoteArgs(self):
    self.assertEqual(ShellQuoteArgs(['a', 'b', 'c']), "a b c")
    self.assertEqual(ShellQuoteArgs(['a', 'b"', 'c']), "a 'b\"' c")
    self.assertEqual(ShellQuoteArgs(['a', 'b\'', 'c']), "a 'b'\\\''' c")


class TestTcpPing(unittest.TestCase):
  """Testcase for TCP version of ping - against listen(2)ing port"""

  def setUp(self):
    self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.listener.bind((constants.LOCALHOST_IP_ADDRESS, 0))
    self.listenerport = self.listener.getsockname()[1]
    self.listener.listen(1)

  def tearDown(self):
    self.listener.shutdown(socket.SHUT_RDWR)
    del self.listener
    del self.listenerport

  def testTcpPingToLocalHostAccept(self):
    self.assert_(TcpPing(constants.LOCALHOST_IP_ADDRESS,
                         self.listenerport,
                         timeout=10,
                         live_port_needed=True,
                         source=constants.LOCALHOST_IP_ADDRESS,
                         ),
                 "failed to connect to test listener")

    self.assert_(TcpPing(constants.LOCALHOST_IP_ADDRESS,
                         self.listenerport,
                         timeout=10,
                         live_port_needed=True,
                         ),
                 "failed to connect to test listener (no source)")


class TestTcpPingDeaf(unittest.TestCase):
  """Testcase for TCP version of ping - against non listen(2)ing port"""

  def setUp(self):
    self.deaflistener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.deaflistener.bind((constants.LOCALHOST_IP_ADDRESS, 0))
    self.deaflistenerport = self.deaflistener.getsockname()[1]

  def tearDown(self):
    del self.deaflistener
    del self.deaflistenerport

  def testTcpPingToLocalHostAcceptDeaf(self):
    self.failIf(TcpPing(constants.LOCALHOST_IP_ADDRESS,
                        self.deaflistenerport,
                        timeout=constants.TCP_PING_TIMEOUT,
                        live_port_needed=True,
                        source=constants.LOCALHOST_IP_ADDRESS,
                        ), # need successful connect(2)
                "successfully connected to deaf listener")

    self.failIf(TcpPing(constants.LOCALHOST_IP_ADDRESS,
                        self.deaflistenerport,
                        timeout=constants.TCP_PING_TIMEOUT,
                        live_port_needed=True,
                        ), # need successful connect(2)
                "successfully connected to deaf listener (no source addr)")

  def testTcpPingToLocalHostNoAccept(self):
    self.assert_(TcpPing(constants.LOCALHOST_IP_ADDRESS,
                         self.deaflistenerport,
                         timeout=constants.TCP_PING_TIMEOUT,
                         live_port_needed=False,
                         source=constants.LOCALHOST_IP_ADDRESS,
                         ), # ECONNREFUSED is OK
                 "failed to ping alive host on deaf port")

    self.assert_(TcpPing(constants.LOCALHOST_IP_ADDRESS,
                         self.deaflistenerport,
                         timeout=constants.TCP_PING_TIMEOUT,
                         live_port_needed=False,
                         ), # ECONNREFUSED is OK
                 "failed to ping alive host on deaf port (no source addr)")


class TestOwnIpAddress(unittest.TestCase):
  """Testcase for OwnIpAddress"""

  def testOwnLoopback(self):
    """check having the loopback ip"""
    self.failUnless(OwnIpAddress(constants.LOCALHOST_IP_ADDRESS),
                    "Should own the loopback address")

  def testNowOwnAddress(self):
    """check that I don't own an address"""

    # network 192.0.2.0/24 is reserved for test/documentation as per
    # rfc 3330, so we *should* not have an address of this range... if
    # this fails, we should extend the test to multiple addresses
    DST_IP = "192.0.2.1"
    self.failIf(OwnIpAddress(DST_IP), "Should not own IP address %s" % DST_IP)


class TestListVisibleFiles(unittest.TestCase):
  """Test case for ListVisibleFiles"""

  def setUp(self):
    self.path = tempfile.mkdtemp()

  def tearDown(self):
    shutil.rmtree(self.path)

  def _test(self, files, expected):
    # Sort a copy
    expected = expected[:]
    expected.sort()

    for name in files:
      f = open(os.path.join(self.path, name), 'w')
      try:
        f.write("Test\n")
      finally:
        f.close()

    found = ListVisibleFiles(self.path)
    found.sort()

    self.assertEqual(found, expected)

  def testAllVisible(self):
    files = ["a", "b", "c"]
    expected = files
    self._test(files, expected)

  def testNoneVisible(self):
    files = [".a", ".b", ".c"]
    expected = []
    self._test(files, expected)

  def testSomeVisible(self):
    files = ["a", "b", ".c"]
    expected = ["a", "b"]
    self._test(files, expected)


class TestNewUUID(unittest.TestCase):
  """Test case for NewUUID"""

  _re_uuid = re.compile('^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-'
                        '[a-f0-9]{4}-[a-f0-9]{12}$')

  def runTest(self):
    self.failUnless(self._re_uuid.match(utils.NewUUID()))


class TestUniqueSequence(unittest.TestCase):
  """Test case for UniqueSequence"""

  def _test(self, input, expected):
    self.assertEqual(utils.UniqueSequence(input), expected)

  def runTest(self):
    # Ordered input
    self._test([1, 2, 3], [1, 2, 3])
    self._test([1, 1, 2, 2, 3, 3], [1, 2, 3])
    self._test([1, 2, 2, 3], [1, 2, 3])
    self._test([1, 2, 3, 3], [1, 2, 3])

    # Unordered input
    self._test([1, 2, 3, 1, 2, 3], [1, 2, 3])
    self._test([1, 1, 2, 3, 3, 1, 2], [1, 2, 3])

    # Strings
    self._test(["a", "a"], ["a"])
    self._test(["a", "b"], ["a", "b"])
    self._test(["a", "b", "a"], ["a", "b"])


class TestFirstFree(unittest.TestCase):
  """Test case for the FirstFree function"""

  def test(self):
    """Test FirstFree"""
    self.failUnlessEqual(FirstFree([0, 1, 3]), 2)
    self.failUnlessEqual(FirstFree([]), None)
    self.failUnlessEqual(FirstFree([3, 4, 6]), 0)
    self.failUnlessEqual(FirstFree([3, 4, 6], base=3), 5)
    self.failUnlessRaises(AssertionError, FirstFree, [0, 3, 4, 6], base=3)


class TestTailFile(testutils.GanetiTestCase):
  """Test case for the TailFile function"""

  def testEmpty(self):
    fname = self._CreateTempFile()
    self.failUnlessEqual(TailFile(fname), [])
    self.failUnlessEqual(TailFile(fname, lines=25), [])

  def testAllLines(self):
    data = ["test %d" % i for i in range(30)]
    for i in range(30):
      fname = self._CreateTempFile()
      fd = open(fname, "w")
      fd.write("\n".join(data[:i]))
      if i > 0:
        fd.write("\n")
      fd.close()
      self.failUnlessEqual(TailFile(fname, lines=i), data[:i])

  def testPartialLines(self):
    data = ["test %d" % i for i in range(30)]
    fname = self._CreateTempFile()
    fd = open(fname, "w")
    fd.write("\n".join(data))
    fd.write("\n")
    fd.close()
    for i in range(1, 30):
      self.failUnlessEqual(TailFile(fname, lines=i), data[-i:])

  def testBigFile(self):
    data = ["test %d" % i for i in range(30)]
    fname = self._CreateTempFile()
    fd = open(fname, "w")
    fd.write("X" * 1048576)
    fd.write("\n")
    fd.write("\n".join(data))
    fd.write("\n")
    fd.close()
    for i in range(1, 30):
      self.failUnlessEqual(TailFile(fname, lines=i), data[-i:])


class TestFileLock(unittest.TestCase):
  """Test case for the FileLock class"""

  def setUp(self):
    self.tmpfile = tempfile.NamedTemporaryFile()
    self.lock = utils.FileLock(self.tmpfile.name)

  def testSharedNonblocking(self):
    self.lock.Shared(blocking=False)
    self.lock.Close()

  def testExclusiveNonblocking(self):
    self.lock.Exclusive(blocking=False)
    self.lock.Close()

  def testUnlockNonblocking(self):
    self.lock.Unlock(blocking=False)
    self.lock.Close()

  def testSharedBlocking(self):
    self.lock.Shared(blocking=True)
    self.lock.Close()

  def testExclusiveBlocking(self):
    self.lock.Exclusive(blocking=True)
    self.lock.Close()

  def testUnlockBlocking(self):
    self.lock.Unlock(blocking=True)
    self.lock.Close()

  def testSharedExclusiveUnlock(self):
    self.lock.Shared(blocking=False)
    self.lock.Exclusive(blocking=False)
    self.lock.Unlock(blocking=False)
    self.lock.Close()

  def testExclusiveSharedUnlock(self):
    self.lock.Exclusive(blocking=False)
    self.lock.Shared(blocking=False)
    self.lock.Unlock(blocking=False)
    self.lock.Close()

  def testCloseShared(self):
    self.lock.Close()
    self.assertRaises(AssertionError, self.lock.Shared, blocking=False)

  def testCloseExclusive(self):
    self.lock.Close()
    self.assertRaises(AssertionError, self.lock.Exclusive, blocking=False)

  def testCloseUnlock(self):
    self.lock.Close()
    self.assertRaises(AssertionError, self.lock.Unlock, blocking=False)


class TestTimeFunctions(unittest.TestCase):
  """Test case for time functions"""

  def runTest(self):
    self.assertEqual(utils.SplitTime(1), (1, 0))
    self.assertEqual(utils.SplitTime(1.5), (1, 500000))
    self.assertEqual(utils.SplitTime(1218448917.4809151), (1218448917, 480915))
    self.assertEqual(utils.SplitTime(123.48012), (123, 480120))
    self.assertEqual(utils.SplitTime(123.9996), (123, 999600))
    self.assertEqual(utils.SplitTime(123.9995), (123, 999500))
    self.assertEqual(utils.SplitTime(123.9994), (123, 999400))
    self.assertEqual(utils.SplitTime(123.999999999), (123, 999999))

    self.assertRaises(AssertionError, utils.SplitTime, -1)

    self.assertEqual(utils.MergeTime((1, 0)), 1.0)
    self.assertEqual(utils.MergeTime((1, 500000)), 1.5)
    self.assertEqual(utils.MergeTime((1218448917, 500000)), 1218448917.5)

    self.assertEqual(round(utils.MergeTime((1218448917, 481000)), 3),
                     1218448917.481)
    self.assertEqual(round(utils.MergeTime((1, 801000)), 3), 1.801)

    self.assertRaises(AssertionError, utils.MergeTime, (0, -1))
    self.assertRaises(AssertionError, utils.MergeTime, (0, 1000000))
    self.assertRaises(AssertionError, utils.MergeTime, (0, 9999999))
    self.assertRaises(AssertionError, utils.MergeTime, (-1, 0))
    self.assertRaises(AssertionError, utils.MergeTime, (-9999, 0))


class FieldSetTestCase(unittest.TestCase):
  """Test case for FieldSets"""

  def testSimpleMatch(self):
    f = utils.FieldSet("a", "b", "c", "def")
    self.failUnless(f.Matches("a"))
    self.failIf(f.Matches("d"), "Substring matched")
    self.failIf(f.Matches("defghi"), "Prefix string matched")
    self.failIf(f.NonMatching(["b", "c"]))
    self.failIf(f.NonMatching(["a", "b", "c", "def"]))
    self.failUnless(f.NonMatching(["a", "d"]))

  def testRegexMatch(self):
    f = utils.FieldSet("a", "b([0-9]+)", "c")
    self.failUnless(f.Matches("b1"))
    self.failUnless(f.Matches("b99"))
    self.failIf(f.Matches("b/1"))
    self.failIf(f.NonMatching(["b12", "c"]))
    self.failUnless(f.NonMatching(["a", "1"]))

class TestForceDictType(unittest.TestCase):
  """Test case for ForceDictType"""

  def setUp(self):
    self.key_types = {
      'a': constants.VTYPE_INT,
      'b': constants.VTYPE_BOOL,
      'c': constants.VTYPE_STRING,
      'd': constants.VTYPE_SIZE,
      }

  def _fdt(self, dict, allowed_values=None):
    if allowed_values is None:
      ForceDictType(dict, self.key_types)
    else:
      ForceDictType(dict, self.key_types, allowed_values=allowed_values)

    return dict

  def testSimpleDict(self):
    self.assertEqual(self._fdt({}), {})
    self.assertEqual(self._fdt({'a': 1}), {'a': 1})
    self.assertEqual(self._fdt({'a': '1'}), {'a': 1})
    self.assertEqual(self._fdt({'a': 1, 'b': 1}), {'a':1, 'b': True})
    self.assertEqual(self._fdt({'b': 1, 'c': 'foo'}), {'b': True, 'c': 'foo'})
    self.assertEqual(self._fdt({'b': 1, 'c': False}), {'b': True, 'c': ''})
    self.assertEqual(self._fdt({'b': 'false'}), {'b': False})
    self.assertEqual(self._fdt({'b': 'False'}), {'b': False})
    self.assertEqual(self._fdt({'b': 'true'}), {'b': True})
    self.assertEqual(self._fdt({'b': 'True'}), {'b': True})
    self.assertEqual(self._fdt({'d': '4'}), {'d': 4})
    self.assertEqual(self._fdt({'d': '4M'}), {'d': 4})

  def testErrors(self):
    self.assertRaises(errors.TypeEnforcementError, self._fdt, {'a': 'astring'})
    self.assertRaises(errors.TypeEnforcementError, self._fdt, {'c': True})
    self.assertRaises(errors.TypeEnforcementError, self._fdt, {'d': 'astring'})
    self.assertRaises(errors.TypeEnforcementError, self._fdt, {'d': '4 L'})


class TestIsAbsNormPath(unittest.TestCase):
  """Testing case for IsProcessAlive"""

  def _pathTestHelper(self, path, result):
    if result:
      self.assert_(IsNormAbsPath(path),
          "Path %s should result absolute and normalized" % path)
    else:
      self.assert_(not IsNormAbsPath(path),
          "Path %s should not result absolute and normalized" % path)

  def testBase(self):
    self._pathTestHelper('/etc', True)
    self._pathTestHelper('/srv', True)
    self._pathTestHelper('etc', False)
    self._pathTestHelper('/etc/../root', False)
    self._pathTestHelper('/etc/', False)


class TestSafeEncode(unittest.TestCase):
  """Test case for SafeEncode"""

  def testAscii(self):
    for txt in [string.digits, string.letters, string.punctuation]:
      self.failUnlessEqual(txt, SafeEncode(txt))

  def testDoubleEncode(self):
    for i in range(255):
      txt = SafeEncode(chr(i))
      self.failUnlessEqual(txt, SafeEncode(txt))

  def testUnicode(self):
    # 1024 is high enough to catch non-direct ASCII mappings
    for i in range(1024):
      txt = SafeEncode(unichr(i))
      self.failUnlessEqual(txt, SafeEncode(txt))


class TestFormatTime(unittest.TestCase):
  """Testing case for FormatTime"""

  def testNone(self):
    self.failUnlessEqual(FormatTime(None), "N/A")

  def testInvalid(self):
    self.failUnlessEqual(FormatTime(()), "N/A")

  def testNow(self):
    # tests that we accept time.time input
    FormatTime(time.time())
    # tests that we accept int input
    FormatTime(int(time.time()))


if __name__ == '__main__':
  testutils.GanetiTestProgram()
