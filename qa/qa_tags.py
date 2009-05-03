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


"""Tags related QA tests.

"""

from ganeti import utils

import qa_config
import qa_utils

from qa_utils import AssertEqual, StartSSH


_TEMP_TAG_NAMES = ["TEMP-Ganeti-QA-Tag%d" % i for i in range(3)]
_TEMP_TAG_RE = r'^TEMP-Ganeti-QA-Tag\d+$'


def _TestTags(cmdfn):
  """Generic function for add-tags.

  """
  master = qa_config.GetMasterNode()

  cmd = cmdfn('add-tags') + _TEMP_TAG_NAMES
  AssertEqual(StartSSH(master['primary'],
                       utils.ShellQuoteArgs(cmd)).wait(), 0)

  cmd = cmdfn('list-tags')
  AssertEqual(StartSSH(master['primary'],
                       utils.ShellQuoteArgs(cmd)).wait(), 0)

  cmd = ['gnt-cluster', 'search-tags', _TEMP_TAG_RE]
  AssertEqual(StartSSH(master['primary'],
                       utils.ShellQuoteArgs(cmd)).wait(), 0)

  cmd = cmdfn('remove-tags') + _TEMP_TAG_NAMES
  AssertEqual(StartSSH(master['primary'],
                       utils.ShellQuoteArgs(cmd)).wait(), 0)


@qa_utils.DefineHook('tags-cluster')
def TestClusterTags():
  """gnt-cluster tags"""
  _TestTags(lambda subcmd: ['gnt-cluster', subcmd])


@qa_utils.DefineHook('tags-node')
def TestNodeTags(node):
  """gnt-node tags"""
  _TestTags(lambda subcmd: ['gnt-node', subcmd, node['primary']])


@qa_utils.DefineHook('tags-instance')
def TestInstanceTags(instance):
  """gnt-instance tags"""
  _TestTags(lambda subcmd: ['gnt-instance', subcmd, instance['name']])
