#
#

# Copyright (C) 2006, 2007, 2008, 2009, 2010, 2011 Google Inc.
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

"""Node related commands"""

# pylint: disable-msg=W0401,W0613,W0614,C0103
# W0401: Wildcard import ganeti.cli
# W0613: Unused argument, since all functions follow the same API
# W0614: Unused import %s from wildcard import (since we need cli)
# C0103: Invalid name gnt-node

from ganeti.cli import *
from ganeti import cli
from ganeti import bootstrap
from ganeti import opcodes
from ganeti import utils
from ganeti import constants
from ganeti import errors
from ganeti import netutils
from cStringIO import StringIO


#: default list of field for L{ListNodes}
_LIST_DEF_FIELDS = [
  "name", "dtotal", "dfree",
  "mtotal", "mnode", "mfree",
  "pinst_cnt", "sinst_cnt",
  ]


#: Default field list for L{ListVolumes}
_LIST_VOL_DEF_FIELDS = ["node", "phys", "vg", "name", "size", "instance"]


#: default list of field for L{ListStorage}
_LIST_STOR_DEF_FIELDS = [
  constants.SF_NODE,
  constants.SF_TYPE,
  constants.SF_NAME,
  constants.SF_SIZE,
  constants.SF_USED,
  constants.SF_FREE,
  constants.SF_ALLOCATABLE,
  ]


#: default list of power commands
_LIST_POWER_COMMANDS = ["on", "off", "cycle", "status"]


#: headers (and full field list) for L{ListStorage}
_LIST_STOR_HEADERS = {
  constants.SF_NODE: "Node",
  constants.SF_TYPE: "Type",
  constants.SF_NAME: "Name",
  constants.SF_SIZE: "Size",
  constants.SF_USED: "Used",
  constants.SF_FREE: "Free",
  constants.SF_ALLOCATABLE: "Allocatable",
  }


#: User-facing storage unit types
_USER_STORAGE_TYPE = {
  constants.ST_FILE: "file",
  constants.ST_LVM_PV: "lvm-pv",
  constants.ST_LVM_VG: "lvm-vg",
  }

_STORAGE_TYPE_OPT = \
  cli_option("-t", "--storage-type",
             dest="user_storage_type",
             choices=_USER_STORAGE_TYPE.keys(),
             default=None,
             metavar="STORAGE_TYPE",
             help=("Storage type (%s)" %
                   utils.CommaJoin(_USER_STORAGE_TYPE.keys())))

_REPAIRABLE_STORAGE_TYPES = \
  [st for st, so in constants.VALID_STORAGE_OPERATIONS.iteritems()
   if constants.SO_FIX_CONSISTENCY in so]

_MODIFIABLE_STORAGE_TYPES = constants.MODIFIABLE_STORAGE_FIELDS.keys()


NONODE_SETUP_OPT = cli_option("--no-node-setup", default=True,
                              action="store_false", dest="node_setup",
                              help=("Do not make initial SSH setup on remote"
                                    " node (needs to be done manually)"))


def ConvertStorageType(user_storage_type):
  """Converts a user storage type to its internal name.

  """
  try:
    return _USER_STORAGE_TYPE[user_storage_type]
  except KeyError:
    raise errors.OpPrereqError("Unknown storage type: %s" % user_storage_type,
                               errors.ECODE_INVAL)


def _RunSetupSSH(options, nodes):
  """Wrapper around utils.RunCmd to call setup-ssh

  @param options: The command line options
  @param nodes: The nodes to setup

  """
  cmd = [constants.SETUP_SSH]

  # Pass --debug|--verbose to the external script if set on our invocation
  # --debug overrides --verbose
  if options.debug:
    cmd.append("--debug")
  elif options.verbose:
    cmd.append("--verbose")
  if not options.ssh_key_check:
    cmd.append("--no-ssh-key-check")
  if options.force_join:
    cmd.append("--force-join")

  cmd.extend(nodes)

  result = utils.RunCmd(cmd, interactive=True)

  if result.failed:
    errmsg = ("Command '%s' failed with exit code %s; output %r" %
              (result.cmd, result.exit_code, result.output))
    raise errors.OpExecError(errmsg)


@UsesRPC
def AddNode(opts, args):
  """Add a node to the cluster.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should contain only one element, the new node name
  @rtype: int
  @return: the desired exit code

  """
  cl = GetClient()
  node = netutils.GetHostname(name=args[0]).name
  readd = opts.readd

  try:
    output = cl.QueryNodes(names=[node], fields=['name', 'sip', 'master'],
                           use_locking=False)
    node_exists, sip, is_master = output[0]
  except (errors.OpPrereqError, errors.OpExecError):
    node_exists = ""
    sip = None

  if readd:
    if not node_exists:
      ToStderr("Node %s not in the cluster"
               " - please retry without '--readd'", node)
      return 1
    if is_master:
      ToStderr("Node %s is the master, cannot readd", node)
      return 1
  else:
    if node_exists:
      ToStderr("Node %s already in the cluster (as %s)"
               " - please retry with '--readd'", node, node_exists)
      return 1
    sip = opts.secondary_ip

  # read the cluster name from the master
  output = cl.QueryConfigValues(['cluster_name'])
  cluster_name = output[0]

  if not readd and opts.node_setup:
    ToStderr("-- WARNING -- \n"
             "Performing this operation is going to replace the ssh daemon"
             " keypair\n"
             "on the target machine (%s) with the ones of the"
             " current one\n"
             "and grant full intra-cluster ssh root access to/from it\n", node)

  if opts.node_setup:
    _RunSetupSSH(opts, [node])

  bootstrap.SetupNodeDaemon(cluster_name, node, opts.ssh_key_check)

  op = opcodes.OpNodeAdd(node_name=args[0], secondary_ip=sip,
                         readd=opts.readd, group=opts.nodegroup,
                         vm_capable=opts.vm_capable, ndparams=opts.ndparams,
                         master_capable=opts.master_capable)
  SubmitOpCode(op, opts=opts)


def ListNodes(opts, args):
  """List nodes and their properties.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: nodes to list, or empty for all
  @rtype: int
  @return: the desired exit code

  """
  selected_fields = ParseFields(opts.output, _LIST_DEF_FIELDS)

  fmtoverride = dict.fromkeys(["pinst_list", "sinst_list", "tags"],
                              (",".join, False))

  return GenericList(constants.QR_NODE, selected_fields, args, opts.units,
                     opts.separator, not opts.no_headers,
                     format_override=fmtoverride, verbose=opts.verbose)


def ListNodeFields(opts, args):
  """List node fields.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: fields to list, or empty for all
  @rtype: int
  @return: the desired exit code

  """
  return GenericListFields(constants.QR_NODE, args, opts.separator,
                           not opts.no_headers)


def EvacuateNode(opts, args):
  """Relocate all secondary instance from a node.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should be an empty list
  @rtype: int
  @return: the desired exit code

  """
  cl = GetClient()
  force = opts.force

  dst_node = opts.dst_node
  iallocator = opts.iallocator

  op = opcodes.OpNodeEvacStrategy(nodes=args,
                                  iallocator=iallocator,
                                  remote_node=dst_node)

  result = SubmitOpCode(op, cl=cl, opts=opts)
  if not result:
    # no instances to migrate
    ToStderr("No secondary instances on node(s) %s, exiting.",
             utils.CommaJoin(args))
    return constants.EXIT_SUCCESS

  if not force and not AskUser("Relocate instance(s) %s from node(s) %s?" %
                               (",".join("'%s'" % name[0] for name in result),
                               utils.CommaJoin(args))):
    return constants.EXIT_CONFIRMATION

  jex = JobExecutor(cl=cl, opts=opts)
  for row in result:
    iname = row[0]
    node = row[1]
    ToStdout("Will relocate instance %s to node %s", iname, node)
    op = opcodes.OpInstanceReplaceDisks(instance_name=iname,
                                        remote_node=node, disks=[],
                                        mode=constants.REPLACE_DISK_CHG,
                                        early_release=opts.early_release)
    jex.QueueJob(iname, op)
  results = jex.GetResults()
  bad_cnt = len([row for row in results if not row[0]])
  if bad_cnt == 0:
    ToStdout("All %d instance(s) failed over successfully.", len(results))
    rcode = constants.EXIT_SUCCESS
  else:
    ToStdout("There were errors during the failover:\n"
             "%d error(s) out of %d instance(s).", bad_cnt, len(results))
    rcode = constants.EXIT_FAILURE
  return rcode


def FailoverNode(opts, args):
  """Failover all primary instance on a node.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should be an empty list
  @rtype: int
  @return: the desired exit code

  """
  cl = GetClient()
  force = opts.force
  selected_fields = ["name", "pinst_list"]

  # these fields are static data anyway, so it doesn't matter, but
  # locking=True should be safer
  result = cl.QueryNodes(names=args, fields=selected_fields,
                         use_locking=False)
  node, pinst = result[0]

  if not pinst:
    ToStderr("No primary instances on node %s, exiting.", node)
    return 0

  pinst = utils.NiceSort(pinst)

  retcode = 0

  if not force and not AskUser("Fail over instance(s) %s?" %
                               (",".join("'%s'" % name for name in pinst))):
    return 2

  jex = JobExecutor(cl=cl, opts=opts)
  for iname in pinst:
    op = opcodes.OpInstanceFailover(instance_name=iname,
                                    ignore_consistency=opts.ignore_consistency)
    jex.QueueJob(iname, op)
  results = jex.GetResults()
  bad_cnt = len([row for row in results if not row[0]])
  if bad_cnt == 0:
    ToStdout("All %d instance(s) failed over successfully.", len(results))
  else:
    ToStdout("There were errors during the failover:\n"
             "%d error(s) out of %d instance(s).", bad_cnt, len(results))
  return retcode


def MigrateNode(opts, args):
  """Migrate all primary instance on a node.

  """
  cl = GetClient()
  force = opts.force
  selected_fields = ["name", "pinst_list"]

  result = cl.QueryNodes(names=args, fields=selected_fields, use_locking=False)
  node, pinst = result[0]

  if not pinst:
    ToStdout("No primary instances on node %s, exiting." % node)
    return 0

  pinst = utils.NiceSort(pinst)

  if not force and not AskUser("Migrate instance(s) %s?" %
                               (",".join("'%s'" % name for name in pinst))):
    return 2

  # this should be removed once --non-live is deprecated
  if not opts.live and opts.migration_mode is not None:
    raise errors.OpPrereqError("Only one of the --non-live and "
                               "--migration-mode options can be passed",
                               errors.ECODE_INVAL)
  if not opts.live: # --non-live passed
    mode = constants.HT_MIGRATION_NONLIVE
  else:
    mode = opts.migration_mode
  op = opcodes.OpNodeMigrate(node_name=args[0], mode=mode)
  SubmitOpCode(op, cl=cl, opts=opts)


def ShowNodeConfig(opts, args):
  """Show node information.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should either be an empty list, in which case
      we show information about all nodes, or should contain
      a list of nodes to be queried for information
  @rtype: int
  @return: the desired exit code

  """
  cl = GetClient()
  result = cl.QueryNodes(fields=["name", "pip", "sip",
                                 "pinst_list", "sinst_list",
                                 "master_candidate", "drained", "offline",
                                 "master_capable", "vm_capable", "powered",
                                 "ndparams", "custom_ndparams"],
                         names=args, use_locking=False)

  for (name, primary_ip, secondary_ip, pinst, sinst, is_mc, drained, offline,
       master_capable, vm_capable, powered, ndparams,
       ndparams_custom) in result:
    ToStdout("Node name: %s", name)
    ToStdout("  primary ip: %s", primary_ip)
    ToStdout("  secondary ip: %s", secondary_ip)
    ToStdout("  master candidate: %s", is_mc)
    ToStdout("  drained: %s", drained)
    ToStdout("  offline: %s", offline)
    if powered is not None:
      ToStdout("  powered: %s", powered)
    ToStdout("  master_capable: %s", master_capable)
    ToStdout("  vm_capable: %s", vm_capable)
    if vm_capable:
      if pinst:
        ToStdout("  primary for instances:")
        for iname in utils.NiceSort(pinst):
          ToStdout("    - %s", iname)
      else:
        ToStdout("  primary for no instances")
      if sinst:
        ToStdout("  secondary for instances:")
        for iname in utils.NiceSort(sinst):
          ToStdout("    - %s", iname)
      else:
        ToStdout("  secondary for no instances")
    ToStdout("  node parameters:")
    buf = StringIO()
    FormatParameterDict(buf, ndparams_custom, ndparams, level=2)
    ToStdout(buf.getvalue().rstrip("\n"))

  return 0


def RemoveNode(opts, args):
  """Remove a node from the cluster.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should contain only one element, the name of
      the node to be removed
  @rtype: int
  @return: the desired exit code

  """
  op = opcodes.OpNodeRemove(node_name=args[0])
  SubmitOpCode(op, opts=opts)
  return 0


def PowercycleNode(opts, args):
  """Remove a node from the cluster.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should contain only one element, the name of
      the node to be removed
  @rtype: int
  @return: the desired exit code

  """
  node = args[0]
  if (not opts.confirm and
      not AskUser("Are you sure you want to hard powercycle node %s?" % node)):
    return 2

  op = opcodes.OpNodePowercycle(node_name=node, force=opts.force)
  result = SubmitOpCode(op, opts=opts)
  if result:
    ToStderr(result)
  return 0


def PowerNode(opts, args):
  """Change/ask power state of a node.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should contain only one element, the name of
      the node to be removed
  @rtype: int
  @return: the desired exit code

  """
  command = args[0]
  node = args[1]

  if command not in _LIST_POWER_COMMANDS:
    ToStderr("power subcommand %s not supported." % command)
    return constants.EXIT_FAILURE

  oob_command = "power-%s" % command

  opcodelist = []
  if oob_command == constants.OOB_POWER_OFF:
    opcodelist.append(opcodes.OpNodeSetParams(node_name=node, offline=True,
                                              auto_promote=opts.auto_promote))

  opcodelist.append(opcodes.OpOobCommand(node_names=[node],
                                         command=oob_command))

  cli.SetGenericOpcodeOpts(opcodelist, opts)

  job_id = cli.SendJob(opcodelist)

  # We just want the OOB Opcode status
  # If it fails PollJob gives us the error message in it
  result = cli.PollJob(job_id)[-1]

  if result:
    (_, data_tuple) = result[0]
    if data_tuple[0] != constants.RS_NORMAL:
      if data_tuple[0] == constants.RS_UNAVAIL:
        result = "OOB is not supported"
      else:
        result = "RPC failed, look out for warning in the output"
      ToStderr(result)
      return constants.EXIT_FAILURE
    else:
      if oob_command == constants.OOB_POWER_STATUS:
        text = "The machine is %spowered"
        if data_tuple[1][constants.OOB_POWER_STATUS_POWERED]:
          result = text % ""
        else:
          result = text % "not "
        ToStdout(result)

  return constants.EXIT_SUCCESS


def ListVolumes(opts, args):
  """List logical volumes on node(s).

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should either be an empty list, in which case
      we list data for all nodes, or contain a list of nodes
      to display data only for those
  @rtype: int
  @return: the desired exit code

  """
  selected_fields = ParseFields(opts.output, _LIST_VOL_DEF_FIELDS)

  op = opcodes.OpNodeQueryvols(nodes=args, output_fields=selected_fields)
  output = SubmitOpCode(op, opts=opts)

  if not opts.no_headers:
    headers = {"node": "Node", "phys": "PhysDev",
               "vg": "VG", "name": "Name",
               "size": "Size", "instance": "Instance"}
  else:
    headers = None

  unitfields = ["size"]

  numfields = ["size"]

  data = GenerateTable(separator=opts.separator, headers=headers,
                       fields=selected_fields, unitfields=unitfields,
                       numfields=numfields, data=output, units=opts.units)

  for line in data:
    ToStdout(line)

  return 0


def ListStorage(opts, args):
  """List physical volumes on node(s).

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should either be an empty list, in which case
      we list data for all nodes, or contain a list of nodes
      to display data only for those
  @rtype: int
  @return: the desired exit code

  """
  # TODO: Default to ST_FILE if LVM is disabled on the cluster
  if opts.user_storage_type is None:
    opts.user_storage_type = constants.ST_LVM_PV

  storage_type = ConvertStorageType(opts.user_storage_type)

  selected_fields = ParseFields(opts.output, _LIST_STOR_DEF_FIELDS)

  op = opcodes.OpNodeQueryStorage(nodes=args,
                                  storage_type=storage_type,
                                  output_fields=selected_fields)
  output = SubmitOpCode(op, opts=opts)

  if not opts.no_headers:
    headers = {
      constants.SF_NODE: "Node",
      constants.SF_TYPE: "Type",
      constants.SF_NAME: "Name",
      constants.SF_SIZE: "Size",
      constants.SF_USED: "Used",
      constants.SF_FREE: "Free",
      constants.SF_ALLOCATABLE: "Allocatable",
      }
  else:
    headers = None

  unitfields = [constants.SF_SIZE, constants.SF_USED, constants.SF_FREE]
  numfields = [constants.SF_SIZE, constants.SF_USED, constants.SF_FREE]

  # change raw values to nicer strings
  for row in output:
    for idx, field in enumerate(selected_fields):
      val = row[idx]
      if field == constants.SF_ALLOCATABLE:
        if val:
          val = "Y"
        else:
          val = "N"
      row[idx] = str(val)

  data = GenerateTable(separator=opts.separator, headers=headers,
                       fields=selected_fields, unitfields=unitfields,
                       numfields=numfields, data=output, units=opts.units)

  for line in data:
    ToStdout(line)

  return 0


def ModifyStorage(opts, args):
  """Modify storage volume on a node.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should contain 3 items: node name, storage type and volume name
  @rtype: int
  @return: the desired exit code

  """
  (node_name, user_storage_type, volume_name) = args

  storage_type = ConvertStorageType(user_storage_type)

  changes = {}

  if opts.allocatable is not None:
    changes[constants.SF_ALLOCATABLE] = opts.allocatable

  if changes:
    op = opcodes.OpNodeModifyStorage(node_name=node_name,
                                     storage_type=storage_type,
                                     name=volume_name,
                                     changes=changes)
    SubmitOpCode(op, opts=opts)
  else:
    ToStderr("No changes to perform, exiting.")


def RepairStorage(opts, args):
  """Repairs a storage volume on a node.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should contain 3 items: node name, storage type and volume name
  @rtype: int
  @return: the desired exit code

  """
  (node_name, user_storage_type, volume_name) = args

  storage_type = ConvertStorageType(user_storage_type)

  op = opcodes.OpRepairNodeStorage(node_name=node_name,
                                   storage_type=storage_type,
                                   name=volume_name,
                                   ignore_consistency=opts.ignore_consistency)
  SubmitOpCode(op, opts=opts)


def SetNodeParams(opts, args):
  """Modifies a node.

  @param opts: the command line options selected by the user
  @type args: list
  @param args: should contain only one element, the node name
  @rtype: int
  @return: the desired exit code

  """
  all_changes = [opts.master_candidate, opts.drained, opts.offline,
                 opts.master_capable, opts.vm_capable, opts.secondary_ip,
                 opts.ndparams]
  if all_changes.count(None) == len(all_changes):
    ToStderr("Please give at least one of the parameters.")
    return 1

  op = opcodes.OpNodeSetParams(node_name=args[0],
                               master_candidate=opts.master_candidate,
                               offline=opts.offline,
                               drained=opts.drained,
                               master_capable=opts.master_capable,
                               vm_capable=opts.vm_capable,
                               secondary_ip=opts.secondary_ip,
                               force=opts.force,
                               ndparams=opts.ndparams,
                               auto_promote=opts.auto_promote,
                               powered=opts.node_powered)

  # even if here we process the result, we allow submit only
  result = SubmitOrSend(op, opts)

  if result:
    ToStdout("Modified node %s", args[0])
    for param, data in result:
      ToStdout(" - %-5s -> %s", param, data)
  return 0


commands = {
  'add': (
    AddNode, [ArgHost(min=1, max=1)],
    [SECONDARY_IP_OPT, READD_OPT, NOSSH_KEYCHECK_OPT, NODE_FORCE_JOIN_OPT,
     NONODE_SETUP_OPT, VERBOSE_OPT, NODEGROUP_OPT, PRIORITY_OPT,
     CAPAB_MASTER_OPT, CAPAB_VM_OPT, NODE_PARAMS_OPT],
    "[-s ip] [--readd] [--no-ssh-key-check] [--force-join]"
    " [--no-node-setup] [--verbose]"
    " <node_name>",
    "Add a node to the cluster"),
  'evacuate': (
    EvacuateNode, [ArgNode(min=1)],
    [FORCE_OPT, IALLOCATOR_OPT, NEW_SECONDARY_OPT, EARLY_RELEASE_OPT,
     PRIORITY_OPT],
    "[-f] {-I <iallocator> | -n <dst>} <node>",
    "Relocate the secondary instances from a node"
    " to other nodes (only for instances with drbd disk template)"),
  'failover': (
    FailoverNode, ARGS_ONE_NODE, [FORCE_OPT, IGNORE_CONSIST_OPT, PRIORITY_OPT],
    "[-f] <node>",
    "Stops the primary instances on a node and start them on their"
    " secondary node (only for instances with drbd disk template)"),
  'migrate': (
    MigrateNode, ARGS_ONE_NODE,
    [FORCE_OPT, NONLIVE_OPT, MIGRATION_MODE_OPT, PRIORITY_OPT],
    "[-f] <node>",
    "Migrate all the primary instance on a node away from it"
    " (only for instances of type drbd)"),
  'info': (
    ShowNodeConfig, ARGS_MANY_NODES, [],
    "[<node_name>...]", "Show information about the node(s)"),
  'list': (
    ListNodes, ARGS_MANY_NODES,
    [NOHDR_OPT, SEP_OPT, USEUNITS_OPT, FIELDS_OPT, VERBOSE_OPT],
    "[nodes...]",
    "Lists the nodes in the cluster. The available fields can be shown using"
    " the \"list-fields\" command (see the man page for details)."
    " The default field list is (in order): %s." %
    utils.CommaJoin(_LIST_DEF_FIELDS)),
  "list-fields": (
    ListNodeFields, [ArgUnknown()],
    [NOHDR_OPT, SEP_OPT],
    "[fields...]",
    "Lists all available fields for nodes"),
  'modify': (
    SetNodeParams, ARGS_ONE_NODE,
    [FORCE_OPT, SUBMIT_OPT, MC_OPT, DRAINED_OPT, OFFLINE_OPT,
     CAPAB_MASTER_OPT, CAPAB_VM_OPT, SECONDARY_IP_OPT,
     AUTO_PROMOTE_OPT, DRY_RUN_OPT, PRIORITY_OPT, NODE_PARAMS_OPT,
     NODE_POWERED_OPT],
    "<node_name>", "Alters the parameters of a node"),
  'powercycle': (
    PowercycleNode, ARGS_ONE_NODE,
    [FORCE_OPT, CONFIRM_OPT, DRY_RUN_OPT, PRIORITY_OPT],
    "<node_name>", "Tries to forcefully powercycle a node"),
  'power': (
    PowerNode,
    [ArgChoice(min=1, max=1, choices=_LIST_POWER_COMMANDS),
     ArgNode(min=1, max=1)],
    [SUBMIT_OPT, AUTO_PROMOTE_OPT, PRIORITY_OPT],
    "on|off|cycle|status <node>",
    "Change power state of node by calling out-of-band helper."),
  'remove': (
    RemoveNode, ARGS_ONE_NODE, [DRY_RUN_OPT, PRIORITY_OPT],
    "<node_name>", "Removes a node from the cluster"),
  'volumes': (
    ListVolumes, [ArgNode()],
    [NOHDR_OPT, SEP_OPT, USEUNITS_OPT, FIELDS_OPT, PRIORITY_OPT],
    "[<node_name>...]", "List logical volumes on node(s)"),
  'list-storage': (
    ListStorage, ARGS_MANY_NODES,
    [NOHDR_OPT, SEP_OPT, USEUNITS_OPT, FIELDS_OPT, _STORAGE_TYPE_OPT,
     PRIORITY_OPT],
    "[<node_name>...]", "List physical volumes on node(s). The available"
    " fields are (see the man page for details): %s." %
    (utils.CommaJoin(_LIST_STOR_HEADERS))),
  'modify-storage': (
    ModifyStorage,
    [ArgNode(min=1, max=1),
     ArgChoice(min=1, max=1, choices=_MODIFIABLE_STORAGE_TYPES),
     ArgFile(min=1, max=1)],
    [ALLOCATABLE_OPT, DRY_RUN_OPT, PRIORITY_OPT],
    "<node_name> <storage_type> <name>", "Modify storage volume on a node"),
  'repair-storage': (
    RepairStorage,
    [ArgNode(min=1, max=1),
     ArgChoice(min=1, max=1, choices=_REPAIRABLE_STORAGE_TYPES),
     ArgFile(min=1, max=1)],
    [IGNORE_CONSIST_OPT, DRY_RUN_OPT, PRIORITY_OPT],
    "<node_name> <storage_type> <name>",
    "Repairs a storage volume on a node"),
  'list-tags': (
    ListTags, ARGS_ONE_NODE, [],
    "<node_name>", "List the tags of the given node"),
  'add-tags': (
    AddTags, [ArgNode(min=1, max=1), ArgUnknown()], [TAG_SRC_OPT, PRIORITY_OPT],
    "<node_name> tag...", "Add tags to the given node"),
  'remove-tags': (
    RemoveTags, [ArgNode(min=1, max=1), ArgUnknown()],
    [TAG_SRC_OPT, PRIORITY_OPT],
    "<node_name> tag...", "Remove tags from the given node"),
  }


def Main():
  return GenericMain(commands, override={"tag_type": constants.TAG_NODE})
