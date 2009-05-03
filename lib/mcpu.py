#
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


"""Module implementing the logic behind the cluster operations

This module implements the logic for doing operations in the cluster. There
are two kinds of classes defined:
  - logical units, which know how to deal with their specific opcode only
  - the processor, which dispatches the opcodes to their logical units

"""


from ganeti import opcodes
from ganeti import constants
from ganeti import errors
from ganeti import rpc
from ganeti import cmdlib
from ganeti import config
from ganeti import ssconf
from ganeti import logger

class Processor(object):
  """Object which runs OpCodes"""
  DISPATCH_TABLE = {
    # Cluster
    opcodes.OpInitCluster: cmdlib.LUInitCluster,
    opcodes.OpDestroyCluster: cmdlib.LUDestroyCluster,
    opcodes.OpQueryClusterInfo: cmdlib.LUQueryClusterInfo,
    opcodes.OpClusterCopyFile: cmdlib.LUClusterCopyFile,
    opcodes.OpRunClusterCommand: cmdlib.LURunClusterCommand,
    opcodes.OpVerifyCluster: cmdlib.LUVerifyCluster,
    opcodes.OpMasterFailover: cmdlib.LUMasterFailover,
    opcodes.OpDumpClusterConfig: cmdlib.LUDumpClusterConfig,
    opcodes.OpRenameCluster: cmdlib.LURenameCluster,
    opcodes.OpVerifyDisks: cmdlib.LUVerifyDisks,
    # node lu
    opcodes.OpAddNode: cmdlib.LUAddNode,
    opcodes.OpQueryNodes: cmdlib.LUQueryNodes,
    opcodes.OpQueryNodeVolumes: cmdlib.LUQueryNodeVolumes,
    opcodes.OpRemoveNode: cmdlib.LURemoveNode,
    # instance lu
    opcodes.OpCreateInstance: cmdlib.LUCreateInstance,
    opcodes.OpReinstallInstance: cmdlib.LUReinstallInstance,
    opcodes.OpRemoveInstance: cmdlib.LURemoveInstance,
    opcodes.OpRenameInstance: cmdlib.LURenameInstance,
    opcodes.OpActivateInstanceDisks: cmdlib.LUActivateInstanceDisks,
    opcodes.OpShutdownInstance: cmdlib.LUShutdownInstance,
    opcodes.OpStartupInstance: cmdlib.LUStartupInstance,
    opcodes.OpRebootInstance: cmdlib.LURebootInstance,
    opcodes.OpDeactivateInstanceDisks: cmdlib.LUDeactivateInstanceDisks,
    opcodes.OpAddMDDRBDComponent: cmdlib.LUAddMDDRBDComponent,
    opcodes.OpRemoveMDDRBDComponent: cmdlib.LURemoveMDDRBDComponent,
    opcodes.OpReplaceDisks: cmdlib.LUReplaceDisks,
    opcodes.OpFailoverInstance: cmdlib.LUFailoverInstance,
    opcodes.OpConnectConsole: cmdlib.LUConnectConsole,
    opcodes.OpQueryInstances: cmdlib.LUQueryInstances,
    opcodes.OpQueryInstanceData: cmdlib.LUQueryInstanceData,
    opcodes.OpSetInstanceParms: cmdlib.LUSetInstanceParms,
    # os lu
    opcodes.OpDiagnoseOS: cmdlib.LUDiagnoseOS,
    # exports lu
    opcodes.OpQueryExports: cmdlib.LUQueryExports,
    opcodes.OpExportInstance: cmdlib.LUExportInstance,
    # tags lu
    opcodes.OpGetTags: cmdlib.LUGetTags,
    opcodes.OpSearchTags: cmdlib.LUSearchTags,
    opcodes.OpAddTags: cmdlib.LUAddTags,
    opcodes.OpDelTags: cmdlib.LUDelTags,
    # test lu
    opcodes.OpTestDelay: cmdlib.LUTestDelay,
    }

  def __init__(self, feedback=None):
    """Constructor for Processor

    Args:
     - feedback_fn: the feedback function (taking one string) to be run when
                    interesting events are happening
    """
    self.cfg = None
    self.sstore = None
    self._feedback_fn = feedback

  def ExecOpCode(self, op):
    """Execute an opcode.

    Args:
     - cfg: the configuration in which we execute this opcode
     - opcode: the opcode to be executed

    """
    if not isinstance(op, opcodes.OpCode):
      raise errors.ProgrammerError("Non-opcode instance passed"
                                   " to ExecOpcode")

    lu_class = self.DISPATCH_TABLE.get(op.__class__, None)
    if lu_class is None:
      raise errors.OpCodeUnknown("Unknown opcode")

    if lu_class.REQ_CLUSTER and self.cfg is None:
      self.cfg = config.ConfigWriter()
      self.sstore = ssconf.SimpleStore()
    if self.cfg is not None:
      write_count = self.cfg.write_count
    else:
      write_count = 0
    lu = lu_class(self, op, self.cfg, self.sstore)
    lu.CheckPrereq()
    hm = HooksMaster(rpc.call_hooks_runner, self, lu)
    hm.RunPhase(constants.HOOKS_PHASE_PRE)
    result = lu.Exec(self._feedback_fn)
    hm.RunPhase(constants.HOOKS_PHASE_POST)
    if lu.cfg is not None:
      # we use lu.cfg and not self.cfg as for init cluster, self.cfg
      # is None but lu.cfg has been recently initialized in the
      # lu.Exec method
      if write_count != lu.cfg.write_count:
        hm.RunConfigUpdate()

    return result

  def ChainOpCode(self, op):
    """Chain and execute an opcode.

    This is used by LUs when they need to execute a child LU.

    Args:
     - opcode: the opcode to be executed

    """
    if not isinstance(op, opcodes.OpCode):
      raise errors.ProgrammerError("Non-opcode instance passed"
                                   " to ExecOpcode")

    lu_class = self.DISPATCH_TABLE.get(op.__class__, None)
    if lu_class is None:
      raise errors.OpCodeUnknown("Unknown opcode")

    if lu_class.REQ_CLUSTER and self.cfg is None:
      self.cfg = config.ConfigWriter()
      self.sstore = ssconf.SimpleStore()
    #do_hooks = lu_class.HPATH is not None
    lu = lu_class(self, op, self.cfg, self.sstore)
    lu.CheckPrereq()
    #if do_hooks:
    #  hm = HooksMaster(rpc.call_hooks_runner, self, lu)
    #  hm.RunPhase(constants.HOOKS_PHASE_PRE)
    result = lu.Exec(self._feedback_fn)
    #if do_hooks:
    #  hm.RunPhase(constants.HOOKS_PHASE_POST)
    return result

  def LogStep(self, current, total, message):
    """Log a change in LU execution progress.

    """
    logger.Debug("Step %d/%d %s" % (current, total, message))
    self._feedback_fn("STEP %d/%d %s" % (current, total, message))

  def LogWarning(self, message, hint=None):
    """Log a warning to the logs and the user.

    """
    logger.Error(message)
    self._feedback_fn(" - WARNING: %s" % message)
    if hint:
      self._feedback_fn("      Hint: %s" % hint)

  def LogInfo(self, message):
    """Log an informational message to the logs and the user.

    """
    logger.Info(message)
    self._feedback_fn(" - INFO: %s" % message)


class HooksMaster(object):
  """Hooks master.

  This class distributes the run commands to the nodes based on the
  specific LU class.

  In order to remove the direct dependency on the rpc module, the
  constructor needs a function which actually does the remote
  call. This will usually be rpc.call_hooks_runner, but any function
  which behaves the same works.

  """
  def __init__(self, callfn, proc, lu):
    self.callfn = callfn
    self.proc = proc
    self.lu = lu
    self.op = lu.op
    self.env, node_list_pre, node_list_post = self._BuildEnv()
    self.node_list = {constants.HOOKS_PHASE_PRE: node_list_pre,
                      constants.HOOKS_PHASE_POST: node_list_post}

  def _BuildEnv(self):
    """Compute the environment and the target nodes.

    Based on the opcode and the current node list, this builds the
    environment for the hooks and the target node list for the run.

    """
    env = {
      "PATH": "/sbin:/bin:/usr/sbin:/usr/bin",
      "GANETI_HOOKS_VERSION": constants.HOOKS_VERSION,
      "GANETI_OP_CODE": self.op.OP_ID,
      "GANETI_OBJECT_TYPE": self.lu.HTYPE,
      "GANETI_DATA_DIR": constants.DATA_DIR,
      }

    if self.lu.HPATH is not None:
      lu_env, lu_nodes_pre, lu_nodes_post = self.lu.BuildHooksEnv()
      if lu_env:
        for key in lu_env:
          env["GANETI_" + key] = lu_env[key]
    else:
      lu_nodes_pre = lu_nodes_post = []

    return env, frozenset(lu_nodes_pre), frozenset(lu_nodes_post)

  def _RunWrapper(self, node_list, hpath, phase):
    """Simple wrapper over self.callfn.

    This method fixes the environment before doing the rpc call.

    """
    env = self.env.copy()
    env["GANETI_HOOKS_PHASE"] = phase
    env["GANETI_HOOKS_PATH"] = hpath
    if self.lu.sstore is not None:
      env["GANETI_CLUSTER"] = self.lu.sstore.GetClusterName()
      env["GANETI_MASTER"] = self.lu.sstore.GetMasterNode()

    env = dict([(str(key), str(val)) for key, val in env.iteritems()])

    return self.callfn(node_list, hpath, phase, env)

  def RunPhase(self, phase):
    """Run all the scripts for a phase.

    This is the main function of the HookMaster.

    """
    if not self.node_list[phase]:
      # empty node list, we should not attempt to run this as either
      # we're in the cluster init phase and the rpc client part can't
      # even attempt to run, or this LU doesn't do hooks at all
      return
    hpath = self.lu.HPATH
    results = self._RunWrapper(self.node_list[phase], hpath, phase)
    if phase == constants.HOOKS_PHASE_PRE:
      errs = []
      if not results:
        raise errors.HooksFailure("Communication failure")
      for node_name in results:
        res = results[node_name]
        if res is False or not isinstance(res, list):
          self.proc.LogWarning("Communication failure to node %s" % node_name)
          continue
        for script, hkr, output in res:
          if hkr == constants.HKR_FAIL:
            output = output.strip().encode("string_escape")
            errs.append((node_name, script, output))
      if errs:
        raise errors.HooksAbort(errs)

  def RunConfigUpdate(self):
    """Run the special configuration update hook

    This is a special hook that runs only on the master after each
    top-level LI if the configuration has been updated.

    """
    phase = constants.HOOKS_PHASE_POST
    hpath = constants.HOOKS_NAME_CFGUPDATE
    if self.lu.sstore is None:
      raise errors.ProgrammerError("Null sstore on config update hook")
    nodes = [self.lu.sstore.GetMasterNode()]
    results = self._RunWrapper(nodes, hpath, phase)
