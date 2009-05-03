#
#

# Copyright (C) 2006, 2007, 2008 Google Inc.
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


"""Module implementing the master-side code."""

# pylint: disable-msg=W0613,W0201

import os
import os.path
import sha
import time
import tempfile
import re
import platform

from ganeti import rpc
from ganeti import ssh
from ganeti import logger
from ganeti import utils
from ganeti import errors
from ganeti import hypervisor
from ganeti import config
from ganeti import constants
from ganeti import objects
from ganeti import opcodes
from ganeti import ssconf
from ganeti import serializer


class LogicalUnit(object):
  """Logical Unit base class.

  Subclasses must follow these rules:
    - implement CheckPrereq which also fills in the opcode instance
      with all the fields (even if as None)
    - implement Exec
    - implement BuildHooksEnv
    - redefine HPATH and HTYPE
    - optionally redefine their run requirements (REQ_CLUSTER,
      REQ_MASTER); note that all commands require root permissions

  """
  HPATH = None
  HTYPE = None
  _OP_REQP = []
  REQ_CLUSTER = True
  REQ_MASTER = True

  def __init__(self, processor, op, cfg, sstore):
    """Constructor for LogicalUnit.

    This needs to be overriden in derived classes in order to check op
    validity.

    """
    self.proc = processor
    self.op = op
    self.cfg = cfg
    self.sstore = sstore
    for attr_name in self._OP_REQP:
      attr_val = getattr(op, attr_name, None)
      if attr_val is None:
        raise errors.OpPrereqError("Required parameter '%s' missing" %
                                   attr_name)
    if self.REQ_CLUSTER:
      if not cfg.IsCluster():
        raise errors.OpPrereqError("Cluster not initialized yet,"
                                   " use 'gnt-cluster init' first.")
      if self.REQ_MASTER:
        master = sstore.GetMasterNode()
        if master != utils.HostInfo().name:
          raise errors.OpPrereqError("Commands must be run on the master"
                                     " node %s" % master)

  def CheckPrereq(self):
    """Check prerequisites for this LU.

    This method should check that the prerequisites for the execution
    of this LU are fulfilled. It can do internode communication, but
    it should be idempotent - no cluster or system changes are
    allowed.

    The method should raise errors.OpPrereqError in case something is
    not fulfilled. Its return value is ignored.

    This method should also update all the parameters of the opcode to
    their canonical form; e.g. a short node name must be fully
    expanded after this method has successfully completed (so that
    hooks, logging, etc. work correctly).

    """
    raise NotImplementedError

  def Exec(self, feedback_fn):
    """Execute the LU.

    This method should implement the actual work. It should raise
    errors.OpExecError for failures that are somewhat dealt with in
    code, or expected.

    """
    raise NotImplementedError

  def BuildHooksEnv(self):
    """Build hooks environment for this LU.

    This method should return a three-node tuple consisting of: a dict
    containing the environment that will be used for running the
    specific hook for this LU, a list of node names on which the hook
    should run before the execution, and a list of node names on which
    the hook should run after the execution.

    The keys of the dict must not have 'GANETI_' prefixed as this will
    be handled in the hooks runner. Also note additional keys will be
    added by the hooks runner. If the LU doesn't define any
    environment, an empty dict (and not None) should be returned.

    No nodes should be returned as an empty list (and not None).

    Note that if the HPATH for a LU class is None, this function will
    not be called.

    """
    raise NotImplementedError

  def HooksCallBack(self, phase, hook_results, feedback_fn, lu_result):
    """Notify the LU about the results of its hooks.

    This method is called every time a hooks phase is executed, and notifies
    the Logical Unit about the hooks' result. The LU can then use it to alter
    its result based on the hooks.  By default the method does nothing and the
    previous result is passed back unchanged but any LU can define it if it
    wants to use the local cluster hook-scripts somehow.

    Args:
      phase: the hooks phase that has just been run
      hooks_results: the results of the multi-node hooks rpc call
      feedback_fn: function to send feedback back to the caller
      lu_result: the previous result this LU had, or None in the PRE phase.

    """
    return lu_result


class NoHooksLU(LogicalUnit):
  """Simple LU which runs no hooks.

  This LU is intended as a parent for other LogicalUnits which will
  run no hooks, in order to reduce duplicate code.

  """
  HPATH = None
  HTYPE = None


def _AddHostToEtcHosts(hostname):
  """Wrapper around utils.SetEtcHostsEntry.

  """
  hi = utils.HostInfo(name=hostname)
  utils.SetEtcHostsEntry(constants.ETC_HOSTS, hi.ip, hi.name, [hi.ShortName()])


def _RemoveHostFromEtcHosts(hostname):
  """Wrapper around utils.RemoveEtcHostsEntry.

  """
  hi = utils.HostInfo(name=hostname)
  utils.RemoveEtcHostsEntry(constants.ETC_HOSTS, hi.name)
  utils.RemoveEtcHostsEntry(constants.ETC_HOSTS, hi.ShortName())


def _GetWantedNodes(lu, nodes):
  """Returns list of checked and expanded node names.

  Args:
    nodes: List of nodes (strings) or None for all

  """
  if not isinstance(nodes, list):
    raise errors.OpPrereqError("Invalid argument type 'nodes'")

  if nodes:
    wanted = []

    for name in nodes:
      node = lu.cfg.ExpandNodeName(name)
      if node is None:
        raise errors.OpPrereqError("No such node name '%s'" % name)
      wanted.append(node)

  else:
    wanted = lu.cfg.GetNodeList()
  return utils.NiceSort(wanted)


def _GetWantedInstances(lu, instances):
  """Returns list of checked and expanded instance names.

  Args:
    instances: List of instances (strings) or None for all

  """
  if not isinstance(instances, list):
    raise errors.OpPrereqError("Invalid argument type 'instances'")

  if instances:
    wanted = []

    for name in instances:
      instance = lu.cfg.ExpandInstanceName(name)
      if instance is None:
        raise errors.OpPrereqError("No such instance name '%s'" % name)
      wanted.append(instance)

  else:
    wanted = lu.cfg.GetInstanceList()
  return utils.NiceSort(wanted)


def _CheckOutputFields(static, dynamic, selected):
  """Checks whether all selected fields are valid.

  Args:
    static: Static fields
    dynamic: Dynamic fields

  """
  static_fields = frozenset(static)
  dynamic_fields = frozenset(dynamic)

  all_fields = static_fields | dynamic_fields

  if not all_fields.issuperset(selected):
    raise errors.OpPrereqError("Unknown output fields selected: %s"
                               % ",".join(frozenset(selected).
                                          difference(all_fields)))


def _BuildInstanceHookEnv(name, primary_node, secondary_nodes, os_type, status,
                          memory, vcpus, nics):
  """Builds instance related env variables for hooks from single variables.

  Args:
    secondary_nodes: List of secondary nodes as strings
  """
  env = {
    "OP_TARGET": name,
    "INSTANCE_NAME": name,
    "INSTANCE_PRIMARY": primary_node,
    "INSTANCE_SECONDARIES": " ".join(secondary_nodes),
    "INSTANCE_OS_TYPE": os_type,
    "INSTANCE_STATUS": status,
    "INSTANCE_MEMORY": memory,
    "INSTANCE_VCPUS": vcpus,
  }

  if nics:
    nic_count = len(nics)
    for idx, (ip, bridge, mac) in enumerate(nics):
      if ip is None:
        ip = ""
      env["INSTANCE_NIC%d_IP" % idx] = ip
      env["INSTANCE_NIC%d_BRIDGE" % idx] = bridge
      env["INSTANCE_NIC%d_HWADDR" % idx] = mac
  else:
    nic_count = 0

  env["INSTANCE_NIC_COUNT"] = nic_count

  return env


def _BuildInstanceHookEnvByObject(instance, override=None):
  """Builds instance related env variables for hooks from an object.

  Args:
    instance: objects.Instance object of instance
    override: dict of values to override
  """
  args = {
    'name': instance.name,
    'primary_node': instance.primary_node,
    'secondary_nodes': instance.secondary_nodes,
    'os_type': instance.os,
    'status': instance.os,
    'memory': instance.memory,
    'vcpus': instance.vcpus,
    'nics': [(nic.ip, nic.bridge, nic.mac) for nic in instance.nics],
  }
  if override:
    args.update(override)
  return _BuildInstanceHookEnv(**args)


def _UpdateKnownHosts(fullnode, ip, pubkey):
  """Ensure a node has a correct known_hosts entry.

  Args:
    fullnode - Fully qualified domain name of host. (str)
    ip       - IPv4 address of host (str)
    pubkey   - the public key of the cluster

  """
  if os.path.exists(constants.SSH_KNOWN_HOSTS_FILE):
    f = open(constants.SSH_KNOWN_HOSTS_FILE, 'r+')
  else:
    f = open(constants.SSH_KNOWN_HOSTS_FILE, 'w+')

  inthere = False

  save_lines = []
  add_lines = []
  removed = False

  for rawline in f:
    logger.Debug('read %s' % (repr(rawline),))

    parts = rawline.rstrip('\r\n').split()

    # Ignore unwanted lines
    if len(parts) >= 3 and not rawline.lstrip()[0] == '#':
      fields = parts[0].split(',')
      key = parts[2]

      haveall = True
      havesome = False
      for spec in [ ip, fullnode ]:
        if spec not in fields:
          haveall = False
        if spec in fields:
          havesome = True

      logger.Debug("key, pubkey = %s." % (repr((key, pubkey)),))
      if haveall and key == pubkey:
        inthere = True
        save_lines.append(rawline)
        logger.Debug("Keeping known_hosts '%s'." % (repr(rawline),))
        continue

      if havesome and (not haveall or key != pubkey):
        removed = True
        logger.Debug("Discarding known_hosts '%s'." % (repr(rawline),))
        continue

    save_lines.append(rawline)

  if not inthere:
    add_lines.append('%s,%s ssh-rsa %s\n' % (fullnode, ip, pubkey))
    logger.Debug("Adding known_hosts '%s'." % (repr(add_lines[-1]),))

  if removed:
    save_lines = save_lines + add_lines

    # Write a new file and replace old.
    fd, tmpname = tempfile.mkstemp('.tmp', 'known_hosts.',
                                   constants.DATA_DIR)
    newfile = os.fdopen(fd, 'w')
    try:
      newfile.write(''.join(save_lines))
    finally:
      newfile.close()
    logger.Debug("Wrote new known_hosts.")
    os.rename(tmpname, constants.SSH_KNOWN_HOSTS_FILE)

  elif add_lines:
    # Simply appending a new line will do the trick.
    f.seek(0, 2)
    for add in add_lines:
      f.write(add)

  f.close()


def _HasValidVG(vglist, vgname):
  """Checks if the volume group list is valid.

  A non-None return value means there's an error, and the return value
  is the error message.

  """
  vgsize = vglist.get(vgname, None)
  if vgsize is None:
    return "volume group '%s' missing" % vgname
  elif vgsize < 20480:
    return ("volume group '%s' too small (20480MiB required, %dMib found)" %
            (vgname, vgsize))
  return None


def _InitSSHSetup(node):
  """Setup the SSH configuration for the cluster.


  This generates a dsa keypair for root, adds the pub key to the
  permitted hosts and adds the hostkey to its own known hosts.

  Args:
    node: the name of this host as a fqdn

  """
  priv_key, pub_key, auth_keys = ssh.GetUserFiles(constants.GANETI_RUNAS)

  for name in priv_key, pub_key:
    if os.path.exists(name):
      utils.CreateBackup(name)
    utils.RemoveFile(name)

  result = utils.RunCmd(["ssh-keygen", "-t", "dsa",
                         "-f", priv_key,
                         "-q", "-N", ""])
  if result.failed:
    raise errors.OpExecError("Could not generate ssh keypair, error %s" %
                             result.output)

  f = open(pub_key, 'r')
  try:
    utils.AddAuthorizedKey(auth_keys, f.read(8192))
  finally:
    f.close()


def _InitGanetiServerSetup(ss):
  """Setup the necessary configuration for the initial node daemon.

  This creates the nodepass file containing the shared password for
  the cluster and also generates the SSL certificate.

  """
  # Create pseudo random password
  randpass = sha.new(os.urandom(64)).hexdigest()
  # and write it into sstore
  ss.SetKey(ss.SS_NODED_PASS, randpass)

  result = utils.RunCmd(["openssl", "req", "-new", "-newkey", "rsa:1024",
                         "-days", str(365*5), "-nodes", "-x509",
                         "-keyout", constants.SSL_CERT_FILE,
                         "-out", constants.SSL_CERT_FILE, "-batch"])
  if result.failed:
    raise errors.OpExecError("could not generate server ssl cert, command"
                             " %s had exitcode %s and error message %s" %
                             (result.cmd, result.exit_code, result.output))

  os.chmod(constants.SSL_CERT_FILE, 0400)

  result = utils.RunCmd([constants.NODE_INITD_SCRIPT, "restart"])

  if result.failed:
    raise errors.OpExecError("Could not start the node daemon, command %s"
                             " had exitcode %s and error %s" %
                             (result.cmd, result.exit_code, result.output))


def _CheckInstanceBridgesExist(instance):
  """Check that the brigdes needed by an instance exist.

  """
  # check bridges existance
  brlist = [nic.bridge for nic in instance.nics]
  if not rpc.call_bridges_exist(instance.primary_node, brlist):
    raise errors.OpPrereqError("one or more target bridges %s does not"
                               " exist on destination node '%s'" %
                               (brlist, instance.primary_node))


class LUInitCluster(LogicalUnit):
  """Initialise the cluster.

  """
  HPATH = "cluster-init"
  HTYPE = constants.HTYPE_CLUSTER
  _OP_REQP = ["cluster_name", "hypervisor_type", "vg_name", "mac_prefix",
              "def_bridge", "master_netdev"]
  REQ_CLUSTER = False

  def BuildHooksEnv(self):
    """Build hooks env.

    Notes: Since we don't require a cluster, we must manually add
    ourselves in the post-run node list.

    """
    env = {"OP_TARGET": self.op.cluster_name}
    return env, [], [self.hostname.name]

  def CheckPrereq(self):
    """Verify that the passed name is a valid one.

    """
    if config.ConfigWriter.IsCluster():
      raise errors.OpPrereqError("Cluster is already initialised")

    if self.op.hypervisor_type == constants.HT_XEN_HVM31:
      if not os.path.exists(constants.VNC_PASSWORD_FILE):
        raise errors.OpPrereqError("Please prepare the cluster VNC"
                                   "password file %s" %
                                   constants.VNC_PASSWORD_FILE)

    self.hostname = hostname = utils.HostInfo()

    if hostname.ip.startswith("127."):
      raise errors.OpPrereqError("This host's IP resolves to the private"
                                 " range (%s). Please fix DNS or %s." %
                                 (hostname.ip, constants.ETC_HOSTS))

    if not utils.TcpPing(hostname.ip, constants.DEFAULT_NODED_PORT,
                         source=constants.LOCALHOST_IP_ADDRESS):
      raise errors.OpPrereqError("Inconsistency: this host's name resolves"
                                 " to %s,\nbut this ip address does not"
                                 " belong to this host."
                                 " Aborting." % hostname.ip)

    self.clustername = clustername = utils.HostInfo(self.op.cluster_name)

    if utils.TcpPing(clustername.ip, constants.DEFAULT_NODED_PORT,
                     timeout=5):
      raise errors.OpPrereqError("Cluster IP already active. Aborting.")

    secondary_ip = getattr(self.op, "secondary_ip", None)
    if secondary_ip and not utils.IsValidIP(secondary_ip):
      raise errors.OpPrereqError("Invalid secondary ip given")
    if (secondary_ip and
        secondary_ip != hostname.ip and
        (not utils.TcpPing(secondary_ip, constants.DEFAULT_NODED_PORT,
                           source=constants.LOCALHOST_IP_ADDRESS))):
      raise errors.OpPrereqError("You gave %s as secondary IP,"
                                 " but it does not belong to this host." %
                                 secondary_ip)
    self.secondary_ip = secondary_ip

    # checks presence of the volume group given
    vgstatus = _HasValidVG(utils.ListVolumeGroups(), self.op.vg_name)

    if vgstatus:
      raise errors.OpPrereqError("Error: %s" % vgstatus)

    if not re.match("^[0-9a-z]{2}:[0-9a-z]{2}:[0-9a-z]{2}$",
                    self.op.mac_prefix):
      raise errors.OpPrereqError("Invalid mac prefix given '%s'" %
                                 self.op.mac_prefix)

    if self.op.hypervisor_type not in constants.HYPER_TYPES:
      raise errors.OpPrereqError("Invalid hypervisor type given '%s'" %
                                 self.op.hypervisor_type)

    result = utils.RunCmd(["ip", "link", "show", "dev", self.op.master_netdev])
    if result.failed:
      raise errors.OpPrereqError("Invalid master netdev given (%s): '%s'" %
                                 (self.op.master_netdev,
                                  result.output.strip()))

    if not (os.path.isfile(constants.NODE_INITD_SCRIPT) and
            os.access(constants.NODE_INITD_SCRIPT, os.X_OK)):
      raise errors.OpPrereqError("Init.d script '%s' missing or not"
                                 " executable." % constants.NODE_INITD_SCRIPT)

  def Exec(self, feedback_fn):
    """Initialize the cluster.

    """
    clustername = self.clustername
    hostname = self.hostname

    # set up the simple store
    self.sstore = ss = ssconf.SimpleStore()
    ss.SetKey(ss.SS_HYPERVISOR, self.op.hypervisor_type)
    ss.SetKey(ss.SS_MASTER_NODE, hostname.name)
    ss.SetKey(ss.SS_MASTER_IP, clustername.ip)
    ss.SetKey(ss.SS_MASTER_NETDEV, self.op.master_netdev)
    ss.SetKey(ss.SS_CLUSTER_NAME, clustername.name)

    # set up the inter-node password and certificate
    _InitGanetiServerSetup(ss)

    # start the master ip
    rpc.call_node_start_master(hostname.name)

    # set up ssh config and /etc/hosts
    f = open(constants.SSH_HOST_RSA_PUB, 'r')
    try:
      sshline = f.read()
    finally:
      f.close()
    sshkey = sshline.split(" ")[1]

    _AddHostToEtcHosts(hostname.name)

    _UpdateKnownHosts(hostname.name, hostname.ip, sshkey)

    _InitSSHSetup(hostname.name)

    # init of cluster config file
    self.cfg = cfgw = config.ConfigWriter()
    cfgw.InitConfig(hostname.name, hostname.ip, self.secondary_ip,
                    sshkey, self.op.mac_prefix,
                    self.op.vg_name, self.op.def_bridge)


class LUDestroyCluster(NoHooksLU):
  """Logical unit for destroying the cluster.

  """
  _OP_REQP = []

  def CheckPrereq(self):
    """Check prerequisites.

    This checks whether the cluster is empty.

    Any errors are signalled by raising errors.OpPrereqError.

    """
    master = self.sstore.GetMasterNode()

    nodelist = self.cfg.GetNodeList()
    if len(nodelist) != 1 or nodelist[0] != master:
      raise errors.OpPrereqError("There are still %d node(s) in"
                                 " this cluster." % (len(nodelist) - 1))
    instancelist = self.cfg.GetInstanceList()
    if instancelist:
      raise errors.OpPrereqError("There are still %d instance(s) in"
                                 " this cluster." % len(instancelist))

  def Exec(self, feedback_fn):
    """Destroys the cluster.

    """
    master = self.sstore.GetMasterNode()
    if not rpc.call_node_stop_master(master):
      raise errors.OpExecError("Could not disable the master role")
    priv_key, pub_key, _ = ssh.GetUserFiles(constants.GANETI_RUNAS)
    utils.CreateBackup(priv_key)
    utils.CreateBackup(pub_key)
    rpc.call_node_leave_cluster(master)


class LUVerifyCluster(LogicalUnit):
  """Verifies the cluster status.

  """
  HPATH = "cluster-verify"
  HTYPE = constants.HTYPE_CLUSTER
  _OP_REQP = ["skip_checks"]

  def _VerifyNode(self, node, file_list, local_cksum, vglist, node_result,
                  remote_version, feedback_fn):
    """Run multiple tests against a node.

    Test list:
      - compares ganeti version
      - checks vg existance and size > 20G
      - checks config file checksum
      - checks ssh to other nodes

    Args:
      node: name of the node to check
      file_list: required list of files
      local_cksum: dictionary of local files and their checksums

    """
    # compares ganeti version
    local_version = constants.PROTOCOL_VERSION
    if not remote_version:
      feedback_fn("  - ERROR: connection to %s failed" % (node))
      return True

    if local_version != remote_version:
      feedback_fn("  - ERROR: sw version mismatch: master %s, node(%s) %s" %
                      (local_version, node, remote_version))
      return True

    # checks vg existance and size > 20G

    bad = False
    if not vglist:
      feedback_fn("  - ERROR: unable to check volume groups on node %s." %
                      (node,))
      bad = True
    else:
      vgstatus = _HasValidVG(vglist, self.cfg.GetVGName())
      if vgstatus:
        feedback_fn("  - ERROR: %s on node %s" % (vgstatus, node))
        bad = True

    # checks config file checksum
    # checks ssh to any

    if 'filelist' not in node_result:
      bad = True
      feedback_fn("  - ERROR: node hasn't returned file checksum data")
    else:
      remote_cksum = node_result['filelist']
      for file_name in file_list:
        if file_name not in remote_cksum:
          bad = True
          feedback_fn("  - ERROR: file '%s' missing" % file_name)
        elif remote_cksum[file_name] != local_cksum[file_name]:
          bad = True
          feedback_fn("  - ERROR: file '%s' has wrong checksum" % file_name)

    if 'nodelist' not in node_result:
      bad = True
      feedback_fn("  - ERROR: node hasn't returned node ssh connectivity data")
    else:
      if node_result['nodelist']:
        bad = True
        for node in node_result['nodelist']:
          feedback_fn("  - ERROR: ssh communication with node '%s': %s" %
                          (node, node_result['nodelist'][node]))
    if 'node-net-test' not in node_result:
      bad = True
      feedback_fn("  - ERROR: node hasn't returned node tcp connectivity data")
    else:
      if node_result['node-net-test']:
        bad = True
        nlist = utils.NiceSort(node_result['node-net-test'].keys())
        for node in nlist:
          feedback_fn("  - ERROR: tcp communication with node '%s': %s" %
                          (node, node_result['node-net-test'][node]))

    hyp_result = node_result.get('hypervisor', None)
    if hyp_result is not None:
      feedback_fn("  - ERROR: hypervisor verify failure: '%s'" % hyp_result)
    return bad

  def _VerifyInstance(self, instance, instanceconfig, node_vol_is,
                      node_instance, feedback_fn):
    """Verify an instance.

    This function checks to see if the required block devices are
    available on the instance's node.

    """
    bad = False

    node_current = instanceconfig.primary_node

    node_vol_should = {}
    instanceconfig.MapLVsByNode(node_vol_should)

    for node in node_vol_should:
      for volume in node_vol_should[node]:
        if node not in node_vol_is or volume not in node_vol_is[node]:
          feedback_fn("  - ERROR: volume %s missing on node %s" %
                          (volume, node))
          bad = True

    if not instanceconfig.status == 'down':
      if (node_current not in node_instance or
          not instance in node_instance[node_current]):
        feedback_fn("  - ERROR: instance %s not running on node %s" %
                        (instance, node_current))
        bad = True

    for node in node_instance:
      if (not node == node_current):
        if instance in node_instance[node]:
          feedback_fn("  - ERROR: instance %s should not run on node %s" %
                          (instance, node))
          bad = True

    return bad

  def _VerifyOrphanVolumes(self, node_vol_should, node_vol_is, feedback_fn):
    """Verify if there are any unknown volumes in the cluster.

    The .os, .swap and backup volumes are ignored. All other volumes are
    reported as unknown.

    """
    bad = False

    for node in node_vol_is:
      for volume in node_vol_is[node]:
        if node not in node_vol_should or volume not in node_vol_should[node]:
          feedback_fn("  - ERROR: volume %s on node %s should not exist" %
                      (volume, node))
          bad = True
    return bad

  def _VerifyOrphanInstances(self, instancelist, node_instance, feedback_fn):
    """Verify the list of running instances.

    This checks what instances are running but unknown to the cluster.

    """
    bad = False
    for node in node_instance:
      for runninginstance in node_instance[node]:
        if runninginstance not in instancelist:
          feedback_fn("  - ERROR: instance %s on node %s should not exist" %
                          (runninginstance, node))
          bad = True
    return bad

  def _VerifyNPlusOneMemory(self, node_info, instance_cfg, feedback_fn):
    """Verify N+1 Memory Resilience.

    Check that if one single node dies we can still start all the instances it
    was primary for.

    """
    bad = False

    for node, nodeinfo in node_info.iteritems():
      # This code checks that every node which is now listed as secondary has
      # enough memory to host all instances it is supposed to should a single
      # other node in the cluster fail.
      # FIXME: not ready for failover to an arbitrary node
      # FIXME: does not support file-backed instances
      # WARNING: we currently take into account down instances as well as up
      # ones, considering that even if they're down someone might want to start
      # them even in the event of a node failure.
      for prinode, instances in nodeinfo['sinst-by-pnode'].iteritems():
        needed_mem = 0
        for instance in instances:
          if instance_cfg[instance].auto_balance:
            needed_mem += instance_cfg[instance].memory
        if nodeinfo['mfree'] < needed_mem:
          feedback_fn("  - ERROR: not enough memory on node %s to accomodate"
                      " failovers should node %s fail" % (node, prinode))
          bad = True
    return bad

  def CheckPrereq(self):
    """Check prerequisites.

    Transform the list of checks we're going to skip into a set and check that
    all its members are valid.

    """
    self.skip_set = frozenset(self.op.skip_checks)
    if not constants.VERIFY_OPTIONAL_CHECKS.issuperset(self.skip_set):
      raise errors.OpPrereqError("Invalid checks to be skipped specified")

  def BuildHooksEnv(self):
    """Build hooks env.

    Cluster-Verify hooks just rone in the post phase and their failure makes
    the output be logged in the verify output and the verification to fail.

    """
    all_nodes = self.cfg.GetNodeList()
    tags = self.cfg.GetClusterInfo().GetTags()
    # TODO: populate the environment with useful information for verify hooks
    env = {
      "CLUSTER_TAGS": " ".join(tags),
      }
    return env, [], all_nodes

  def Exec(self, feedback_fn):
    """Verify integrity of cluster, performing various test on nodes.

    """
    bad = False
    feedback_fn("* Verifying global settings")
    for msg in self.cfg.VerifyConfig():
      feedback_fn("  - ERROR: %s" % msg)

    vg_name = self.cfg.GetVGName()
    nodelist = utils.NiceSort(self.cfg.GetNodeList())
    nodeinfo = [self.cfg.GetNodeInfo(nname) for nname in nodelist]
    instancelist = utils.NiceSort(self.cfg.GetInstanceList())
    i_non_redundant = [] # Non redundant instances
    i_non_a_balanced = [] # Non auto-balanced instances
    node_volume = {}
    node_instance = {}
    node_info = {}
    instance_cfg = {}

    # FIXME: verify OS list
    # do local checksums
    file_names = list(self.sstore.GetFileList())
    file_names.append(constants.SSL_CERT_FILE)
    file_names.append(constants.CLUSTER_CONF_FILE)
    local_checksums = utils.FingerprintFiles(file_names)

    feedback_fn("* Gathering data (%d nodes)" % len(nodelist))
    all_volumeinfo = rpc.call_volume_list(nodelist, vg_name)
    all_instanceinfo = rpc.call_instance_list(nodelist)
    all_vglist = rpc.call_vg_list(nodelist)
    node_verify_param = {
      'filelist': file_names,
      'nodelist': nodelist,
      'hypervisor': None,
      'node-net-test': [(node.name, node.primary_ip, node.secondary_ip)
                        for node in nodeinfo]
      }
    all_nvinfo = rpc.call_node_verify(nodelist, node_verify_param)
    all_rversion = rpc.call_version(nodelist)
    all_ninfo = rpc.call_node_info(nodelist, self.cfg.GetVGName())

    incomplete_nodeinfo = False

    for node in nodelist:
      feedback_fn("* Verifying node %s" % node)
      result = self._VerifyNode(node, file_names, local_checksums,
                                all_vglist[node], all_nvinfo[node],
                                all_rversion[node], feedback_fn)
      bad = bad or result

      # node_volume
      volumeinfo = all_volumeinfo[node]

      if isinstance(volumeinfo, basestring):
        feedback_fn("  - ERROR: LVM problem on node %s: %s" %
                    (node, volumeinfo[-400:].encode('string_escape')))
        bad = True
        node_volume[node] = {}
      elif not isinstance(volumeinfo, dict):
        feedback_fn("  - ERROR: connection to %s failed" % (node,))
        bad = True
        incomplete_nodeinfo = True
        continue
      else:
        node_volume[node] = volumeinfo

      # node_instance
      nodeinstance = all_instanceinfo[node]
      if type(nodeinstance) != list:
        feedback_fn("  - ERROR: connection to %s failed" % (node,))
        bad = True
        incomplete_nodeinfo = True
        continue

      node_instance[node] = nodeinstance

      # node_info
      nodeinfo = all_ninfo[node]
      if not isinstance(nodeinfo, dict):
        feedback_fn("  - ERROR: connection to %s failed" % (node,))
        bad = True
        incomplete_nodeinfo = True
        continue

      try:
        node_info[node] = {
          "mfree": int(nodeinfo['memory_free']),
          "dfree": int(nodeinfo['vg_free']),
          "pinst": [],
          "sinst": [],
          # dictionary holding all instances this node is secondary for,
          # grouped by their primary node. Each key is a cluster node, and each
          # value is a list of instances which have the key as primary and the
          # current node as secondary.  this is handy to calculate N+1 memory
          # availability if you can only failover from a primary to its
          # secondary.
          "sinst-by-pnode": {},
        }
      except (ValueError, TypeError):
        feedback_fn("  - ERROR: invalid value returned from node %s" % (node,))
        bad = True
        incomplete_nodeinfo = True
        continue

    node_vol_should = {}

    for instance in instancelist:
      feedback_fn("* Verifying instance %s" % instance)
      inst_config = self.cfg.GetInstanceInfo(instance)
      result =  self._VerifyInstance(instance, inst_config, node_volume,
                                     node_instance, feedback_fn)
      bad = bad or result

      inst_config.MapLVsByNode(node_vol_should)

      instance_cfg[instance] = inst_config

      pnode = inst_config.primary_node
      if pnode in node_info:
        node_info[pnode]['pinst'].append(instance)
      else:
        feedback_fn("  - ERROR: instance %s, connection to primary node"
                    " %s failed" % (instance, pnode))
        bad = True

      # If the instance is non-redundant we cannot survive losing its primary
      # node, so we are not N+1 compliant. On the other hand we have no disk
      # templates with more than one secondary so that situation is not well
      # supported either.
      # FIXME: does not support file-backed instances
      if len(inst_config.secondary_nodes) == 0:
        i_non_redundant.append(instance)
      elif len(inst_config.secondary_nodes) > 1:
        feedback_fn("  - WARNING: multiple secondaries for instance %s"
                    % instance)

      if not inst_config.auto_balance:
        i_non_a_balanced.append(instance)

      for snode in inst_config.secondary_nodes:
        if snode in node_info:
          node_info[snode]['sinst'].append(instance)
          if pnode not in node_info[snode]['sinst-by-pnode']:
            node_info[snode]['sinst-by-pnode'][pnode] = []
          node_info[snode]['sinst-by-pnode'][pnode].append(instance)
        else:
          feedback_fn("  - ERROR: instance %s, connection to secondary node"
                      " %s failed" % (instance, snode))

    feedback_fn("* Verifying orphan volumes")
    result = self._VerifyOrphanVolumes(node_vol_should, node_volume,
                                       feedback_fn)
    bad = bad or result

    feedback_fn("* Verifying remaining instances")
    result = self._VerifyOrphanInstances(instancelist, node_instance,
                                         feedback_fn)
    bad = bad or result

    if (constants.VERIFY_NPLUSONE_MEM not in self.skip_set and
        not incomplete_nodeinfo):
      feedback_fn("* Verifying N+1 Memory redundancy")
      result = self._VerifyNPlusOneMemory(node_info, instance_cfg, feedback_fn)
      bad = bad or result

    feedback_fn("* Other Notes")
    if i_non_redundant:
      feedback_fn("  - NOTICE: %d non-redundant instance(s) found."
                  % len(i_non_redundant))

    if i_non_a_balanced:
      feedback_fn("  - NOTICE: %d non-auto-balanced instance(s) found."
                  % len(i_non_a_balanced))

    return int(bad)

  def HooksCallBack(self, phase, hooks_results, feedback_fn, lu_result):
    """Analize the post-hooks' result, handle it, and send some
    nicely-formatted feedback back to the user.

    Args:
      phase: the hooks phase that has just been run
      hooks_results: the results of the multi-node hooks rpc call
      feedback_fn: function to send feedback back to the caller
      lu_result: previous Exec result

    """
    # We only really run POST phase hooks, and are only interested in their results
    if phase == constants.HOOKS_PHASE_POST:
      # Used to change hooks' output to proper indentation
      indent_re = re.compile('^', re.M)
      feedback_fn("* Hooks Results")
      if not hooks_results:
        feedback_fn("  - ERROR: general communication failure")
        lu_result = 1
      else:
        for node_name in hooks_results:
          show_node_header = True
          res = hooks_results[node_name]
          if res is False or not isinstance(res, list):
            feedback_fn("    Communication failure")
            lu_result = 1
            continue
          for script, hkr, output in res:
            if hkr == constants.HKR_FAIL:
              # The node header is only shown once, if there are
              # failing hooks on that node
              if show_node_header:
                feedback_fn("  Node %s:" % node_name)
                show_node_header = False
              feedback_fn("    ERROR: Script %s failed, output:" % script)
              output = indent_re.sub('      ', output)
              feedback_fn("%s" % output)
              lu_result = 1

      return lu_result


class LUVerifyDisks(NoHooksLU):
  """Verifies the cluster disks status.

  """
  _OP_REQP = []

  def CheckPrereq(self):
    """Check prerequisites.

    This has no prerequisites.

    """
    pass

  def Exec(self, feedback_fn):
    """Verify integrity of cluster disks.

    """
    result = res_nodes, res_nlvm, res_instances, res_missing = [], {}, [], {}

    vg_name = self.cfg.GetVGName()
    nodes = utils.NiceSort(self.cfg.GetNodeList())
    instances = [self.cfg.GetInstanceInfo(name)
                 for name in self.cfg.GetInstanceList()]

    nv_dict = {}
    for inst in instances:
      inst_lvs = {}
      if (inst.status != "up" or
          inst.disk_template not in constants.DTS_NET_MIRROR):
        continue
      inst.MapLVsByNode(inst_lvs)
      # transform { iname: {node: [vol,],},} to {(node, vol): iname}
      for node, vol_list in inst_lvs.iteritems():
        for vol in vol_list:
          nv_dict[(node, vol)] = inst

    if not nv_dict:
      return result

    node_lvs = rpc.call_volume_list(nodes, vg_name)

    to_act = set()
    for node in nodes:
      # node_volume
      lvs = node_lvs[node]

      if isinstance(lvs, basestring):
        logger.Info("error enumerating LVs on node %s: %s" % (node, lvs))
        res_nlvm[node] = lvs
      elif not isinstance(lvs, dict):
        logger.Info("connection to node %s failed or invalid data returned" %
                    (node,))
        res_nodes.append(node)
        continue

      for lv_name, (_, lv_inactive, lv_online) in lvs.iteritems():
        inst = nv_dict.pop((node, lv_name), None)
        if (not lv_online and inst is not None
            and inst.name not in res_instances):
          res_instances.append(inst.name)

    # any leftover items in nv_dict are missing LVs, let's arrange the
    # data better
    for key, inst in nv_dict.iteritems():
      if inst.name not in res_missing:
        res_missing[inst.name] = []
      res_missing[inst.name].append(key)

    return result


class LURenameCluster(LogicalUnit):
  """Rename the cluster.

  """
  HPATH = "cluster-rename"
  HTYPE = constants.HTYPE_CLUSTER
  _OP_REQP = ["name"]

  def BuildHooksEnv(self):
    """Build hooks env.

    """
    env = {
      "OP_TARGET": self.sstore.GetClusterName(),
      "NEW_NAME": self.op.name,
      }
    mn = self.sstore.GetMasterNode()
    return env, [mn], [mn]

  def CheckPrereq(self):
    """Verify that the passed name is a valid one.

    """
    hostname = utils.HostInfo(self.op.name)

    new_name = hostname.name
    self.ip = new_ip = hostname.ip
    old_name = self.sstore.GetClusterName()
    old_ip = self.sstore.GetMasterIP()
    if new_name == old_name and new_ip == old_ip:
      raise errors.OpPrereqError("Neither the name nor the IP address of the"
                                 " cluster has changed")
    if new_ip != old_ip:
      if utils.TcpPing(new_ip, constants.DEFAULT_NODED_PORT):
        raise errors.OpPrereqError("The given cluster IP address (%s) is"
                                   " reachable on the network. Aborting." %
                                   new_ip)

    self.op.name = new_name

  def Exec(self, feedback_fn):
    """Rename the cluster.

    """
    clustername = self.op.name
    ip = self.ip
    ss = self.sstore

    # shutdown the master IP
    master = ss.GetMasterNode()
    if not rpc.call_node_stop_master(master):
      raise errors.OpExecError("Could not disable the master role")

    try:
      # modify the sstore
      ss.SetKey(ss.SS_MASTER_IP, ip)
      ss.SetKey(ss.SS_CLUSTER_NAME, clustername)

      # Distribute updated ss config to all nodes
      myself = self.cfg.GetNodeInfo(master)
      dist_nodes = self.cfg.GetNodeList()
      if myself.name in dist_nodes:
        dist_nodes.remove(myself.name)

      logger.Debug("Copying updated ssconf data to all nodes")
      for keyname in [ss.SS_CLUSTER_NAME, ss.SS_MASTER_IP]:
        fname = ss.KeyToFilename(keyname)
        result = rpc.call_upload_file(dist_nodes, fname)
        for to_node in dist_nodes:
          if not result[to_node]:
            logger.Error("copy of file %s to node %s failed" %
                         (fname, to_node))
    finally:
      if not rpc.call_node_start_master(master):
        logger.Error("Could not re-enable the master role on the master,"
                     " please restart manually.")


def _WaitForSync(cfgw, instance, proc, oneshot=False, unlock=False):
  """Sleep and poll for an instance's disk to sync.

  """
  if not instance.disks:
    return True

  if not oneshot:
    proc.LogInfo("Waiting for instance %s to sync disks." % instance.name)

  node = instance.primary_node

  for dev in instance.disks:
    cfgw.SetDiskID(dev, node)

  retries = 0
  while True:
    max_time = 0
    done = True
    cumul_degraded = False
    rstats = rpc.call_blockdev_getmirrorstatus(node, instance.disks)
    if not rstats:
      proc.LogWarning("Can't get any data from node %s" % node)
      retries += 1
      if retries >= 10:
        raise errors.RemoteError("Can't contact node %s for mirror data,"
                                 " aborting." % node)
      time.sleep(6)
      continue
    retries = 0
    for i in range(len(rstats)):
      mstat = rstats[i]
      if mstat is None:
        proc.LogWarning("Can't compute data for node %s/%s" %
                        (node, instance.disks[i].iv_name))
        continue
      # we ignore the ldisk parameter
      perc_done, est_time, is_degraded, _ = mstat
      cumul_degraded = cumul_degraded or (is_degraded and perc_done is None)
      if perc_done is not None:
        done = False
        if est_time is not None:
          rem_time = "%d estimated seconds remaining" % est_time
          max_time = est_time
        else:
          rem_time = "no time estimate"
        proc.LogInfo("- device %s: %5.2f%% done, %s" %
                     (instance.disks[i].iv_name, perc_done, rem_time))
    if done or oneshot:
      break

    if unlock:
      utils.Unlock('cmd')
    try:
      time.sleep(min(60, max_time))
    finally:
      if unlock:
        utils.Lock('cmd')

  if done:
    proc.LogInfo("Instance %s's disks are in sync." % instance.name)
  return not cumul_degraded


def _CheckDiskConsistency(cfgw, dev, node, on_primary, ldisk=False):
  """Check that mirrors are not degraded.

  The ldisk parameter, if True, will change the test from the
  is_degraded attribute (which represents overall non-ok status for
  the device(s)) to the ldisk (representing the local storage status).

  """
  cfgw.SetDiskID(dev, node)
  if ldisk:
    idx = 6
  else:
    idx = 5

  result = True
  if on_primary or dev.AssembleOnSecondary():
    rstats = rpc.call_blockdev_find(node, dev)
    if not rstats:
      logger.ToStderr("Node %s: Disk degraded, not found or node down" % node)
      result = False
    else:
      result = result and (not rstats[idx])
  if dev.children:
    for child in dev.children:
      result = result and _CheckDiskConsistency(cfgw, child, node, on_primary)

  return result


class LUDiagnoseOS(NoHooksLU):
  """Logical unit for OS diagnose/query.

  """
  _OP_REQP = ["output_fields", "names"]

  def CheckPrereq(self):
    """Check prerequisites.

    This always succeeds, since this is a pure query LU.

    """
    if self.op.names:
      raise errors.OpPrereqError("Selective OS query not supported")

    self.dynamic_fields = frozenset(["name", "valid", "node_status"])
    _CheckOutputFields(static=[],
                       dynamic=self.dynamic_fields,
                       selected=self.op.output_fields)

  @staticmethod
  def _DiagnoseByOS(node_list, rlist):
    """Remaps a per-node return list into an a per-os per-node dictionary

      Args:
        node_list: a list with the names of all nodes
        rlist: a map with node names as keys and OS objects as values

      Returns:
        map: a map with osnames as keys and as value another map, with
             nodes as
             keys and list of OS objects as values
             e.g. {"debian-etch": {"node1": [<object>,...],
                                   "node2": [<object>,]}
                  }

    """
    all_os = {}
    for node_name, nr in rlist.iteritems():
      if not nr:
        continue
      for os in nr:
        if os.name not in all_os:
          # build a list of nodes for this os containing empty lists
          # for each node in node_list
          all_os[os.name] = {}
          for nname in node_list:
            all_os[os.name][nname] = []
        all_os[os.name][node_name].append(os)
    return all_os

  def Exec(self, feedback_fn):
    """Compute the list of OSes.

    """
    node_list = self.cfg.GetNodeList()
    node_data = rpc.call_os_diagnose(node_list)
    if node_data == False:
      raise errors.OpExecError("Can't gather the list of OSes")
    pol = self._DiagnoseByOS(node_list, node_data)
    output = []
    for os_name, os_data in pol.iteritems():
      row = []
      for field in self.op.output_fields:
        if field == "name":
          val = os_name
        elif field == "valid":
          val = utils.all([osl and osl[0] for osl in os_data.values()])
        elif field == "node_status":
          val = {}
          for node_name, nos_list in os_data.iteritems():
            val[node_name] = [(v.status, v.path) for v in nos_list]
        else:
          raise errors.ParameterError(field)
        row.append(val)
      output.append(row)

    return output


class LURemoveNode(LogicalUnit):
  """Logical unit for removing a node.

  """
  HPATH = "node-remove"
  HTYPE = constants.HTYPE_NODE
  _OP_REQP = ["node_name"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This doesn't run on the target node in the pre phase as a failed
    node would not allows itself to run.

    """
    env = {
      "OP_TARGET": self.op.node_name,
      "NODE_NAME": self.op.node_name,
      }
    all_nodes = self.cfg.GetNodeList()
    all_nodes.remove(self.op.node_name)
    return env, all_nodes, all_nodes

  def CheckPrereq(self):
    """Check prerequisites.

    This checks:
     - the node exists in the configuration
     - it does not have primary or secondary instances
     - it's not the master

    Any errors are signalled by raising errors.OpPrereqError.

    """
    node = self.cfg.GetNodeInfo(self.cfg.ExpandNodeName(self.op.node_name))
    if node is None:
      raise errors.OpPrereqError, ("Node '%s' is unknown." % self.op.node_name)

    instance_list = self.cfg.GetInstanceList()

    masternode = self.sstore.GetMasterNode()
    if node.name == masternode:
      raise errors.OpPrereqError("Node is the master node,"
                                 " you need to failover first.")

    for instance_name in instance_list:
      instance = self.cfg.GetInstanceInfo(instance_name)
      if node.name == instance.primary_node:
        raise errors.OpPrereqError("Instance %s still running on the node,"
                                   " please remove first." % instance_name)
      if node.name in instance.secondary_nodes:
        raise errors.OpPrereqError("Instance %s has node as a secondary,"
                                   " please remove first." % instance_name)
    self.op.node_name = node.name
    self.node = node

  def Exec(self, feedback_fn):
    """Removes the node from the cluster.

    """
    node = self.node
    logger.Info("stopping the node daemon and removing configs from node %s" %
                node.name)

    rpc.call_node_leave_cluster(node.name)

    ssh.SSHCall(node.name, 'root', "%s stop" % constants.NODE_INITD_SCRIPT)

    logger.Info("Removing node %s from config" % node.name)

    self.cfg.RemoveNode(node.name)

    _RemoveHostFromEtcHosts(node.name)


class LUQueryNodes(NoHooksLU):
  """Logical unit for querying nodes.

  """
  _OP_REQP = ["output_fields", "names"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the fields required are valid output fields.

    """
    self.dynamic_fields = frozenset([
      "dtotal", "dfree",
      "mtotal", "mnode", "mfree",
      "bootid",
      "ctotal", "cnodes", "csockets",
      ])

    _CheckOutputFields(static=["name", "pinst_cnt", "sinst_cnt",
                               "pinst_list", "sinst_list",
                               "pip", "sip", "tags"],
                       dynamic=self.dynamic_fields,
                       selected=self.op.output_fields)

    self.wanted = _GetWantedNodes(self, self.op.names)

  def Exec(self, feedback_fn):
    """Computes the list of nodes and their attributes.

    """
    nodenames = self.wanted
    nodelist = [self.cfg.GetNodeInfo(name) for name in nodenames]

    # begin data gathering

    if self.dynamic_fields.intersection(self.op.output_fields):
      live_data = {}
      node_data = rpc.call_node_info(nodenames, self.cfg.GetVGName())
      for name in nodenames:
        nodeinfo = node_data.get(name, None)
        if nodeinfo:
          fn = utils.TryConvert
          live_data[name] = {
            "mtotal": fn(int, nodeinfo.get('memory_total', None)),
            "mnode": fn(int, nodeinfo.get('memory_dom0', None)),
            "mfree": fn(int, nodeinfo.get('memory_free', None)),
            "dtotal": fn(int, nodeinfo.get('vg_size', None)),
            "dfree": fn(int, nodeinfo.get('vg_free', None)),
            "ctotal": fn(int, nodeinfo.get('cpu_total', None)),
            "bootid": nodeinfo.get('bootid', None),
            "cnodes": fn(int, nodeinfo.get('cpu_nodes', None)),
            "csockets": fn(int, nodeinfo.get('cpu_sockets', None)),
            }
        else:
          live_data[name] = {}
    else:
      live_data = dict.fromkeys(nodenames, {})

    node_to_primary = dict([(name, set()) for name in nodenames])
    node_to_secondary = dict([(name, set()) for name in nodenames])

    inst_fields = frozenset(("pinst_cnt", "pinst_list",
                             "sinst_cnt", "sinst_list"))
    if inst_fields & frozenset(self.op.output_fields):
      instancelist = self.cfg.GetInstanceList()

      for instance_name in instancelist:
        inst = self.cfg.GetInstanceInfo(instance_name)
        if inst.primary_node in node_to_primary:
          node_to_primary[inst.primary_node].add(inst.name)
        for secnode in inst.secondary_nodes:
          if secnode in node_to_secondary:
            node_to_secondary[secnode].add(inst.name)

    # end data gathering

    output = []
    for node in nodelist:
      node_output = []
      for field in self.op.output_fields:
        if field == "name":
          val = node.name
        elif field == "pinst_list":
          val = list(node_to_primary[node.name])
        elif field == "sinst_list":
          val = list(node_to_secondary[node.name])
        elif field == "pinst_cnt":
          val = len(node_to_primary[node.name])
        elif field == "sinst_cnt":
          val = len(node_to_secondary[node.name])
        elif field == "pip":
          val = node.primary_ip
        elif field == "sip":
          val = node.secondary_ip
        elif field == "tags":
          val = list(node.GetTags())
        elif field in self.dynamic_fields:
          val = live_data[node.name].get(field, None)
        else:
          raise errors.ParameterError(field)
        node_output.append(val)
      output.append(node_output)

    return output


class LUQueryNodeVolumes(NoHooksLU):
  """Logical unit for getting volumes on node(s).

  """
  _OP_REQP = ["nodes", "output_fields"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the fields required are valid output fields.

    """
    self.nodes = _GetWantedNodes(self, self.op.nodes)

    _CheckOutputFields(static=["node"],
                       dynamic=["phys", "vg", "name", "size", "instance"],
                       selected=self.op.output_fields)


  def Exec(self, feedback_fn):
    """Computes the list of nodes and their attributes.

    """
    nodenames = self.nodes
    volumes = rpc.call_node_volumes(nodenames)

    ilist = [self.cfg.GetInstanceInfo(iname) for iname
             in self.cfg.GetInstanceList()]

    lv_by_node = dict([(inst, inst.MapLVsByNode()) for inst in ilist])

    output = []
    for node in nodenames:
      if node not in volumes or not volumes[node]:
        continue

      node_vols = volumes[node][:]
      node_vols.sort(key=lambda vol: vol['dev'])

      for vol in node_vols:
        node_output = []
        for field in self.op.output_fields:
          if field == "node":
            val = node
          elif field == "phys":
            val = vol['dev']
          elif field == "vg":
            val = vol['vg']
          elif field == "name":
            val = vol['name']
          elif field == "size":
            val = int(float(vol['size']))
          elif field == "instance":
            for inst in ilist:
              if node not in lv_by_node[inst]:
                continue
              if vol['name'] in lv_by_node[inst][node]:
                val = inst.name
                break
            else:
              val = '-'
          else:
            raise errors.ParameterError(field)
          node_output.append(str(val))

        output.append(node_output)

    return output


class LUAddNode(LogicalUnit):
  """Logical unit for adding node to the cluster.

  """
  HPATH = "node-add"
  HTYPE = constants.HTYPE_NODE
  _OP_REQP = ["node_name"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This will run on all nodes before, and on all nodes + the new node after.

    """
    env = {
      "OP_TARGET": self.op.node_name,
      "NODE_NAME": self.op.node_name,
      "NODE_PIP": self.op.primary_ip,
      "NODE_SIP": self.op.secondary_ip,
      }
    nodes_0 = self.cfg.GetNodeList()
    nodes_1 = nodes_0 + [self.op.node_name, ]
    return env, nodes_0, nodes_1

  def CheckPrereq(self):
    """Check prerequisites.

    This checks:
     - the new node is not already in the config
     - it is resolvable
     - its parameters (single/dual homed) matches the cluster

    Any errors are signalled by raising errors.OpPrereqError.

    """
    node_name = self.op.node_name
    cfg = self.cfg

    dns_data = utils.HostInfo(node_name)

    node = dns_data.name
    primary_ip = self.op.primary_ip = dns_data.ip
    secondary_ip = getattr(self.op, "secondary_ip", None)
    if secondary_ip is None:
      secondary_ip = primary_ip
    if not utils.IsValidIP(secondary_ip):
      raise errors.OpPrereqError("Invalid secondary IP given")
    self.op.secondary_ip = secondary_ip

    node_list = cfg.GetNodeList()
    if not self.op.readd and node in node_list:
      raise errors.OpPrereqError("Node %s is already in the configuration" %
                                 node)
    elif self.op.readd and node not in node_list:
      raise errors.OpPrereqError("Node %s is not in the configuration" % node)

    for existing_node_name in node_list:
      existing_node = cfg.GetNodeInfo(existing_node_name)

      if self.op.readd and node == existing_node_name:
        if (existing_node.primary_ip != primary_ip or
            existing_node.secondary_ip != secondary_ip):
          raise errors.OpPrereqError("Readded node doesn't have the same IP"
                                     " address configuration as before")
        continue

      if (existing_node.primary_ip == primary_ip or
          existing_node.secondary_ip == primary_ip or
          existing_node.primary_ip == secondary_ip or
          existing_node.secondary_ip == secondary_ip):
        raise errors.OpPrereqError("New node ip address(es) conflict with"
                                   " existing node %s" % existing_node.name)

    # check that the type of the node (single versus dual homed) is the
    # same as for the master
    myself = cfg.GetNodeInfo(self.sstore.GetMasterNode())
    master_singlehomed = myself.secondary_ip == myself.primary_ip
    newbie_singlehomed = secondary_ip == primary_ip
    if master_singlehomed != newbie_singlehomed:
      if master_singlehomed:
        raise errors.OpPrereqError("The master has no private ip but the"
                                   " new node has one")
      else:
        raise errors.OpPrereqError("The master has a private ip but the"
                                   " new node doesn't have one")

    # checks reachablity
    if not utils.TcpPing(primary_ip, constants.DEFAULT_NODED_PORT):
      raise errors.OpPrereqError("Node not reachable by ping")

    if not newbie_singlehomed:
      # check reachability from my secondary ip to newbie's secondary ip
      if not utils.TcpPing(secondary_ip, constants.DEFAULT_NODED_PORT,
                           source=myself.secondary_ip):
        raise errors.OpPrereqError("Node secondary ip not reachable by TCP"
                                   " based ping to noded port")

    self.new_node = objects.Node(name=node,
                                 primary_ip=primary_ip,
                                 secondary_ip=secondary_ip)

    if self.sstore.GetHypervisorType() == constants.HT_XEN_HVM31:
      if not os.path.exists(constants.VNC_PASSWORD_FILE):
        raise errors.OpPrereqError("Cluster VNC password file %s missing" %
                                   constants.VNC_PASSWORD_FILE)

  def Exec(self, feedback_fn):
    """Adds the new node to the cluster.

    """
    new_node = self.new_node
    node = new_node.name

    # set up inter-node password and certificate and restarts the node daemon
    gntpass = self.sstore.GetNodeDaemonPassword()
    if not re.match('^[a-zA-Z0-9.]{1,64}$', gntpass):
      raise errors.OpExecError("ganeti password corruption detected")
    f = open(constants.SSL_CERT_FILE)
    try:
      gntpem = f.read(8192)
    finally:
      f.close()
    # in the base64 pem encoding, neither '!' nor '.' are valid chars,
    # so we use this to detect an invalid certificate; as long as the
    # cert doesn't contain this, the here-document will be correctly
    # parsed by the shell sequence below
    if re.search('^!EOF\.', gntpem, re.MULTILINE):
      raise errors.OpExecError("invalid PEM encoding in the SSL certificate")
    if not gntpem.endswith("\n"):
      raise errors.OpExecError("PEM must end with newline")
    logger.Info("copy cluster pass to %s and starting the node daemon" % node)

    # and then connect with ssh to set password and start ganeti-noded
    # note that all the below variables are sanitized at this point,
    # either by being constants or by the checks above
    ss = self.sstore
    mycommand = ("umask 077 && "
                 "echo '%s' > '%s' && "
                 "cat > '%s' << '!EOF.' && \n"
                 "%s!EOF.\n%s restart" %
                 (gntpass, ss.KeyToFilename(ss.SS_NODED_PASS),
                  constants.SSL_CERT_FILE, gntpem,
                  constants.NODE_INITD_SCRIPT))

    result = ssh.SSHCall(node, 'root', mycommand, batch=False, ask_key=True)
    if result.failed:
      raise errors.OpExecError("Remote command on node %s, error: %s,"
                               " output: %s" %
                               (node, result.fail_reason, result.output))

    # check connectivity
    time.sleep(4)

    result = rpc.call_version([node])[node]
    if result:
      if constants.PROTOCOL_VERSION == result:
        logger.Info("communication to node %s fine, sw version %s match" %
                    (node, result))
      else:
        raise errors.OpExecError("Version mismatch master version %s,"
                                 " node version %s" %
                                 (constants.PROTOCOL_VERSION, result))
    else:
      raise errors.OpExecError("Cannot get version from the new node")

    # setup ssh on node
    logger.Info("copy ssh key to node %s" % node)
    priv_key, pub_key, _ = ssh.GetUserFiles(constants.GANETI_RUNAS)
    keyarray = []
    keyfiles = [constants.SSH_HOST_DSA_PRIV, constants.SSH_HOST_DSA_PUB,
                constants.SSH_HOST_RSA_PRIV, constants.SSH_HOST_RSA_PUB,
                priv_key, pub_key]

    for i in keyfiles:
      f = open(i, 'r')
      try:
        keyarray.append(f.read())
      finally:
        f.close()

    result = rpc.call_node_add(node, keyarray[0], keyarray[1], keyarray[2],
                               keyarray[3], keyarray[4], keyarray[5])

    if not result:
      raise errors.OpExecError("Cannot transfer ssh keys to the new node")

    # Add node to our /etc/hosts, and add key to known_hosts
    _AddHostToEtcHosts(new_node.name)

    _UpdateKnownHosts(new_node.name, new_node.primary_ip,
                      self.cfg.GetHostKey())

    if new_node.secondary_ip != new_node.primary_ip:
      if not rpc.call_node_tcp_ping(new_node.name,
                                    constants.LOCALHOST_IP_ADDRESS,
                                    new_node.secondary_ip,
                                    constants.DEFAULT_NODED_PORT,
                                    10, False):
        raise errors.OpExecError("Node claims it doesn't have the secondary ip"
                                 " you gave (%s). Please fix and re-run this"
                                 " command." % new_node.secondary_ip)

    success, msg = ssh.VerifyNodeHostname(node)
    if not success:
      raise errors.OpExecError("Node '%s' claims it has a different hostname"
                               " than the one the resolver gives: %s."
                               " Please fix and re-run this command." %
                               (node, msg))

    # Distribute updated /etc/hosts and known_hosts to all nodes,
    # including the node just added
    myself = self.cfg.GetNodeInfo(self.sstore.GetMasterNode())
    dist_nodes = self.cfg.GetNodeList()
    if not self.op.readd:
      dist_nodes.append(node)
    if myself.name in dist_nodes:
      dist_nodes.remove(myself.name)

    logger.Debug("Copying hosts and known_hosts to all nodes")
    for fname in (constants.ETC_HOSTS, constants.SSH_KNOWN_HOSTS_FILE):
      result = rpc.call_upload_file(dist_nodes, fname)
      for to_node in dist_nodes:
        if not result[to_node]:
          logger.Error("copy of file %s to node %s failed" %
                       (fname, to_node))

    to_copy = ss.GetFileList()
    if self.sstore.GetHypervisorType() == constants.HT_XEN_HVM31:
      to_copy.append(constants.VNC_PASSWORD_FILE)
    for fname in to_copy:
      if not ssh.CopyFileToNode(node, fname):
        logger.Error("could not copy file %s to node %s" % (fname, node))

    if not self.op.readd:
      logger.Info("adding node %s to cluster.conf" % node)
      self.cfg.AddNode(new_node)


class LUMasterFailover(LogicalUnit):
  """Failover the master node to the current node.

  This is a special LU in that it must run on a non-master node.

  """
  HPATH = "master-failover"
  HTYPE = constants.HTYPE_CLUSTER
  REQ_MASTER = False
  _OP_REQP = []

  def BuildHooksEnv(self):
    """Build hooks env.

    This will run on the new master only in the pre phase, and on all
    the nodes in the post phase.

    """
    env = {
      "OP_TARGET": self.new_master,
      "NEW_MASTER": self.new_master,
      "OLD_MASTER": self.old_master,
      }
    return env, [self.new_master], self.cfg.GetNodeList()

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that we are not already the master.

    """
    self.new_master = utils.HostInfo().name
    self.old_master = self.sstore.GetMasterNode()

    if self.old_master == self.new_master:
      raise errors.OpPrereqError("This commands must be run on the node"
                                 " where you want the new master to be."
                                 " %s is already the master" %
                                 self.old_master)

  def Exec(self, feedback_fn):
    """Failover the master node.

    This command, when run on a non-master node, will cause the current
    master to cease being master, and the non-master to become new
    master.

    """
    #TODO: do not rely on gethostname returning the FQDN
    logger.Info("setting master to %s, old master: %s" %
                (self.new_master, self.old_master))

    if not rpc.call_node_stop_master(self.old_master):
      logger.Error("could disable the master role on the old master"
                   " %s, please disable manually" % self.old_master)

    ss = self.sstore
    ss.SetKey(ss.SS_MASTER_NODE, self.new_master)
    if not rpc.call_upload_file(self.cfg.GetNodeList(),
                                ss.KeyToFilename(ss.SS_MASTER_NODE)):
      logger.Error("could not distribute the new simple store master file"
                   " to the other nodes, please check.")

    if not rpc.call_node_start_master(self.new_master):
      logger.Error("could not start the master role on the new master"
                   " %s, please check" % self.new_master)
      feedback_fn("Error in activating the master IP on the new master,"
                  " please fix manually.")



class LUQueryClusterInfo(NoHooksLU):
  """Query cluster configuration.

  """
  _OP_REQP = []
  REQ_MASTER = False

  def CheckPrereq(self):
    """No prerequsites needed for this LU.

    """
    pass

  def Exec(self, feedback_fn):
    """Return cluster config.

    """
    result = {
      "name": self.sstore.GetClusterName(),
      "software_version": constants.RELEASE_VERSION,
      "protocol_version": constants.PROTOCOL_VERSION,
      "config_version": constants.CONFIG_VERSION,
      "os_api_version": constants.OS_API_VERSION,
      "export_version": constants.EXPORT_VERSION,
      "master": self.sstore.GetMasterNode(),
      "architecture": (platform.architecture()[0], platform.machine()),
      "hypervisor_type": self.sstore.GetHypervisorType(),
      }

    return result


class LUClusterCopyFile(NoHooksLU):
  """Copy file to cluster.

  """
  _OP_REQP = ["nodes", "filename"]

  def CheckPrereq(self):
    """Check prerequisites.

    It should check that the named file exists and that the given list
    of nodes is valid.

    """
    if not os.path.exists(self.op.filename):
      raise errors.OpPrereqError("No such filename '%s'" % self.op.filename)

    self.nodes = _GetWantedNodes(self, self.op.nodes)

  def Exec(self, feedback_fn):
    """Copy a file from master to some nodes.

    Args:
      opts - class with options as members
      args - list containing a single element, the file name
    Opts used:
      nodes - list containing the name of target nodes; if empty, all nodes

    """
    filename = self.op.filename

    myname = utils.HostInfo().name

    for node in self.nodes:
      if node == myname:
        continue
      if not ssh.CopyFileToNode(node, filename):
        logger.Error("Copy of file %s to node %s failed" % (filename, node))


class LUDumpClusterConfig(NoHooksLU):
  """Return a text-representation of the cluster-config.

  """
  _OP_REQP = []

  def CheckPrereq(self):
    """No prerequisites.

    """
    pass

  def Exec(self, feedback_fn):
    """Dump a representation of the cluster config to the standard output.

    """
    return self.cfg.DumpConfig()


class LURunClusterCommand(NoHooksLU):
  """Run a command on some nodes.

  """
  _OP_REQP = ["command", "nodes"]

  def CheckPrereq(self):
    """Check prerequisites.

    It checks that the given list of nodes is valid.

    """
    self.nodes = _GetWantedNodes(self, self.op.nodes)

  def Exec(self, feedback_fn):
    """Run a command on some nodes.

    """
    # put the master at the end of the nodes list
    master_node = self.sstore.GetMasterNode()
    if master_node in self.nodes:
      self.nodes.remove(master_node)
      self.nodes.append(master_node)

    data = []
    for node in self.nodes:
      result = ssh.SSHCall(node, "root", self.op.command)
      data.append((node, result.output, result.exit_code))

    return data


class LUActivateInstanceDisks(NoHooksLU):
  """Bring up an instance's disks.

  """
  _OP_REQP = ["instance_name"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    self.instance = instance


  def Exec(self, feedback_fn):
    """Activate the disks.

    """
    disks_ok, disks_info = _AssembleInstanceDisks(self.instance, self.cfg)
    if not disks_ok:
      raise errors.OpExecError("Cannot activate block devices")

    return disks_info


def _AssembleInstanceDisks(instance, cfg, ignore_secondaries=False):
  """Prepare the block devices for an instance.

  This sets up the block devices on all nodes.

  Args:
    instance: a ganeti.objects.Instance object
    ignore_secondaries: if true, errors on secondary nodes won't result
                        in an error return from the function

  Returns:
    false if the operation failed
    list of (host, instance_visible_name, node_visible_name) if the operation
         suceeded with the mapping from node devices to instance devices
  """
  device_info = []
  disks_ok = True
  iname = instance.name
  # With the two passes mechanism we try to reduce the window of
  # opportunity for the race condition of switching DRBD to primary
  # before handshaking occured, but we do not eliminate it

  # The proper fix would be to wait (with some limits) until the
  # connection has been made and drbd transitions from WFConnection
  # into any other network-connected state (Connected, SyncTarget,
  # SyncSource, etc.)

  # 1st pass, assemble on all nodes in secondary mode
  for inst_disk in instance.disks:
    for node, node_disk in inst_disk.ComputeNodeTree(instance.primary_node):
      cfg.SetDiskID(node_disk, node)
      result = rpc.call_blockdev_assemble(node, node_disk, iname, False)
      if not result:
        logger.Error("could not prepare block device %s on node %s"
                     " (is_primary=False, pass=1)" % (inst_disk.iv_name, node))
        if not ignore_secondaries:
          disks_ok = False

  # FIXME: race condition on drbd migration to primary

  # 2nd pass, do only the primary node
  for inst_disk in instance.disks:
    for node, node_disk in inst_disk.ComputeNodeTree(instance.primary_node):
      if node != instance.primary_node:
        continue
      cfg.SetDiskID(node_disk, node)
      result = rpc.call_blockdev_assemble(node, node_disk, iname, True)
      if not result:
        logger.Error("could not prepare block device %s on node %s"
                     " (is_primary=True, pass=2)" % (inst_disk.iv_name, node))
        disks_ok = False
    device_info.append((instance.primary_node, inst_disk.iv_name, result))

  # leave the disks configured for the primary node
  # this is a workaround that would be fixed better by
  # improving the logical/physical id handling
  for disk in instance.disks:
    cfg.SetDiskID(disk, instance.primary_node)

  return disks_ok, device_info


def _StartInstanceDisks(cfg, instance, force):
  """Start the disks of an instance.

  """
  disks_ok, dummy = _AssembleInstanceDisks(instance, cfg,
                                           ignore_secondaries=force)
  if not disks_ok:
    _ShutdownInstanceDisks(instance, cfg)
    if force is not None and not force:
      logger.Error("If the message above refers to a secondary node,"
                   " you can retry the operation using '--force'.")
    raise errors.OpExecError("Disk consistency error")


class LUDeactivateInstanceDisks(NoHooksLU):
  """Shutdown an instance's disks.

  """
  _OP_REQP = ["instance_name"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    self.instance = instance

  def Exec(self, feedback_fn):
    """Deactivate the disks

    """
    instance = self.instance
    ins_l = rpc.call_instance_list([instance.primary_node])
    ins_l = ins_l[instance.primary_node]
    if not type(ins_l) is list:
      raise errors.OpExecError("Can't contact node '%s'" %
                               instance.primary_node)

    if self.instance.name in ins_l:
      raise errors.OpExecError("Instance is running, can't shutdown"
                               " block devices.")

    _ShutdownInstanceDisks(instance, self.cfg)


def _ShutdownInstanceDisks(instance, cfg, ignore_primary=False):
  """Shutdown block devices of an instance.

  This does the shutdown on all nodes of the instance.

  If the ignore_primary is false, errors on the primary node are
  ignored.

  """
  result = True
  for disk in instance.disks:
    for node, top_disk in disk.ComputeNodeTree(instance.primary_node):
      cfg.SetDiskID(top_disk, node)
      if not rpc.call_blockdev_shutdown(node, top_disk):
        logger.Error("could not shutdown block device %s on node %s" %
                     (disk.iv_name, node))
        if not ignore_primary or node != instance.primary_node:
          result = False
  return result


def _CheckNodeFreeMemory(cfg, node, reason, requested):
  """Checks if a node has enough free memory.

  This function check if a given node has the needed amount of free
  memory. In case the node has less memory or we cannot get the
  information from the node, this function raise an OpPrereqError
  exception.

  Args:
    - cfg: a ConfigWriter instance
    - node: the node name
    - reason: string to use in the error message
    - requested: the amount of memory in MiB

  """
  nodeinfo = rpc.call_node_info([node], cfg.GetVGName())
  if not (nodeinfo and isinstance(nodeinfo, dict) and
          node in nodeinfo and isinstance(nodeinfo[node], dict)):
    raise errors.OpPrereqError("Could not contact node %s for resource"
                             " information" % (node,))

  free_mem = nodeinfo[node].get('memory_free')
  if not isinstance(free_mem, int):
    raise errors.OpPrereqError("Can't compute free memory on node %s, result"
                             " was '%s'" % (node, free_mem))
  if requested > free_mem:
    raise errors.OpPrereqError("Not enough memory on node %s for %s:"
                             " needed %s MiB, available %s MiB" %
                             (node, reason, requested, free_mem))


class LUStartupInstance(LogicalUnit):
  """Starts an instance.

  """
  HPATH = "instance-start"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "force"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = {
      "FORCE": self.op.force,
      }
    env.update(_BuildInstanceHookEnvByObject(self.instance))
    nl = ([self.sstore.GetMasterNode(), self.instance.primary_node] +
          list(self.instance.secondary_nodes))
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)

    # check bridges existance
    _CheckInstanceBridgesExist(instance)

    _CheckNodeFreeMemory(self.cfg, instance.primary_node,
                         "starting instance %s" % instance.name,
                         instance.memory)

    self.instance = instance
    self.op.instance_name = instance.name

  def Exec(self, feedback_fn):
    """Start the instance.

    """
    instance = self.instance
    force = self.op.force
    extra_args = getattr(self.op, "extra_args", "")

    self.cfg.MarkInstanceUp(instance.name)

    node_current = instance.primary_node

    _StartInstanceDisks(self.cfg, instance, force)

    if not rpc.call_instance_start(node_current, instance, extra_args):
      _ShutdownInstanceDisks(instance, self.cfg)
      raise errors.OpExecError("Could not start instance")


class LURebootInstance(LogicalUnit):
  """Reboot an instance.

  """
  HPATH = "instance-reboot"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "ignore_secondaries", "reboot_type"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = {
      "IGNORE_SECONDARIES": self.op.ignore_secondaries,
      }
    env.update(_BuildInstanceHookEnvByObject(self.instance))
    nl = ([self.sstore.GetMasterNode(), self.instance.primary_node] +
          list(self.instance.secondary_nodes))
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)

    # check bridges existance
    _CheckInstanceBridgesExist(instance)

    self.instance = instance
    self.op.instance_name = instance.name

  def Exec(self, feedback_fn):
    """Reboot the instance.

    """
    instance = self.instance
    ignore_secondaries = self.op.ignore_secondaries
    reboot_type = self.op.reboot_type
    extra_args = getattr(self.op, "extra_args", "")

    node_current = instance.primary_node

    if reboot_type not in [constants.INSTANCE_REBOOT_SOFT,
                           constants.INSTANCE_REBOOT_HARD,
                           constants.INSTANCE_REBOOT_FULL]:
      raise errors.ParameterError("reboot type not in [%s, %s, %s]" %
                                  (constants.INSTANCE_REBOOT_SOFT,
                                   constants.INSTANCE_REBOOT_HARD,
                                   constants.INSTANCE_REBOOT_FULL))

    if reboot_type in [constants.INSTANCE_REBOOT_SOFT,
                       constants.INSTANCE_REBOOT_HARD]:
      if not rpc.call_instance_reboot(node_current, instance,
                                      reboot_type, extra_args):
        raise errors.OpExecError("Could not reboot instance")
    else:
      if not rpc.call_instance_shutdown(node_current, instance):
        raise errors.OpExecError("could not shutdown instance for full reboot")
      _ShutdownInstanceDisks(instance, self.cfg)
      _StartInstanceDisks(self.cfg, instance, ignore_secondaries)
      if not rpc.call_instance_start(node_current, instance, extra_args):
        _ShutdownInstanceDisks(instance, self.cfg)
        raise errors.OpExecError("Could not start instance for full reboot")

    self.cfg.MarkInstanceUp(instance.name)


class LUShutdownInstance(LogicalUnit):
  """Shutdown an instance.

  """
  HPATH = "instance-stop"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = _BuildInstanceHookEnvByObject(self.instance)
    nl = ([self.sstore.GetMasterNode(), self.instance.primary_node] +
          list(self.instance.secondary_nodes))
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    self.instance = instance

  def Exec(self, feedback_fn):
    """Shutdown the instance.

    """
    instance = self.instance
    node_current = instance.primary_node
    self.cfg.MarkInstanceDown(instance.name)
    if not rpc.call_instance_shutdown(node_current, instance):
      logger.Error("could not shutdown instance")

    _ShutdownInstanceDisks(instance, self.cfg)


class LUReinstallInstance(LogicalUnit):
  """Reinstall an instance.

  """
  HPATH = "instance-reinstall"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = _BuildInstanceHookEnvByObject(self.instance)
    nl = ([self.sstore.GetMasterNode(), self.instance.primary_node] +
          list(self.instance.secondary_nodes))
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster and is not running.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    if instance.disk_template == constants.DT_DISKLESS:
      raise errors.OpPrereqError("Instance '%s' has no disks" %
                                 self.op.instance_name)
    if instance.status != "down":
      raise errors.OpPrereqError("Instance '%s' is marked to be up" %
                                 self.op.instance_name)
    remote_info = rpc.call_instance_info(instance.primary_node, instance.name)
    if remote_info:
      raise errors.OpPrereqError("Instance '%s' is running on the node %s" %
                                 (self.op.instance_name,
                                  instance.primary_node))

    self.op.os_type = getattr(self.op, "os_type", None)
    if self.op.os_type is not None:
      # OS verification
      pnode = self.cfg.GetNodeInfo(
        self.cfg.ExpandNodeName(instance.primary_node))
      if pnode is None:
        raise errors.OpPrereqError("Primary node '%s' is unknown" %
                                   self.op.pnode)
      os_obj = rpc.call_os_get(pnode.name, self.op.os_type)
      if not os_obj:
        raise errors.OpPrereqError("OS '%s' not in supported OS list for"
                                   " primary node"  % self.op.os_type)

    self.instance = instance

  def Exec(self, feedback_fn):
    """Reinstall the instance.

    """
    inst = self.instance

    if self.op.os_type is not None:
      feedback_fn("Changing OS to '%s'..." % self.op.os_type)
      inst.os = self.op.os_type
      self.cfg.AddInstance(inst)

    _StartInstanceDisks(self.cfg, inst, None)
    try:
      feedback_fn("Running the instance OS create scripts...")
      if not rpc.call_instance_os_add(inst.primary_node, inst, "sda", "sdb"):
        raise errors.OpExecError("Could not install OS for instance %s"
                                 " on node %s" %
                                 (inst.name, inst.primary_node))
    finally:
      _ShutdownInstanceDisks(inst, self.cfg)


class LURenameInstance(LogicalUnit):
  """Rename an instance.

  """
  HPATH = "instance-rename"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "new_name"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = _BuildInstanceHookEnvByObject(self.instance)
    env["INSTANCE_NEW_NAME"] = self.op.new_name
    nl = ([self.sstore.GetMasterNode(), self.instance.primary_node] +
          list(self.instance.secondary_nodes))
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster and is not running.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    if instance.status != "down":
      raise errors.OpPrereqError("Instance '%s' is marked to be up" %
                                 self.op.instance_name)
    remote_info = rpc.call_instance_info(instance.primary_node, instance.name)
    if remote_info:
      raise errors.OpPrereqError("Instance '%s' is running on the node %s" %
                                 (self.op.instance_name,
                                  instance.primary_node))
    self.instance = instance

    # new name verification
    name_info = utils.HostInfo(self.op.new_name)

    self.op.new_name = new_name = name_info.name
    instance_list = self.cfg.GetInstanceList()
    if new_name in instance_list:
      raise errors.OpPrereqError("Instance '%s' is already in the cluster" %
                                 new_name)

    if not getattr(self.op, "ignore_ip", False):
      if utils.TcpPing(name_info.ip, constants.DEFAULT_NODED_PORT):
        raise errors.OpPrereqError("IP %s of instance %s already in use" %
                                   (name_info.ip, new_name))


  def Exec(self, feedback_fn):
    """Reinstall the instance.

    """
    inst = self.instance
    old_name = inst.name

    self.cfg.RenameInstance(inst.name, self.op.new_name)

    # re-read the instance from the configuration after rename
    inst = self.cfg.GetInstanceInfo(self.op.new_name)

    _StartInstanceDisks(self.cfg, inst, None)
    try:
      if not rpc.call_instance_run_rename(inst.primary_node, inst, old_name,
                                          "sda", "sdb"):
        msg = ("Could not run OS rename script for instance %s on node %s"
               " (but the instance has been renamed in Ganeti)" %
               (inst.name, inst.primary_node))
        logger.Error(msg)
    finally:
      _ShutdownInstanceDisks(inst, self.cfg)


class LURemoveInstance(LogicalUnit):
  """Remove an instance.

  """
  HPATH = "instance-remove"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "ignore_failures"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = _BuildInstanceHookEnvByObject(self.instance)
    nl = [self.sstore.GetMasterNode()]
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    self.instance = instance

  def Exec(self, feedback_fn):
    """Remove the instance.

    """
    instance = self.instance
    logger.Info("shutting down instance %s on node %s" %
                (instance.name, instance.primary_node))

    if not rpc.call_instance_shutdown(instance.primary_node, instance):
      if self.op.ignore_failures:
        feedback_fn("Warning: can't shutdown instance")
      else:
        raise errors.OpExecError("Could not shutdown instance %s on node %s" %
                                 (instance.name, instance.primary_node))

    logger.Info("removing block devices for instance %s" % instance.name)

    if not _RemoveDisks(instance, self.cfg):
      if self.op.ignore_failures:
        feedback_fn("Warning: can't remove instance's disks")
      else:
        raise errors.OpExecError("Can't remove instance's disks")

    logger.Info("removing instance %s out of cluster config" % instance.name)

    self.cfg.RemoveInstance(instance.name)


class LUQueryInstances(NoHooksLU):
  """Logical unit for querying instances.

  """
  _OP_REQP = ["output_fields", "names"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the fields required are valid output fields.

    """
    self.dynamic_fields = frozenset(["oper_state", "oper_ram", "status"])
    _CheckOutputFields(static=["name", "os", "pnode", "snodes",
                               "admin_state", "admin_ram",
                               "disk_template", "ip", "mac", "bridge",
                               "sda_size", "sdb_size", "vcpus", "tags",
                               "auto_balance",
                               "network_port", "kernel_path", "initrd_path",
                               "hvm_boot_order", "hvm_acpi", "hvm_pae",
                               "hvm_cdrom_image_path", "hvm_nic_type",
                               "hvm_disk_type", "vnc_bind_address"],
                       dynamic=self.dynamic_fields,
                       selected=self.op.output_fields)

    self.wanted = _GetWantedInstances(self, self.op.names)

  def Exec(self, feedback_fn):
    """Computes the list of nodes and their attributes.

    """
    instance_names = self.wanted
    instance_list = [self.cfg.GetInstanceInfo(iname) for iname
                     in instance_names]

    # begin data gathering

    nodes = frozenset([inst.primary_node for inst in instance_list])

    bad_nodes = []
    if self.dynamic_fields.intersection(self.op.output_fields):
      live_data = {}
      node_data = rpc.call_all_instances_info(nodes)
      for name in nodes:
        result = node_data[name]
        if result:
          live_data.update(result)
        elif result == False:
          bad_nodes.append(name)
        # else no instance is alive
    else:
      live_data = dict([(name, {}) for name in instance_names])

    # end data gathering

    output = []
    for instance in instance_list:
      iout = []
      for field in self.op.output_fields:
        if field == "name":
          val = instance.name
        elif field == "os":
          val = instance.os
        elif field == "pnode":
          val = instance.primary_node
        elif field == "snodes":
          val = list(instance.secondary_nodes)
        elif field == "admin_state":
          val = (instance.status != "down")
        elif field == "oper_state":
          if instance.primary_node in bad_nodes:
            val = None
          else:
            val = bool(live_data.get(instance.name))
        elif field == "status":
          if instance.primary_node in bad_nodes:
            val = "ERROR_nodedown"
          else:
            running = bool(live_data.get(instance.name))
            if running:
              if instance.status != "down":
                val = "running"
              else:
                val = "ERROR_up"
            else:
              if instance.status != "down":
                val = "ERROR_down"
              else:
                val = "ADMIN_down"
        elif field == "admin_ram":
          val = instance.memory
        elif field == "oper_ram":
          if instance.primary_node in bad_nodes:
            val = None
          elif instance.name in live_data:
            val = live_data[instance.name].get("memory", "?")
          else:
            val = "-"
        elif field == "disk_template":
          val = instance.disk_template
        elif field == "ip":
          val = instance.nics[0].ip
        elif field == "bridge":
          val = instance.nics[0].bridge
        elif field == "mac":
          val = instance.nics[0].mac
        elif field == "sda_size" or field == "sdb_size":
          disk = instance.FindDisk(field[:3])
          if disk is None:
            val = None
          else:
            val = disk.size
        elif field == "vcpus":
          val = instance.vcpus
        elif field == "tags":
          val = list(instance.GetTags())
        elif field == "auto_balance":
          val = instance.auto_balance
        elif field in ("network_port", "kernel_path", "initrd_path",
                       "hvm_boot_order", "hvm_acpi", "hvm_pae",
                       "hvm_cdrom_image_path", "hvm_nic_type",
                       "hvm_disk_type", "vnc_bind_address"):
          val = getattr(instance, field, None)
          if val is None:
            if field in ("hvm_nic_type", "hvm_disk_type",
                         "kernel_path", "initrd_path"):
              val = "default"
            else:
              val = "-"
        else:
          raise errors.ParameterError(field)
        iout.append(val)
      output.append(iout)

    return output


class LUFailoverInstance(LogicalUnit):
  """Failover an instance.

  """
  HPATH = "instance-failover"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "ignore_consistency"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = {
      "IGNORE_CONSISTENCY": self.op.ignore_consistency,
      }
    env.update(_BuildInstanceHookEnvByObject(self.instance))
    nl = [self.sstore.GetMasterNode()] + list(self.instance.secondary_nodes)
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)

    if instance.disk_template not in constants.DTS_NET_MIRROR:
      raise errors.OpPrereqError("Instance's disk layout is not"
                                 " network mirrored, cannot failover.")

    secondary_nodes = instance.secondary_nodes
    if not secondary_nodes:
      raise errors.ProgrammerError("no secondary node but using "
                                   "DT_REMOTE_RAID1 template")

    target_node = secondary_nodes[0]
    # check memory requirements on the secondary node
    _CheckNodeFreeMemory(self.cfg, target_node, "failing over instance %s" %
                         instance.name, instance.memory)

    # check bridge existance
    brlist = [nic.bridge for nic in instance.nics]
    if not rpc.call_bridges_exist(target_node, brlist):
      raise errors.OpPrereqError("One or more target bridges %s does not"
                                 " exist on destination node '%s'" %
                                 (brlist, target_node))

    self.instance = instance

  def Exec(self, feedback_fn):
    """Failover an instance.

    The failover is done by shutting it down on its present node and
    starting it on the secondary.

    """
    instance = self.instance

    source_node = instance.primary_node
    target_node = instance.secondary_nodes[0]

    feedback_fn("* checking disk consistency between source and target")
    for dev in instance.disks:
      # for remote_raid1, these are md over drbd
      if not _CheckDiskConsistency(self.cfg, dev, target_node, False):
        if instance.status == "up" and not self.op.ignore_consistency:
          raise errors.OpExecError("Disk %s is degraded on target node,"
                                   " aborting failover." % dev.iv_name)

    feedback_fn("* shutting down instance on source node")
    logger.Info("Shutting down instance %s on node %s" %
                (instance.name, source_node))

    if not rpc.call_instance_shutdown(source_node, instance):
      if self.op.ignore_consistency:
        logger.Error("Could not shutdown instance %s on node %s. Proceeding"
                     " anyway. Please make sure node %s is down"  %
                     (instance.name, source_node, source_node))
      else:
        raise errors.OpExecError("Could not shutdown instance %s on node %s" %
                                 (instance.name, source_node))

    feedback_fn("* deactivating the instance's disks on source node")
    if not _ShutdownInstanceDisks(instance, self.cfg, ignore_primary=True):
      raise errors.OpExecError("Can't shut down the instance's disks.")

    instance.primary_node = target_node
    # distribute new instance config to the other nodes
    self.cfg.Update(instance)

    # Only start the instance if it's marked as up
    if instance.status == "up":
      feedback_fn("* activating the instance's disks on target node")
      logger.Info("Starting instance %s on node %s" %
                  (instance.name, target_node))

      disks_ok, dummy = _AssembleInstanceDisks(instance, self.cfg,
                                               ignore_secondaries=True)
      if not disks_ok:
        _ShutdownInstanceDisks(instance, self.cfg)
        raise errors.OpExecError("Can't activate the instance's disks")

      feedback_fn("* starting the instance on the target node")
      if not rpc.call_instance_start(target_node, instance, None):
        _ShutdownInstanceDisks(instance, self.cfg)
        raise errors.OpExecError("Could not start instance %s on node %s." %
                                 (instance.name, target_node))


class LUMigrateInstance(LogicalUnit):
  """Migrate an instance.

  This is migration without shutting down, compared to the failover,
  which is done with shutdown.

  """
  HPATH = "instance-migrate"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "live", "cleanup"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = _BuildInstanceHookEnvByObject(self.instance)
    nl = [self.sstore.GetMasterNode()] + list(self.instance.secondary_nodes)
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)

    if instance.disk_template != constants.DT_DRBD8:
      raise errors.OpPrereqError("Instance's disk layout is not"
                                 " drbd8, cannot migrate.")

    secondary_nodes = instance.secondary_nodes
    if not secondary_nodes:
      raise errors.ProgrammerError("no secondary node but using "
                                   "drbd8 disk template")

    target_node = secondary_nodes[0]
    # check memory requirements on the secondary node
    _CheckNodeFreeMemory(self.cfg, target_node, "migrating instance %s" %
                         instance.name, instance.memory)

    # check bridge existance
    brlist = [nic.bridge for nic in instance.nics]
    if not rpc.call_bridges_exist(target_node, brlist):
      raise errors.OpPrereqError("One or more target bridges %s does not"
                                 " exist on destination node '%s'" %
                                 (brlist, target_node))

    if not self.op.cleanup:
      migratable = rpc.call_instance_migratable(instance.primary_node,
                                                instance)
      if not migratable:
        raise errors.OpPrereqError("Can't contact node '%s'" %
                                   instance.primary_node)
      if not migratable[0]:
        raise errors.OpPrereqError("Can't migrate: %s - please use failover" %
                                   migratable[1])

    self.instance = instance

  def _WaitUntilSync(self):
    """Poll with custom rpc for disk sync.

    This uses our own step-based rpc call.

    """
    self.feedback_fn("* wait until resync is done")
    all_done = False
    while not all_done:
      all_done = True
      result = rpc.call_drbd_reconfig_net(self.all_nodes, self.instance.name,
                                          self.instance.disks,
                                          self.nodes_ip, False,
                                          constants.DRBD_RECONF_RPC_WFSYNC)
      min_percent = 100
      for node in self.all_nodes:
        if not result[node] or not result[node][0]:
          raise errors.OpExecError("Cannot resync disks on node %s" % (node,))
        node_done, node_percent = result[node][1]
        all_done = all_done and node_done
        if node_percent is not None:
          min_percent = min(min_percent, node_percent)
      if not all_done:
        if min_percent < 100:
          self.feedback_fn("   - progress: %.1f%%" % min_percent)
        time.sleep(2)

  def _EnsureSecondary(self, node):
    """Demote a node to secondary.

    """
    self.feedback_fn("* switching node %s to secondary mode" % node)
    result = rpc.call_drbd_reconfig_net([node], self.instance.name,
                                        self.instance.disks,
                                        self.nodes_ip, False,
                                        constants.DRBD_RECONF_RPC_SECONDARY)
    if not result[node] or not result[node][0]:
        raise errors.OpExecError("Cannot change disk to secondary on node %s,"
                                 " error %s" %
                                 (node, result[node][1]))

  def _GoStandalone(self):
    """Disconnect from the network.

    """
    self.feedback_fn("* changing into standalone mode")
    result = rpc.call_drbd_reconfig_net(self.all_nodes, self.instance.name,
                                        self.instance.disks,
                                        self.nodes_ip, True,
                                        constants.DRBD_RECONF_RPC_DISCONNECT)
    for node in self.all_nodes:
      if not result[node] or not result[node][0]:
        raise errors.OpExecError("Cannot disconnect disks node %s,"
                                 " error %s" % (node, result[node][1]))

  def _GoReconnect(self, multimaster):
    """Reconnect to the network.

    """
    if multimaster:
      msg = "dual-master"
    else:
      msg = "single-master"
    self.feedback_fn("* changing disks into %s mode" % msg)
    result = rpc.call_drbd_reconfig_net(self.all_nodes, self.instance.name,
                                        self.instance.disks,
                                        self.nodes_ip,
                                        multimaster,
                                        constants.DRBD_RECONF_RPC_RECONNECT)
    for node in self.all_nodes:
      if not result[node] or not result[node][0]:
        raise errors.OpExecError("Cannot change disks config on node %s,"
                                 " error %s" % (node, result[node][1]))

  def _IdentifyDisks(self):
    """Start the migration RPC sequence.

    """
    self.feedback_fn("* identifying disks")
    result = rpc.call_drbd_reconfig_net(self.all_nodes,
                                        self.instance.name,
                                        self.instance.disks,
                                        self.nodes_ip, True,
                                        constants.DRBD_RECONF_RPC_INIT)
    for node in self.all_nodes:
      if not result[node] or not result[node][0]:
        raise errors.OpExecError("Cannot identify disks node %s,"
                                 " error %s" % (node, result[node][1]))

  def _ExecCleanup(self):
    """Try to cleanup after a failed migration.

    The cleanup is done by:
      - check that the instance is running only on one node
        (and update the config if needed)
      - change disks on its secondary node to secondary
      - wait until disks are fully synchronized
      - disconnect from the network
      - change disks into single-master mode
      - wait again until disks are fully synchronized

    """
    instance = self.instance
    target_node = self.target_node
    source_node = self.source_node

    # check running on only one node
    self.feedback_fn("* checking where the instance actually runs"
                     " (if this hangs, the hypervisor might be in"
                     " a bad state)")
    ins_l = rpc.call_instance_list(self.all_nodes)
    for node in self.all_nodes:
      if not type(ins_l[node]) is list:
        raise errors.OpExecError("Can't contact node '%s'" % node)

    runningon_source = instance.name in ins_l[source_node]
    runningon_target = instance.name in ins_l[target_node]

    if runningon_source and runningon_target:
      raise errors.OpExecError("Instance seems to be running on two nodes,"
                               " or the hypervisor is confused. You will have"
                               " to ensure manually that it runs only on one"
                               " and restart this operation.")

    if not (runningon_source or runningon_target):
      raise errors.OpExecError("Instance does not seem to be running at all."
                               " In this case, it's safer to repair by"
                               " running 'gnt-instance stop' to ensure disk"
                               " shutdown, and then restarting it.")

    if runningon_target:
      # the migration has actually succeeded, we need to update the config
      self.feedback_fn("* instance running on secondary node (%s),"
                       " updating config" % target_node)
      instance.primary_node = target_node
      self.cfg.Update(instance)
      demoted_node = source_node
    else:
      self.feedback_fn("* instance confirmed to be running on its"
                       " primary node (%s)" % source_node)
      demoted_node = target_node

    self._IdentifyDisks()

    self._EnsureSecondary(demoted_node)
    self._WaitUntilSync()
    self._GoStandalone()
    self._GoReconnect(False)
    self._WaitUntilSync()

    self.feedback_fn("* done")

  def _ExecMigration(self):
    """Migrate an instance.

    The migrate is done by:
      - change the disks into dual-master mode
      - wait until disks are fully synchronized again
      - migrate the instance
      - change disks on the new secondary node (the old primary) to secondary
      - wait until disks are fully synchronized
      - change disks into single-master mode

    """
    instance = self.instance
    target_node = self.target_node
    source_node = self.source_node

    self.feedback_fn("* checking disk consistency between source and target")
    for dev in instance.disks:
      if not _CheckDiskConsistency(self.cfg, dev, target_node, False):
        raise errors.OpExecError("Disk %s is degraded or not fully"
                                 " synchronized on target node,"
                                 " aborting migrate." % dev.iv_name)

    self._IdentifyDisks()

    self._EnsureSecondary(target_node)
    self._GoStandalone()
    self._GoReconnect(True)
    self._WaitUntilSync()

    self.feedback_fn("* migrating instance to %s" % target_node)
    time.sleep(10)
    result = rpc.call_instance_migrate(source_node, instance,
                                       self.nodes_ip[target_node],
                                       self.op.live)
    if not result or not result[0]:
      logger.Error("Instance migration failed, trying to revert disk status")
      try:
        self._EnsureSecondary(target_node)
        self._GoStandalone()
        self._GoReconnect(False)
        self._WaitUntilSync()
      except errors.OpExecError, err:
        logger.Error("Can't reconnect the drives: error '%s'\n"
                     "Please look and recover the instance status" % str(err))

      raise errors.OpExecError("Could not migrate instance %s: %s" %
                               (instance.name, result[1]))
    time.sleep(10)

    instance.primary_node = target_node
    # distribute new instance config to the other nodes
    self.cfg.Update(instance)

    self._EnsureSecondary(source_node)
    self._WaitUntilSync()
    self._GoStandalone()
    self._GoReconnect(False)
    self._WaitUntilSync()

    self.feedback_fn("* done")

  def Exec(self, feedback_fn):
    """Perform the migration.

    """
    self.feedback_fn = feedback_fn

    self.source_node = self.instance.primary_node
    self.target_node = self.instance.secondary_nodes[0]
    self.all_nodes = [self.source_node, self.target_node]
    self.nodes_ip = {
      self.source_node: self.cfg.GetNodeInfo(self.source_node).secondary_ip,
      self.target_node: self.cfg.GetNodeInfo(self.target_node).secondary_ip,
      }
    if self.op.cleanup:
      return self._ExecCleanup()
    else:
      return self._ExecMigration()


def _CreateBlockDevOnPrimary(cfg, node, instance, device, info):
  """Create a tree of block devices on the primary node.

  This always creates all devices.

  """
  if device.children:
    for child in device.children:
      if not _CreateBlockDevOnPrimary(cfg, node, instance, child, info):
        return False

  cfg.SetDiskID(device, node)
  new_id = rpc.call_blockdev_create(node, device, device.size,
                                    instance.name, True, info)
  if not new_id:
    return False
  if device.physical_id is None:
    device.physical_id = new_id
  return True


def _CreateBlockDevOnSecondary(cfg, node, instance, device, force, info):
  """Create a tree of block devices on a secondary node.

  If this device type has to be created on secondaries, create it and
  all its children.

  If not, just recurse to children keeping the same 'force' value.

  """
  if device.CreateOnSecondary():
    force = True
  if device.children:
    for child in device.children:
      if not _CreateBlockDevOnSecondary(cfg, node, instance,
                                        child, force, info):
        return False

  if not force:
    return True
  cfg.SetDiskID(device, node)
  new_id = rpc.call_blockdev_create(node, device, device.size,
                                    instance.name, False, info)
  if not new_id:
    return False
  if device.physical_id is None:
    device.physical_id = new_id
  return True


def _GenerateUniqueNames(cfg, exts):
  """Generate a suitable LV name.

  This will generate a logical volume name for the given instance.

  """
  results = []
  for val in exts:
    new_id = cfg.GenerateUniqueID()
    results.append("%s%s" % (new_id, val))
  return results


def _GenerateMDDRBDBranch(cfg, primary, secondary, size, names):
  """Generate a drbd device complete with its children.

  """
  port = cfg.AllocatePort()
  vgname = cfg.GetVGName()
  dev_data = objects.Disk(dev_type=constants.LD_LV, size=size,
                          logical_id=(vgname, names[0]))
  dev_meta = objects.Disk(dev_type=constants.LD_LV, size=128,
                          logical_id=(vgname, names[1]))
  drbd_dev = objects.Disk(dev_type=constants.LD_DRBD7, size=size,
                          logical_id = (primary, secondary, port),
                          children = [dev_data, dev_meta])
  return drbd_dev


def _GenerateDRBD8Branch(cfg, primary, secondary, size, names, iv_name):
  """Generate a drbd8 device complete with its children.

  """
  port = cfg.AllocatePort()
  vgname = cfg.GetVGName()
  dev_data = objects.Disk(dev_type=constants.LD_LV, size=size,
                          logical_id=(vgname, names[0]))
  dev_meta = objects.Disk(dev_type=constants.LD_LV, size=128,
                          logical_id=(vgname, names[1]))
  drbd_dev = objects.Disk(dev_type=constants.LD_DRBD8, size=size,
                          logical_id = (primary, secondary, port),
                          children = [dev_data, dev_meta],
                          iv_name=iv_name)
  return drbd_dev

def _GenerateDiskTemplate(cfg, template_name,
                          instance_name, primary_node,
                          secondary_nodes, disk_sz, swap_sz):
  """Generate the entire disk layout for a given template type.

  """
  #TODO: compute space requirements

  vgname = cfg.GetVGName()
  if template_name == constants.DT_DISKLESS:
    disks = []
  elif template_name == constants.DT_PLAIN:
    if len(secondary_nodes) != 0:
      raise errors.ProgrammerError("Wrong template configuration")

    names = _GenerateUniqueNames(cfg, [".sda", ".sdb"])
    sda_dev = objects.Disk(dev_type=constants.LD_LV, size=disk_sz,
                           logical_id=(vgname, names[0]),
                           iv_name = "sda")
    sdb_dev = objects.Disk(dev_type=constants.LD_LV, size=swap_sz,
                           logical_id=(vgname, names[1]),
                           iv_name = "sdb")
    disks = [sda_dev, sdb_dev]
  elif template_name == constants.DT_LOCAL_RAID1:
    if len(secondary_nodes) != 0:
      raise errors.ProgrammerError("Wrong template configuration")


    names = _GenerateUniqueNames(cfg, [".sda_m1", ".sda_m2",
                                       ".sdb_m1", ".sdb_m2"])
    sda_dev_m1 = objects.Disk(dev_type=constants.LD_LV, size=disk_sz,
                              logical_id=(vgname, names[0]))
    sda_dev_m2 = objects.Disk(dev_type=constants.LD_LV, size=disk_sz,
                              logical_id=(vgname, names[1]))
    md_sda_dev = objects.Disk(dev_type=constants.LD_MD_R1, iv_name = "sda",
                              size=disk_sz,
                              children = [sda_dev_m1, sda_dev_m2])
    sdb_dev_m1 = objects.Disk(dev_type=constants.LD_LV, size=swap_sz,
                              logical_id=(vgname, names[2]))
    sdb_dev_m2 = objects.Disk(dev_type=constants.LD_LV, size=swap_sz,
                              logical_id=(vgname, names[3]))
    md_sdb_dev = objects.Disk(dev_type=constants.LD_MD_R1, iv_name = "sdb",
                              size=swap_sz,
                              children = [sdb_dev_m1, sdb_dev_m2])
    disks = [md_sda_dev, md_sdb_dev]
  elif template_name == constants.DT_REMOTE_RAID1:
    if len(secondary_nodes) != 1:
      raise errors.ProgrammerError("Wrong template configuration")
    remote_node = secondary_nodes[0]
    names = _GenerateUniqueNames(cfg, [".sda_data", ".sda_meta",
                                       ".sdb_data", ".sdb_meta"])
    drbd_sda_dev = _GenerateMDDRBDBranch(cfg, primary_node, remote_node,
                                         disk_sz, names[0:2])
    md_sda_dev = objects.Disk(dev_type=constants.LD_MD_R1, iv_name="sda",
                              children = [drbd_sda_dev], size=disk_sz)
    drbd_sdb_dev = _GenerateMDDRBDBranch(cfg, primary_node, remote_node,
                                         swap_sz, names[2:4])
    md_sdb_dev = objects.Disk(dev_type=constants.LD_MD_R1, iv_name="sdb",
                              children = [drbd_sdb_dev], size=swap_sz)
    disks = [md_sda_dev, md_sdb_dev]
  elif template_name == constants.DT_DRBD8:
    if len(secondary_nodes) != 1:
      raise errors.ProgrammerError("Wrong template configuration")
    remote_node = secondary_nodes[0]
    names = _GenerateUniqueNames(cfg, [".sda_data", ".sda_meta",
                                       ".sdb_data", ".sdb_meta"])
    drbd_sda_dev = _GenerateDRBD8Branch(cfg, primary_node, remote_node,
                                         disk_sz, names[0:2], "sda")
    drbd_sdb_dev = _GenerateDRBD8Branch(cfg, primary_node, remote_node,
                                         swap_sz, names[2:4], "sdb")
    disks = [drbd_sda_dev, drbd_sdb_dev]
  else:
    raise errors.ProgrammerError("Invalid disk template '%s'" % template_name)
  return disks


def _GetInstanceInfoText(instance):
  """Compute that text that should be added to the disk's metadata.

  """
  return "originstname+%s" % instance.name


def _CreateDisks(cfg, instance):
  """Create all disks for an instance.

  This abstracts away some work from AddInstance.

  Args:
    instance: the instance object

  Returns:
    True or False showing the success of the creation process

  """
  info = _GetInstanceInfoText(instance)

  for device in instance.disks:
    logger.Info("creating volume %s for instance %s" %
              (device.iv_name, instance.name))
    #HARDCODE
    for secondary_node in instance.secondary_nodes:
      if not _CreateBlockDevOnSecondary(cfg, secondary_node, instance,
                                        device, False, info):
        logger.Error("failed to create volume %s (%s) on secondary node %s!" %
                     (device.iv_name, device, secondary_node))
        return False
    #HARDCODE
    if not _CreateBlockDevOnPrimary(cfg, instance.primary_node,
                                    instance, device, info):
      logger.Error("failed to create volume %s on primary!" %
                   device.iv_name)
      return False
  return True


def _RemoveDisks(instance, cfg):
  """Remove all disks for an instance.

  This abstracts away some work from `AddInstance()` and
  `RemoveInstance()`. Note that in case some of the devices couldn't
  be removed, the removal will continue with the other ones (compare
  with `_CreateDisks()`).

  Args:
    instance: the instance object

  Returns:
    True or False showing the success of the removal proces

  """
  logger.Info("removing block devices for instance %s" % instance.name)

  result = True
  for device in instance.disks:
    for node, disk in device.ComputeNodeTree(instance.primary_node):
      cfg.SetDiskID(disk, node)
      if not rpc.call_blockdev_remove(node, disk):
        logger.Error("could not remove block device %s on node %s,"
                     " continuing anyway" %
                     (device.iv_name, node))
        result = False
  return result


def _ComputeDiskSize(disk_template, disk_size, swap_size):
  """Compute disk size requirements in the volume group

  This is currently hard-coded for the two-drive layout.

  """
  # Required free disk space as a function of disk and swap space
  req_size_dict = {
    constants.DT_DISKLESS: None,
    constants.DT_PLAIN: disk_size + swap_size,
    constants.DT_LOCAL_RAID1: (disk_size + swap_size) * 2,
    # 256 MB are added for drbd metadata, 128MB for each drbd device
    constants.DT_REMOTE_RAID1: disk_size + swap_size + 256,
    constants.DT_DRBD8: disk_size + swap_size + 256,
  }

  if disk_template not in req_size_dict:
    raise errors.ProgrammerError("Disk template '%s' size requirement"
                                 " is unknown" %  disk_template)

  return req_size_dict[disk_template]


class LUCreateInstance(LogicalUnit):
  """Create an instance.

  """
  HPATH = "instance-add"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "mem_size", "disk_size",
              "disk_template", "swap_size", "mode", "start", "vcpus",
              "wait_for_sync", "ip_check", "mac", "auto_balance"]

  def _RunAllocator(self):
    """Run the allocator based on input opcode.

    """
    disks = [{"size": self.op.disk_size, "mode": "w"},
             {"size": self.op.swap_size, "mode": "w"}]
    nics = [{"mac": self.op.mac, "ip": getattr(self.op, "ip", None),
             "bridge": self.op.bridge}]
    ial = IAllocator(self.cfg, self.sstore,
                     mode=constants.IALLOCATOR_MODE_ALLOC,
                     name=self.op.instance_name,
                     disk_template=self.op.disk_template,
                     tags=[],
                     os=self.op.os_type,
                     vcpus=self.op.vcpus,
                     mem_size=self.op.mem_size,
                     disks=disks,
                     nics=nics,
                     )

    ial.Run(self.op.iallocator)

    if not ial.success:
      raise errors.OpPrereqError("Can't compute nodes using"
                                 " iallocator '%s': %s" % (self.op.iallocator,
                                                           ial.info))
    if len(ial.nodes) != ial.required_nodes:
      raise errors.OpPrereqError("iallocator '%s' returned invalid number"
                                 " of nodes (%s), required %s" %
                                 (self.op.iallocator, len(ial.nodes),
                                  ial.required_nodes))
    self.op.pnode = ial.nodes[0]
    logger.ToStdout("Selected nodes for the instance: %s" %
                    (", ".join(ial.nodes),))
    logger.Info("Selected nodes for instance %s via iallocator %s: %s" %
                (self.op.instance_name, self.op.iallocator, ial.nodes))
    if ial.required_nodes == 2:
      self.op.snode = ial.nodes[1]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on master, primary and secondary nodes of the instance.

    """
    env = {
      "INSTANCE_DISK_TEMPLATE": self.op.disk_template,
      "INSTANCE_DISK_SIZE": self.op.disk_size,
      "INSTANCE_SWAP_SIZE": self.op.swap_size,
      "INSTANCE_ADD_MODE": self.op.mode,
      }
    if self.op.mode == constants.INSTANCE_IMPORT:
      env["INSTANCE_SRC_NODE"] = self.op.src_node
      env["INSTANCE_SRC_PATH"] = self.op.src_path
      env["INSTANCE_SRC_IMAGE"] = self.src_image

    env.update(_BuildInstanceHookEnv(name=self.op.instance_name,
      primary_node=self.op.pnode,
      secondary_nodes=self.secondaries,
      status=self.instance_status,
      os_type=self.op.os_type,
      memory=self.op.mem_size,
      vcpus=self.op.vcpus,
      nics=[(self.inst_ip, self.op.bridge, self.op.mac)],
    ))

    nl = ([self.sstore.GetMasterNode(), self.op.pnode] +
          self.secondaries)
    return env, nl, nl


  def CheckPrereq(self):
    """Check prerequisites.

    """
    # set optional parameters to none if they don't exist
    for attr in ["kernel_path", "initrd_path", "hvm_boot_order", "pnode",
                 "iallocator", "hvm_acpi", "hvm_pae", "hvm_cdrom_image_path",
                 "hvm_nic_type", "hvm_disk_type", "vnc_bind_address"]:
      if not hasattr(self.op, attr):
        setattr(self.op, attr, None)

    if self.op.mode not in (constants.INSTANCE_CREATE,
                            constants.INSTANCE_IMPORT):
      raise errors.OpPrereqError("Invalid instance creation mode '%s'" %
                                 self.op.mode)

    if self.op.mode == constants.INSTANCE_IMPORT:
      src_node = getattr(self.op, "src_node", None)
      src_path = getattr(self.op, "src_path", None)
      if src_node is None or src_path is None:
        raise errors.OpPrereqError("Importing an instance requires source"
                                   " node and path options")
      src_node_full = self.cfg.ExpandNodeName(src_node)
      if src_node_full is None:
        raise errors.OpPrereqError("Unknown source node '%s'" % src_node)
      self.op.src_node = src_node = src_node_full

      if not os.path.isabs(src_path):
        raise errors.OpPrereqError("The source path must be absolute")

      export_info = rpc.call_export_info(src_node, src_path)

      if not export_info:
        raise errors.OpPrereqError("No export found in dir %s" % src_path)

      if not export_info.has_section(constants.INISECT_EXP):
        raise errors.ProgrammerError("Corrupted export config")

      ei_version = export_info.get(constants.INISECT_EXP, 'version')
      if (int(ei_version) != constants.EXPORT_VERSION):
        raise errors.OpPrereqError("Wrong export version %s (wanted %d)" %
                                   (ei_version, constants.EXPORT_VERSION))

      if int(export_info.get(constants.INISECT_INS, 'disk_count')) > 1:
        raise errors.OpPrereqError("Can't import instance with more than"
                                   " one data disk")

      # FIXME: are the old os-es, disk sizes, etc. useful?
      self.op.os_type = export_info.get(constants.INISECT_EXP, 'os')
      diskimage = os.path.join(src_path, export_info.get(constants.INISECT_INS,
                                                         'disk0_dump'))
      self.src_image = diskimage
    else: # INSTANCE_CREATE
      if getattr(self.op, "os_type", None) is None:
        raise errors.OpPrereqError("No guest OS specified")

    #### instance parameters check

    # disk template and mirror node verification
    if self.op.disk_template not in constants.DISK_TEMPLATES:
      raise errors.OpPrereqError("Invalid disk template name")

    # instance name verification
    hostname1 = utils.HostInfo(self.op.instance_name)

    self.op.instance_name = instance_name = hostname1.name
    instance_list = self.cfg.GetInstanceList()
    if instance_name in instance_list:
      raise errors.OpPrereqError("Instance '%s' is already in the cluster" %
                                 instance_name)

    if (self.op.mode == constants.INSTANCE_IMPORT and
        self.op.mac == constants.VALUE_AUTO):
      old_name = export_info.get(constants.INISECT_INS, 'name')
      if instance_name == old_name:
        if int(export_info.get(constants.INISECT_INS, 'nic_count')) >= 1:
          self.op.mac = export_info.get(constants.INISECT_INS, 'nic_0_mac')

    # ip validity checks
    ip = getattr(self.op, "ip", None)
    if ip is None or ip.lower() == "none":
      inst_ip = None
    elif ip.lower() == constants.VALUE_AUTO:
      inst_ip = hostname1.ip
    else:
      if not utils.IsValidIP(ip):
        raise errors.OpPrereqError("given IP address '%s' doesn't look"
                                   " like a valid IP" % ip)
      inst_ip = ip
    self.inst_ip = self.op.ip = inst_ip

    if self.op.start and not self.op.ip_check:
      raise errors.OpPrereqError("Cannot ignore IP address conflicts when"
                                 " adding an instance in start mode")

    if self.op.ip_check:
      if utils.TcpPing(hostname1.ip, constants.DEFAULT_NODED_PORT):
        raise errors.OpPrereqError("IP %s of instance %s already in use" %
                                   (hostname1.ip, instance_name))

    # MAC address verification
    if self.op.mac not in (constants.VALUE_AUTO, constants.VALUE_GENERATE):
      if not utils.IsValidMac(self.op.mac.lower()):
        raise errors.OpPrereqError("invalid MAC address specified: %s" %
                                   self.op.mac)

    # bridge verification
    bridge = getattr(self.op, "bridge", None)
    if bridge is None:
      self.op.bridge = self.cfg.GetDefBridge()
    else:
      self.op.bridge = bridge

    # boot order verification
    if self.op.hvm_boot_order is not None:
      if len(self.op.hvm_boot_order.strip("acdn")) != 0:
        raise errors.OpPrereqError("invalid boot order specified,"
                                   " must be one or more of [acdn]")
    #### allocator run

    if [self.op.iallocator, self.op.pnode].count(None) != 1:
      raise errors.OpPrereqError("One and only one of iallocator and primary"
                                 " node must be given")

    if self.op.iallocator is not None:
      self._RunAllocator()

    #### node related checks

    # check primary node
    pnode = self.cfg.GetNodeInfo(self.cfg.ExpandNodeName(self.op.pnode))
    if pnode is None:
      raise errors.OpPrereqError("Primary node '%s' is unknown" %
                                 self.op.pnode)
    self.op.pnode = pnode.name
    self.pnode = pnode
    self.secondaries = []

    # mirror node verification
    if self.op.disk_template in constants.DTS_NET_MIRROR:
      if getattr(self.op, "snode", None) is None:
        raise errors.OpPrereqError("The networked disk templates need"
                                   " a mirror node")

      snode_name = self.cfg.ExpandNodeName(self.op.snode)
      if snode_name is None:
        raise errors.OpPrereqError("Unknown secondary node '%s'" %
                                   self.op.snode)
      elif snode_name == pnode.name:
        raise errors.OpPrereqError("The secondary node cannot be"
                                   " the primary node.")
      self.secondaries.append(snode_name)

    req_size = _ComputeDiskSize(self.op.disk_template,
                                self.op.disk_size, self.op.swap_size)

    # Check lv size requirements
    if req_size is not None:
      nodenames = [pnode.name] + self.secondaries
      nodeinfo = rpc.call_node_info(nodenames, self.cfg.GetVGName())
      for node in nodenames:
        info = nodeinfo.get(node, None)
        if not info:
          raise errors.OpPrereqError("Cannot get current information"
                                     " from node '%s'" % node)
        vg_free = info.get('vg_free', None)
        if not isinstance(vg_free, int):
          raise errors.OpPrereqError("Can't compute free disk space on"
                                     " node %s" % node)
        if req_size > info['vg_free']:
          raise errors.OpPrereqError("Not enough disk space on target node %s."
                                     " %d MB available, %d MB required" %
                                     (node, info['vg_free'], req_size))

    # os verification
    os_obj = rpc.call_os_get(pnode.name, self.op.os_type)
    if not os_obj:
      raise errors.OpPrereqError("OS '%s' not in supported os list for"
                                 " primary node"  % self.op.os_type)

    if self.op.kernel_path == constants.VALUE_NONE:
      raise errors.OpPrereqError("Can't set instance kernel to none")


    # bridge check on primary node
    if not rpc.call_bridges_exist(self.pnode.name, [self.op.bridge]):
      raise errors.OpPrereqError("target bridge '%s' does not exist on"
                                 " destination node '%s'" %
                                 (self.op.bridge, pnode.name))

    # memory check on primary node
    if self.op.start:
      _CheckNodeFreeMemory(self.cfg, self.pnode.name,
                           "creating instance %s" % self.op.instance_name,
                           self.op.mem_size)

    # hvm_cdrom_image_path verification
    if self.op.hvm_cdrom_image_path is not None:
      if not os.path.isabs(self.op.hvm_cdrom_image_path):
        raise errors.OpPrereqError("The path to the HVM CDROM image must"
                                   " be an absolute path or None, not %s" %
                                   self.op.hvm_cdrom_image_path)
      if not os.path.isfile(self.op.hvm_cdrom_image_path):
        raise errors.OpPrereqError("The HVM CDROM image must either be a"
                                   " regular file or a symlink pointing to"
                                   " an existing regular file, not %s" %
                                   self.op.hvm_cdrom_image_path)

    # vnc_bind_address verification
    if self.op.vnc_bind_address is not None:
      if not utils.IsValidIP(self.op.vnc_bind_address):
        raise errors.OpPrereqError("given VNC bind address '%s' doesn't look"
                                   " like a valid IP address" %
                                   self.op.vnc_bind_address)

    # Xen HVM device type checks
    if self.sstore.GetHypervisorType() == constants.HT_XEN_HVM31:
      if self.op.hvm_nic_type not in constants.HT_HVM_VALID_NIC_TYPES:
        raise errors.OpPrereqError("Invalid NIC type %s specified for Xen HVM"
                                   " hypervisor" % self.op.hvm_nic_type)
      if self.op.hvm_disk_type not in constants.HT_HVM_VALID_DISK_TYPES:
        raise errors.OpPrereqError("Invalid disk type %s specified for Xen HVM"
                                   " hypervisor" % self.op.hvm_disk_type)

    if self.op.start:
      self.instance_status = 'up'
    else:
      self.instance_status = 'down'

  def Exec(self, feedback_fn):
    """Create and add the instance to the cluster.

    """
    instance = self.op.instance_name
    pnode_name = self.pnode.name

    if self.op.mac in (constants.VALUE_AUTO, constants.VALUE_GENERATE):
      mac_address = self.cfg.GenerateMAC()
    else:
      mac_address = self.op.mac

    nic = objects.NIC(bridge=self.op.bridge, mac=mac_address)
    if self.inst_ip is not None:
      nic.ip = self.inst_ip

    ht_kind = self.sstore.GetHypervisorType()
    if ht_kind in constants.HTS_REQ_PORT:
      network_port = self.cfg.AllocatePort()
    else:
      network_port = None

    if self.op.vnc_bind_address is None:
      self.op.vnc_bind_address = constants.VNC_DEFAULT_BIND_ADDRESS

    disks = _GenerateDiskTemplate(self.cfg,
                                  self.op.disk_template,
                                  instance, pnode_name,
                                  self.secondaries, self.op.disk_size,
                                  self.op.swap_size)

    iobj = objects.Instance(name=instance, os=self.op.os_type,
                            primary_node=pnode_name,
                            memory=self.op.mem_size,
                            vcpus=self.op.vcpus,
                            nics=[nic], disks=disks,
                            disk_template=self.op.disk_template,
                            status=self.instance_status,
                            network_port=network_port,
                            kernel_path=self.op.kernel_path,
                            initrd_path=self.op.initrd_path,
                            hvm_boot_order=self.op.hvm_boot_order,
                            hvm_acpi=self.op.hvm_acpi,
                            hvm_pae=self.op.hvm_pae,
                            hvm_cdrom_image_path=self.op.hvm_cdrom_image_path,
                            vnc_bind_address=self.op.vnc_bind_address,
                            hvm_nic_type=self.op.hvm_nic_type,
                            hvm_disk_type=self.op.hvm_disk_type,
                            auto_balance=bool(self.op.auto_balance),
                            )

    feedback_fn("* creating instance disks...")
    if not _CreateDisks(self.cfg, iobj):
      _RemoveDisks(iobj, self.cfg)
      raise errors.OpExecError("Device creation failed, reverting...")

    feedback_fn("adding instance %s to cluster config" % instance)

    self.cfg.AddInstance(iobj)

    if self.op.wait_for_sync:
      disk_abort = not _WaitForSync(self.cfg, iobj, self.proc)
    elif iobj.disk_template in constants.DTS_NET_MIRROR:
      # make sure the disks are not degraded (still sync-ing is ok)
      time.sleep(15)
      feedback_fn("* checking mirrors status")
      disk_abort = not _WaitForSync(self.cfg, iobj, self.proc, oneshot=True)
    else:
      disk_abort = False

    if disk_abort:
      _RemoveDisks(iobj, self.cfg)
      self.cfg.RemoveInstance(iobj.name)
      raise errors.OpExecError("There are some degraded disks for"
                               " this instance")

    feedback_fn("creating os for instance %s on node %s" %
                (instance, pnode_name))

    if iobj.disk_template != constants.DT_DISKLESS:
      if self.op.mode == constants.INSTANCE_CREATE:
        feedback_fn("* running the instance OS create scripts...")
        if not rpc.call_instance_os_add(pnode_name, iobj, "sda", "sdb"):
          raise errors.OpExecError("could not add os for instance %s"
                                   " on node %s" %
                                   (instance, pnode_name))

      elif self.op.mode == constants.INSTANCE_IMPORT:
        feedback_fn("* running the instance OS import scripts...")
        src_node = self.op.src_node
        src_image = self.src_image
        if not rpc.call_instance_os_import(pnode_name, iobj, "sda", "sdb",
                                                src_node, src_image):
          raise errors.OpExecError("Could not import os for instance"
                                   " %s on node %s" %
                                   (instance, pnode_name))
      else:
        # also checked in the prereq part
        raise errors.ProgrammerError("Unknown OS initialization mode '%s'"
                                     % self.op.mode)

    if self.op.start:
      logger.Info("starting instance %s on node %s" % (instance, pnode_name))
      feedback_fn("* starting instance...")
      if not rpc.call_instance_start(pnode_name, iobj, None):
        raise errors.OpExecError("Could not start instance")


class LUConnectConsole(NoHooksLU):
  """Connect to an instance's console.

  This is somewhat special in that it returns the command line that
  you need to run on the master node in order to connect to the
  console.

  """
  _OP_REQP = ["instance_name"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    self.instance = instance

  def Exec(self, feedback_fn):
    """Connect to the console of an instance

    """
    instance = self.instance
    node = instance.primary_node

    node_insts = rpc.call_instance_list([node])[node]
    if node_insts is False:
      raise errors.OpExecError("Can't connect to node %s." % node)

    if instance.name not in node_insts:
      raise errors.OpExecError("Instance %s is not running." % instance.name)

    logger.Debug("connecting to console of %s on %s" % (instance.name, node))

    hyper = hypervisor.GetHypervisor()
    console_cmd = hyper.GetShellCommandForConsole(instance)
    # build ssh cmdline
    argv = ["ssh", "-q", "-t"]
    argv.extend(ssh.KNOWN_HOSTS_OPTS)
    argv.extend(ssh.BATCH_MODE_OPTS)
    argv.append(node)
    argv.append(console_cmd)
    return "ssh", argv


class LUAddMDDRBDComponent(LogicalUnit):
  """Adda new mirror member to an instance's disk.

  """
  HPATH = "mirror-add"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "remote_node", "disk_name"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on the master, the primary and all the secondaries.

    """
    env = {
      "NEW_SECONDARY": self.op.remote_node,
      "DISK_NAME": self.op.disk_name,
      }
    env.update(_BuildInstanceHookEnvByObject(self.instance))
    nl = [self.sstore.GetMasterNode(), self.instance.primary_node,
          self.op.remote_node,] + list(self.instance.secondary_nodes)
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    self.instance = instance

    remote_node = self.cfg.ExpandNodeName(self.op.remote_node)
    if remote_node is None:
      raise errors.OpPrereqError("Node '%s' not known" % self.op.remote_node)
    self.remote_node = remote_node

    if remote_node == instance.primary_node:
      raise errors.OpPrereqError("The specified node is the primary node of"
                                 " the instance.")

    if instance.disk_template != constants.DT_REMOTE_RAID1:
      raise errors.OpPrereqError("Instance's disk layout is not"
                                 " remote_raid1.")
    for disk in instance.disks:
      if disk.iv_name == self.op.disk_name:
        break
    else:
      raise errors.OpPrereqError("Can't find this device ('%s') in the"
                                 " instance." % self.op.disk_name)
    if len(disk.children) > 1:
      raise errors.OpPrereqError("The device already has two slave devices."
                                 " This would create a 3-disk raid1 which we"
                                 " don't allow.")
    self.disk = disk

  def Exec(self, feedback_fn):
    """Add the mirror component

    """
    disk = self.disk
    instance = self.instance

    remote_node = self.remote_node
    lv_names = [".%s_%s" % (disk.iv_name, suf) for suf in ["data", "meta"]]
    names = _GenerateUniqueNames(self.cfg, lv_names)
    new_drbd = _GenerateMDDRBDBranch(self.cfg, instance.primary_node,
                                     remote_node, disk.size, names)

    logger.Info("adding new mirror component on secondary")
    #HARDCODE
    if not _CreateBlockDevOnSecondary(self.cfg, remote_node, instance,
                                      new_drbd, False,
                                      _GetInstanceInfoText(instance)):
      raise errors.OpExecError("Failed to create new component on secondary"
                               " node %s" % remote_node)

    logger.Info("adding new mirror component on primary")
    #HARDCODE
    if not _CreateBlockDevOnPrimary(self.cfg, instance.primary_node,
                                    instance, new_drbd,
                                    _GetInstanceInfoText(instance)):
      # remove secondary dev
      self.cfg.SetDiskID(new_drbd, remote_node)
      rpc.call_blockdev_remove(remote_node, new_drbd)
      raise errors.OpExecError("Failed to create volume on primary")

    # the device exists now
    # call the primary node to add the mirror to md
    logger.Info("adding new mirror component to md")
    if not rpc.call_blockdev_addchildren(instance.primary_node,
                                         disk, [new_drbd]):
      logger.Error("Can't add mirror compoment to md!")
      self.cfg.SetDiskID(new_drbd, remote_node)
      if not rpc.call_blockdev_remove(remote_node, new_drbd):
        logger.Error("Can't rollback on secondary")
      self.cfg.SetDiskID(new_drbd, instance.primary_node)
      if not rpc.call_blockdev_remove(instance.primary_node, new_drbd):
        logger.Error("Can't rollback on primary")
      raise errors.OpExecError("Can't add mirror component to md array")

    disk.children.append(new_drbd)

    self.cfg.AddInstance(instance)

    _WaitForSync(self.cfg, instance, self.proc)

    return 0


class LURemoveMDDRBDComponent(LogicalUnit):
  """Remove a component from a remote_raid1 disk.

  """
  HPATH = "mirror-remove"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "disk_name", "disk_id"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on the master, the primary and all the secondaries.

    """
    env = {
      "DISK_NAME": self.op.disk_name,
      "DISK_ID": self.op.disk_id,
      "OLD_SECONDARY": self.old_secondary,
      }
    env.update(_BuildInstanceHookEnvByObject(self.instance))
    nl = [self.sstore.GetMasterNode(),
          self.instance.primary_node] + list(self.instance.secondary_nodes)
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    self.instance = instance

    if instance.disk_template != constants.DT_REMOTE_RAID1:
      raise errors.OpPrereqError("Instance's disk layout is not"
                                 " remote_raid1.")
    for disk in instance.disks:
      if disk.iv_name == self.op.disk_name:
        break
    else:
      raise errors.OpPrereqError("Can't find this device ('%s') in the"
                                 " instance." % self.op.disk_name)
    for child in disk.children:
      if (child.dev_type == constants.LD_DRBD7 and
          child.logical_id[2] == self.op.disk_id):
        break
    else:
      raise errors.OpPrereqError("Can't find the device with this port.")

    if len(disk.children) < 2:
      raise errors.OpPrereqError("Cannot remove the last component from"
                                 " a mirror.")
    self.disk = disk
    self.child = child
    if self.child.logical_id[0] == instance.primary_node:
      oid = 1
    else:
      oid = 0
    self.old_secondary = self.child.logical_id[oid]

  def Exec(self, feedback_fn):
    """Remove the mirror component

    """
    instance = self.instance
    disk = self.disk
    child = self.child
    logger.Info("remove mirror component")
    self.cfg.SetDiskID(disk, instance.primary_node)
    if not rpc.call_blockdev_removechildren(instance.primary_node,
                                            disk, [child]):
      raise errors.OpExecError("Can't remove child from mirror.")

    for node in child.logical_id[:2]:
      self.cfg.SetDiskID(child, node)
      if not rpc.call_blockdev_remove(node, child):
        logger.Error("Warning: failed to remove device from node %s,"
                     " continuing operation." % node)

    disk.children.remove(child)
    self.cfg.AddInstance(instance)


class LUReplaceDisks(LogicalUnit):
  """Replace the disks of an instance.

  """
  HPATH = "mirrors-replace"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "mode", "disks"]

  def _RunAllocator(self):
    """Compute a new secondary node using an IAllocator.

    """
    ial = IAllocator(self.cfg, self.sstore,
                     mode=constants.IALLOCATOR_MODE_RELOC,
                     name=self.op.instance_name,
                     relocate_from=[self.sec_node])

    ial.Run(self.op.iallocator)

    if not ial.success:
      raise errors.OpPrereqError("Can't compute nodes using"
                                 " iallocator '%s': %s" % (self.op.iallocator,
                                                           ial.info))
    if len(ial.nodes) != ial.required_nodes:
      raise errors.OpPrereqError("iallocator '%s' returned invalid number"
                                 " of nodes (%s), required %s" %
                                 (len(ial.nodes), ial.required_nodes))
    self.op.remote_node = ial.nodes[0]
    logger.ToStdout("Selected new secondary for the instance: %s" %
                    self.op.remote_node)

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on the master, the primary and all the secondaries.

    """
    env = {
      "MODE": self.op.mode,
      "NEW_SECONDARY": self.op.remote_node,
      "OLD_SECONDARY": self.instance.secondary_nodes[0],
      }
    env.update(_BuildInstanceHookEnvByObject(self.instance))
    nl = [
      self.sstore.GetMasterNode(),
      self.instance.primary_node,
      ]
    if self.op.remote_node is not None:
      nl.append(self.op.remote_node)
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    if not hasattr(self.op, "remote_node"):
      self.op.remote_node = None

    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)
    self.instance = instance
    self.op.instance_name = instance.name

    if instance.disk_template not in constants.DTS_NET_MIRROR:
      raise errors.OpPrereqError("Instance's disk layout is not"
                                 " network mirrored.")

    if len(instance.secondary_nodes) != 1:
      raise errors.OpPrereqError("The instance has a strange layout,"
                                 " expected one secondary but found %d" %
                                 len(instance.secondary_nodes))

    self.sec_node = instance.secondary_nodes[0]

    ia_name = getattr(self.op, "iallocator", None)
    if ia_name is not None:
      if self.op.remote_node is not None:
        raise errors.OpPrereqError("Give either the iallocator or the new"
                                   " secondary, not both")
      self._RunAllocator()

    remote_node = self.op.remote_node
    if remote_node is not None:
      remote_node = self.cfg.ExpandNodeName(remote_node)
      if remote_node is None:
        raise errors.OpPrereqError("Node '%s' not known" %
                                   self.op.remote_node)
      self.remote_node_info = self.cfg.GetNodeInfo(remote_node)
    else:
      self.remote_node_info = None
    if remote_node == instance.primary_node:
      raise errors.OpPrereqError("The specified node is the primary node of"
                                 " the instance.")
    elif remote_node == self.sec_node:
      if self.op.mode == constants.REPLACE_DISK_SEC:
        # this is for DRBD8, where we can't execute the same mode of
        # replacement as for drbd7 (no different port allocated)
        raise errors.OpPrereqError("Same secondary given, cannot execute"
                                   " replacement")
      # the user gave the current secondary, switch to
      # 'no-replace-secondary' mode for drbd7
      remote_node = None
    if (instance.disk_template == constants.DT_REMOTE_RAID1 and
        self.op.mode != constants.REPLACE_DISK_ALL):
      raise errors.OpPrereqError("Template 'remote_raid1' only allows all"
                                 " disks replacement, not individual ones")
    if instance.disk_template == constants.DT_DRBD8:
      if (self.op.mode == constants.REPLACE_DISK_ALL and
          remote_node is not None):
        # switch to replace secondary mode
        self.op.mode = constants.REPLACE_DISK_SEC

      if self.op.mode == constants.REPLACE_DISK_ALL:
        raise errors.OpPrereqError("Template 'drbd' only allows primary or"
                                   " secondary disk replacement, not"
                                   " both at once")
      elif self.op.mode == constants.REPLACE_DISK_PRI:
        if remote_node is not None:
          raise errors.OpPrereqError("Template 'drbd' does not allow changing"
                                     " the secondary while doing a primary"
                                     " node disk replacement")
        self.tgt_node = instance.primary_node
        self.oth_node = instance.secondary_nodes[0]
      elif self.op.mode == constants.REPLACE_DISK_SEC:
        self.new_node = remote_node # this can be None, in which case
                                    # we don't change the secondary
        self.tgt_node = instance.secondary_nodes[0]
        self.oth_node = instance.primary_node
      else:
        raise errors.ProgrammerError("Unhandled disk replace mode")

    for name in self.op.disks:
      if instance.FindDisk(name) is None:
        raise errors.OpPrereqError("Disk '%s' not found for instance '%s'" %
                                   (name, instance.name))
    self.op.remote_node = remote_node

  def _ExecRR1(self, feedback_fn):
    """Replace the disks of an instance.

    """
    instance = self.instance
    iv_names = {}
    # start of work
    if self.op.remote_node is None:
      remote_node = self.sec_node
    else:
      remote_node = self.op.remote_node
    cfg = self.cfg
    for dev in instance.disks:
      size = dev.size
      lv_names = [".%s_%s" % (dev.iv_name, suf) for suf in ["data", "meta"]]
      names = _GenerateUniqueNames(cfg, lv_names)
      new_drbd = _GenerateMDDRBDBranch(cfg, instance.primary_node,
                                       remote_node, size, names)
      iv_names[dev.iv_name] = (dev, dev.children[0], new_drbd)
      logger.Info("adding new mirror component on secondary for %s" %
                  dev.iv_name)
      #HARDCODE
      if not _CreateBlockDevOnSecondary(cfg, remote_node, instance,
                                        new_drbd, False,
                                        _GetInstanceInfoText(instance)):
        raise errors.OpExecError("Failed to create new component on secondary"
                                 " node %s. Full abort, cleanup manually!" %
                                 remote_node)

      logger.Info("adding new mirror component on primary")
      #HARDCODE
      if not _CreateBlockDevOnPrimary(cfg, instance.primary_node,
                                      instance, new_drbd,
                                      _GetInstanceInfoText(instance)):
        # remove secondary dev
        cfg.SetDiskID(new_drbd, remote_node)
        rpc.call_blockdev_remove(remote_node, new_drbd)
        raise errors.OpExecError("Failed to create volume on primary!"
                                 " Full abort, cleanup manually!!")

      # the device exists now
      # call the primary node to add the mirror to md
      logger.Info("adding new mirror component to md")
      if not rpc.call_blockdev_addchildren(instance.primary_node, dev,
                                           [new_drbd]):
        logger.Error("Can't add mirror compoment to md!")
        cfg.SetDiskID(new_drbd, remote_node)
        if not rpc.call_blockdev_remove(remote_node, new_drbd):
          logger.Error("Can't rollback on secondary")
        cfg.SetDiskID(new_drbd, instance.primary_node)
        if not rpc.call_blockdev_remove(instance.primary_node, new_drbd):
          logger.Error("Can't rollback on primary")
        raise errors.OpExecError("Full abort, cleanup manually!!")

      dev.children.append(new_drbd)
      cfg.AddInstance(instance)

    # this can fail as the old devices are degraded and _WaitForSync
    # does a combined result over all disks, so we don't check its
    # return value
    _WaitForSync(cfg, instance, self.proc, unlock=True)

    # so check manually all the devices
    for name in iv_names:
      dev, child, new_drbd = iv_names[name]
      cfg.SetDiskID(dev, instance.primary_node)
      is_degr = rpc.call_blockdev_find(instance.primary_node, dev)[5]
      if is_degr:
        raise errors.OpExecError("MD device %s is degraded!" % name)
      cfg.SetDiskID(new_drbd, instance.primary_node)
      is_degr = rpc.call_blockdev_find(instance.primary_node, new_drbd)[5]
      if is_degr:
        raise errors.OpExecError("New drbd device %s is degraded!" % name)

    for name in iv_names:
      dev, child, new_drbd = iv_names[name]
      logger.Info("remove mirror %s component" % name)
      cfg.SetDiskID(dev, instance.primary_node)
      if not rpc.call_blockdev_removechildren(instance.primary_node,
                                              dev, [child]):
        logger.Error("Can't remove child from mirror, aborting"
                     " *this device cleanup*.\nYou need to cleanup manually!!")
        continue

      for node in child.logical_id[:2]:
        logger.Info("remove child device on %s" % node)
        cfg.SetDiskID(child, node)
        if not rpc.call_blockdev_remove(node, child):
          logger.Error("Warning: failed to remove device from node %s,"
                       " continuing operation." % node)

      dev.children.remove(child)

      cfg.AddInstance(instance)

  def _ExecD8DiskOnly(self, feedback_fn):
    """Replace a disk on the primary or secondary for dbrd8.

    The algorithm for replace is quite complicated:
      - for each disk to be replaced:
        - create new LVs on the target node with unique names
        - detach old LVs from the drbd device
        - rename old LVs to name_replaced.<time_t>
        - rename new LVs to old LVs
        - attach the new LVs (with the old names now) to the drbd device
      - wait for sync across all devices
      - for each modified disk:
        - remove old LVs (which have the name name_replaces.<time_t>)

    Failures are not very well handled.

    """
    steps_total = 6
    warning, info = (self.proc.LogWarning, self.proc.LogInfo)
    instance = self.instance
    iv_names = {}
    vgname = self.cfg.GetVGName()
    # start of work
    cfg = self.cfg
    tgt_node = self.tgt_node
    oth_node = self.oth_node

    # Step: check device activation
    self.proc.LogStep(1, steps_total, "check device existence")
    info("checking volume groups")
    my_vg = cfg.GetVGName()
    results = rpc.call_vg_list([oth_node, tgt_node])
    if not results:
      raise errors.OpExecError("Can't list volume groups on the nodes")
    for node in oth_node, tgt_node:
      res = results.get(node, False)
      if not res or my_vg not in res:
        raise errors.OpExecError("Volume group '%s' not found on %s" %
                                 (my_vg, node))
    for dev in instance.disks:
      if not dev.iv_name in self.op.disks:
        continue
      for node in tgt_node, oth_node:
        info("checking %s on %s" % (dev.iv_name, node))
        cfg.SetDiskID(dev, node)
        if not rpc.call_blockdev_find(node, dev):
          raise errors.OpExecError("Can't find device %s on node %s" %
                                   (dev.iv_name, node))

    # Step: check other node consistency
    self.proc.LogStep(2, steps_total, "check peer consistency")
    for dev in instance.disks:
      if not dev.iv_name in self.op.disks:
        continue
      info("checking %s consistency on %s" % (dev.iv_name, oth_node))
      if not _CheckDiskConsistency(self.cfg, dev, oth_node,
                                   oth_node==instance.primary_node):
        raise errors.OpExecError("Peer node (%s) has degraded storage, unsafe"
                                 " to replace disks on this node (%s)" %
                                 (oth_node, tgt_node))

    # Step: create new storage
    self.proc.LogStep(3, steps_total, "allocate new storage")
    for dev in instance.disks:
      if not dev.iv_name in self.op.disks:
        continue
      size = dev.size
      cfg.SetDiskID(dev, tgt_node)
      lv_names = [".%s_%s" % (dev.iv_name, suf) for suf in ["data", "meta"]]
      names = _GenerateUniqueNames(cfg, lv_names)
      lv_data = objects.Disk(dev_type=constants.LD_LV, size=size,
                             logical_id=(vgname, names[0]))
      lv_meta = objects.Disk(dev_type=constants.LD_LV, size=128,
                             logical_id=(vgname, names[1]))
      new_lvs = [lv_data, lv_meta]
      old_lvs = dev.children
      iv_names[dev.iv_name] = (dev, old_lvs, new_lvs)
      info("creating new local storage on %s for %s" %
           (tgt_node, dev.iv_name))
      # since we *always* want to create this LV, we use the
      # _Create...OnPrimary (which forces the creation), even if we
      # are talking about the secondary node
      for new_lv in new_lvs:
        if not _CreateBlockDevOnPrimary(cfg, tgt_node, instance, new_lv,
                                        _GetInstanceInfoText(instance)):
          raise errors.OpExecError("Failed to create new LV named '%s' on"
                                   " node '%s'" %
                                   (new_lv.logical_id[1], tgt_node))

    # Step: for each lv, detach+rename*2+attach
    self.proc.LogStep(4, steps_total, "change drbd configuration")
    for dev, old_lvs, new_lvs in iv_names.itervalues():
      info("detaching %s drbd from local storage" % dev.iv_name)
      if not rpc.call_blockdev_removechildren(tgt_node, dev, old_lvs):
        raise errors.OpExecError("Can't detach drbd from local storage on node"
                                 " %s for device %s" % (tgt_node, dev.iv_name))
      #dev.children = []
      #cfg.Update(instance)

      # ok, we created the new LVs, so now we know we have the needed
      # storage; as such, we proceed on the target node to rename
      # old_lv to _old, and new_lv to old_lv; note that we rename LVs
      # using the assumption that logical_id == physical_id (which in
      # turn is the unique_id on that node)

      # FIXME(iustin): use a better name for the replaced LVs
      temp_suffix = int(time.time())
      ren_fn = lambda d, suff: (d.physical_id[0],
                                d.physical_id[1] + "_replaced-%s" % suff)
      # build the rename list based on what LVs exist on the node
      rlist = []
      for to_ren in old_lvs:
        find_res = rpc.call_blockdev_find(tgt_node, to_ren)
        if find_res is not None: # device exists
          rlist.append((to_ren, ren_fn(to_ren, temp_suffix)))

      info("renaming the old LVs on the target node")
      if not rpc.call_blockdev_rename(tgt_node, rlist):
        raise errors.OpExecError("Can't rename old LVs on node %s" % tgt_node)
      # now we rename the new LVs to the old LVs
      info("renaming the new LVs on the target node")
      rlist = [(new, old.physical_id) for old, new in zip(old_lvs, new_lvs)]
      if not rpc.call_blockdev_rename(tgt_node, rlist):
        raise errors.OpExecError("Can't rename new LVs on node %s" % tgt_node)

      for old, new in zip(old_lvs, new_lvs):
        new.logical_id = old.logical_id
        cfg.SetDiskID(new, tgt_node)

      for disk in old_lvs:
        disk.logical_id = ren_fn(disk, temp_suffix)
        cfg.SetDiskID(disk, tgt_node)

      # now that the new lvs have the old name, we can add them to the device
      info("adding new mirror component on %s" % tgt_node)
      if not rpc.call_blockdev_addchildren(tgt_node, dev, new_lvs):
        for new_lv in new_lvs:
          if not rpc.call_blockdev_remove(tgt_node, new_lv):
            warning("Can't rollback device %s", hint="manually cleanup unused"
                    " logical volumes")
        raise errors.OpExecError("Can't add local storage to drbd")

      dev.children = new_lvs
      cfg.Update(instance)

    # Step: wait for sync

    # this can fail as the old devices are degraded and _WaitForSync
    # does a combined result over all disks, so we don't check its
    # return value
    self.proc.LogStep(5, steps_total, "sync devices")
    _WaitForSync(cfg, instance, self.proc, unlock=True)

    # so check manually all the devices
    for name, (dev, old_lvs, new_lvs) in iv_names.iteritems():
      cfg.SetDiskID(dev, instance.primary_node)
      is_degr = rpc.call_blockdev_find(instance.primary_node, dev)[5]
      if is_degr:
        raise errors.OpExecError("DRBD device %s is degraded!" % name)

    # Step: remove old storage
    self.proc.LogStep(6, steps_total, "removing old storage")
    for name, (dev, old_lvs, new_lvs) in iv_names.iteritems():
      info("remove logical volumes for %s" % name)
      for lv in old_lvs:
        cfg.SetDiskID(lv, tgt_node)
        if not rpc.call_blockdev_remove(tgt_node, lv):
          warning("Can't remove old LV", hint="manually remove unused LVs")
          continue

  def _ExecD8Secondary(self, feedback_fn):
    """Replace the secondary node for drbd8.

    The algorithm for replace is quite complicated:
      - for all disks of the instance:
        - create new LVs on the new node with same names
        - shutdown the drbd device on the old secondary
        - disconnect the drbd network on the primary
        - create the drbd device on the new secondary
        - network attach the drbd on the primary, using an artifice:
          the drbd code for Attach() will connect to the network if it
          finds a device which is connected to the good local disks but
          not network enabled
      - wait for sync across all devices
      - remove all disks from the old secondary

    Failures are not very well handled.

    """
    steps_total = 6
    warning, info = (self.proc.LogWarning, self.proc.LogInfo)
    instance = self.instance
    iv_names = {}
    vgname = self.cfg.GetVGName()
    # start of work
    cfg = self.cfg
    old_node = self.tgt_node
    new_node = self.new_node
    pri_node = instance.primary_node

    # Step: check device activation
    self.proc.LogStep(1, steps_total, "check device existence")
    info("checking volume groups")
    my_vg = cfg.GetVGName()
    results = rpc.call_vg_list([pri_node, new_node])
    if not results:
      raise errors.OpExecError("Can't list volume groups on the nodes")
    for node in pri_node, new_node:
      res = results.get(node, False)
      if not res or my_vg not in res:
        raise errors.OpExecError("Volume group '%s' not found on %s" %
                                 (my_vg, node))
    for dev in instance.disks:
      if not dev.iv_name in self.op.disks:
        continue
      info("checking %s on %s" % (dev.iv_name, pri_node))
      cfg.SetDiskID(dev, pri_node)
      if not rpc.call_blockdev_find(pri_node, dev):
        raise errors.OpExecError("Can't find device %s on node %s" %
                                 (dev.iv_name, pri_node))

    # Step: check other node consistency
    self.proc.LogStep(2, steps_total, "check peer consistency")
    for dev in instance.disks:
      if not dev.iv_name in self.op.disks:
        continue
      info("checking %s consistency on %s" % (dev.iv_name, pri_node))
      if not _CheckDiskConsistency(self.cfg, dev, pri_node, True, ldisk=True):
        raise errors.OpExecError("Primary node (%s) has degraded storage,"
                                 " unsafe to replace the secondary" %
                                 pri_node)

    # Step: create new storage
    self.proc.LogStep(3, steps_total, "allocate new storage")
    for dev in instance.disks:
      size = dev.size
      info("adding new local storage on %s for %s" % (new_node, dev.iv_name))
      # since we *always* want to create this LV, we use the
      # _Create...OnPrimary (which forces the creation), even if we
      # are talking about the secondary node
      for new_lv in dev.children:
        if not _CreateBlockDevOnPrimary(cfg, new_node, instance, new_lv,
                                        _GetInstanceInfoText(instance)):
          raise errors.OpExecError("Failed to create new LV named '%s' on"
                                   " node '%s'" %
                                   (new_lv.logical_id[1], new_node))

      iv_names[dev.iv_name] = (dev, dev.children)

    self.proc.LogStep(4, steps_total, "changing drbd configuration")
    for dev in instance.disks:
      size = dev.size
      info("activating a new drbd on %s for %s" % (new_node, dev.iv_name))
      # create new devices on new_node
      new_drbd = objects.Disk(dev_type=constants.LD_DRBD8,
                              logical_id=(pri_node, new_node,
                                          dev.logical_id[2]),
                              children=dev.children)
      if not _CreateBlockDevOnSecondary(cfg, new_node, instance,
                                        new_drbd, False,
                                      _GetInstanceInfoText(instance)):
        raise errors.OpExecError("Failed to create new DRBD on"
                                 " node '%s'" % new_node)

    for dev in instance.disks:
      # we have new devices, shutdown the drbd on the old secondary
      info("shutting down drbd for %s on old node" % dev.iv_name)
      cfg.SetDiskID(dev, old_node)
      if not rpc.call_blockdev_shutdown(old_node, dev):
        warning("Failed to shutdown drbd for %s on old node" % dev.iv_name,
                hint="Please cleanup this device manually as soon as possible")

    info("detaching primary drbds from the network (=> standalone)")
    done = 0
    for dev in instance.disks:
      cfg.SetDiskID(dev, pri_node)
      # set the physical (unique in bdev terms) id to None, meaning
      # detach from network
      dev.physical_id = (None,) * len(dev.physical_id)
      # and 'find' the device, which will 'fix' it to match the
      # standalone state
      if rpc.call_blockdev_find(pri_node, dev):
        done += 1
      else:
        warning("Failed to detach drbd %s from network, unusual case" %
                dev.iv_name)

    if not done:
      # no detaches succeeded (very unlikely)
      raise errors.OpExecError("Can't detach at least one DRBD from old node")

    # if we managed to detach at least one, we update all the disks of
    # the instance to point to the new secondary
    info("updating instance configuration")
    for dev in instance.disks:
      dev.logical_id = (pri_node, new_node) + dev.logical_id[2:]
      cfg.SetDiskID(dev, pri_node)
    cfg.Update(instance)

    # and now perform the drbd attach
    info("attaching primary drbds to new secondary (standalone => connected)")
    failures = []
    for dev in instance.disks:
      info("attaching primary drbd for %s to new secondary node" % dev.iv_name)
      # since the attach is smart, it's enough to 'find' the device,
      # it will automatically activate the network, if the physical_id
      # is correct
      cfg.SetDiskID(dev, pri_node)
      if not rpc.call_blockdev_find(pri_node, dev):
        warning("can't attach drbd %s to new secondary!" % dev.iv_name,
                "please do a gnt-instance info to see the status of disks")

    # this can fail as the old devices are degraded and _WaitForSync
    # does a combined result over all disks, so we don't check its
    # return value
    self.proc.LogStep(5, steps_total, "sync devices")
    _WaitForSync(cfg, instance, self.proc, unlock=True)

    # so check manually all the devices
    for name, (dev, old_lvs) in iv_names.iteritems():
      cfg.SetDiskID(dev, pri_node)
      is_degr = rpc.call_blockdev_find(pri_node, dev)[5]
      if is_degr:
        raise errors.OpExecError("DRBD device %s is degraded!" % name)

    self.proc.LogStep(6, steps_total, "removing old storage")
    for name, (dev, old_lvs) in iv_names.iteritems():
      info("remove logical volumes for %s" % name)
      for lv in old_lvs:
        cfg.SetDiskID(lv, old_node)
        if not rpc.call_blockdev_remove(old_node, lv):
          warning("Can't remove LV on old secondary",
                  hint="Cleanup stale volumes by hand")

  def Exec(self, feedback_fn):
    """Execute disk replacement.

    This dispatches the disk replacement to the appropriate handler.

    """
    instance = self.instance

    # Activate the instance disks if we're replacing them on a down instance
    if instance.status == "down":
      op = opcodes.OpActivateInstanceDisks(instance_name=instance.name)
      self.proc.ChainOpCode(op)

    if instance.disk_template == constants.DT_REMOTE_RAID1:
      fn = self._ExecRR1
    elif instance.disk_template == constants.DT_DRBD8:
      if self.op.remote_node is None:
        fn = self._ExecD8DiskOnly
      else:
        fn = self._ExecD8Secondary
    else:
      raise errors.ProgrammerError("Unhandled disk replacement case")

    ret = fn(feedback_fn)

    # Deactivate the instance disks if we're replacing them on a down instance
    if instance.status == "down":
      op = opcodes.OpDeactivateInstanceDisks(instance_name=instance.name)
      self.proc.ChainOpCode(op)

    return ret


class LUGrowDisk(LogicalUnit):
  """Grow a disk of an instance.

  """
  HPATH = "disk-grow"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "disk", "amount", "wait_for_sync"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on the master, the primary and all the secondaries.

    """
    env = {
      "DISK": self.op.disk,
      "AMOUNT": self.op.amount,
      }
    env.update(_BuildInstanceHookEnvByObject(self.instance))
    nl = [
      self.sstore.GetMasterNode(),
      self.instance.primary_node,
      ]
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance is in the cluster.

    """
    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("Instance '%s' not known" %
                                 self.op.instance_name)

    if self.op.amount <= 0:
      raise errors.OpPrereqError("Invalid grow-by amount: %s" % self.op.amount)

    self.instance = instance
    self.op.instance_name = instance.name

    if instance.disk_template not in (constants.DT_PLAIN, constants.DT_DRBD8):
      raise errors.OpPrereqError("Instance's disk layout does not support"
                                 " growing.")

    self.disk = instance.FindDisk(self.op.disk)
    if self.disk is None:
      raise errors.OpPrereqError("Disk '%s' not found for instance '%s'" %
                                 (self.op.disk, instance.name))

    nodenames = [instance.primary_node] + list(instance.secondary_nodes)
    nodeinfo = rpc.call_node_info(nodenames, self.cfg.GetVGName())
    for node in nodenames:
      info = nodeinfo.get(node, None)
      if not info:
        raise errors.OpPrereqError("Cannot get current information"
                                   " from node '%s'" % node)
      vg_free = info.get('vg_free', None)
      if not isinstance(vg_free, int):
        raise errors.OpPrereqError("Can't compute free disk space on"
                                   " node %s" % node)
      if self.op.amount > info['vg_free']:
        raise errors.OpPrereqError("Not enough disk space on target node %s:"
                                   " %d MiB available, %d MiB required" %
                                   (node, info['vg_free'], self.op.amount))
      is_primary = (node == instance.primary_node)
      if not _CheckDiskConsistency(self.cfg, self.disk, node, is_primary):
        raise errors.OpPrereqError("Disk %s is degraded or not fully"
                                 " synchronized on node %s,"
                                 " aborting grow." % (self.op.disk, node))

  def Exec(self, feedback_fn):
    """Execute disk grow.

    """
    instance = self.instance
    disk = self.disk
    for node in (instance.secondary_nodes + (instance.primary_node,)):
      self.cfg.SetDiskID(disk, node)
      result = rpc.call_blockdev_grow(node, disk, self.op.amount)
      if not result or not isinstance(result, tuple) or len(result) != 2:
        raise errors.OpExecError("grow request failed to node %s" % node)
      elif not result[0]:
        raise errors.OpExecError("grow request failed to node %s: %s" %
                                 (node, result[1]))
    disk.RecordGrow(self.op.amount)
    self.cfg.Update(instance)
    if self.op.wait_for_sync:
      disk_abort = not _WaitForSync(self.cfg, instance, self.proc)
      if disk_abort:
        logger.Error("Warning: disk sync-ing has not returned a good status.\n"
                     " Please check the instance.")


class LUQueryInstanceData(NoHooksLU):
  """Query runtime instance data.

  """
  _OP_REQP = ["instances", "static"]

  def CheckPrereq(self):
    """Check prerequisites.

    This only checks the optional instance list against the existing names.

    """
    if not isinstance(self.op.instances, list):
      raise errors.OpPrereqError("Invalid argument type 'instances'")
    if self.op.instances:
      self.wanted_instances = []
      names = self.op.instances
      for name in names:
        instance = self.cfg.GetInstanceInfo(self.cfg.ExpandInstanceName(name))
        if instance is None:
          raise errors.OpPrereqError("No such instance name '%s'" % name)
        self.wanted_instances.append(instance)
    else:
      self.wanted_instances = [self.cfg.GetInstanceInfo(name) for name
                               in self.cfg.GetInstanceList()]
    return


  def _ComputeDiskStatus(self, instance, snode, dev):
    """Compute block device status.

    """
    static = self.op.static
    if not static:
      self.cfg.SetDiskID(dev, instance.primary_node)
      dev_pstatus = rpc.call_blockdev_find(instance.primary_node, dev)
    else:
      dev_pstatus = None

    if dev.dev_type in constants.LDS_DRBD:
      # we change the snode then (otherwise we use the one passed in)
      if dev.logical_id[0] == instance.primary_node:
        snode = dev.logical_id[1]
      else:
        snode = dev.logical_id[0]

    if snode and not static:
      self.cfg.SetDiskID(dev, snode)
      dev_sstatus = rpc.call_blockdev_find(snode, dev)
    else:
      dev_sstatus = None

    if dev.children:
      dev_children = [self._ComputeDiskStatus(instance, snode, child)
                      for child in dev.children]
    else:
      dev_children = []

    data = {
      "iv_name": dev.iv_name,
      "dev_type": dev.dev_type,
      "logical_id": dev.logical_id,
      "physical_id": dev.physical_id,
      "pstatus": dev_pstatus,
      "sstatus": dev_sstatus,
      "children": dev_children,
      }

    return data

  def Exec(self, feedback_fn):
    """Gather and return data"""
    result = {}
    for instance in self.wanted_instances:
      if not self.op.static:
        remote_info = rpc.call_instance_info(instance.primary_node,
                                                  instance.name)
        if remote_info and "state" in remote_info:
          remote_state = "up"
        else:
          remote_state = "down"
      else:
        remote_state = None
      if instance.status == "down":
        config_state = "down"
      else:
        config_state = "up"

      disks = [self._ComputeDiskStatus(instance, None, device)
               for device in instance.disks]

      idict = {
        "name": instance.name,
        "config_state": config_state,
        "run_state": remote_state,
        "pnode": instance.primary_node,
        "snodes": instance.secondary_nodes,
        "os": instance.os,
        "memory": instance.memory,
        "nics": [(nic.mac, nic.ip, nic.bridge) for nic in instance.nics],
        "disks": disks,
        "vcpus": instance.vcpus,
        "auto_balance": instance.auto_balance,
        }

      htkind = self.sstore.GetHypervisorType()
      if htkind == constants.HT_XEN_PVM30:
        idict["kernel_path"] = instance.kernel_path
        idict["initrd_path"] = instance.initrd_path

      if htkind == constants.HT_XEN_HVM31:
        idict["hvm_boot_order"] = instance.hvm_boot_order
        idict["hvm_acpi"] = instance.hvm_acpi
        idict["hvm_pae"] = instance.hvm_pae
        idict["hvm_cdrom_image_path"] = instance.hvm_cdrom_image_path
        idict["hvm_nic_type"] = instance.hvm_nic_type
        idict["hvm_disk_type"] = instance.hvm_disk_type

      if htkind in constants.HTS_REQ_PORT:
        if instance.vnc_bind_address is None:
          vnc_bind_address = constants.VNC_DEFAULT_BIND_ADDRESS
        else:
          vnc_bind_address = instance.vnc_bind_address
        if instance.network_port is None:
          vnc_console_port = None
        elif vnc_bind_address == constants.BIND_ADDRESS_GLOBAL:
          vnc_console_port = "%s:%s" % (instance.primary_node,
                                       instance.network_port)
        elif vnc_bind_address == constants.LOCALHOST_IP_ADDRESS:
          vnc_console_port = "%s:%s on node %s" % (vnc_bind_address,
                                                   instance.network_port,
                                                   instance.primary_node)
        else:
          vnc_console_port = "%s:%s" % (instance.vnc_bind_address,
                                        instance.network_port)
        idict["vnc_console_port"] = vnc_console_port
        idict["vnc_bind_address"] = vnc_bind_address
        idict["network_port"] = instance.network_port

      result[instance.name] = idict

    return result


class LUSetInstanceParms(LogicalUnit):
  """Modifies an instances's parameters.

  """
  HPATH = "instance-modify"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This runs on the master, primary and secondaries.

    """
    args = dict()
    if self.mem:
      args['memory'] = self.mem
    if self.vcpus:
      args['vcpus'] = self.vcpus
    if self.do_ip or self.do_bridge or self.mac:
      if self.do_ip:
        ip = self.ip
      else:
        ip = self.instance.nics[0].ip
      if self.bridge:
        bridge = self.bridge
      else:
        bridge = self.instance.nics[0].bridge
      if self.mac:
        mac = self.mac
      else:
        mac = self.instance.nics[0].mac
      args['nics'] = [(ip, bridge, mac)]
    env = _BuildInstanceHookEnvByObject(self.instance, override=args)
    nl = [self.sstore.GetMasterNode(),
          self.instance.primary_node] + list(self.instance.secondary_nodes)
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This only checks the instance list against the existing names.

    """
    self.mem = getattr(self.op, "mem", None)
    self.vcpus = getattr(self.op, "vcpus", None)
    self.ip = getattr(self.op, "ip", None)
    self.mac = getattr(self.op, "mac", None)
    self.bridge = getattr(self.op, "bridge", None)
    self.kernel_path = getattr(self.op, "kernel_path", None)
    self.initrd_path = getattr(self.op, "initrd_path", None)
    self.hvm_boot_order = getattr(self.op, "hvm_boot_order", None)
    self.hvm_acpi = getattr(self.op, "hvm_acpi", None)
    self.hvm_pae = getattr(self.op, "hvm_pae", None)
    self.hvm_nic_type = getattr(self.op, "hvm_nic_type", None)
    self.hvm_disk_type = getattr(self.op, "hvm_disk_type", None)
    self.hvm_cdrom_image_path = getattr(self.op, "hvm_cdrom_image_path", None)
    self.vnc_bind_address = getattr(self.op, "vnc_bind_address", None)
    self.force = getattr(self.op, "force", None)
    self.auto_balance = getattr(self.op, "auto_balance", None)
    all_parms = [
      self.mem, self.vcpus, self.ip, self.bridge, self.mac,
      self.kernel_path, self.initrd_path, self.hvm_boot_order,
      self.hvm_acpi, self.hvm_pae, self.hvm_cdrom_image_path,
      self.vnc_bind_address, self.hvm_nic_type, self.hvm_disk_type,
      self.auto_balance,
      ]
    if all_parms.count(None) == len(all_parms):
      raise errors.OpPrereqError("No changes submitted")
    if self.mem is not None:
      try:
        self.mem = int(self.mem)
      except ValueError, err:
        raise errors.OpPrereqError("Invalid memory size: %s" % str(err))
    if self.vcpus is not None:
      try:
        self.vcpus = int(self.vcpus)
      except ValueError, err:
        raise errors.OpPrereqError("Invalid vcpus number: %s" % str(err))
    if self.ip is not None:
      self.do_ip = True
      if self.ip.lower() == "none":
        self.ip = None
      else:
        if not utils.IsValidIP(self.ip):
          raise errors.OpPrereqError("Invalid IP address '%s'." % self.ip)
    else:
      self.do_ip = False
    self.do_bridge = (self.bridge is not None)
    if self.mac is not None:
      if self.cfg.IsMacInUse(self.mac):
        raise errors.OpPrereqError('MAC address %s already in use in cluster' %
                                   self.mac)
      if not utils.IsValidMac(self.mac):
        raise errors.OpPrereqError('Invalid MAC address %s' % self.mac)

    if self.kernel_path is not None:
      self.do_kernel_path = True
      if self.kernel_path == constants.VALUE_NONE:
        raise errors.OpPrereqError("Can't set instance to no kernel")

      if self.kernel_path != constants.VALUE_DEFAULT:
        if not os.path.isabs(self.kernel_path):
          raise errors.OpPrereqError("The kernel path must be an absolute"
                                    " filename")
    else:
      self.do_kernel_path = False

    if self.initrd_path is not None:
      self.do_initrd_path = True
      if self.initrd_path not in (constants.VALUE_NONE,
                                  constants.VALUE_DEFAULT):
        if not os.path.isabs(self.initrd_path):
          raise errors.OpPrereqError("The initrd path must be an absolute"
                                    " filename")
    else:
      self.do_initrd_path = False

    # boot order verification
    if self.hvm_boot_order is not None:
      if self.hvm_boot_order != constants.VALUE_DEFAULT:
        if len(self.hvm_boot_order.strip("acdn")) != 0:
          raise errors.OpPrereqError("invalid boot order specified,"
                                     " must be one or more of [acdn]"
                                     " or 'default'")

    # hvm_cdrom_image_path verification
    if self.op.hvm_cdrom_image_path is not None:
      if not (os.path.isabs(self.op.hvm_cdrom_image_path) or
              self.op.hvm_cdrom_image_path.lower() == "none"):
        raise errors.OpPrereqError("The path to the HVM CDROM image must"
                                   " be an absolute path or None, not %s" %
                                   self.op.hvm_cdrom_image_path)
      if not (os.path.isfile(self.op.hvm_cdrom_image_path) or
              self.op.hvm_cdrom_image_path.lower() == "none"):
        raise errors.OpPrereqError("The HVM CDROM image must either be a"
                                   " regular file or a symlink pointing to"
                                   " an existing regular file, not %s" %
                                   self.op.hvm_cdrom_image_path)

    # vnc_bind_address verification
    if self.op.vnc_bind_address is not None:
      if not utils.IsValidIP(self.op.vnc_bind_address):
        raise errors.OpPrereqError("given VNC bind address '%s' doesn't look"
                                   " like a valid IP address" %
                                   self.op.vnc_bind_address)

    # Xen HVM device type checks
    if self.sstore.GetHypervisorType() == constants.HT_XEN_HVM31:
      if self.op.hvm_nic_type is not None:
        if self.op.hvm_nic_type not in constants.HT_HVM_VALID_NIC_TYPES:
          raise errors.OpPrereqError("Invalid NIC type %s specified for Xen"
                                     " HVM  hypervisor" % self.op.hvm_nic_type)
      if self.op.hvm_disk_type is not None:
        if self.op.hvm_disk_type not in constants.HT_HVM_VALID_DISK_TYPES:
          raise errors.OpPrereqError("Invalid disk type %s specified for Xen"
                                     " HVM hypervisor" % self.op.hvm_disk_type)

    # auto balance setting
    if self.auto_balance is not None:
      # convert the value to a proper bool value, if it's not
      self.auto_balance = bool(self.auto_balance)

    instance = self.cfg.GetInstanceInfo(
      self.cfg.ExpandInstanceName(self.op.instance_name))
    if instance is None:
      raise errors.OpPrereqError("No such instance name '%s'" %
                                 self.op.instance_name)
    self.op.instance_name = instance.name
    self.instance = instance
    self.warn = []
    if self.mem is not None and not self.force:
      pnode = self.instance.primary_node
      nodelist = [pnode]
      if instance.auto_balance:
        nodelist.extend(instance.secondary_nodes)
      instance_info = rpc.call_instance_info(pnode, instance.name)
      nodeinfo = rpc.call_node_info(nodelist, self.cfg.GetVGName())

      if pnode not in nodeinfo or not isinstance(nodeinfo[pnode], dict):
        # Assume the primary node is unreachable and go ahead
        self.warn.append("Can't get info from primary node %s" % pnode)
      else:
        if instance_info:
          current_mem = instance_info['memory']
        else:
          # Assume instance not running
          # (there is a slight race condition here, but it's not very probable,
          # and we have no other way to check)
          current_mem = 0
        miss_mem = self.mem - current_mem - nodeinfo[pnode]['memory_free']
        if miss_mem > 0:
          raise errors.OpPrereqError("This change will prevent the instance"
                                     " from starting, due to %d MB of memory"
                                     " missing on its primary node" % miss_mem)

      if instance.auto_balance:
        for node in instance.secondary_nodes:
          if node not in nodeinfo or not isinstance(nodeinfo[node], dict):
            self.warn.append("Can't get info from secondary node %s" % node)
          elif self.mem > nodeinfo[node]['memory_free']:
            self.warn.append("Not enough memory to failover instance to"
                             " secondary node %s" % node)
    return

  def Exec(self, feedback_fn):
    """Modifies an instance.

    All parameters take effect only at the next restart of the instance.
    """
    # Process here the warnings from CheckPrereq, as we don't have a
    # feedback_fn there.
    for warn in self.warn:
      feedback_fn("WARNING: %s" % warn)

    result = []
    instance = self.instance
    if self.mem:
      instance.memory = self.mem
      result.append(("mem", self.mem))
    if self.vcpus:
      instance.vcpus = self.vcpus
      result.append(("vcpus",  self.vcpus))
    if self.do_ip:
      instance.nics[0].ip = self.ip
      result.append(("ip", self.ip))
    if self.bridge:
      instance.nics[0].bridge = self.bridge
      result.append(("bridge", self.bridge))
    if self.mac:
      instance.nics[0].mac = self.mac
      result.append(("mac", self.mac))
    if self.do_kernel_path:
      instance.kernel_path = self.kernel_path
      result.append(("kernel_path", self.kernel_path))
    if self.do_initrd_path:
      instance.initrd_path = self.initrd_path
      result.append(("initrd_path", self.initrd_path))
    if self.hvm_boot_order:
      if self.hvm_boot_order == constants.VALUE_DEFAULT:
        instance.hvm_boot_order = None
      else:
        instance.hvm_boot_order = self.hvm_boot_order
      result.append(("hvm_boot_order", self.hvm_boot_order))
    if self.hvm_acpi is not None:
      instance.hvm_acpi = self.hvm_acpi
      result.append(("hvm_acpi", self.hvm_acpi))
    if self.hvm_pae is not None:
      instance.hvm_pae = self.hvm_pae
      result.append(("hvm_pae", self.hvm_pae))
    if self.hvm_nic_type is not None:
      instance.hvm_nic_type = self.hvm_nic_type
      result.append(("hvm_nic_type", self.hvm_nic_type))
    if self.hvm_disk_type is not None:
      instance.hvm_disk_type = self.hvm_disk_type
      result.append(("hvm_disk_type", self.hvm_disk_type))
    if self.hvm_cdrom_image_path:
      if self.hvm_cdrom_image_path == constants.VALUE_NONE:
        instance.hvm_cdrom_image_path = None
      else:
        instance.hvm_cdrom_image_path = self.hvm_cdrom_image_path
      result.append(("hvm_cdrom_image_path", self.hvm_cdrom_image_path))
    if self.vnc_bind_address:
      instance.vnc_bind_address = self.vnc_bind_address
      result.append(("vnc_bind_address", self.vnc_bind_address))
    if self.auto_balance is not None:
      instance.auto_balance = self.auto_balance
      result.append(("auto_balance", self.auto_balance))

    self.cfg.AddInstance(instance)

    return result


class LUQueryExports(NoHooksLU):
  """Query the exports list

  """
  _OP_REQP = []

  def CheckPrereq(self):
    """Check that the nodelist contains only existing nodes.

    """
    self.nodes = _GetWantedNodes(self, getattr(self.op, "nodes", None))

  def Exec(self, feedback_fn):
    """Compute the list of all the exported system images.

    Returns:
      a dictionary with the structure node->(export-list)
      where export-list is a list of the instances exported on
      that node.

    """
    return rpc.call_export_list(self.nodes)


class LUExportInstance(LogicalUnit):
  """Export an instance to an image in the cluster.

  """
  HPATH = "instance-export"
  HTYPE = constants.HTYPE_INSTANCE
  _OP_REQP = ["instance_name", "target_node", "shutdown"]

  def BuildHooksEnv(self):
    """Build hooks env.

    This will run on the master, primary node and target node.

    """
    env = {
      "EXPORT_NODE": self.op.target_node,
      "EXPORT_DO_SHUTDOWN": self.op.shutdown,
      }
    env.update(_BuildInstanceHookEnvByObject(self.instance))
    nl = [self.sstore.GetMasterNode(), self.instance.primary_node,
          self.op.target_node]
    return env, nl, nl

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that the instance and node names are valid.

    """
    instance_name = self.cfg.ExpandInstanceName(self.op.instance_name)
    self.instance = self.cfg.GetInstanceInfo(instance_name)
    if self.instance is None:
      raise errors.OpPrereqError("Instance '%s' not found" %
                                 self.op.instance_name)

    # node verification
    dst_node_short = self.cfg.ExpandNodeName(self.op.target_node)
    self.dst_node = self.cfg.GetNodeInfo(dst_node_short)

    if self.dst_node is None:
      raise errors.OpPrereqError("Destination node '%s' is unknown." %
                                 self.op.target_node)
    self.op.target_node = self.dst_node.name

  def Exec(self, feedback_fn):
    """Export an instance to an image in the cluster.

    """
    instance = self.instance
    dst_node = self.dst_node
    src_node = instance.primary_node
    if self.op.shutdown:
      # shutdown the instance, but not the disks
      if not rpc.call_instance_shutdown(src_node, instance):
        raise errors.OpExecError("Could not shutdown instance %s on node %s" %
                                 (instance.name, src_node))

    vgname = self.cfg.GetVGName()

    snap_disks = []

    try:
      for disk in instance.disks:
        if disk.iv_name == "sda":
          # new_dev_name will be a snapshot of an lvm leaf of the one we passed
          new_dev_name = rpc.call_blockdev_snapshot(src_node, disk)

          if not new_dev_name:
            logger.Error("could not snapshot block device %s on node %s" %
                         (disk.logical_id[1], src_node))
          else:
            new_dev = objects.Disk(dev_type=constants.LD_LV, size=disk.size,
                                      logical_id=(vgname, new_dev_name),
                                      physical_id=(vgname, new_dev_name),
                                      iv_name=disk.iv_name)
            snap_disks.append(new_dev)

    finally:
      if self.op.shutdown and instance.status == "up":
        if not rpc.call_instance_start(src_node, instance, None):
          _ShutdownInstanceDisks(instance, self.cfg)
          raise errors.OpExecError("Could not start instance")

    # TODO: check for size

    for dev in snap_disks:
      if not rpc.call_snapshot_export(src_node, dev, dst_node.name,
                                           instance):
        logger.Error("could not export block device %s from node"
                     " %s to node %s" %
                     (dev.logical_id[1], src_node, dst_node.name))
      if not rpc.call_blockdev_remove(src_node, dev):
        logger.Error("could not remove snapshot block device %s from"
                     " node %s" % (dev.logical_id[1], src_node))

    if not rpc.call_finalize_export(dst_node.name, instance, snap_disks):
      logger.Error("could not finalize export for instance %s on node %s" %
                   (instance.name, dst_node.name))

    nodelist = self.cfg.GetNodeList()
    nodelist.remove(dst_node.name)

    # on one-node clusters nodelist will be empty after the removal
    # if we proceed the backup would be removed because OpQueryExports
    # substitutes an empty list with the full cluster node list.
    if nodelist:
      op = opcodes.OpQueryExports(nodes=nodelist)
      exportlist = self.proc.ChainOpCode(op)
      for node in exportlist:
        if instance.name in exportlist[node]:
          if not rpc.call_export_remove(node, instance.name):
            logger.Error("could not remove older export for instance %s"
                         " on node %s" % (instance.name, node))


class LURemoveExport(NoHooksLU):
  """Remove exports related to the named instance.

  """
  _OP_REQP = ["instance_name"]

  def CheckPrereq(self):
    """Check prerequisites.
    """
    pass

  def Exec(self, feedback_fn):
    """Remove any export.

    """
    instance_name = self.cfg.ExpandInstanceName(self.op.instance_name)
    # If the instance was not found we'll try with the name that was passed in.
    # This will only work if it was an FQDN, though.
    fqdn_warn = False
    if not instance_name:
      fqdn_warn = True
      instance_name = self.op.instance_name

    op = opcodes.OpQueryExports(nodes=[])
    exportlist = self.proc.ChainOpCode(op)
    found = False
    for node in exportlist:
      if instance_name in exportlist[node]:
        found = True
        if not rpc.call_export_remove(node, instance_name):
          logger.Error("could not remove export for instance %s"
                       " on node %s" % (instance_name, node))

    if fqdn_warn and not found:
      feedback_fn("Export not found. If trying to remove an export belonging"
                  " to a deleted instance please use its Fully Qualified"
                  " Domain Name.")


class TagsLU(NoHooksLU):
  """Generic tags LU.

  This is an abstract class which is the parent of all the other tags LUs.

  """
  def CheckPrereq(self):
    """Check prerequisites.

    """
    if self.op.kind == constants.TAG_CLUSTER:
      self.target = self.cfg.GetClusterInfo()
    elif self.op.kind == constants.TAG_NODE:
      name = self.cfg.ExpandNodeName(self.op.name)
      if name is None:
        raise errors.OpPrereqError("Invalid node name (%s)" %
                                   (self.op.name,))
      self.op.name = name
      self.target = self.cfg.GetNodeInfo(name)
    elif self.op.kind == constants.TAG_INSTANCE:
      name = self.cfg.ExpandInstanceName(self.op.name)
      if name is None:
        raise errors.OpPrereqError("Invalid instance name (%s)" %
                                   (self.op.name,))
      self.op.name = name
      self.target = self.cfg.GetInstanceInfo(name)
    else:
      raise errors.OpPrereqError("Wrong tag type requested (%s)" %
                                 str(self.op.kind))


class LUGetTags(TagsLU):
  """Returns the tags of a given object.

  """
  _OP_REQP = ["kind", "name"]

  def Exec(self, feedback_fn):
    """Returns the tag list.

    """
    return self.target.GetTags()


class LUSearchTags(NoHooksLU):
  """Searches the tags for a given pattern.

  """
  _OP_REQP = ["pattern"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks the pattern passed for validity by compiling it.

    """
    try:
      self.re = re.compile(self.op.pattern)
    except re.error, err:
      raise errors.OpPrereqError("Invalid search pattern '%s': %s" %
                                 (self.op.pattern, err))

  def Exec(self, feedback_fn):
    """Returns the tag list.

    """
    cfg = self.cfg
    tgts = [("/cluster", cfg.GetClusterInfo())]
    ilist = [cfg.GetInstanceInfo(name) for name in cfg.GetInstanceList()]
    tgts.extend([("/instances/%s" % i.name, i) for i in ilist])
    nlist = [cfg.GetNodeInfo(name) for name in cfg.GetNodeList()]
    tgts.extend([("/nodes/%s" % n.name, n) for n in nlist])
    results = []
    for path, target in tgts:
      for tag in target.GetTags():
        if self.re.search(tag):
          results.append((path, tag))
    return results


class LUAddTags(TagsLU):
  """Sets a tag on a given object.

  """
  _OP_REQP = ["kind", "name", "tags"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks the type and length of the tag name and value.

    """
    TagsLU.CheckPrereq(self)
    for tag in self.op.tags:
      objects.TaggableObject.ValidateTag(tag)

  def Exec(self, feedback_fn):
    """Sets the tag.

    """
    try:
      for tag in self.op.tags:
        self.target.AddTag(tag)
    except errors.TagError, err:
      raise errors.OpExecError("Error while setting tag: %s" % str(err))
    try:
      self.cfg.Update(self.target)
    except errors.ConfigurationError:
      raise errors.OpRetryError("There has been a modification to the"
                                " config file and the operation has been"
                                " aborted. Please retry.")


class LUDelTags(TagsLU):
  """Delete a list of tags from a given object.

  """
  _OP_REQP = ["kind", "name", "tags"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that we have the given tag.

    """
    TagsLU.CheckPrereq(self)
    for tag in self.op.tags:
      objects.TaggableObject.ValidateTag(tag, removal=True)
    del_tags = frozenset(self.op.tags)
    cur_tags = self.target.GetTags()
    if not del_tags <= cur_tags:
      diff_tags = del_tags - cur_tags
      diff_names = ["'%s'" % tag for tag in diff_tags]
      diff_names.sort()
      raise errors.OpPrereqError("Tag(s) %s not found" %
                                 (",".join(diff_names)))

  def Exec(self, feedback_fn):
    """Remove the tag from the object.

    """
    for tag in self.op.tags:
      self.target.RemoveTag(tag)
    try:
      self.cfg.Update(self.target)
    except errors.ConfigurationError:
      raise errors.OpRetryError("There has been a modification to the"
                                " config file and the operation has been"
                                " aborted. Please retry.")

class LUTestDelay(NoHooksLU):
  """Sleep for a specified amount of time.

  This LU sleeps on the master and/or nodes for a specified amoutn of
  time.

  """
  _OP_REQP = ["duration", "on_master", "on_nodes"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks that we have a good list of nodes and/or the duration
    is valid.

    """

    if self.op.on_nodes:
      self.op.on_nodes = _GetWantedNodes(self, self.op.on_nodes)

  def Exec(self, feedback_fn):
    """Do the actual sleep.

    """
    if self.op.on_master:
      if not utils.TestDelay(self.op.duration):
        raise errors.OpExecError("Error during master delay test")
    if self.op.on_nodes:
      result = rpc.call_test_delay(self.op.on_nodes, self.op.duration)
      if not result:
        raise errors.OpExecError("Complete failure from rpc call")
      for node, node_result in result.items():
        if not node_result:
          raise errors.OpExecError("Failure during rpc call to node %s,"
                                   " result: %s" % (node, node_result))


class IAllocator(object):
  """IAllocator framework.

  An IAllocator instance has three sets of attributes:
    - cfg/sstore that are needed to query the cluster
    - input data (all members of the _KEYS class attribute are required)
    - four buffer attributes (in|out_data|text), that represent the
      input (to the external script) in text and data structure format,
      and the output from it, again in two formats
    - the result variables from the script (success, info, nodes) for
      easy usage

  """
  _ALLO_KEYS = [
    "mem_size", "disks", "disk_template",
    "os", "tags", "nics", "vcpus",
    ]
  _RELO_KEYS = [
    "relocate_from",
    ]

  def __init__(self, cfg, sstore, mode, name, **kwargs):
    self.cfg = cfg
    self.sstore = sstore
    # init buffer variables
    self.in_text = self.out_text = self.in_data = self.out_data = None
    # init all input fields so that pylint is happy
    self.mode = mode
    self.name = name
    self.mem_size = self.disks = self.disk_template = None
    self.os = self.tags = self.nics = self.vcpus = None
    self.relocate_from = None
    # computed fields
    self.required_nodes = None
    # init result fields
    self.success = self.info = self.nodes = None
    if self.mode == constants.IALLOCATOR_MODE_ALLOC:
      keyset = self._ALLO_KEYS
    elif self.mode == constants.IALLOCATOR_MODE_RELOC:
      keyset = self._RELO_KEYS
    else:
      raise errors.ProgrammerError("Unknown mode '%s' passed to the"
                                   " IAllocator" % self.mode)
    for key in kwargs:
      if key not in keyset:
        raise errors.ProgrammerError("Invalid input parameter '%s' to"
                                     " IAllocator" % key)
      setattr(self, key, kwargs[key])
    for key in keyset:
      if key not in kwargs:
        raise errors.ProgrammerError("Missing input parameter '%s' to"
                                     " IAllocator" % key)
    self._BuildInputData()

  def _ComputeClusterData(self):
    """Compute the generic allocator input data.

    This is the data that is independent of the actual operation.

    """
    cfg = self.cfg
    # cluster data
    data = {
      "version": 1,
      "cluster_name": self.sstore.GetClusterName(),
      "cluster_tags": list(cfg.GetClusterInfo().GetTags()),
      "hypervisor_type": self.sstore.GetHypervisorType(),
      # we don't have job IDs
      }

    i_list = [cfg.GetInstanceInfo(iname) for iname in cfg.GetInstanceList()]

    # node data
    node_results = {}
    node_list = cfg.GetNodeList()
    node_data = rpc.call_node_info(node_list, cfg.GetVGName())
    for nname in node_list:
      ninfo = cfg.GetNodeInfo(nname)
      if nname not in node_data or not isinstance(node_data[nname], dict):
        raise errors.OpExecError("Can't get data for node %s" % nname)
      remote_info = node_data[nname]
      for attr in ['memory_total', 'memory_free', 'memory_dom0',
                   'vg_size', 'vg_free', 'cpu_total']:
        if attr not in remote_info:
          raise errors.OpExecError("Node '%s' didn't return attribute '%s'" %
                                   (nname, attr))
        try:
          remote_info[attr] = int(remote_info[attr])
        except ValueError, err:
          raise errors.OpExecError("Node '%s' returned invalid value for '%s':"
                                   " %s" % (nname, attr, str(err)))
      # compute memory used by primary instances
      i_p_mem = i_p_up_mem = 0
      for iinfo in i_list:
        if iinfo.primary_node == nname:
          i_p_mem += iinfo.memory
          if iinfo.status == "up":
            i_p_up_mem += iinfo.memory

      # compute memory used by instances
      pnr = {
        "tags": list(ninfo.GetTags()),
        "total_memory": remote_info['memory_total'],
        "reserved_memory": remote_info['memory_dom0'],
        "free_memory": remote_info['memory_free'],
        "i_pri_memory": i_p_mem,
        "i_pri_up_memory": i_p_up_mem,
        "total_disk": remote_info['vg_size'],
        "free_disk": remote_info['vg_free'],
        "primary_ip": ninfo.primary_ip,
        "secondary_ip": ninfo.secondary_ip,
        "total_cpus": remote_info['cpu_total'],
        }
      node_results[nname] = pnr
    data["nodes"] = node_results

    # instance data
    instance_data = {}
    for iinfo in i_list:
      nic_data = [{"mac": n.mac, "ip": n.ip, "bridge": n.bridge}
                  for n in iinfo.nics]
      pir = {
        "tags": list(iinfo.GetTags()),
        "should_run": iinfo.status == "up",
        "vcpus": iinfo.vcpus,
        "memory": iinfo.memory,
        "os": iinfo.os,
        "nodes": [iinfo.primary_node] + list(iinfo.secondary_nodes),
        "nics": nic_data,
        "disks": [{"size": dsk.size, "mode": "w"} for dsk in iinfo.disks],
        "disk_template": iinfo.disk_template,
        }
      instance_data[iinfo.name] = pir

    data["instances"] = instance_data

    self.in_data = data

  def _AddNewInstance(self):
    """Add new instance data to allocator structure.

    This in combination with _AllocatorGetClusterData will create the
    correct structure needed as input for the allocator.

    The checks for the completeness of the opcode must have already been
    done.

    """
    data = self.in_data
    if len(self.disks) != 2:
      raise errors.OpExecError("Only two-disk configurations supported")

    disk_space = _ComputeDiskSize(self.disk_template,
                                  self.disks[0]["size"], self.disks[1]["size"])

    if self.disk_template in constants.DTS_NET_MIRROR:
      self.required_nodes = 2
    else:
      self.required_nodes = 1
    request = {
      "type": "allocate",
      "name": self.name,
      "disk_template": self.disk_template,
      "tags": self.tags,
      "os": self.os,
      "vcpus": self.vcpus,
      "memory": self.mem_size,
      "disks": self.disks,
      "disk_space_total": disk_space,
      "nics": self.nics,
      "required_nodes": self.required_nodes,
      }
    data["request"] = request

  def _AddRelocateInstance(self):
    """Add relocate instance data to allocator structure.

    This in combination with _IAllocatorGetClusterData will create the
    correct structure needed as input for the allocator.

    The checks for the completeness of the opcode must have already been
    done.

    """
    instance = self.cfg.GetInstanceInfo(self.name)
    if instance is None:
      raise errors.ProgrammerError("Unknown instance '%s' passed to"
                                   " IAllocator" % self.name)

    if instance.disk_template not in constants.DTS_NET_MIRROR:
      raise errors.OpPrereqError("Can't relocate non-mirrored instances")

    if len(instance.secondary_nodes) != 1:
      raise errors.OpPrereqError("Instance has not exactly one secondary node")

    self.required_nodes = 1

    disk_space = _ComputeDiskSize(instance.disk_template,
                                  instance.disks[0].size,
                                  instance.disks[1].size)

    request = {
      "type": "relocate",
      "name": self.name,
      "disk_space_total": disk_space,
      "required_nodes": self.required_nodes,
      "relocate_from": self.relocate_from,
      }
    self.in_data["request"] = request

  def _BuildInputData(self):
    """Build input data structures.

    """
    self._ComputeClusterData()

    if self.mode == constants.IALLOCATOR_MODE_ALLOC:
      self._AddNewInstance()
    else:
      self._AddRelocateInstance()

    self.in_text = serializer.Dump(self.in_data)

  def Run(self, name, validate=True, call_fn=rpc.call_iallocator_runner):
    """Run an instance allocator and return the results.

    """
    data = self.in_text

    result = call_fn(self.sstore.GetMasterNode(), name, self.in_text)

    if not isinstance(result, tuple) or len(result) != 4:
      raise errors.OpExecError("Invalid result from master iallocator runner")

    rcode, stdout, stderr, fail = result

    if rcode == constants.IARUN_NOTFOUND:
      raise errors.OpExecError("Can't find allocator '%s'" % name)
    elif rcode == constants.IARUN_FAILURE:
        raise errors.OpExecError("Instance allocator call failed: %s,"
                                 " output: %s" %
                                 (fail, stdout+stderr))
    self.out_text = stdout
    if validate:
      self._ValidateResult()

  def _ValidateResult(self):
    """Process the allocator results.

    This will process and if successful save the result in
    self.out_data and the other parameters.

    """
    try:
      rdict = serializer.Load(self.out_text)
    except Exception, err:
      raise errors.OpExecError("Can't parse iallocator results: %s" % str(err))

    if not isinstance(rdict, dict):
      raise errors.OpExecError("Can't parse iallocator results: not a dict")

    for key in "success", "info", "nodes":
      if key not in rdict:
        raise errors.OpExecError("Can't parse iallocator results:"
                                 " missing key '%s'" % key)
      setattr(self, key, rdict[key])

    if not isinstance(rdict["nodes"], list):
      raise errors.OpExecError("Can't parse iallocator results: 'nodes' key"
                               " is not a list")
    self.out_data = rdict


class LUTestAllocator(NoHooksLU):
  """Run allocator tests.

  This LU runs the allocator tests

  """
  _OP_REQP = ["direction", "mode", "name"]

  def CheckPrereq(self):
    """Check prerequisites.

    This checks the opcode parameters depending on the director and mode test.

    """
    if self.op.mode == constants.IALLOCATOR_MODE_ALLOC:
      for attr in ["name", "mem_size", "disks", "disk_template",
                   "os", "tags", "nics", "vcpus"]:
        if not hasattr(self.op, attr):
          raise errors.OpPrereqError("Missing attribute '%s' on opcode input" %
                                     attr)
      iname = self.cfg.ExpandInstanceName(self.op.name)
      if iname is not None:
        raise errors.OpPrereqError("Instance '%s' already in the cluster" %
                                   iname)
      if not isinstance(self.op.nics, list):
        raise errors.OpPrereqError("Invalid parameter 'nics'")
      for row in self.op.nics:
        if (not isinstance(row, dict) or
            "mac" not in row or
            "ip" not in row or
            "bridge" not in row):
          raise errors.OpPrereqError("Invalid contents of the"
                                     " 'nics' parameter")
      if not isinstance(self.op.disks, list):
        raise errors.OpPrereqError("Invalid parameter 'disks'")
      if len(self.op.disks) != 2:
        raise errors.OpPrereqError("Only two-disk configurations supported")
      for row in self.op.disks:
        if (not isinstance(row, dict) or
            "size" not in row or
            not isinstance(row["size"], int) or
            "mode" not in row or
            row["mode"] not in ['r', 'w']):
          raise errors.OpPrereqError("Invalid contents of the"
                                     " 'disks' parameter")
    elif self.op.mode == constants.IALLOCATOR_MODE_RELOC:
      if not hasattr(self.op, "name"):
        raise errors.OpPrereqError("Missing attribute 'name' on opcode input")
      fname = self.cfg.ExpandInstanceName(self.op.name)
      if fname is None:
        raise errors.OpPrereqError("Instance '%s' not found for relocation" %
                                   self.op.name)
      self.op.name = fname
      self.relocate_from = self.cfg.GetInstanceInfo(fname).secondary_nodes
    else:
      raise errors.OpPrereqError("Invalid test allocator mode '%s'" %
                                 self.op.mode)

    if self.op.direction == constants.IALLOCATOR_DIR_OUT:
      if not hasattr(self.op, "allocator") or self.op.allocator is None:
        raise errors.OpPrereqError("Missing allocator name")
    elif self.op.direction != constants.IALLOCATOR_DIR_IN:
      raise errors.OpPrereqError("Wrong allocator test '%s'" %
                                 self.op.direction)

  def Exec(self, feedback_fn):
    """Run the allocator test.

    """
    if self.op.mode == constants.IALLOCATOR_MODE_ALLOC:
      ial = IAllocator(self.cfg, self.sstore,
                       mode=self.op.mode,
                       name=self.op.name,
                       mem_size=self.op.mem_size,
                       disks=self.op.disks,
                       disk_template=self.op.disk_template,
                       os=self.op.os,
                       tags=self.op.tags,
                       nics=self.op.nics,
                       vcpus=self.op.vcpus,
                       )
    else:
      ial = IAllocator(self.cfg, self.sstore,
                       mode=self.op.mode,
                       name=self.op.name,
                       relocate_from=list(self.relocate_from),
                       )

    if self.op.direction == constants.IALLOCATOR_DIR_IN:
      result = ial.in_text
    else:
      ial.Run(self.op.allocator, validate=False)
      result = ial.out_text
    return result