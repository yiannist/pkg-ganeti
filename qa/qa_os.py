#
#

# Copyright (C) 2007 Google Inc.
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


"""OS related QA tests.

"""

import os
import os.path

from ganeti import utils
from ganeti import constants

import qa_config
import qa_utils

from qa_utils import AssertEqual, StartSSH


_TEMP_OS_NAME = "TEMP-Ganeti-QA-OS"
_TEMP_OS_PATH = os.path.join(constants.OS_SEARCH_PATH[0], _TEMP_OS_NAME)


def TestOsList():
  """gnt-os list"""
  master = qa_config.GetMasterNode()

  cmd = ['gnt-os', 'list']
  AssertEqual(StartSSH(master['primary'],
                       utils.ShellQuoteArgs(cmd)).wait(), 0)


def TestOsDiagnose():
  """gnt-os diagnose"""
  master = qa_config.GetMasterNode()

  cmd = ['gnt-os', 'diagnose']
  AssertEqual(StartSSH(master['primary'],
                       utils.ShellQuoteArgs(cmd)).wait(), 0)


def _SetupTempOs(node, dir, valid):
  """Creates a temporary OS definition on the given node.

  """
  sq = utils.ShellQuoteArgs
  parts = [sq(["rm", "-rf", dir]),
           sq(["mkdir", "-p", dir]),
           sq(["cd", dir]),
           sq(["ln", "-fs", "/bin/true", "export"]),
           sq(["ln", "-fs", "/bin/true", "import"]),
           sq(["ln", "-fs", "/bin/true", "rename"])]

  if valid:
    parts.append(sq(["ln", "-fs", "/bin/true", "create"]))

  parts.append(sq(["echo", str(constants.OS_API_VERSION)]) +
               " >ganeti_api_version")

  cmd = ' && '.join(parts)

  print qa_utils.FormatInfo("Setting up %s with %s OS definition" %
                            (node["primary"],
                             ["an invalid", "a valid"][int(valid)]))

  AssertEqual(StartSSH(node['primary'], cmd).wait(), 0)


def _RemoveTempOs(node, dir):
  """Removes a temporary OS definition.

  """
  cmd = ['rm', '-rf', dir]
  AssertEqual(StartSSH(node['primary'],
                       utils.ShellQuoteArgs(cmd)).wait(), 0)


def _TestOs(mode):
  """Generic function for OS definition testing

  """
  master = qa_config.GetMasterNode()
  dir = _TEMP_OS_PATH

  nodes = []
  try:
    i = 0
    for node in qa_config.get('nodes'):
      nodes.append(node)
      if mode == 0:
        valid = False
      elif mode == 1:
        valid = True
      else:
        valid = bool(i % 2)
      _SetupTempOs(node, dir, valid)
      i += 1

    cmd = ['gnt-os', 'diagnose']
    result = StartSSH(master['primary'],
                      utils.ShellQuoteArgs(cmd)).wait()
    if mode == 1:
      AssertEqual(result, 0)
    else:
      AssertEqual(result, 1)
  finally:
    for node in nodes:
      _RemoveTempOs(node, dir)


def TestOsValid():
  """Testing valid OS definition"""
  return _TestOs(1)


def TestOsInvalid():
  """Testing invalid OS definition"""
  return _TestOs(0)


def TestOsPartiallyValid():
  """Testing partially valid OS definition"""
  return _TestOs(2)
