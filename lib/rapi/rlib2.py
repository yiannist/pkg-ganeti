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


"""Remote API resource implementations.

PUT or POST?
============

According to RFC2616 the main difference between PUT and POST is that
POST can create new resources but PUT can only create the resource the
URI was pointing to on the PUT request.

In the context of this module POST on ``/2/instances`` to change an existing
entity is legitimate, while PUT would not be. PUT creates a new entity (e.g. a
new instance) with a name specified in the request.

Quoting from RFC2616, section 9.6::

  The fundamental difference between the POST and PUT requests is reflected in
  the different meaning of the Request-URI. The URI in a POST request
  identifies the resource that will handle the enclosed entity. That resource
  might be a data-accepting process, a gateway to some other protocol, or a
  separate entity that accepts annotations. In contrast, the URI in a PUT
  request identifies the entity enclosed with the request -- the user agent
  knows what URI is intended and the server MUST NOT attempt to apply the
  request to some other resource. If the server desires that the request be
  applied to a different URI, it MUST send a 301 (Moved Permanently) response;
  the user agent MAY then make its own decision regarding whether or not to
  redirect the request.

So when adding new methods, if they are operating on the URI entity itself,
PUT should be prefered over POST.

"""

# pylint: disable=C0103

# C0103: Invalid name, since the R_* names are not conforming

from ganeti import opcodes
from ganeti import http
from ganeti import constants
from ganeti import cli
from ganeti import rapi
from ganeti import ht
from ganeti import compat
from ganeti.rapi import baserlib


_COMMON_FIELDS = ["ctime", "mtime", "uuid", "serial_no", "tags"]
I_FIELDS = ["name", "admin_state", "os",
            "pnode", "snodes",
            "disk_template",
            "nic.ips", "nic.macs", "nic.modes", "nic.links", "nic.bridges",
            "network_port",
            "disk.sizes", "disk_usage",
            "beparams", "hvparams",
            "oper_state", "oper_ram", "oper_vcpus", "status",
            "custom_hvparams", "custom_beparams", "custom_nicparams",
            ] + _COMMON_FIELDS

N_FIELDS = ["name", "offline", "master_candidate", "drained",
            "dtotal", "dfree",
            "mtotal", "mnode", "mfree",
            "pinst_cnt", "sinst_cnt",
            "ctotal", "cnodes", "csockets",
            "pip", "sip", "role",
            "pinst_list", "sinst_list",
            "master_capable", "vm_capable",
            "group.uuid",
            ] + _COMMON_FIELDS

G_FIELDS = [
  "alloc_policy",
  "name",
  "node_cnt",
  "node_list",
  ] + _COMMON_FIELDS

J_FIELDS_BULK = [
  "id", "ops", "status", "summary",
  "opstatus",
  "received_ts", "start_ts", "end_ts",
  ]

J_FIELDS = J_FIELDS_BULK + [
  "oplog",
  "opresult",
  ]

_NR_DRAINED = "drained"
_NR_MASTER_CANDIATE = "master-candidate"
_NR_MASTER = "master"
_NR_OFFLINE = "offline"
_NR_REGULAR = "regular"

_NR_MAP = {
  constants.NR_MASTER: _NR_MASTER,
  constants.NR_MCANDIDATE: _NR_MASTER_CANDIATE,
  constants.NR_DRAINED: _NR_DRAINED,
  constants.NR_OFFLINE: _NR_OFFLINE,
  constants.NR_REGULAR: _NR_REGULAR,
  }

assert frozenset(_NR_MAP.keys()) == constants.NR_ALL

# Request data version field
_REQ_DATA_VERSION = "__version__"

# Feature string for instance creation request data version 1
_INST_CREATE_REQV1 = "instance-create-reqv1"

# Feature string for instance reinstall request version 1
_INST_REINSTALL_REQV1 = "instance-reinstall-reqv1"

# Feature string for node migration version 1
_NODE_MIGRATE_REQV1 = "node-migrate-reqv1"

# Feature string for node evacuation with LU-generated jobs
_NODE_EVAC_RES1 = "node-evac-res1"

ALL_FEATURES = frozenset([
  _INST_CREATE_REQV1,
  _INST_REINSTALL_REQV1,
  _NODE_MIGRATE_REQV1,
  _NODE_EVAC_RES1,
  ])

# Timeout for /2/jobs/[job_id]/wait. Gives job up to 10 seconds to change.
_WFJC_TIMEOUT = 10


class R_version(baserlib.R_Generic):
  """/version resource.

  This resource should be used to determine the remote API version and
  to adapt clients accordingly.

  """
  @staticmethod
  def GET():
    """Returns the remote API version.

    """
    return constants.RAPI_VERSION


class R_2_info(baserlib.R_Generic):
  """/2/info resource.

  """
  @staticmethod
  def GET():
    """Returns cluster information.

    """
    client = baserlib.GetClient()
    return client.QueryClusterInfo()


class R_2_features(baserlib.R_Generic):
  """/2/features resource.

  """
  @staticmethod
  def GET():
    """Returns list of optional RAPI features implemented.

    """
    return list(ALL_FEATURES)


class R_2_os(baserlib.R_Generic):
  """/2/os resource.

  """
  @staticmethod
  def GET():
    """Return a list of all OSes.

    Can return error 500 in case of a problem.

    Example: ["debian-etch"]

    """
    cl = baserlib.GetClient()
    op = opcodes.OpOsDiagnose(output_fields=["name", "variants"], names=[])
    job_id = baserlib.SubmitJob([op], cl)
    # we use custom feedback function, instead of print we log the status
    result = cli.PollJob(job_id, cl, feedback_fn=baserlib.FeedbackFn)
    diagnose_data = result[0]

    if not isinstance(diagnose_data, list):
      raise http.HttpBadGateway(message="Can't get OS list")

    os_names = []
    for (name, variants) in diagnose_data:
      os_names.extend(cli.CalculateOSNames(name, variants))

    return os_names


class R_2_redist_config(baserlib.R_Generic):
  """/2/redistribute-config resource.

  """
  @staticmethod
  def PUT():
    """Redistribute configuration to all nodes.

    """
    return baserlib.SubmitJob([opcodes.OpClusterRedistConf()])


class R_2_cluster_modify(baserlib.R_Generic):
  """/2/modify resource.

  """
  def PUT(self):
    """Modifies cluster parameters.

    @return: a job id

    """
    op = baserlib.FillOpcode(opcodes.OpClusterSetParams, self.request_body,
                             None)

    return baserlib.SubmitJob([op])


class R_2_jobs(baserlib.R_Generic):
  """/2/jobs resource.

  """
  def GET(self):
    """Returns a dictionary of jobs.

    @return: a dictionary with jobs id and uri.

    """
    client = baserlib.GetClient()

    if self.useBulk():
      bulkdata = client.QueryJobs(None, J_FIELDS_BULK)
      return baserlib.MapBulkFields(bulkdata, J_FIELDS_BULK)
    else:
      jobdata = map(compat.fst, client.QueryJobs(None, ["id"]))
      return baserlib.BuildUriList(jobdata, "/2/jobs/%s",
                                   uri_fields=("id", "uri"))


class R_2_jobs_id(baserlib.R_Generic):
  """/2/jobs/[job_id] resource.

  """
  def GET(self):
    """Returns a job status.

    @return: a dictionary with job parameters.
        The result includes:
            - id: job ID as a number
            - status: current job status as a string
            - ops: involved OpCodes as a list of dictionaries for each
              opcodes in the job
            - opstatus: OpCodes status as a list
            - opresult: OpCodes results as a list of lists

    """
    job_id = self.items[0]
    result = baserlib.GetClient().QueryJobs([job_id, ], J_FIELDS)[0]
    if result is None:
      raise http.HttpNotFound()
    return baserlib.MapFields(J_FIELDS, result)

  def DELETE(self):
    """Cancel not-yet-started job.

    """
    job_id = self.items[0]
    result = baserlib.GetClient().CancelJob(job_id)
    return result


class R_2_jobs_id_wait(baserlib.R_Generic):
  """/2/jobs/[job_id]/wait resource.

  """
  # WaitForJobChange provides access to sensitive information and blocks
  # machine resources (it's a blocking RAPI call), hence restricting access.
  GET_ACCESS = [rapi.RAPI_ACCESS_WRITE]

  def GET(self):
    """Waits for job changes.

    """
    job_id = self.items[0]

    fields = self.getBodyParameter("fields")
    prev_job_info = self.getBodyParameter("previous_job_info", None)
    prev_log_serial = self.getBodyParameter("previous_log_serial", None)

    if not isinstance(fields, list):
      raise http.HttpBadRequest("The 'fields' parameter should be a list")

    if not (prev_job_info is None or isinstance(prev_job_info, list)):
      raise http.HttpBadRequest("The 'previous_job_info' parameter should"
                                " be a list")

    if not (prev_log_serial is None or
            isinstance(prev_log_serial, (int, long))):
      raise http.HttpBadRequest("The 'previous_log_serial' parameter should"
                                " be a number")

    client = baserlib.GetClient()
    result = client.WaitForJobChangeOnce(job_id, fields,
                                         prev_job_info, prev_log_serial,
                                         timeout=_WFJC_TIMEOUT)
    if not result:
      raise http.HttpNotFound()

    if result == constants.JOB_NOTCHANGED:
      # No changes
      return None

    (job_info, log_entries) = result

    return {
      "job_info": job_info,
      "log_entries": log_entries,
      }


class R_2_nodes(baserlib.R_Generic):
  """/2/nodes resource.

  """
  def GET(self):
    """Returns a list of all nodes.

    """
    client = baserlib.GetClient()

    if self.useBulk():
      bulkdata = client.QueryNodes([], N_FIELDS, False)
      return baserlib.MapBulkFields(bulkdata, N_FIELDS)
    else:
      nodesdata = client.QueryNodes([], ["name"], False)
      nodeslist = [row[0] for row in nodesdata]
      return baserlib.BuildUriList(nodeslist, "/2/nodes/%s",
                                   uri_fields=("id", "uri"))


class R_2_nodes_name(baserlib.R_Generic):
  """/2/nodes/[node_name] resource.

  """
  def GET(self):
    """Send information about a node.

    """
    node_name = self.items[0]
    client = baserlib.GetClient()

    result = baserlib.HandleItemQueryErrors(client.QueryNodes,
                                            names=[node_name], fields=N_FIELDS,
                                            use_locking=self.useLocking())

    return baserlib.MapFields(N_FIELDS, result[0])


class R_2_nodes_name_role(baserlib.R_Generic):
  """ /2/nodes/[node_name]/role resource.

  """
  def GET(self):
    """Returns the current node role.

    @return: Node role

    """
    node_name = self.items[0]
    client = baserlib.GetClient()
    result = client.QueryNodes(names=[node_name], fields=["role"],
                               use_locking=self.useLocking())

    return _NR_MAP[result[0][0]]

  def PUT(self):
    """Sets the node role.

    @return: a job id

    """
    if not isinstance(self.request_body, basestring):
      raise http.HttpBadRequest("Invalid body contents, not a string")

    node_name = self.items[0]
    role = self.request_body

    if role == _NR_REGULAR:
      candidate = False
      offline = False
      drained = False

    elif role == _NR_MASTER_CANDIATE:
      candidate = True
      offline = drained = None

    elif role == _NR_DRAINED:
      drained = True
      candidate = offline = None

    elif role == _NR_OFFLINE:
      offline = True
      candidate = drained = None

    else:
      raise http.HttpBadRequest("Can't set '%s' role" % role)

    op = opcodes.OpNodeSetParams(node_name=node_name,
                                 master_candidate=candidate,
                                 offline=offline,
                                 drained=drained,
                                 force=bool(self.useForce()))

    return baserlib.SubmitJob([op])


class R_2_nodes_name_evacuate(baserlib.R_Generic):
  """/2/nodes/[node_name]/evacuate resource.

  """
  def POST(self):
    """Evacuate all instances off a node.

    """
    op = baserlib.FillOpcode(opcodes.OpNodeEvacuate, self.request_body, {
      "node_name": self.items[0],
      "dry_run": self.dryRun(),
      })

    return baserlib.SubmitJob([op])


class R_2_nodes_name_migrate(baserlib.R_Generic):
  """/2/nodes/[node_name]/migrate resource.

  """
  def POST(self):
    """Migrate all primary instances from a node.

    """
    node_name = self.items[0]

    if self.queryargs:
      # Support old-style requests
      if "live" in self.queryargs and "mode" in self.queryargs:
        raise http.HttpBadRequest("Only one of 'live' and 'mode' should"
                                  " be passed")

      if "live" in self.queryargs:
        if self._checkIntVariable("live", default=1):
          mode = constants.HT_MIGRATION_LIVE
        else:
          mode = constants.HT_MIGRATION_NONLIVE
      else:
        mode = self._checkStringVariable("mode", default=None)

      data = {
        "mode": mode,
        }
    else:
      data = self.request_body

    op = baserlib.FillOpcode(opcodes.OpNodeMigrate, data, {
      "node_name": node_name,
      })

    return baserlib.SubmitJob([op])


class R_2_nodes_name_storage(baserlib.R_Generic):
  """/2/nodes/[node_name]/storage resource.

  """
  # LUNodeQueryStorage acquires locks, hence restricting access to GET
  GET_ACCESS = [rapi.RAPI_ACCESS_WRITE]

  def GET(self):
    node_name = self.items[0]

    storage_type = self._checkStringVariable("storage_type", None)
    if not storage_type:
      raise http.HttpBadRequest("Missing the required 'storage_type'"
                                " parameter")

    output_fields = self._checkStringVariable("output_fields", None)
    if not output_fields:
      raise http.HttpBadRequest("Missing the required 'output_fields'"
                                " parameter")

    op = opcodes.OpNodeQueryStorage(nodes=[node_name],
                                    storage_type=storage_type,
                                    output_fields=output_fields.split(","))
    return baserlib.SubmitJob([op])


class R_2_nodes_name_storage_modify(baserlib.R_Generic):
  """/2/nodes/[node_name]/storage/modify resource.

  """
  def PUT(self):
    node_name = self.items[0]

    storage_type = self._checkStringVariable("storage_type", None)
    if not storage_type:
      raise http.HttpBadRequest("Missing the required 'storage_type'"
                                " parameter")

    name = self._checkStringVariable("name", None)
    if not name:
      raise http.HttpBadRequest("Missing the required 'name'"
                                " parameter")

    changes = {}

    if "allocatable" in self.queryargs:
      changes[constants.SF_ALLOCATABLE] = \
        bool(self._checkIntVariable("allocatable", default=1))

    op = opcodes.OpNodeModifyStorage(node_name=node_name,
                                     storage_type=storage_type,
                                     name=name,
                                     changes=changes)
    return baserlib.SubmitJob([op])


class R_2_nodes_name_storage_repair(baserlib.R_Generic):
  """/2/nodes/[node_name]/storage/repair resource.

  """
  def PUT(self):
    node_name = self.items[0]

    storage_type = self._checkStringVariable("storage_type", None)
    if not storage_type:
      raise http.HttpBadRequest("Missing the required 'storage_type'"
                                " parameter")

    name = self._checkStringVariable("name", None)
    if not name:
      raise http.HttpBadRequest("Missing the required 'name'"
                                " parameter")

    op = opcodes.OpRepairNodeStorage(node_name=node_name,
                                     storage_type=storage_type,
                                     name=name)
    return baserlib.SubmitJob([op])


def _ParseCreateGroupRequest(data, dry_run):
  """Parses a request for creating a node group.

  @rtype: L{opcodes.OpGroupAdd}
  @return: Group creation opcode

  """
  override = {
    "dry_run": dry_run,
    }

  rename = {
    "name": "group_name",
    }

  return baserlib.FillOpcode(opcodes.OpGroupAdd, data, override,
                             rename=rename)


class R_2_groups(baserlib.R_Generic):
  """/2/groups resource.

  """
  def GET(self):
    """Returns a list of all node groups.

    """
    client = baserlib.GetClient()

    if self.useBulk():
      bulkdata = client.QueryGroups([], G_FIELDS, False)
      return baserlib.MapBulkFields(bulkdata, G_FIELDS)
    else:
      data = client.QueryGroups([], ["name"], False)
      groupnames = [row[0] for row in data]
      return baserlib.BuildUriList(groupnames, "/2/groups/%s",
                                   uri_fields=("name", "uri"))

  def POST(self):
    """Create a node group.

    @return: a job id

    """
    baserlib.CheckType(self.request_body, dict, "Body contents")
    op = _ParseCreateGroupRequest(self.request_body, self.dryRun())
    return baserlib.SubmitJob([op])


class R_2_groups_name(baserlib.R_Generic):
  """/2/groups/[group_name] resource.

  """
  def GET(self):
    """Send information about a node group.

    """
    group_name = self.items[0]
    client = baserlib.GetClient()

    result = baserlib.HandleItemQueryErrors(client.QueryGroups,
                                            names=[group_name], fields=G_FIELDS,
                                            use_locking=self.useLocking())

    return baserlib.MapFields(G_FIELDS, result[0])

  def DELETE(self):
    """Delete a node group.

    """
    op = opcodes.OpGroupRemove(group_name=self.items[0],
                               dry_run=bool(self.dryRun()))

    return baserlib.SubmitJob([op])


def _ParseModifyGroupRequest(name, data):
  """Parses a request for modifying a node group.

  @rtype: L{opcodes.OpGroupSetParams}
  @return: Group modify opcode

  """
  return baserlib.FillOpcode(opcodes.OpGroupSetParams, data, {
    "group_name": name,
    })


class R_2_groups_name_modify(baserlib.R_Generic):
  """/2/groups/[group_name]/modify resource.

  """
  def PUT(self):
    """Changes some parameters of node group.

    @return: a job id

    """
    baserlib.CheckType(self.request_body, dict, "Body contents")

    op = _ParseModifyGroupRequest(self.items[0], self.request_body)

    return baserlib.SubmitJob([op])


def _ParseRenameGroupRequest(name, data, dry_run):
  """Parses a request for renaming a node group.

  @type name: string
  @param name: name of the node group to rename
  @type data: dict
  @param data: the body received by the rename request
  @type dry_run: bool
  @param dry_run: whether to perform a dry run

  @rtype: L{opcodes.OpGroupRename}
  @return: Node group rename opcode

  """
  return baserlib.FillOpcode(opcodes.OpGroupRename, data, {
    "group_name": name,
    "dry_run": dry_run,
    })


class R_2_groups_name_rename(baserlib.R_Generic):
  """/2/groups/[group_name]/rename resource.

  """
  def PUT(self):
    """Changes the name of a node group.

    @return: a job id

    """
    baserlib.CheckType(self.request_body, dict, "Body contents")
    op = _ParseRenameGroupRequest(self.items[0], self.request_body,
                                  self.dryRun())
    return baserlib.SubmitJob([op])


class R_2_groups_name_assign_nodes(baserlib.R_Generic):
  """/2/groups/[group_name]/assign-nodes resource.

  """
  def PUT(self):
    """Assigns nodes to a group.

    @return: a job id

    """
    op = baserlib.FillOpcode(opcodes.OpGroupAssignNodes, self.request_body, {
      "group_name": self.items[0],
      "dry_run": self.dryRun(),
      "force": self.useForce(),
      })

    return baserlib.SubmitJob([op])


def _ParseInstanceCreateRequestVersion1(data, dry_run):
  """Parses an instance creation request version 1.

  @rtype: L{opcodes.OpInstanceCreate}
  @return: Instance creation opcode

  """
  override = {
    "dry_run": dry_run,
    }

  rename = {
    "os": "os_type",
    "name": "instance_name",
    }

  return baserlib.FillOpcode(opcodes.OpInstanceCreate, data, override,
                             rename=rename)


class R_2_instances(baserlib.R_Generic):
  """/2/instances resource.

  """
  def GET(self):
    """Returns a list of all available instances.

    """
    client = baserlib.GetClient()

    use_locking = self.useLocking()
    if self.useBulk():
      bulkdata = client.QueryInstances([], I_FIELDS, use_locking)
      return baserlib.MapBulkFields(bulkdata, I_FIELDS)
    else:
      instancesdata = client.QueryInstances([], ["name"], use_locking)
      instanceslist = [row[0] for row in instancesdata]
      return baserlib.BuildUriList(instanceslist, "/2/instances/%s",
                                   uri_fields=("id", "uri"))

  def POST(self):
    """Create an instance.

    @return: a job id

    """
    if not isinstance(self.request_body, dict):
      raise http.HttpBadRequest("Invalid body contents, not a dictionary")

    # Default to request data version 0
    data_version = self.getBodyParameter(_REQ_DATA_VERSION, 0)

    if data_version == 0:
      raise http.HttpBadRequest("Instance creation request version 0 is no"
                                " longer supported")
    elif data_version == 1:
      data = self.request_body.copy()
      # Remove "__version__"
      data.pop(_REQ_DATA_VERSION, None)
      op = _ParseInstanceCreateRequestVersion1(data, self.dryRun())
    else:
      raise http.HttpBadRequest("Unsupported request data version %s" %
                                data_version)

    return baserlib.SubmitJob([op])


class R_2_instances_name(baserlib.R_Generic):
  """/2/instances/[instance_name] resource.

  """
  def GET(self):
    """Send information about an instance.

    """
    client = baserlib.GetClient()
    instance_name = self.items[0]

    result = baserlib.HandleItemQueryErrors(client.QueryInstances,
                                            names=[instance_name],
                                            fields=I_FIELDS,
                                            use_locking=self.useLocking())

    return baserlib.MapFields(I_FIELDS, result[0])

  def DELETE(self):
    """Delete an instance.

    """
    op = opcodes.OpInstanceRemove(instance_name=self.items[0],
                                  ignore_failures=False,
                                  dry_run=bool(self.dryRun()))
    return baserlib.SubmitJob([op])


class R_2_instances_name_info(baserlib.R_Generic):
  """/2/instances/[instance_name]/info resource.

  """
  def GET(self):
    """Request detailed instance information.

    """
    instance_name = self.items[0]
    static = bool(self._checkIntVariable("static", default=0))

    op = opcodes.OpInstanceQueryData(instances=[instance_name],
                                     static=static)
    return baserlib.SubmitJob([op])


class R_2_instances_name_reboot(baserlib.R_Generic):
  """/2/instances/[instance_name]/reboot resource.

  Implements an instance reboot.

  """
  def POST(self):
    """Reboot an instance.

    The URI takes type=[hard|soft|full] and
    ignore_secondaries=[False|True] parameters.

    """
    instance_name = self.items[0]
    reboot_type = self.queryargs.get("type",
                                     [constants.INSTANCE_REBOOT_HARD])[0]
    ignore_secondaries = bool(self._checkIntVariable("ignore_secondaries"))
    op = opcodes.OpInstanceReboot(instance_name=instance_name,
                                  reboot_type=reboot_type,
                                  ignore_secondaries=ignore_secondaries,
                                  dry_run=bool(self.dryRun()))

    return baserlib.SubmitJob([op])


class R_2_instances_name_startup(baserlib.R_Generic):
  """/2/instances/[instance_name]/startup resource.

  Implements an instance startup.

  """
  def PUT(self):
    """Startup an instance.

    The URI takes force=[False|True] parameter to start the instance
    if even if secondary disks are failing.

    """
    instance_name = self.items[0]
    force_startup = bool(self._checkIntVariable("force"))
    no_remember = bool(self._checkIntVariable("no_remember"))
    op = opcodes.OpInstanceStartup(instance_name=instance_name,
                                   force=force_startup,
                                   dry_run=bool(self.dryRun()),
                                   no_remember=no_remember)

    return baserlib.SubmitJob([op])


def _ParseShutdownInstanceRequest(name, data, dry_run, no_remember):
  """Parses a request for an instance shutdown.

  @rtype: L{opcodes.OpInstanceShutdown}
  @return: Instance shutdown opcode

  """
  return baserlib.FillOpcode(opcodes.OpInstanceShutdown, data, {
    "instance_name": name,
    "dry_run": dry_run,
    "no_remember": no_remember,
    })


class R_2_instances_name_shutdown(baserlib.R_Generic):
  """/2/instances/[instance_name]/shutdown resource.

  Implements an instance shutdown.

  """
  def PUT(self):
    """Shutdown an instance.

    @return: a job id

    """
    no_remember = bool(self._checkIntVariable("no_remember"))
    op = _ParseShutdownInstanceRequest(self.items[0], self.request_body,
                                       bool(self.dryRun()), no_remember)

    return baserlib.SubmitJob([op])


def _ParseInstanceReinstallRequest(name, data):
  """Parses a request for reinstalling an instance.

  """
  if not isinstance(data, dict):
    raise http.HttpBadRequest("Invalid body contents, not a dictionary")

  ostype = baserlib.CheckParameter(data, "os", default=None)
  start = baserlib.CheckParameter(data, "start", exptype=bool,
                                  default=True)
  osparams = baserlib.CheckParameter(data, "osparams", default=None)

  ops = [
    opcodes.OpInstanceShutdown(instance_name=name),
    opcodes.OpInstanceReinstall(instance_name=name, os_type=ostype,
                                osparams=osparams),
    ]

  if start:
    ops.append(opcodes.OpInstanceStartup(instance_name=name, force=False))

  return ops


class R_2_instances_name_reinstall(baserlib.R_Generic):
  """/2/instances/[instance_name]/reinstall resource.

  Implements an instance reinstall.

  """
  def POST(self):
    """Reinstall an instance.

    The URI takes os=name and nostartup=[0|1] optional
    parameters. By default, the instance will be started
    automatically.

    """
    if self.request_body:
      if self.queryargs:
        raise http.HttpBadRequest("Can't combine query and body parameters")

      body = self.request_body
    elif self.queryargs:
      # Legacy interface, do not modify/extend
      body = {
        "os": self._checkStringVariable("os"),
        "start": not self._checkIntVariable("nostartup"),
        }
    else:
      body = {}

    ops = _ParseInstanceReinstallRequest(self.items[0], body)

    return baserlib.SubmitJob(ops)


def _ParseInstanceReplaceDisksRequest(name, data):
  """Parses a request for an instance export.

  @rtype: L{opcodes.OpInstanceReplaceDisks}
  @return: Instance export opcode

  """
  override = {
    "instance_name": name,
    }

  # Parse disks
  try:
    raw_disks = data.pop("disks")
  except KeyError:
    pass
  else:
    if raw_disks:
      if ht.TListOf(ht.TInt)(raw_disks): # pylint: disable=E1102
        data["disks"] = raw_disks
      else:
        # Backwards compatibility for strings of the format "1, 2, 3"
        try:
          data["disks"] = [int(part) for part in raw_disks.split(",")]
        except (TypeError, ValueError), err:
          raise http.HttpBadRequest("Invalid disk index passed: %s" % str(err))

  return baserlib.FillOpcode(opcodes.OpInstanceReplaceDisks, data, override)


class R_2_instances_name_replace_disks(baserlib.R_Generic):
  """/2/instances/[instance_name]/replace-disks resource.

  """
  def POST(self):
    """Replaces disks on an instance.

    """
    if self.request_body:
      body = self.request_body
    elif self.queryargs:
      # Legacy interface, do not modify/extend
      body = {
        "remote_node": self._checkStringVariable("remote_node", default=None),
        "mode": self._checkStringVariable("mode", default=None),
        "disks": self._checkStringVariable("disks", default=None),
        "iallocator": self._checkStringVariable("iallocator", default=None),
        }
    else:
      body = {}

    op = _ParseInstanceReplaceDisksRequest(self.items[0], body)

    return baserlib.SubmitJob([op])


class R_2_instances_name_activate_disks(baserlib.R_Generic):
  """/2/instances/[instance_name]/activate-disks resource.

  """
  def PUT(self):
    """Activate disks for an instance.

    The URI might contain ignore_size to ignore current recorded size.

    """
    instance_name = self.items[0]
    ignore_size = bool(self._checkIntVariable("ignore_size"))

    op = opcodes.OpInstanceActivateDisks(instance_name=instance_name,
                                         ignore_size=ignore_size)

    return baserlib.SubmitJob([op])


class R_2_instances_name_deactivate_disks(baserlib.R_Generic):
  """/2/instances/[instance_name]/deactivate-disks resource.

  """
  def PUT(self):
    """Deactivate disks for an instance.

    """
    instance_name = self.items[0]

    op = opcodes.OpInstanceDeactivateDisks(instance_name=instance_name)

    return baserlib.SubmitJob([op])


class R_2_instances_name_prepare_export(baserlib.R_Generic):
  """/2/instances/[instance_name]/prepare-export resource.

  """
  def PUT(self):
    """Prepares an export for an instance.

    @return: a job id

    """
    instance_name = self.items[0]
    mode = self._checkStringVariable("mode")

    op = opcodes.OpBackupPrepare(instance_name=instance_name,
                                 mode=mode)

    return baserlib.SubmitJob([op])


def _ParseExportInstanceRequest(name, data):
  """Parses a request for an instance export.

  @rtype: L{opcodes.OpBackupExport}
  @return: Instance export opcode

  """
  # Rename "destination" to "target_node"
  try:
    data["target_node"] = data.pop("destination")
  except KeyError:
    pass

  return baserlib.FillOpcode(opcodes.OpBackupExport, data, {
    "instance_name": name,
    })


class R_2_instances_name_export(baserlib.R_Generic):
  """/2/instances/[instance_name]/export resource.

  """
  def PUT(self):
    """Exports an instance.

    @return: a job id

    """
    if not isinstance(self.request_body, dict):
      raise http.HttpBadRequest("Invalid body contents, not a dictionary")

    op = _ParseExportInstanceRequest(self.items[0], self.request_body)

    return baserlib.SubmitJob([op])


def _ParseMigrateInstanceRequest(name, data):
  """Parses a request for an instance migration.

  @rtype: L{opcodes.OpInstanceMigrate}
  @return: Instance migration opcode

  """
  return baserlib.FillOpcode(opcodes.OpInstanceMigrate, data, {
    "instance_name": name,
    })


class R_2_instances_name_migrate(baserlib.R_Generic):
  """/2/instances/[instance_name]/migrate resource.

  """
  def PUT(self):
    """Migrates an instance.

    @return: a job id

    """
    baserlib.CheckType(self.request_body, dict, "Body contents")

    op = _ParseMigrateInstanceRequest(self.items[0], self.request_body)

    return baserlib.SubmitJob([op])


class R_2_instances_name_failover(baserlib.R_Generic):
  """/2/instances/[instance_name]/failover resource.

  """
  def PUT(self):
    """Does a failover of an instance.

    @return: a job id

    """
    baserlib.CheckType(self.request_body, dict, "Body contents")

    op = baserlib.FillOpcode(opcodes.OpInstanceFailover, self.request_body, {
      "instance_name": self.items[0],
      })

    return baserlib.SubmitJob([op])


def _ParseRenameInstanceRequest(name, data):
  """Parses a request for renaming an instance.

  @rtype: L{opcodes.OpInstanceRename}
  @return: Instance rename opcode

  """
  return baserlib.FillOpcode(opcodes.OpInstanceRename, data, {
    "instance_name": name,
    })


class R_2_instances_name_rename(baserlib.R_Generic):
  """/2/instances/[instance_name]/rename resource.

  """
  def PUT(self):
    """Changes the name of an instance.

    @return: a job id

    """
    baserlib.CheckType(self.request_body, dict, "Body contents")

    op = _ParseRenameInstanceRequest(self.items[0], self.request_body)

    return baserlib.SubmitJob([op])


def _ParseModifyInstanceRequest(name, data):
  """Parses a request for modifying an instance.

  @rtype: L{opcodes.OpInstanceSetParams}
  @return: Instance modify opcode

  """
  return baserlib.FillOpcode(opcodes.OpInstanceSetParams, data, {
    "instance_name": name,
    })


class R_2_instances_name_modify(baserlib.R_Generic):
  """/2/instances/[instance_name]/modify resource.

  """
  def PUT(self):
    """Changes some parameters of an instance.

    @return: a job id

    """
    baserlib.CheckType(self.request_body, dict, "Body contents")

    op = _ParseModifyInstanceRequest(self.items[0], self.request_body)

    return baserlib.SubmitJob([op])


class R_2_instances_name_disk_grow(baserlib.R_Generic):
  """/2/instances/[instance_name]/disk/[disk_index]/grow resource.

  """
  def POST(self):
    """Increases the size of an instance disk.

    @return: a job id

    """
    op = baserlib.FillOpcode(opcodes.OpInstanceGrowDisk, self.request_body, {
      "instance_name": self.items[0],
      "disk": int(self.items[1]),
      })

    return baserlib.SubmitJob([op])


class R_2_instances_name_console(baserlib.R_Generic):
  """/2/instances/[instance_name]/console resource.

  """
  GET_ACCESS = [rapi.RAPI_ACCESS_WRITE]

  def GET(self):
    """Request information for connecting to instance's console.

    @return: Serialized instance console description, see
             L{objects.InstanceConsole}

    """
    client = baserlib.GetClient()

    ((console, ), ) = client.QueryInstances([self.items[0]], ["console"], False)

    if console is None:
      raise http.HttpServiceUnavailable("Instance console unavailable")

    assert isinstance(console, dict)
    return console


def _GetQueryFields(args):
  """

  """
  try:
    fields = args["fields"]
  except KeyError:
    raise http.HttpBadRequest("Missing 'fields' query argument")

  return _SplitQueryFields(fields[0])


def _SplitQueryFields(fields):
  """

  """
  return [i.strip() for i in fields.split(",")]


class R_2_query(baserlib.R_Generic):
  """/2/query/[resource] resource.

  """
  # Results might contain sensitive information
  GET_ACCESS = [rapi.RAPI_ACCESS_WRITE]

  def _Query(self, fields, filter_):
    return baserlib.GetClient().Query(self.items[0], fields, filter_).ToDict()

  def GET(self):
    """Returns resource information.

    @return: Query result, see L{objects.QueryResponse}

    """
    return self._Query(_GetQueryFields(self.queryargs), None)

  def PUT(self):
    """Submits job querying for resources.

    @return: Query result, see L{objects.QueryResponse}

    """
    body = self.request_body

    baserlib.CheckType(body, dict, "Body contents")

    try:
      fields = body["fields"]
    except KeyError:
      fields = _GetQueryFields(self.queryargs)

    return self._Query(fields, self.request_body.get("filter", None))


class R_2_query_fields(baserlib.R_Generic):
  """/2/query/[resource]/fields resource.

  """
  def GET(self):
    """Retrieves list of available fields for a resource.

    @return: List of serialized L{objects.QueryFieldDefinition}

    """
    try:
      raw_fields = self.queryargs["fields"]
    except KeyError:
      fields = None
    else:
      fields = _SplitQueryFields(raw_fields[0])

    return baserlib.GetClient().QueryFields(self.items[0], fields).ToDict()


class _R_Tags(baserlib.R_Generic):
  """ Quasiclass for tagging resources

  Manages tags. When inheriting this class you must define the
  TAG_LEVEL for it.

  """
  TAG_LEVEL = None

  def __init__(self, items, queryargs, req):
    """A tag resource constructor.

    We have to override the default to sort out cluster naming case.

    """
    baserlib.R_Generic.__init__(self, items, queryargs, req)

    if self.TAG_LEVEL == constants.TAG_CLUSTER:
      self.name = None
    else:
      self.name = items[0]

  def GET(self):
    """Returns a list of tags.

    Example: ["tag1", "tag2", "tag3"]

    """
    # pylint: disable=W0212
    return baserlib._Tags_GET(self.TAG_LEVEL, name=self.name)

  def PUT(self):
    """Add a set of tags.

    The request as a list of strings should be PUT to this URI. And
    you'll have back a job id.

    """
    # pylint: disable=W0212
    if "tag" not in self.queryargs:
      raise http.HttpBadRequest("Please specify tag(s) to add using the"
                                " the 'tag' parameter")
    return baserlib._Tags_PUT(self.TAG_LEVEL,
                              self.queryargs["tag"], name=self.name,
                              dry_run=bool(self.dryRun()))

  def DELETE(self):
    """Delete a tag.

    In order to delete a set of tags, the DELETE
    request should be addressed to URI like:
    /tags?tag=[tag]&tag=[tag]

    """
    # pylint: disable=W0212
    if "tag" not in self.queryargs:
      # no we not gonna delete all tags
      raise http.HttpBadRequest("Cannot delete all tags - please specify"
                                " tag(s) using the 'tag' parameter")
    return baserlib._Tags_DELETE(self.TAG_LEVEL,
                                 self.queryargs["tag"],
                                 name=self.name,
                                 dry_run=bool(self.dryRun()))


class R_2_instances_name_tags(_R_Tags):
  """ /2/instances/[instance_name]/tags resource.

  Manages per-instance tags.

  """
  TAG_LEVEL = constants.TAG_INSTANCE


class R_2_nodes_name_tags(_R_Tags):
  """ /2/nodes/[node_name]/tags resource.

  Manages per-node tags.

  """
  TAG_LEVEL = constants.TAG_NODE


class R_2_groups_name_tags(_R_Tags):
  """ /2/groups/[group_name]/tags resource.

  Manages per-nodegroup tags.

  """
  TAG_LEVEL = constants.TAG_NODEGROUP


class R_2_tags(_R_Tags):
  """ /2/tags resource.

  Manages cluster tags.

  """
  TAG_LEVEL = constants.TAG_CLUSTER
