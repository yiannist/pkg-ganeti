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


"""QA configuration.

"""


import yaml

import qa_error


cfg = None
options = None


def Load(path):
  """Loads the passed configuration file.

  """
  global cfg

  f = open(path, 'r')
  try:
    cfg = yaml.load(f.read())
  finally:
    f.close()

  Validate()


def Validate():
  if len(cfg['nodes']) < 1:
    raise qa_error.Error("Need at least one node")
  if len(cfg['instances']) < 1:
    raise qa_error.Error("Need at least one instance")

  if (TestEnabled('instance-add-remote-raid-disk') and
      TestEnabled('instance-add-drbd-disk')):
    raise qa_error.Error('Tests for disk templates remote_raid1 and drbd'
                         ' cannot be enabled at the same time.')


def get(name, default=None):
  return cfg.get(name, default)


def TestEnabled(test):
  """Returns True if the given test is enabled."""
  return cfg.get('tests', {}).get(test, False)


def GetMasterNode():
  return cfg['nodes'][0]


def AcquireInstance():
  """Returns an instance which isn't in use.

  """
  # Filter out unwanted instances
  tmp_flt = lambda inst: not inst.get('_used', False)
  instances = filter(tmp_flt, cfg['instances'])
  del tmp_flt

  if len(instances) == 0:
    raise qa_error.OutOfInstancesError("No instances left")

  inst = instances[0]
  inst['_used'] = True
  return inst


def ReleaseInstance(inst):
  inst['_used'] = False


def AcquireNode(exclude=None):
  """Returns the least used node.

  """
  master = GetMasterNode()

  # Filter out unwanted nodes
  # TODO: Maybe combine filters
  if exclude is None:
    nodes = cfg['nodes'][:]
  elif isinstance(exclude, (list, tuple)):
    nodes = filter(lambda node: node not in exclude, cfg['nodes'])
  else:
    nodes = filter(lambda node: node != exclude, cfg['nodes'])

  tmp_flt = lambda node: node.get('_added', False) or node == master
  nodes = filter(tmp_flt, nodes)
  del tmp_flt

  if len(nodes) == 0:
    raise qa_error.OutOfNodesError("No nodes left")

  # Get node with least number of uses
  def compare(a, b):
    result = cmp(a.get('_count', 0), b.get('_count', 0))
    if result == 0:
      result = cmp(a['primary'], b['primary'])
    return result

  nodes.sort(cmp=compare)

  node = nodes[0]
  node['_count'] = node.get('_count', 0) + 1
  return node


def ReleaseNode(node):
  node['_count'] = node.get('_count', 0) - 1
