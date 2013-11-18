#
#

# Copyright (C) 2010, 2011, 2012, 2013 Google Inc.
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

"""Node group related commands"""

# pylint: disable=W0401,W0614
# W0401: Wildcard import ganeti.cli
# W0614: Unused import %s from wildcard import (since we need cli)

from cStringIO import StringIO

from ganeti.cli import *
from ganeti import constants
from ganeti import opcodes
from ganeti import utils
from ganeti import compat


#: default list of fields for L{ListGroups}
_LIST_DEF_FIELDS = ["name", "node_cnt", "pinst_cnt", "alloc_policy", "ndparams"]

_ENV_OVERRIDE = compat.UniqueFrozenset(["list"])


def AddGroup(opts, args):
  """Add a node group to the cluster.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: a list of length 1 with the name of the group to create
  @rtype: int
  @return: the desired exit code

  """
  ipolicy = CreateIPolicyFromOpts(
    minmax_ispecs=opts.ipolicy_bounds_specs,
    ipolicy_vcpu_ratio=opts.ipolicy_vcpu_ratio,
    ipolicy_spindle_ratio=opts.ipolicy_spindle_ratio,
    ipolicy_disk_templates=opts.ipolicy_disk_templates,
    group_ipolicy=True)

  (group_name,) = args
  diskparams = dict(opts.diskparams)

  if opts.disk_state:
    disk_state = utils.FlatToDict(opts.disk_state)
  else:
    disk_state = {}
  hv_state = dict(opts.hv_state)

  op = opcodes.OpGroupAdd(group_name=group_name, ndparams=opts.ndparams,
                          alloc_policy=opts.alloc_policy,
                          diskparams=diskparams, ipolicy=ipolicy,
                          hv_state=hv_state,
                          disk_state=disk_state)
  SubmitOrSend(op, opts)


def AssignNodes(opts, args):
  """Assign nodes to a group.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: args[0]: group to assign nodes to; args[1:]: nodes to assign
  @rtype: int
  @return: the desired exit code

  """
  group_name = args[0]
  node_names = args[1:]

  op = opcodes.OpGroupAssignNodes(group_name=group_name, nodes=node_names,
                                  force=opts.force)
  SubmitOrSend(op, opts)


def _FmtDict(data):
  """Format dict data into command-line format.

  @param data: The input dict to be formatted
  @return: The formatted dict

  """
  if not data:
    return "(empty)"

  return utils.CommaJoin(["%s=%s" % (key, value)
                          for key, value in data.items()])


def ListGroups(opts, args):
  """List node groups and their properties.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: groups to list, or empty for all
  @rtype: int
  @return: the desired exit code

  """
  desired_fields = ParseFields(opts.output, _LIST_DEF_FIELDS)
  fmtoverride = {
    "node_list": (",".join, False),
    "pinst_list": (",".join, False),
    "ndparams": (_FmtDict, False),
    }

  cl = GetClient(query=True)

  return GenericList(constants.QR_GROUP, desired_fields, args, None,
                     opts.separator, not opts.no_headers,
                     format_override=fmtoverride, verbose=opts.verbose,
                     force_filter=opts.force_filter, cl=cl)


def ListGroupFields(opts, args):
  """List node fields.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: fields to list, or empty for all
  @rtype: int
  @return: the desired exit code

  """
  cl = GetClient(query=True)

  return GenericListFields(constants.QR_GROUP, args, opts.separator,
                           not opts.no_headers, cl=cl)


def SetGroupParams(opts, args):
  """Modifies a node group's parameters.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should contain only one element, the node group name

  @rtype: int
  @return: the desired exit code

  """
  allmods = [opts.ndparams, opts.alloc_policy, opts.diskparams, opts.hv_state,
             opts.disk_state, opts.ipolicy_bounds_specs,
             opts.ipolicy_vcpu_ratio, opts.ipolicy_spindle_ratio,
             opts.diskparams, opts.ipolicy_disk_templates]
  if allmods.count(None) == len(allmods):
    ToStderr("Please give at least one of the parameters.")
    return 1

  if opts.disk_state:
    disk_state = utils.FlatToDict(opts.disk_state)
  else:
    disk_state = {}

  hv_state = dict(opts.hv_state)

  diskparams = dict(opts.diskparams)

  # create ipolicy object
  ipolicy = CreateIPolicyFromOpts(
    minmax_ispecs=opts.ipolicy_bounds_specs,
    ipolicy_disk_templates=opts.ipolicy_disk_templates,
    ipolicy_vcpu_ratio=opts.ipolicy_vcpu_ratio,
    ipolicy_spindle_ratio=opts.ipolicy_spindle_ratio,
    group_ipolicy=True,
    allowed_values=[constants.VALUE_DEFAULT])

  op = opcodes.OpGroupSetParams(group_name=args[0],
                                ndparams=opts.ndparams,
                                alloc_policy=opts.alloc_policy,
                                hv_state=hv_state,
                                disk_state=disk_state,
                                diskparams=diskparams,
                                ipolicy=ipolicy)

  result = SubmitOrSend(op, opts)

  if result:
    ToStdout("Modified node group %s", args[0])
    for param, data in result:
      ToStdout(" - %-5s -> %s", param, data)

  return 0


def RemoveGroup(opts, args):
  """Remove a node group from the cluster.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: a list of length 1 with the name of the group to remove
  @rtype: int
  @return: the desired exit code

  """
  (group_name,) = args
  op = opcodes.OpGroupRemove(group_name=group_name)
  SubmitOrSend(op, opts)


def RenameGroup(opts, args):
  """Rename a node group.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: a list of length 2, [old_name, new_name]
  @rtype: int
  @return: the desired exit code

  """
  group_name, new_name = args
  op = opcodes.OpGroupRename(group_name=group_name, new_name=new_name)
  SubmitOrSend(op, opts)


def EvacuateGroup(opts, args):
  """Evacuate a node group.

  """
  (group_name, ) = args

  cl = GetClient()

  op = opcodes.OpGroupEvacuate(group_name=group_name,
                               iallocator=opts.iallocator,
                               target_groups=opts.to,
                               early_release=opts.early_release)
  result = SubmitOrSend(op, opts, cl=cl)

  # Keep track of submitted jobs
  jex = JobExecutor(cl=cl, opts=opts)

  for (status, job_id) in result[constants.JOB_IDS_KEY]:
    jex.AddJobId(None, status, job_id)

  results = jex.GetResults()
  bad_cnt = len([row for row in results if not row[0]])
  if bad_cnt == 0:
    ToStdout("All instances evacuated successfully.")
    rcode = constants.EXIT_SUCCESS
  else:
    ToStdout("There were %s errors during the evacuation.", bad_cnt)
    rcode = constants.EXIT_FAILURE

  return rcode


def _FormatGroupInfo(group):
  (name, ndparams, custom_ndparams, diskparams, custom_diskparams,
   ipolicy, custom_ipolicy) = group
  return [
    ("Node group", name),
    ("Node parameters", FormatParamsDictInfo(custom_ndparams, ndparams)),
    ("Disk parameters", FormatParamsDictInfo(custom_diskparams, diskparams)),
    ("Instance policy", FormatPolicyInfo(custom_ipolicy, ipolicy, False)),
    ]


def GroupInfo(_, args):
  """Shows info about node group.

  """
  cl = GetClient(query=True)
  selected_fields = ["name",
                     "ndparams", "custom_ndparams",
                     "diskparams", "custom_diskparams",
                     "ipolicy", "custom_ipolicy"]
  result = cl.QueryGroups(names=args, fields=selected_fields,
                          use_locking=False)

  PrintGenericInfo([
    _FormatGroupInfo(group) for group in result
    ])


def _GetCreateCommand(group):
  (name, ipolicy) = group
  buf = StringIO()
  buf.write("gnt-group add")
  PrintIPolicyCommand(buf, ipolicy, True)
  buf.write(" ")
  buf.write(name)
  return buf.getvalue()


def ShowCreateCommand(opts, args):
  """Shows the command that can be used to re-create a node group.

  Currently it works only for ipolicy specs.

  """
  cl = GetClient(query=True)
  selected_fields = ["name"]
  if opts.include_defaults:
    selected_fields += ["ipolicy"]
  else:
    selected_fields += ["custom_ipolicy"]
  result = cl.QueryGroups(names=args, fields=selected_fields,
                          use_locking=False)

  for group in result:
    ToStdout(_GetCreateCommand(group))


commands = {
  "add": (
    AddGroup, ARGS_ONE_GROUP,
    [DRY_RUN_OPT, ALLOC_POLICY_OPT, NODE_PARAMS_OPT, DISK_PARAMS_OPT,
     HV_STATE_OPT, DISK_STATE_OPT, PRIORITY_OPT]
    + SUBMIT_OPTS + INSTANCE_POLICY_OPTS,
    "<group_name>", "Add a new node group to the cluster"),
  "assign-nodes": (
    AssignNodes, ARGS_ONE_GROUP + ARGS_MANY_NODES,
    [DRY_RUN_OPT, FORCE_OPT, PRIORITY_OPT] + SUBMIT_OPTS,
    "<group_name> <node>...", "Assign nodes to a group"),
  "list": (
    ListGroups, ARGS_MANY_GROUPS,
    [NOHDR_OPT, SEP_OPT, FIELDS_OPT, VERBOSE_OPT, FORCE_FILTER_OPT],
    "[<group_name>...]",
    "Lists the node groups in the cluster. The available fields can be shown"
    " using the \"list-fields\" command (see the man page for details)."
    " The default list is (in order): %s." % utils.CommaJoin(_LIST_DEF_FIELDS)),
  "list-fields": (
    ListGroupFields, [ArgUnknown()], [NOHDR_OPT, SEP_OPT], "[fields...]",
    "Lists all available fields for node groups"),
  "modify": (
    SetGroupParams, ARGS_ONE_GROUP,
    [DRY_RUN_OPT] + SUBMIT_OPTS +
    [ALLOC_POLICY_OPT, NODE_PARAMS_OPT, HV_STATE_OPT, DISK_STATE_OPT,
     DISK_PARAMS_OPT, PRIORITY_OPT]
    + INSTANCE_POLICY_OPTS,
    "<group_name>", "Alters the parameters of a node group"),
  "remove": (
    RemoveGroup, ARGS_ONE_GROUP, [DRY_RUN_OPT, PRIORITY_OPT] + SUBMIT_OPTS,
    "[--dry-run] <group-name>",
    "Remove an (empty) node group from the cluster"),
  "rename": (
    RenameGroup, [ArgGroup(min=2, max=2)],
    [DRY_RUN_OPT] + SUBMIT_OPTS + [PRIORITY_OPT],
    "[--dry-run] <group-name> <new-name>", "Rename a node group"),
  "evacuate": (
    EvacuateGroup, [ArgGroup(min=1, max=1)],
    [TO_GROUP_OPT, IALLOCATOR_OPT, EARLY_RELEASE_OPT] + SUBMIT_OPTS,
    "[-I <iallocator>] [--to <group>]",
    "Evacuate all instances within a group"),
  "list-tags": (
    ListTags, ARGS_ONE_GROUP, [],
    "<group_name>", "List the tags of the given group"),
  "add-tags": (
    AddTags, [ArgGroup(min=1, max=1), ArgUnknown()],
    [TAG_SRC_OPT, PRIORITY_OPT] + SUBMIT_OPTS,
    "<group_name> tag...", "Add tags to the given group"),
  "remove-tags": (
    RemoveTags, [ArgGroup(min=1, max=1), ArgUnknown()],
    [TAG_SRC_OPT, PRIORITY_OPT] + SUBMIT_OPTS,
    "<group_name> tag...", "Remove tags from the given group"),
  "info": (
    GroupInfo, ARGS_MANY_GROUPS, [], "[<group_name>...]",
    "Show group information"),
  "show-ispecs-cmd": (
    ShowCreateCommand, ARGS_MANY_GROUPS, [INCLUDEDEFAULTS_OPT],
    "[--include-defaults] [<group_name>...]",
    "Show the command line to re-create a group"),
  }


def Main():
  return GenericMain(commands,
                     override={"tag_type": constants.TAG_NODEGROUP},
                     env_override=_ENV_OVERRIDE)
