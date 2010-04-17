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


"""Inter-node RPC library.

"""

# pylint: disable-msg=C0103,R0201,R0904
# C0103: Invalid name, since call_ are not valid
# R0201: Method could be a function, we keep all rpcs instance methods
# as not to change them back and forth between static/instance methods
# if they need to start using instance attributes
# R0904: Too many public methods

import os
import logging
import zlib
import base64

from ganeti import utils
from ganeti import objects
from ganeti import http
from ganeti import serializer
from ganeti import constants
from ganeti import errors

# pylint has a bug here, doesn't see this import
import ganeti.http.client  # pylint: disable-msg=W0611


# Module level variable
_http_manager = None


def Init():
  """Initializes the module-global HTTP client manager.

  Must be called before using any RPC function.

  """
  global _http_manager # pylint: disable-msg=W0603

  assert not _http_manager, "RPC module initialized more than once"

  http.InitSsl()

  _http_manager = http.client.HttpClientManager()


def Shutdown():
  """Stops the module-global HTTP client manager.

  Must be called before quitting the program.

  """
  global _http_manager # pylint: disable-msg=W0603

  if _http_manager:
    _http_manager.Shutdown()
    _http_manager = None


class RpcResult(object):
  """RPC Result class.

  This class holds an RPC result. It is needed since in multi-node
  calls we can't raise an exception just because one one out of many
  failed, and therefore we use this class to encapsulate the result.

  @ivar data: the data payload, for successful results, or None
  @ivar call: the name of the RPC call
  @ivar node: the name of the node to which we made the call
  @ivar offline: whether the operation failed because the node was
      offline, as opposed to actual failure; offline=True will always
      imply failed=True, in order to allow simpler checking if
      the user doesn't care about the exact failure mode
  @ivar fail_msg: the error message if the call failed

  """
  def __init__(self, data=None, failed=False, offline=False,
               call=None, node=None):
    self.offline = offline
    self.call = call
    self.node = node

    if offline:
      self.fail_msg = "Node is marked offline"
      self.data = self.payload = None
    elif failed:
      self.fail_msg = self._EnsureErr(data)
      self.data = self.payload = None
    else:
      self.data = data
      if not isinstance(self.data, (tuple, list)):
        self.fail_msg = ("RPC layer error: invalid result type (%s)" %
                         type(self.data))
        self.payload = None
      elif len(data) != 2:
        self.fail_msg = ("RPC layer error: invalid result length (%d), "
                         "expected 2" % len(self.data))
        self.payload = None
      elif not self.data[0]:
        self.fail_msg = self._EnsureErr(self.data[1])
        self.payload = None
      else:
        # finally success
        self.fail_msg = None
        self.payload = data[1]

    assert hasattr(self, "call")
    assert hasattr(self, "data")
    assert hasattr(self, "fail_msg")
    assert hasattr(self, "node")
    assert hasattr(self, "offline")
    assert hasattr(self, "payload")

  @staticmethod
  def _EnsureErr(val):
    """Helper to ensure we return a 'True' value for error."""
    if val:
      return val
    else:
      return "No error information"

  def Raise(self, msg, prereq=False, ecode=None):
    """If the result has failed, raise an OpExecError.

    This is used so that LU code doesn't have to check for each
    result, but instead can call this function.

    """
    if not self.fail_msg:
      return

    if not msg: # one could pass None for default message
      msg = ("Call '%s' to node '%s' has failed: %s" %
             (self.call, self.node, self.fail_msg))
    else:
      msg = "%s: %s" % (msg, self.fail_msg)
    if prereq:
      ec = errors.OpPrereqError
    else:
      ec = errors.OpExecError
    if ecode is not None:
      args = (msg, prereq)
    else:
      args = (msg, )
    raise ec(*args) # pylint: disable-msg=W0142


class Client:
  """RPC Client class.

  This class, given a (remote) method name, a list of parameters and a
  list of nodes, will contact (in parallel) all nodes, and return a
  dict of results (key: node name, value: result).

  One current bug is that generic failure is still signaled by
  'False' result, which is not good. This overloading of values can
  cause bugs.

  """
  def __init__(self, procedure, body, port):
    self.procedure = procedure
    self.body = body
    self.port = port
    self.nc = {}

    self._ssl_params = \
      http.HttpSslParams(ssl_key_path=constants.SSL_CERT_FILE,
                         ssl_cert_path=constants.SSL_CERT_FILE)

  def ConnectList(self, node_list, address_list=None):
    """Add a list of nodes to the target nodes.

    @type node_list: list
    @param node_list: the list of node names to connect
    @type address_list: list or None
    @keyword address_list: either None or a list with node addresses,
        which must have the same length as the node list

    """
    if address_list is None:
      address_list = [None for _ in node_list]
    else:
      assert len(node_list) == len(address_list), \
             "Name and address lists should have the same length"
    for node, address in zip(node_list, address_list):
      self.ConnectNode(node, address)

  def ConnectNode(self, name, address=None):
    """Add a node to the target list.

    @type name: str
    @param name: the node name
    @type address: str
    @keyword address: the node address, if known

    """
    if address is None:
      address = name

    self.nc[name] = \
      http.client.HttpClientRequest(address, self.port, http.HTTP_PUT,
                                    "/%s" % self.procedure,
                                    post_data=self.body,
                                    ssl_params=self._ssl_params,
                                    ssl_verify_peer=True)

  def GetResults(self):
    """Call nodes and return results.

    @rtype: list
    @return: List of RPC results

    """
    assert _http_manager, "RPC module not initialized"

    _http_manager.ExecRequests(self.nc.values())

    results = {}

    for name, req in self.nc.iteritems():
      if req.success and req.resp_status_code == http.HTTP_OK:
        results[name] = RpcResult(data=serializer.LoadJson(req.resp_body),
                                  node=name, call=self.procedure)
        continue

      # TODO: Better error reporting
      if req.error:
        msg = req.error
      else:
        msg = req.resp_body

      logging.error("RPC error in %s from node %s: %s",
                    self.procedure, name, msg)
      results[name] = RpcResult(data=msg, failed=True, node=name,
                                call=self.procedure)

    return results


class RpcRunner(object):
  """RPC runner class"""

  def __init__(self, cfg):
    """Initialized the rpc runner.

    @type cfg:  C{config.ConfigWriter}
    @param cfg: the configuration object that will be used to get data
                about the cluster

    """
    self._cfg = cfg
    self.port = utils.GetDaemonPort(constants.NODED)

  def _InstDict(self, instance, hvp=None, bep=None):
    """Convert the given instance to a dict.

    This is done via the instance's ToDict() method and additionally
    we fill the hvparams with the cluster defaults.

    @type instance: L{objects.Instance}
    @param instance: an Instance object
    @type hvp: dict or None
    @param hvp: a dictionary with overridden hypervisor parameters
    @type bep: dict or None
    @param bep: a dictionary with overridden backend parameters
    @rtype: dict
    @return: the instance dict, with the hvparams filled with the
        cluster defaults

    """
    idict = instance.ToDict()
    cluster = self._cfg.GetClusterInfo()
    idict["hvparams"] = cluster.FillHV(instance)
    if hvp is not None:
      idict["hvparams"].update(hvp)
    idict["beparams"] = cluster.FillBE(instance)
    if bep is not None:
      idict["beparams"].update(bep)
    for nic in idict["nics"]:
      nic['nicparams'] = objects.FillDict(
        cluster.nicparams[constants.PP_DEFAULT],
        nic['nicparams'])
    return idict

  def _ConnectList(self, client, node_list, call):
    """Helper for computing node addresses.

    @type client: L{ganeti.rpc.Client}
    @param client: a C{Client} instance
    @type node_list: list
    @param node_list: the node list we should connect
    @type call: string
    @param call: the name of the remote procedure call, for filling in
        correctly any eventual offline nodes' results

    """
    all_nodes = self._cfg.GetAllNodesInfo()
    name_list = []
    addr_list = []
    skip_dict = {}
    for node in node_list:
      if node in all_nodes:
        if all_nodes[node].offline:
          skip_dict[node] = RpcResult(node=node, offline=True, call=call)
          continue
        val = all_nodes[node].primary_ip
      else:
        val = None
      addr_list.append(val)
      name_list.append(node)
    if name_list:
      client.ConnectList(name_list, address_list=addr_list)
    return skip_dict

  def _ConnectNode(self, client, node, call):
    """Helper for computing one node's address.

    @type client: L{ganeti.rpc.Client}
    @param client: a C{Client} instance
    @type node: str
    @param node: the node we should connect
    @type call: string
    @param call: the name of the remote procedure call, for filling in
        correctly any eventual offline nodes' results

    """
    node_info = self._cfg.GetNodeInfo(node)
    if node_info is not None:
      if node_info.offline:
        return RpcResult(node=node, offline=True, call=call)
      addr = node_info.primary_ip
    else:
      addr = None
    client.ConnectNode(node, address=addr)

  def _MultiNodeCall(self, node_list, procedure, args):
    """Helper for making a multi-node call

    """
    body = serializer.DumpJson(args, indent=False)
    c = Client(procedure, body, self.port)
    skip_dict = self._ConnectList(c, node_list, procedure)
    skip_dict.update(c.GetResults())
    return skip_dict

  @classmethod
  def _StaticMultiNodeCall(cls, node_list, procedure, args,
                           address_list=None):
    """Helper for making a multi-node static call

    """
    body = serializer.DumpJson(args, indent=False)
    c = Client(procedure, body, utils.GetDaemonPort(constants.NODED))
    c.ConnectList(node_list, address_list=address_list)
    return c.GetResults()

  def _SingleNodeCall(self, node, procedure, args):
    """Helper for making a single-node call

    """
    body = serializer.DumpJson(args, indent=False)
    c = Client(procedure, body, self.port)
    result = self._ConnectNode(c, node, procedure)
    if result is None:
      # we did connect, node is not offline
      result = c.GetResults()[node]
    return result

  @classmethod
  def _StaticSingleNodeCall(cls, node, procedure, args):
    """Helper for making a single-node static call

    """
    body = serializer.DumpJson(args, indent=False)
    c = Client(procedure, body, utils.GetDaemonPort(constants.NODED))
    c.ConnectNode(node)
    return c.GetResults()[node]

  @staticmethod
  def _Compress(data):
    """Compresses a string for transport over RPC.

    Small amounts of data are not compressed.

    @type data: str
    @param data: Data
    @rtype: tuple
    @return: Encoded data to send

    """
    # Small amounts of data are not compressed
    if len(data) < 512:
      return (constants.RPC_ENCODING_NONE, data)

    # Compress with zlib and encode in base64
    return (constants.RPC_ENCODING_ZLIB_BASE64,
            base64.b64encode(zlib.compress(data, 3)))

  #
  # Begin RPC calls
  #

  def call_lv_list(self, node_list, vg_name):
    """Gets the logical volumes present in a given volume group.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "lv_list", [vg_name])

  def call_vg_list(self, node_list):
    """Gets the volume group list.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "vg_list", [])

  def call_storage_list(self, node_list, su_name, su_args, name, fields):
    """Get list of storage units.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "storage_list",
                               [su_name, su_args, name, fields])

  def call_storage_modify(self, node, su_name, su_args, name, changes):
    """Modify a storage unit.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "storage_modify",
                                [su_name, su_args, name, changes])

  def call_storage_execute(self, node, su_name, su_args, name, op):
    """Executes an operation on a storage unit.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "storage_execute",
                                [su_name, su_args, name, op])

  def call_bridges_exist(self, node, bridges_list):
    """Checks if a node has all the bridges given.

    This method checks if all bridges given in the bridges_list are
    present on the remote node, so that an instance that uses interfaces
    on those bridges can be started.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "bridges_exist", [bridges_list])

  def call_instance_start(self, node, instance, hvp, bep):
    """Starts an instance.

    This is a single-node call.

    """
    idict = self._InstDict(instance, hvp=hvp, bep=bep)
    return self._SingleNodeCall(node, "instance_start", [idict])

  def call_instance_shutdown(self, node, instance, timeout):
    """Stops an instance.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "instance_shutdown",
                                [self._InstDict(instance), timeout])

  def call_migration_info(self, node, instance):
    """Gather the information necessary to prepare an instance migration.

    This is a single-node call.

    @type node: string
    @param node: the node on which the instance is currently running
    @type instance: C{objects.Instance}
    @param instance: the instance definition

    """
    return self._SingleNodeCall(node, "migration_info",
                                [self._InstDict(instance)])

  def call_accept_instance(self, node, instance, info, target):
    """Prepare a node to accept an instance.

    This is a single-node call.

    @type node: string
    @param node: the target node for the migration
    @type instance: C{objects.Instance}
    @param instance: the instance definition
    @type info: opaque/hypervisor specific (string/data)
    @param info: result for the call_migration_info call
    @type target: string
    @param target: target hostname (usually ip address) (on the node itself)

    """
    return self._SingleNodeCall(node, "accept_instance",
                                [self._InstDict(instance), info, target])

  def call_finalize_migration(self, node, instance, info, success):
    """Finalize any target-node migration specific operation.

    This is called both in case of a successful migration and in case of error
    (in which case it should abort the migration).

    This is a single-node call.

    @type node: string
    @param node: the target node for the migration
    @type instance: C{objects.Instance}
    @param instance: the instance definition
    @type info: opaque/hypervisor specific (string/data)
    @param info: result for the call_migration_info call
    @type success: boolean
    @param success: whether the migration was a success or a failure

    """
    return self._SingleNodeCall(node, "finalize_migration",
                                [self._InstDict(instance), info, success])

  def call_instance_migrate(self, node, instance, target, live):
    """Migrate an instance.

    This is a single-node call.

    @type node: string
    @param node: the node on which the instance is currently running
    @type instance: C{objects.Instance}
    @param instance: the instance definition
    @type target: string
    @param target: the target node name
    @type live: boolean
    @param live: whether the migration should be done live or not (the
        interpretation of this parameter is left to the hypervisor)

    """
    return self._SingleNodeCall(node, "instance_migrate",
                                [self._InstDict(instance), target, live])

  def call_instance_reboot(self, node, inst, reboot_type, shutdown_timeout):
    """Reboots an instance.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "instance_reboot",
                                [self._InstDict(inst), reboot_type,
                                 shutdown_timeout])

  def call_instance_os_add(self, node, inst, reinstall, debug):
    """Installs an OS on the given instance.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "instance_os_add",
                                [self._InstDict(inst), reinstall, debug])

  def call_instance_run_rename(self, node, inst, old_name, debug):
    """Run the OS rename script for an instance.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "instance_run_rename",
                                [self._InstDict(inst), old_name, debug])

  def call_instance_info(self, node, instance, hname):
    """Returns information about a single instance.

    This is a single-node call.

    @type node: list
    @param node: the list of nodes to query
    @type instance: string
    @param instance: the instance name
    @type hname: string
    @param hname: the hypervisor type of the instance

    """
    return self._SingleNodeCall(node, "instance_info", [instance, hname])

  def call_instance_migratable(self, node, instance):
    """Checks whether the given instance can be migrated.

    This is a single-node call.

    @param node: the node to query
    @type instance: L{objects.Instance}
    @param instance: the instance to check


    """
    return self._SingleNodeCall(node, "instance_migratable",
                                [self._InstDict(instance)])

  def call_all_instances_info(self, node_list, hypervisor_list):
    """Returns information about all instances on the given nodes.

    This is a multi-node call.

    @type node_list: list
    @param node_list: the list of nodes to query
    @type hypervisor_list: list
    @param hypervisor_list: the hypervisors to query for instances

    """
    return self._MultiNodeCall(node_list, "all_instances_info",
                               [hypervisor_list])

  def call_instance_list(self, node_list, hypervisor_list):
    """Returns the list of running instances on a given node.

    This is a multi-node call.

    @type node_list: list
    @param node_list: the list of nodes to query
    @type hypervisor_list: list
    @param hypervisor_list: the hypervisors to query for instances

    """
    return self._MultiNodeCall(node_list, "instance_list", [hypervisor_list])

  def call_node_tcp_ping(self, node, source, target, port, timeout,
                         live_port_needed):
    """Do a TcpPing on the remote node

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "node_tcp_ping",
                                [source, target, port, timeout,
                                 live_port_needed])

  def call_node_has_ip_address(self, node, address):
    """Checks if a node has the given IP address.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "node_has_ip_address", [address])

  def call_node_info(self, node_list, vg_name, hypervisor_type):
    """Return node information.

    This will return memory information and volume group size and free
    space.

    This is a multi-node call.

    @type node_list: list
    @param node_list: the list of nodes to query
    @type vg_name: C{string}
    @param vg_name: the name of the volume group to ask for disk space
        information
    @type hypervisor_type: C{str}
    @param hypervisor_type: the name of the hypervisor to ask for
        memory information

    """
    return self._MultiNodeCall(node_list, "node_info",
                               [vg_name, hypervisor_type])

  def call_node_add(self, node, dsa, dsapub, rsa, rsapub, ssh, sshpub):
    """Add a node to the cluster.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "node_add",
                                [dsa, dsapub, rsa, rsapub, ssh, sshpub])

  def call_node_verify(self, node_list, checkdict, cluster_name):
    """Request verification of given parameters.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "node_verify",
                               [checkdict, cluster_name])

  @classmethod
  def call_node_start_master(cls, node, start_daemons, no_voting):
    """Tells a node to activate itself as a master.

    This is a single-node call.

    """
    return cls._StaticSingleNodeCall(node, "node_start_master",
                                     [start_daemons, no_voting])

  @classmethod
  def call_node_stop_master(cls, node, stop_daemons):
    """Tells a node to demote itself from master status.

    This is a single-node call.

    """
    return cls._StaticSingleNodeCall(node, "node_stop_master", [stop_daemons])

  @classmethod
  def call_master_info(cls, node_list):
    """Query master info.

    This is a multi-node call.

    """
    # TODO: should this method query down nodes?
    return cls._StaticMultiNodeCall(node_list, "master_info", [])

  @classmethod
  def call_version(cls, node_list):
    """Query node version.

    This is a multi-node call.

    """
    return cls._StaticMultiNodeCall(node_list, "version", [])

  def call_blockdev_create(self, node, bdev, size, owner, on_primary, info):
    """Request creation of a given block device.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_create",
                                [bdev.ToDict(), size, owner, on_primary, info])

  def call_blockdev_remove(self, node, bdev):
    """Request removal of a given block device.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_remove", [bdev.ToDict()])

  def call_blockdev_rename(self, node, devlist):
    """Request rename of the given block devices.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_rename",
                                [(d.ToDict(), uid) for d, uid in devlist])

  def call_blockdev_assemble(self, node, disk, owner, on_primary):
    """Request assembling of a given block device.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_assemble",
                                [disk.ToDict(), owner, on_primary])

  def call_blockdev_shutdown(self, node, disk):
    """Request shutdown of a given block device.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_shutdown", [disk.ToDict()])

  def call_blockdev_addchildren(self, node, bdev, ndevs):
    """Request adding a list of children to a (mirroring) device.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_addchildren",
                                [bdev.ToDict(),
                                 [disk.ToDict() for disk in ndevs]])

  def call_blockdev_removechildren(self, node, bdev, ndevs):
    """Request removing a list of children from a (mirroring) device.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_removechildren",
                                [bdev.ToDict(),
                                 [disk.ToDict() for disk in ndevs]])

  def call_blockdev_getmirrorstatus(self, node, disks):
    """Request status of a (mirroring) device.

    This is a single-node call.

    """
    result = self._SingleNodeCall(node, "blockdev_getmirrorstatus",
                                  [dsk.ToDict() for dsk in disks])
    if not result.fail_msg:
      result.payload = [objects.BlockDevStatus.FromDict(i)
                        for i in result.payload]
    return result

  def call_blockdev_find(self, node, disk):
    """Request identification of a given block device.

    This is a single-node call.

    """
    result = self._SingleNodeCall(node, "blockdev_find", [disk.ToDict()])
    if not result.fail_msg and result.payload is not None:
      result.payload = objects.BlockDevStatus.FromDict(result.payload)
    return result

  def call_blockdev_close(self, node, instance_name, disks):
    """Closes the given block devices.

    This is a single-node call.

    """
    params = [instance_name, [cf.ToDict() for cf in disks]]
    return self._SingleNodeCall(node, "blockdev_close", params)

  def call_blockdev_getsizes(self, node, disks):
    """Returns the size of the given disks.

    This is a single-node call.

    """
    params = [[cf.ToDict() for cf in disks]]
    return self._SingleNodeCall(node, "blockdev_getsize", params)

  def call_drbd_disconnect_net(self, node_list, nodes_ip, disks):
    """Disconnects the network of the given drbd devices.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "drbd_disconnect_net",
                               [nodes_ip, [cf.ToDict() for cf in disks]])

  def call_drbd_attach_net(self, node_list, nodes_ip,
                           disks, instance_name, multimaster):
    """Disconnects the given drbd devices.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "drbd_attach_net",
                               [nodes_ip, [cf.ToDict() for cf in disks],
                                instance_name, multimaster])

  def call_drbd_wait_sync(self, node_list, nodes_ip, disks):
    """Waits for the synchronization of drbd devices is complete.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "drbd_wait_sync",
                               [nodes_ip, [cf.ToDict() for cf in disks]])

  @classmethod
  def call_upload_file(cls, node_list, file_name, address_list=None):
    """Upload a file.

    The node will refuse the operation in case the file is not on the
    approved file list.

    This is a multi-node call.

    @type node_list: list
    @param node_list: the list of node names to upload to
    @type file_name: str
    @param file_name: the filename to upload
    @type address_list: list or None
    @keyword address_list: an optional list of node addresses, in order
        to optimize the RPC speed

    """
    file_contents = utils.ReadFile(file_name)
    data = cls._Compress(file_contents)
    st = os.stat(file_name)
    params = [file_name, data, st.st_mode, st.st_uid, st.st_gid,
              st.st_atime, st.st_mtime]
    return cls._StaticMultiNodeCall(node_list, "upload_file", params,
                                    address_list=address_list)

  @classmethod
  def call_write_ssconf_files(cls, node_list, values):
    """Write ssconf files.

    This is a multi-node call.

    """
    return cls._StaticMultiNodeCall(node_list, "write_ssconf_files", [values])

  def call_os_diagnose(self, node_list):
    """Request a diagnose of OS definitions.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "os_diagnose", [])

  def call_os_get(self, node, name):
    """Returns an OS definition.

    This is a single-node call.

    """
    result = self._SingleNodeCall(node, "os_get", [name])
    if not result.fail_msg and isinstance(result.payload, dict):
      result.payload = objects.OS.FromDict(result.payload)
    return result

  def call_hooks_runner(self, node_list, hpath, phase, env):
    """Call the hooks runner.

    Args:
      - op: the OpCode instance
      - env: a dictionary with the environment

    This is a multi-node call.

    """
    params = [hpath, phase, env]
    return self._MultiNodeCall(node_list, "hooks_runner", params)

  def call_iallocator_runner(self, node, name, idata):
    """Call an iallocator on a remote node

    Args:
      - name: the iallocator name
      - input: the json-encoded input string

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "iallocator_runner", [name, idata])

  def call_blockdev_grow(self, node, cf_bdev, amount):
    """Request a snapshot of the given block device.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_grow",
                                [cf_bdev.ToDict(), amount])

  def call_blockdev_export(self, node, cf_bdev,
                           dest_node, dest_path, cluster_name):
    """Export a given disk to another node.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_export",
                                [cf_bdev.ToDict(), dest_node, dest_path,
                                 cluster_name])

  def call_blockdev_snapshot(self, node, cf_bdev):
    """Request a snapshot of the given block device.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "blockdev_snapshot", [cf_bdev.ToDict()])

  def call_snapshot_export(self, node, snap_bdev, dest_node, instance,
                           cluster_name, idx, debug):
    """Request the export of a given snapshot.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "snapshot_export",
                                [snap_bdev.ToDict(), dest_node,
                                 self._InstDict(instance), cluster_name,
                                 idx, debug])

  def call_finalize_export(self, node, instance, snap_disks):
    """Request the completion of an export operation.

    This writes the export config file, etc.

    This is a single-node call.

    """
    flat_disks = []
    for disk in snap_disks:
      if isinstance(disk, bool):
        flat_disks.append(disk)
      else:
        flat_disks.append(disk.ToDict())

    return self._SingleNodeCall(node, "finalize_export",
                                [self._InstDict(instance), flat_disks])

  def call_export_info(self, node, path):
    """Queries the export information in a given path.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "export_info", [path])

  def call_instance_os_import(self, node, inst, src_node, src_images,
                              cluster_name, debug):
    """Request the import of a backup into an instance.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "instance_os_import",
                                [self._InstDict(inst), src_node, src_images,
                                 cluster_name, debug])

  def call_export_list(self, node_list):
    """Gets the stored exports list.

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "export_list", [])

  def call_export_remove(self, node, export):
    """Requests removal of a given export.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "export_remove", [export])

  @classmethod
  def call_node_leave_cluster(cls, node, modify_ssh_setup):
    """Requests a node to clean the cluster information it has.

    This will remove the configuration information from the ganeti data
    dir.

    This is a single-node call.

    """
    return cls._StaticSingleNodeCall(node, "node_leave_cluster",
                                     [modify_ssh_setup])

  def call_node_volumes(self, node_list):
    """Gets all volumes on node(s).

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "node_volumes", [])

  def call_node_demote_from_mc(self, node):
    """Demote a node from the master candidate role.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "node_demote_from_mc", [])


  def call_node_powercycle(self, node, hypervisor):
    """Tries to powercycle a node.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "node_powercycle", [hypervisor])


  def call_test_delay(self, node_list, duration):
    """Sleep for a fixed time on given node(s).

    This is a multi-node call.

    """
    return self._MultiNodeCall(node_list, "test_delay", [duration])

  def call_file_storage_dir_create(self, node, file_storage_dir):
    """Create the given file storage directory.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "file_storage_dir_create",
                                [file_storage_dir])

  def call_file_storage_dir_remove(self, node, file_storage_dir):
    """Remove the given file storage directory.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "file_storage_dir_remove",
                                [file_storage_dir])

  def call_file_storage_dir_rename(self, node, old_file_storage_dir,
                                   new_file_storage_dir):
    """Rename file storage directory.

    This is a single-node call.

    """
    return self._SingleNodeCall(node, "file_storage_dir_rename",
                                [old_file_storage_dir, new_file_storage_dir])

  @classmethod
  def call_jobqueue_update(cls, node_list, address_list, file_name, content):
    """Update job queue.

    This is a multi-node call.

    """
    return cls._StaticMultiNodeCall(node_list, "jobqueue_update",
                                    [file_name, cls._Compress(content)],
                                    address_list=address_list)

  @classmethod
  def call_jobqueue_purge(cls, node):
    """Purge job queue.

    This is a single-node call.

    """
    return cls._StaticSingleNodeCall(node, "jobqueue_purge", [])

  @classmethod
  def call_jobqueue_rename(cls, node_list, address_list, rename):
    """Rename a job queue file.

    This is a multi-node call.

    """
    return cls._StaticMultiNodeCall(node_list, "jobqueue_rename", rename,
                                    address_list=address_list)

  @classmethod
  def call_jobqueue_set_drain(cls, node_list, drain_flag):
    """Set the drain flag on the queue.

    This is a multi-node call.

    @type node_list: list
    @param node_list: the list of nodes to query
    @type drain_flag: bool
    @param drain_flag: if True, will set the drain flag, otherwise reset it.

    """
    return cls._StaticMultiNodeCall(node_list, "jobqueue_set_drain",
                                    [drain_flag])

  def call_hypervisor_validate_params(self, node_list, hvname, hvparams):
    """Validate the hypervisor params.

    This is a multi-node call.

    @type node_list: list
    @param node_list: the list of nodes to query
    @type hvname: string
    @param hvname: the hypervisor name
    @type hvparams: dict
    @param hvparams: the hypervisor parameters to be validated

    """
    cluster = self._cfg.GetClusterInfo()
    hv_full = objects.FillDict(cluster.hvparams.get(hvname, {}), hvparams)
    return self._MultiNodeCall(node_list, "hypervisor_validate_params",
                               [hvname, hv_full])
