#
#

# Copyright (C) 2006, 2007, 2011, 2012 Google Inc.
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


"""Module implementing the logic for running hooks.

"""

from ganeti import constants
from ganeti import errors
from ganeti import utils
from ganeti import compat
from ganeti import pathutils


def _RpcResultsToHooksResults(rpc_results):
  """Function to convert RPC results to the format expected by HooksMaster.

  @type rpc_results: dict(node: L{rpc.RpcResult})
  @param rpc_results: RPC results
  @rtype: dict(node: (fail_msg, offline, hooks_results))
  @return: RPC results unpacked according to the format expected by
    L({hooksmaster.HooksMaster}

  """
  return dict((node, (rpc_res.fail_msg, rpc_res.offline, rpc_res.payload))
              for (node, rpc_res) in rpc_results.items())


class HooksMaster(object):
  def __init__(self, opcode, hooks_path, nodes, hooks_execution_fn,
               hooks_results_adapt_fn, build_env_fn, log_fn, htype=None,
               cluster_name=None, master_name=None):
    """Base class for hooks masters.

    This class invokes the execution of hooks according to the behaviour
    specified by its parameters.

    @type opcode: string
    @param opcode: opcode of the operation to which the hooks are tied
    @type hooks_path: string
    @param hooks_path: prefix of the hooks directories
    @type nodes: 2-tuple of lists
    @param nodes: 2-tuple of lists containing nodes on which pre-hooks must be
      run and nodes on which post-hooks must be run
    @type hooks_execution_fn: function that accepts the following parameters:
      (node_list, hooks_path, phase, environment)
    @param hooks_execution_fn: function that will execute the hooks; can be
      None, indicating that no conversion is necessary.
    @type hooks_results_adapt_fn: function
    @param hooks_results_adapt_fn: function that will adapt the return value of
      hooks_execution_fn to the format expected by RunPhase
    @type build_env_fn: function that returns a dictionary having strings as
      keys
    @param build_env_fn: function that builds the environment for the hooks
    @type log_fn: function that accepts a string
    @param log_fn: logging function
    @type htype: string or None
    @param htype: None or one of L{constants.HTYPE_CLUSTER},
     L{constants.HTYPE_NODE}, L{constants.HTYPE_INSTANCE}
    @type cluster_name: string
    @param cluster_name: name of the cluster
    @type master_name: string
    @param master_name: name of the master

    """
    self.opcode = opcode
    self.hooks_path = hooks_path
    self.hooks_execution_fn = hooks_execution_fn
    self.hooks_results_adapt_fn = hooks_results_adapt_fn
    self.build_env_fn = build_env_fn
    self.log_fn = log_fn
    self.htype = htype
    self.cluster_name = cluster_name
    self.master_name = master_name

    self.pre_env = self._BuildEnv(constants.HOOKS_PHASE_PRE)
    (self.pre_nodes, self.post_nodes) = nodes

  def _BuildEnv(self, phase):
    """Compute the environment and the target nodes.

    Based on the opcode and the current node list, this builds the
    environment for the hooks and the target node list for the run.

    """
    if phase == constants.HOOKS_PHASE_PRE:
      prefix = "GANETI_"
    elif phase == constants.HOOKS_PHASE_POST:
      prefix = "GANETI_POST_"
    else:
      raise AssertionError("Unknown phase '%s'" % phase)

    env = {}

    if self.hooks_path is not None:
      phase_env = self.build_env_fn()
      if phase_env:
        assert not compat.any(key.upper().startswith(prefix)
                              for key in phase_env)
        env.update(("%s%s" % (prefix, key), value)
                   for (key, value) in phase_env.items())

    if phase == constants.HOOKS_PHASE_PRE:
      assert compat.all((key.startswith("GANETI_") and
                         not key.startswith("GANETI_POST_"))
                        for key in env)

    elif phase == constants.HOOKS_PHASE_POST:
      assert compat.all(key.startswith("GANETI_POST_") for key in env)
      assert isinstance(self.pre_env, dict)

      # Merge with pre-phase environment
      assert not compat.any(key.startswith("GANETI_POST_")
                            for key in self.pre_env)
      env.update(self.pre_env)
    else:
      raise AssertionError("Unknown phase '%s'" % phase)

    return env

  def _RunWrapper(self, node_list, hpath, phase, phase_env):
    """Simple wrapper over self.callfn.

    This method fixes the environment before executing the hooks.

    """
    env = {
      "PATH": constants.HOOKS_PATH,
      "GANETI_HOOKS_VERSION": constants.HOOKS_VERSION,
      "GANETI_OP_CODE": self.opcode,
      "GANETI_DATA_DIR": pathutils.DATA_DIR,
      "GANETI_HOOKS_PHASE": phase,
      "GANETI_HOOKS_PATH": hpath,
      }

    if self.htype:
      env["GANETI_OBJECT_TYPE"] = self.htype

    if self.cluster_name is not None:
      env["GANETI_CLUSTER"] = self.cluster_name

    if self.master_name is not None:
      env["GANETI_MASTER"] = self.master_name

    if phase_env:
      env = utils.algo.JoinDisjointDicts(env, phase_env)

    # Convert everything to strings
    env = dict([(str(key), str(val)) for key, val in env.iteritems()])

    assert compat.all(key == "PATH" or key.startswith("GANETI_")
                      for key in env)

    return self.hooks_execution_fn(node_list, hpath, phase, env)

  def RunPhase(self, phase, nodes=None):
    """Run all the scripts for a phase.

    This is the main function of the HookMaster.
    It executes self.hooks_execution_fn, and after running
    self.hooks_results_adapt_fn on its results it expects them to be in the form
    {node_name: (fail_msg, [(script, result, output), ...]}).

    @param phase: one of L{constants.HOOKS_PHASE_POST} or
        L{constants.HOOKS_PHASE_PRE}; it denotes the hooks phase
    @param nodes: overrides the predefined list of nodes for the given phase
    @return: the processed results of the hooks multi-node rpc call
    @raise errors.HooksFailure: on communication failure to the nodes
    @raise errors.HooksAbort: on failure of one of the hooks

    """
    if phase == constants.HOOKS_PHASE_PRE:
      if nodes is None:
        nodes = self.pre_nodes
      env = self.pre_env
    elif phase == constants.HOOKS_PHASE_POST:
      if nodes is None:
        nodes = self.post_nodes
      env = self._BuildEnv(phase)
    else:
      raise AssertionError("Unknown phase '%s'" % phase)

    if not nodes:
      # empty node list, we should not attempt to run this as either
      # we're in the cluster init phase and the rpc client part can't
      # even attempt to run, or this LU doesn't do hooks at all
      return

    results = self._RunWrapper(nodes, self.hooks_path, phase, env)
    if not results:
      msg = "Communication Failure"
      if phase == constants.HOOKS_PHASE_PRE:
        raise errors.HooksFailure(msg)
      else:
        self.log_fn(msg)
        return results

    converted_res = results
    if self.hooks_results_adapt_fn:
      converted_res = self.hooks_results_adapt_fn(results)

    errs = []
    for node_name, (fail_msg, offline, hooks_results) in converted_res.items():
      if offline:
        continue

      if fail_msg:
        self.log_fn("Communication failure to node %s: %s", node_name, fail_msg)
        continue

      for script, hkr, output in hooks_results:
        if hkr == constants.HKR_FAIL:
          if phase == constants.HOOKS_PHASE_PRE:
            errs.append((node_name, script, output))
          else:
            if not output:
              output = "(no output)"
            self.log_fn("On %s script %s failed, output: %s" %
                        (node_name, script, output))

    if errs and phase == constants.HOOKS_PHASE_PRE:
      raise errors.HooksAbort(errs)

    return results

  def RunConfigUpdate(self):
    """Run the special configuration update hook

    This is a special hook that runs only on the master after each
    top-level LI if the configuration has been updated.

    """
    phase = constants.HOOKS_PHASE_POST
    hpath = constants.HOOKS_NAME_CFGUPDATE
    nodes = [self.master_name]
    self._RunWrapper(nodes, hpath, phase, self.pre_env)

  @staticmethod
  def BuildFromLu(hooks_execution_fn, lu):
    if lu.HPATH is None:
      nodes = (None, None)
    else:
      nodes = map(frozenset, lu.BuildHooksNodes())

    master_name = cluster_name = None
    if lu.cfg:
      master_name = lu.cfg.GetMasterNode()
      cluster_name = lu.cfg.GetClusterName()

    return HooksMaster(lu.op.OP_ID, lu.HPATH, nodes, hooks_execution_fn,
                       _RpcResultsToHooksResults, lu.BuildHooksEnv,
                       lu.LogWarning, lu.HTYPE, cluster_name, master_name)
