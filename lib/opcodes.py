#
#

# Copyright (C) 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013 Google Inc.
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


"""OpCodes module

This module implements the data structures which define the cluster
operations - the so-called opcodes.

Every operation which modifies the cluster state is expressed via
opcodes.

"""

# this are practically structures, so disable the message about too
# few public methods:
# pylint: disable=R0903

import logging
import re
import ipaddr

from ganeti import constants
from ganeti import errors
from ganeti import ht
from ganeti import objects
from ganeti import outils


# Common opcode attributes

#: output fields for a query operation
_POutputFields = ("output_fields", ht.NoDefault, ht.TListOf(ht.TNonEmptyString),
                  "Selected output fields")

#: the shutdown timeout
_PShutdownTimeout = \
  ("shutdown_timeout", constants.DEFAULT_SHUTDOWN_TIMEOUT, ht.TNonNegativeInt,
   "How long to wait for instance to shut down")

#: the force parameter
_PForce = ("force", False, ht.TBool, "Whether to force the operation")

#: a required instance name (for single-instance LUs)
_PInstanceName = ("instance_name", ht.NoDefault, ht.TNonEmptyString,
                  "Instance name")

#: a instance UUID (for single-instance LUs)
_PInstanceUuid = ("instance_uuid", None, ht.TMaybeString,
                  "Instance UUID")

#: Whether to ignore offline nodes
_PIgnoreOfflineNodes = ("ignore_offline_nodes", False, ht.TBool,
                        "Whether to ignore offline nodes")

#: a required node name (for single-node LUs)
_PNodeName = ("node_name", ht.NoDefault, ht.TNonEmptyString, "Node name")

#: a node UUID (for use with _PNodeName)
_PNodeUuid = ("node_uuid", None, ht.TMaybeString, "Node UUID")

#: a required node group name (for single-group LUs)
_PGroupName = ("group_name", ht.NoDefault, ht.TNonEmptyString, "Group name")

#: Migration type (live/non-live)
_PMigrationMode = ("mode", None,
                   ht.TMaybe(ht.TElemOf(constants.HT_MIGRATION_MODES)),
                   "Migration mode")

#: Obsolete 'live' migration mode (boolean)
_PMigrationLive = ("live", None, ht.TMaybeBool,
                   "Legacy setting for live migration, do not use")

#: Tag type
_PTagKind = ("kind", ht.NoDefault, ht.TElemOf(constants.VALID_TAG_TYPES),
             "Tag kind")

#: List of tag strings
_PTags = ("tags", ht.NoDefault, ht.TListOf(ht.TNonEmptyString),
          "List of tag names")

_PForceVariant = ("force_variant", False, ht.TBool,
                  "Whether to force an unknown OS variant")

_PWaitForSync = ("wait_for_sync", True, ht.TBool,
                 "Whether to wait for the disk to synchronize")

_PWaitForSyncFalse = ("wait_for_sync", False, ht.TBool,
                      "Whether to wait for the disk to synchronize"
                      " (defaults to false)")

_PIgnoreConsistency = ("ignore_consistency", False, ht.TBool,
                       "Whether to ignore disk consistency")

_PStorageName = ("name", ht.NoDefault, ht.TMaybeString, "Storage name")

_PUseLocking = ("use_locking", False, ht.TBool,
                "Whether to use synchronization")

_PNameCheck = ("name_check", True, ht.TBool, "Whether to check name")

_PNodeGroupAllocPolicy = \
  ("alloc_policy", None,
   ht.TMaybe(ht.TElemOf(constants.VALID_ALLOC_POLICIES)),
   "Instance allocation policy")

_PGroupNodeParams = ("ndparams", None, ht.TMaybeDict,
                     "Default node parameters for group")

_PQueryWhat = ("what", ht.NoDefault, ht.TElemOf(constants.QR_VIA_OP),
               "Resource(s) to query for")

_PEarlyRelease = ("early_release", False, ht.TBool,
                  "Whether to release locks as soon as possible")

_PIpCheckDoc = "Whether to ensure instance's IP address is inactive"

#: Do not remember instance state changes
_PNoRemember = ("no_remember", False, ht.TBool,
                "Do not remember the state change")

#: Target node for instance migration/failover
_PMigrationTargetNode = ("target_node", None, ht.TMaybeString,
                         "Target node for shared-storage instances")

_PMigrationTargetNodeUuid = ("target_node_uuid", None, ht.TMaybeString,
                             "Target node UUID for shared-storage instances")

_PStartupPaused = ("startup_paused", False, ht.TBool,
                   "Pause instance at startup")

_PVerbose = ("verbose", False, ht.TBool, "Verbose mode")

# Parameters for cluster verification
_PDebugSimulateErrors = ("debug_simulate_errors", False, ht.TBool,
                         "Whether to simulate errors (useful for debugging)")
_PErrorCodes = ("error_codes", False, ht.TBool, "Error codes")
_PSkipChecks = ("skip_checks", ht.EmptyList,
                ht.TListOf(ht.TElemOf(constants.VERIFY_OPTIONAL_CHECKS)),
                "Which checks to skip")
_PIgnoreErrors = ("ignore_errors", ht.EmptyList,
                  ht.TListOf(ht.TElemOf(constants.CV_ALL_ECODES_STRINGS)),
                  "List of error codes that should be treated as warnings")

# Disk parameters
_PDiskParams = \
  ("diskparams", None,
   ht.TMaybe(ht.TDictOf(ht.TElemOf(constants.DISK_TEMPLATES), ht.TDict)),
   "Disk templates' parameter defaults")

# Parameters for node resource model
_PHvState = ("hv_state", None, ht.TMaybeDict, "Set hypervisor states")
_PDiskState = ("disk_state", None, ht.TMaybeDict, "Set disk states")

#: Opportunistic locking
_POpportunisticLocking = \
  ("opportunistic_locking", False, ht.TBool,
   ("Whether to employ opportunistic locking for nodes, meaning nodes"
    " already locked by another opcode won't be considered for instance"
    " allocation (only when an iallocator is used)"))

_PIgnoreIpolicy = ("ignore_ipolicy", False, ht.TBool,
                   "Whether to ignore ipolicy violations")

# Allow runtime changes while migrating
_PAllowRuntimeChgs = ("allow_runtime_changes", True, ht.TBool,
                      "Allow runtime changes (eg. memory ballooning)")

#: IAllocator field builder
_PIAllocFromDesc = lambda desc: ("iallocator", None, ht.TMaybeString, desc)

#: a required network name
_PNetworkName = ("network_name", ht.NoDefault, ht.TNonEmptyString,
                 "Set network name")

_PTargetGroups = \
  ("target_groups", None, ht.TMaybeListOf(ht.TNonEmptyString),
   "Destination group names or UUIDs (defaults to \"all but current group\")")

#: OP_ID conversion regular expression
_OPID_RE = re.compile("([a-z])([A-Z])")

#: Utility function for L{OpClusterSetParams}
_TestClusterOsListItem = \
  ht.TAnd(ht.TIsLength(2), ht.TItems([
    ht.TElemOf(constants.DDMS_VALUES),
    ht.TNonEmptyString,
    ]))

_TestClusterOsList = ht.TMaybeListOf(_TestClusterOsListItem)

# TODO: Generate check from constants.INIC_PARAMS_TYPES
#: Utility function for testing NIC definitions
_TestNicDef = \
  ht.Comment("NIC parameters")(ht.TDictOf(ht.TElemOf(constants.INIC_PARAMS),
                                          ht.TMaybeString))

_TSetParamsResultItemItems = [
  ht.Comment("name of changed parameter")(ht.TNonEmptyString),
  ht.Comment("new value")(ht.TAny),
  ]

_TSetParamsResult = \
  ht.TListOf(ht.TAnd(ht.TIsLength(len(_TSetParamsResultItemItems)),
                     ht.TItems(_TSetParamsResultItemItems)))

# In the disks option we can provide arbitrary parameters too, which
# we may not be able to validate at this level, so we just check the
# format of the dict here and the checks concerning IDISK_PARAMS will
# happen at the LU level
_TDiskParams = \
  ht.Comment("Disk parameters")(ht.TDictOf(ht.TNonEmptyString,
                                           ht.TOr(ht.TNonEmptyString, ht.TInt)))

_TQueryRow = \
  ht.TListOf(ht.TAnd(ht.TIsLength(2),
                     ht.TItems([ht.TElemOf(constants.RS_ALL),
                                ht.TAny])))

_TQueryResult = ht.TListOf(_TQueryRow)

_TOldQueryRow = ht.TListOf(ht.TAny)

_TOldQueryResult = ht.TListOf(_TOldQueryRow)


_SUMMARY_PREFIX = {
  "CLUSTER_": "C_",
  "GROUP_": "G_",
  "NODE_": "N_",
  "INSTANCE_": "I_",
  }

#: Attribute name for dependencies
DEPEND_ATTR = "depends"

#: Attribute name for comment
COMMENT_ATTR = "comment"


def _NameComponents(name):
  """Split an opcode class name into its components

  @type name: string
  @param name: the class name, as OpXxxYyy
  @rtype: array of strings
  @return: the components of the name

  """
  assert name.startswith("Op")
  # Note: (?<=[a-z])(?=[A-Z]) would be ideal, since it wouldn't
  # consume any input, and hence we would just have all the elements
  # in the list, one by one; but it seems that split doesn't work on
  # non-consuming input, hence we have to process the input string a
  # bit
  name = _OPID_RE.sub(r"\1,\2", name)
  elems = name.split(",")
  return elems


def _NameToId(name):
  """Convert an opcode class name to an OP_ID.

  @type name: string
  @param name: the class name, as OpXxxYyy
  @rtype: string
  @return: the name in the OP_XXXX_YYYY format

  """
  if not name.startswith("Op"):
    return None
  return "_".join(n.upper() for n in _NameComponents(name))


def NameToReasonSrc(name):
  """Convert an opcode class name to a source string for the reason trail

  @type name: string
  @param name: the class name, as OpXxxYyy
  @rtype: string
  @return: the name in the OP_XXXX_YYYY format

  """
  if not name.startswith("Op"):
    return None
  return "%s:%s" % (constants.OPCODE_REASON_SRC_OPCODE,
                    "_".join(n.lower() for n in _NameComponents(name)))


def _GenerateObjectTypeCheck(obj, fields_types):
  """Helper to generate type checks for objects.

  @param obj: The object to generate type checks
  @param fields_types: The fields and their types as a dict
  @return: A ht type check function

  """
  assert set(obj.GetAllSlots()) == set(fields_types.keys()), \
    "%s != %s" % (set(obj.GetAllSlots()), set(fields_types.keys()))
  return ht.TStrictDict(True, True, fields_types)


_TQueryFieldDef = \
  _GenerateObjectTypeCheck(objects.QueryFieldDefinition, {
    "name": ht.TNonEmptyString,
    "title": ht.TNonEmptyString,
    "kind": ht.TElemOf(constants.QFT_ALL),
    "doc": ht.TNonEmptyString,
    })


def _BuildDiskTemplateCheck(accept_none):
  """Builds check for disk template.

  @type accept_none: bool
  @param accept_none: whether to accept None as a correct value
  @rtype: callable

  """
  template_check = ht.TElemOf(constants.DISK_TEMPLATES)

  if accept_none:
    template_check = ht.TMaybe(template_check)

  return template_check


def _CheckStorageType(storage_type):
  """Ensure a given storage type is valid.

  """
  if storage_type not in constants.STORAGE_TYPES:
    raise errors.OpPrereqError("Unknown storage type: %s" % storage_type,
                               errors.ECODE_INVAL)
  return True


#: Storage type parameter
_PStorageType = ("storage_type", ht.NoDefault, _CheckStorageType,
                 "Storage type")


@ht.WithDesc("IPv4 network")
def _CheckCIDRNetNotation(value):
  """Ensure a given CIDR notation type is valid.

  """
  try:
    ipaddr.IPv4Network(value)
  except ipaddr.AddressValueError:
    return False
  return True


@ht.WithDesc("IPv4 address")
def _CheckCIDRAddrNotation(value):
  """Ensure a given CIDR notation type is valid.

  """
  try:
    ipaddr.IPv4Address(value)
  except ipaddr.AddressValueError:
    return False
  return True


@ht.WithDesc("IPv6 address")
def _CheckCIDR6AddrNotation(value):
  """Ensure a given CIDR notation type is valid.

  """
  try:
    ipaddr.IPv6Address(value)
  except ipaddr.AddressValueError:
    return False
  return True


@ht.WithDesc("IPv6 network")
def _CheckCIDR6NetNotation(value):
  """Ensure a given CIDR notation type is valid.

  """
  try:
    ipaddr.IPv6Network(value)
  except ipaddr.AddressValueError:
    return False
  return True


_TIpAddress4 = ht.TAnd(ht.TString, _CheckCIDRAddrNotation)
_TIpAddress6 = ht.TAnd(ht.TString, _CheckCIDR6AddrNotation)
_TIpNetwork4 = ht.TAnd(ht.TString, _CheckCIDRNetNotation)
_TIpNetwork6 = ht.TAnd(ht.TString, _CheckCIDR6NetNotation)
_TMaybeAddr4List = ht.TMaybe(ht.TListOf(_TIpAddress4))


class _AutoOpParamSlots(outils.AutoSlots):
  """Meta class for opcode definitions.

  """
  def __new__(mcs, name, bases, attrs):
    """Called when a class should be created.

    @param mcs: The meta class
    @param name: Name of created class
    @param bases: Base classes
    @type attrs: dict
    @param attrs: Class attributes

    """
    assert "OP_ID" not in attrs, "Class '%s' defining OP_ID" % name

    slots = mcs._GetSlots(attrs)
    assert "OP_DSC_FIELD" not in attrs or attrs["OP_DSC_FIELD"] in slots, \
      "Class '%s' uses unknown field in OP_DSC_FIELD" % name
    assert ("OP_DSC_FORMATTER" not in attrs or
            callable(attrs["OP_DSC_FORMATTER"])), \
      ("Class '%s' uses non-callable in OP_DSC_FORMATTER (%s)" %
       (name, type(attrs["OP_DSC_FORMATTER"])))

    attrs["OP_ID"] = _NameToId(name)

    return outils.AutoSlots.__new__(mcs, name, bases, attrs)

  @classmethod
  def _GetSlots(mcs, attrs):
    """Build the slots out of OP_PARAMS.

    """
    # Always set OP_PARAMS to avoid duplicates in BaseOpCode.GetAllParams
    params = attrs.setdefault("OP_PARAMS", [])

    # Use parameter names as slots
    return [pname for (pname, _, _, _) in params]


class BaseOpCode(outils.ValidatedSlots):
  """A simple serializable object.

  This object serves as a parent class for OpCode without any custom
  field handling.

  """
  # pylint: disable=E1101
  # as OP_ID is dynamically defined
  __metaclass__ = _AutoOpParamSlots

  def __getstate__(self):
    """Generic serializer.

    This method just returns the contents of the instance as a
    dictionary.

    @rtype:  C{dict}
    @return: the instance attributes and their values

    """
    state = {}
    for name in self.GetAllSlots():
      if hasattr(self, name):
        state[name] = getattr(self, name)
    return state

  def __setstate__(self, state):
    """Generic unserializer.

    This method just restores from the serialized state the attributes
    of the current instance.

    @param state: the serialized opcode data
    @type state:  C{dict}

    """
    if not isinstance(state, dict):
      raise ValueError("Invalid data to __setstate__: expected dict, got %s" %
                       type(state))

    for name in self.GetAllSlots():
      if name not in state and hasattr(self, name):
        delattr(self, name)

    for name in state:
      setattr(self, name, state[name])

  @classmethod
  def GetAllParams(cls):
    """Compute list of all parameters for an opcode.

    """
    slots = []
    for parent in cls.__mro__:
      slots.extend(getattr(parent, "OP_PARAMS", []))
    return slots

  def Validate(self, set_defaults): # pylint: disable=W0221
    """Validate opcode parameters, optionally setting default values.

    @type set_defaults: bool
    @param set_defaults: Whether to set default values
    @raise errors.OpPrereqError: When a parameter value doesn't match
                                 requirements

    """
    for (attr_name, default, test, _) in self.GetAllParams():
      assert test == ht.NoType or callable(test)

      if not hasattr(self, attr_name):
        if default == ht.NoDefault:
          raise errors.OpPrereqError("Required parameter '%s.%s' missing" %
                                     (self.OP_ID, attr_name),
                                     errors.ECODE_INVAL)
        elif set_defaults:
          if callable(default):
            dval = default()
          else:
            dval = default
          setattr(self, attr_name, dval)

      if test == ht.NoType:
        # no tests here
        continue

      if set_defaults or hasattr(self, attr_name):
        attr_val = getattr(self, attr_name)
        if not test(attr_val):
          logging.error("OpCode %s, parameter %s, has invalid type %s/value"
                        " '%s' expecting type %s",
                        self.OP_ID, attr_name, type(attr_val), attr_val, test)
          raise errors.OpPrereqError("Parameter '%s.%s' fails validation" %
                                     (self.OP_ID, attr_name),
                                     errors.ECODE_INVAL)


def _BuildJobDepCheck(relative):
  """Builds check for job dependencies (L{DEPEND_ATTR}).

  @type relative: bool
  @param relative: Whether to accept relative job IDs (negative)
  @rtype: callable

  """
  if relative:
    job_id = ht.TOr(ht.TJobId, ht.TRelativeJobId)
  else:
    job_id = ht.TJobId

  job_dep = \
    ht.TAnd(ht.TOr(ht.TList, ht.TTuple),
            ht.TIsLength(2),
            ht.TItems([job_id,
                       ht.TListOf(ht.TElemOf(constants.JOBS_FINALIZED))]))

  return ht.TMaybeListOf(job_dep)


TNoRelativeJobDependencies = _BuildJobDepCheck(False)

#: List of submission status and job ID as returned by C{SubmitManyJobs}
_TJobIdListItem = \
  ht.TAnd(ht.TIsLength(2),
          ht.TItems([ht.Comment("success")(ht.TBool),
                     ht.Comment("Job ID if successful, error message"
                                " otherwise")(ht.TOr(ht.TString,
                                                     ht.TJobId))]))
TJobIdList = ht.TListOf(_TJobIdListItem)

#: Result containing only list of submitted jobs
TJobIdListOnly = ht.TStrictDict(True, True, {
  constants.JOB_IDS_KEY: ht.Comment("List of submitted jobs")(TJobIdList),
  })


class OpCode(BaseOpCode):
  """Abstract OpCode.

  This is the root of the actual OpCode hierarchy. All clases derived
  from this class should override OP_ID.

  @cvar OP_ID: The ID of this opcode. This should be unique amongst all
               children of this class.
  @cvar OP_DSC_FIELD: The name of a field whose value will be included in the
                      string returned by Summary(); see the docstring of that
                      method for details).
  @cvar OP_DSC_FORMATTER: A callable that should format the OP_DSC_FIELD; if
                          not present, then the field will be simply converted
                          to string
  @cvar OP_PARAMS: List of opcode attributes, the default values they should
                   get if not already defined, and types they must match.
  @cvar OP_RESULT: Callable to verify opcode result
  @cvar WITH_LU: Boolean that specifies whether this should be included in
      mcpu's dispatch table
  @ivar dry_run: Whether the LU should be run in dry-run mode, i.e. just
                 the check steps
  @ivar priority: Opcode priority for queue

  """
  # pylint: disable=E1101
  # as OP_ID is dynamically defined
  WITH_LU = True
  OP_PARAMS = [
    ("dry_run", None, ht.TMaybeBool, "Run checks only, don't execute"),
    ("debug_level", None, ht.TMaybe(ht.TNonNegativeInt), "Debug level"),
    ("priority", constants.OP_PRIO_DEFAULT,
     ht.TElemOf(constants.OP_PRIO_SUBMIT_VALID), "Opcode priority"),
    (DEPEND_ATTR, None, _BuildJobDepCheck(True),
     "Job dependencies; if used through ``SubmitManyJobs`` relative (negative)"
     " job IDs can be used; see :doc:`design document <design-chained-jobs>`"
     " for details"),
    (COMMENT_ATTR, None, ht.TMaybeString,
     "Comment describing the purpose of the opcode"),
    (constants.OPCODE_REASON, ht.EmptyList, ht.TMaybeList,
     "The reason trail, describing why the OpCode is executed"),
    ]
  OP_RESULT = None

  def __getstate__(self):
    """Specialized getstate for opcodes.

    This method adds to the state dictionary the OP_ID of the class,
    so that on unload we can identify the correct class for
    instantiating the opcode.

    @rtype:   C{dict}
    @return:  the state as a dictionary

    """
    data = BaseOpCode.__getstate__(self)
    data["OP_ID"] = self.OP_ID
    return data

  @classmethod
  def LoadOpCode(cls, data):
    """Generic load opcode method.

    The method identifies the correct opcode class from the dict-form
    by looking for a OP_ID key, if this is not found, or its value is
    not available in this module as a child of this class, we fail.

    @type data:  C{dict}
    @param data: the serialized opcode

    """
    if not isinstance(data, dict):
      raise ValueError("Invalid data to LoadOpCode (%s)" % type(data))
    if "OP_ID" not in data:
      raise ValueError("Invalid data to LoadOpcode, missing OP_ID")
    op_id = data["OP_ID"]
    op_class = None
    if op_id in OP_MAPPING:
      op_class = OP_MAPPING[op_id]
    else:
      raise ValueError("Invalid data to LoadOpCode: OP_ID %s unsupported" %
                       op_id)
    op = op_class()
    new_data = data.copy()
    del new_data["OP_ID"]
    op.__setstate__(new_data)
    return op

  def Summary(self):
    """Generates a summary description of this opcode.

    The summary is the value of the OP_ID attribute (without the "OP_"
    prefix), plus the value of the OP_DSC_FIELD attribute, if one was
    defined; this field should allow to easily identify the operation
    (for an instance creation job, e.g., it would be the instance
    name).

    """
    assert self.OP_ID is not None and len(self.OP_ID) > 3
    # all OP_ID start with OP_, we remove that
    txt = self.OP_ID[3:]
    field_name = getattr(self, "OP_DSC_FIELD", None)
    if field_name:
      field_value = getattr(self, field_name, None)
      field_formatter = getattr(self, "OP_DSC_FORMATTER", None)
      if callable(field_formatter):
        field_value = field_formatter(field_value)
      elif isinstance(field_value, (list, tuple)):
        field_value = ",".join(str(i) for i in field_value)
      txt = "%s(%s)" % (txt, field_value)
    return txt

  def TinySummary(self):
    """Generates a compact summary description of the opcode.

    """
    assert self.OP_ID.startswith("OP_")

    text = self.OP_ID[3:]

    for (prefix, supplement) in _SUMMARY_PREFIX.items():
      if text.startswith(prefix):
        return supplement + text[len(prefix):]

    return text


# cluster opcodes

class OpClusterPostInit(OpCode):
  """Post cluster initialization.

  This opcode does not touch the cluster at all. Its purpose is to run hooks
  after the cluster has been initialized.

  """
  OP_RESULT = ht.TBool


class OpClusterDestroy(OpCode):
  """Destroy the cluster.

  This opcode has no other parameters. All the state is irreversibly
  lost after the execution of this opcode.

  """
  OP_RESULT = ht.TNonEmptyString


class OpClusterQuery(OpCode):
  """Query cluster information."""
  OP_RESULT = ht.TDictOf(ht.TNonEmptyString, ht.TAny)


class OpClusterVerify(OpCode):
  """Submits all jobs necessary to verify the cluster.

  """
  OP_PARAMS = [
    _PDebugSimulateErrors,
    _PErrorCodes,
    _PSkipChecks,
    _PIgnoreErrors,
    _PVerbose,
    ("group_name", None, ht.TMaybeString, "Group to verify"),
    ]
  OP_RESULT = TJobIdListOnly


class OpClusterVerifyConfig(OpCode):
  """Verify the cluster config.

  """
  OP_PARAMS = [
    _PDebugSimulateErrors,
    _PErrorCodes,
    _PIgnoreErrors,
    _PVerbose,
    ]
  OP_RESULT = ht.TBool


class OpClusterVerifyGroup(OpCode):
  """Run verify on a node group from the cluster.

  @type skip_checks: C{list}
  @ivar skip_checks: steps to be skipped from the verify process; this
                     needs to be a subset of
                     L{constants.VERIFY_OPTIONAL_CHECKS}; currently
                     only L{constants.VERIFY_NPLUSONE_MEM} can be passed

  """
  OP_DSC_FIELD = "group_name"
  OP_PARAMS = [
    _PGroupName,
    _PDebugSimulateErrors,
    _PErrorCodes,
    _PSkipChecks,
    _PIgnoreErrors,
    _PVerbose,
    ]
  OP_RESULT = ht.TBool


class OpClusterVerifyDisks(OpCode):
  """Verify the cluster disks.

  """
  OP_RESULT = TJobIdListOnly


class OpGroupVerifyDisks(OpCode):
  """Verifies the status of all disks in a node group.

  Result: a tuple of three elements:
    - dict of node names with issues (values: error msg)
    - list of instances with degraded disks (that should be activated)
    - dict of instances with missing logical volumes (values: (node, vol)
      pairs with details about the missing volumes)

  In normal operation, all lists should be empty. A non-empty instance
  list (3rd element of the result) is still ok (errors were fixed) but
  non-empty node list means some node is down, and probably there are
  unfixable drbd errors.

  Note that only instances that are drbd-based are taken into
  consideration. This might need to be revisited in the future.

  """
  OP_DSC_FIELD = "group_name"
  OP_PARAMS = [
    _PGroupName,
    ]
  OP_RESULT = \
    ht.TAnd(ht.TIsLength(3),
            ht.TItems([ht.TDictOf(ht.TString, ht.TString),
                       ht.TListOf(ht.TString),
                       ht.TDictOf(ht.TString,
                                  ht.TListOf(ht.TListOf(ht.TString)))]))


class OpClusterRepairDiskSizes(OpCode):
  """Verify the disk sizes of the instances and fixes configuration
  mimatches.

  Parameters: optional instances list, in case we want to restrict the
  checks to only a subset of the instances.

  Result: a list of tuples, (instance, disk, parameter, new-size) for changed
  configurations.

  In normal operation, the list should be empty.

  @type instances: list
  @ivar instances: the list of instances to check, or empty for all instances

  """
  OP_PARAMS = [
    ("instances", ht.EmptyList, ht.TListOf(ht.TNonEmptyString), None),
    ]
  OP_RESULT = ht.TListOf(ht.TAnd(ht.TIsLength(4),
                                 ht.TItems([ht.TNonEmptyString,
                                            ht.TNonNegativeInt,
                                            ht.TNonEmptyString,
                                            ht.TNonNegativeInt])))


class OpClusterConfigQuery(OpCode):
  """Query cluster configuration values."""
  OP_PARAMS = [
    _POutputFields,
    ]
  OP_RESULT = ht.TListOf(ht.TAny)


class OpClusterRename(OpCode):
  """Rename the cluster.

  @type name: C{str}
  @ivar name: The new name of the cluster. The name and/or the master IP
              address will be changed to match the new name and its IP
              address.

  """
  OP_DSC_FIELD = "name"
  OP_PARAMS = [
    ("name", ht.NoDefault, ht.TNonEmptyString, None),
    ]
  OP_RESULT = ht.TNonEmptyString


class OpClusterSetParams(OpCode):
  """Change the parameters of the cluster.

  @type vg_name: C{str} or C{None}
  @ivar vg_name: The new volume group name or None to disable LVM usage.

  """
  OP_PARAMS = [
    _PForce,
    _PHvState,
    _PDiskState,
    ("vg_name", None, ht.TMaybe(ht.TString), "Volume group name"),
    ("enabled_hypervisors", None,
     ht.TMaybe(ht.TAnd(ht.TListOf(ht.TElemOf(constants.HYPER_TYPES)),
                       ht.TTrue)),
     "List of enabled hypervisors"),
    ("hvparams", None,
     ht.TMaybe(ht.TDictOf(ht.TNonEmptyString, ht.TDict)),
     "Cluster-wide hypervisor parameter defaults, hypervisor-dependent"),
    ("beparams", None, ht.TMaybeDict,
     "Cluster-wide backend parameter defaults"),
    ("os_hvp", None, ht.TMaybe(ht.TDictOf(ht.TNonEmptyString, ht.TDict)),
     "Cluster-wide per-OS hypervisor parameter defaults"),
    ("osparams", None,
     ht.TMaybe(ht.TDictOf(ht.TNonEmptyString, ht.TDict)),
     "Cluster-wide OS parameter defaults"),
    _PDiskParams,
    ("candidate_pool_size", None, ht.TMaybe(ht.TPositiveInt),
     "Master candidate pool size"),
    ("uid_pool", None, ht.NoType,
     "Set UID pool, must be list of lists describing UID ranges (two items,"
     " start and end inclusive)"),
    ("add_uids", None, ht.NoType,
     "Extend UID pool, must be list of lists describing UID ranges (two"
     " items, start and end inclusive) to be added"),
    ("remove_uids", None, ht.NoType,
     "Shrink UID pool, must be list of lists describing UID ranges (two"
     " items, start and end inclusive) to be removed"),
    ("maintain_node_health", None, ht.TMaybeBool,
     "Whether to automatically maintain node health"),
    ("prealloc_wipe_disks", None, ht.TMaybeBool,
     "Whether to wipe disks before allocating them to instances"),
    ("nicparams", None, ht.TMaybeDict, "Cluster-wide NIC parameter defaults"),
    ("ndparams", None, ht.TMaybeDict, "Cluster-wide node parameter defaults"),
    ("ipolicy", None, ht.TMaybeDict,
     "Cluster-wide :ref:`instance policy <rapi-ipolicy>` specs"),
    ("drbd_helper", None, ht.TMaybe(ht.TString), "DRBD helper program"),
    ("default_iallocator", None, ht.TMaybe(ht.TString),
     "Default iallocator for cluster"),
    ("master_netdev", None, ht.TMaybe(ht.TString),
     "Master network device"),
    ("master_netmask", None, ht.TMaybe(ht.TNonNegativeInt),
     "Netmask of the master IP"),
    ("reserved_lvs", None, ht.TMaybeListOf(ht.TNonEmptyString),
     "List of reserved LVs"),
    ("hidden_os", None, _TestClusterOsList,
     "Modify list of hidden operating systems: each modification must have"
     " two items, the operation and the OS name; the operation can be"
     " ``%s`` or ``%s``" % (constants.DDM_ADD, constants.DDM_REMOVE)),
    ("blacklisted_os", None, _TestClusterOsList,
     "Modify list of blacklisted operating systems: each modification must"
     " have two items, the operation and the OS name; the operation can be"
     " ``%s`` or ``%s``" % (constants.DDM_ADD, constants.DDM_REMOVE)),
    ("use_external_mip_script", None, ht.TMaybeBool,
     "Whether to use an external master IP address setup script"),
    ("enabled_disk_templates", None,
     ht.TMaybe(ht.TAnd(ht.TListOf(ht.TElemOf(constants.DISK_TEMPLATES)),
                       ht.TTrue)),
     "List of enabled disk templates"),
    ("modify_etc_hosts", None, ht.TMaybeBool,
     "Whether the cluster can modify and keep in sync the /etc/hosts files"),
    ("file_storage_dir", None, ht.TMaybe(ht.TString),
     "Default directory for storing file-backed disks"),
    ("shared_file_storage_dir", None, ht.TMaybe(ht.TString),
     "Default directory for storing shared-file-backed disks"),
    ]
  OP_RESULT = ht.TNone


class OpClusterRedistConf(OpCode):
  """Force a full push of the cluster configuration.

  """
  OP_RESULT = ht.TNone


class OpClusterActivateMasterIp(OpCode):
  """Activate the master IP on the master node.

  """
  OP_RESULT = ht.TNone


class OpClusterDeactivateMasterIp(OpCode):
  """Deactivate the master IP on the master node.

  """
  OP_RESULT = ht.TNone


class OpQuery(OpCode):
  """Query for resources/items.

  @ivar what: Resources to query for, must be one of L{constants.QR_VIA_OP}
  @ivar fields: List of fields to retrieve
  @ivar qfilter: Query filter

  """
  OP_DSC_FIELD = "what"
  OP_PARAMS = [
    _PQueryWhat,
    _PUseLocking,
    ("fields", ht.NoDefault, ht.TListOf(ht.TNonEmptyString),
     "Requested fields"),
    ("qfilter", None, ht.TMaybe(ht.TList),
     "Query filter"),
    ]
  OP_RESULT = \
    _GenerateObjectTypeCheck(objects.QueryResponse, {
      "fields": ht.TListOf(_TQueryFieldDef),
      "data": _TQueryResult,
      })


class OpQueryFields(OpCode):
  """Query for available resource/item fields.

  @ivar what: Resources to query for, must be one of L{constants.QR_VIA_OP}
  @ivar fields: List of fields to retrieve

  """
  OP_DSC_FIELD = "what"
  OP_PARAMS = [
    _PQueryWhat,
    ("fields", None, ht.TMaybeListOf(ht.TNonEmptyString),
     "Requested fields; if not given, all are returned"),
    ]
  OP_RESULT = \
    _GenerateObjectTypeCheck(objects.QueryFieldsResponse, {
      "fields": ht.TListOf(_TQueryFieldDef),
      })


class OpOobCommand(OpCode):
  """Interact with OOB."""
  OP_PARAMS = [
    ("node_names", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "List of node names to run the OOB command against"),
    ("node_uuids", None, ht.TMaybeListOf(ht.TNonEmptyString),
     "List of node UUIDs to run the OOB command against"),
    ("command", ht.NoDefault, ht.TElemOf(constants.OOB_COMMANDS),
     "OOB command to be run"),
    ("timeout", constants.OOB_TIMEOUT, ht.TInt,
     "Timeout before the OOB helper will be terminated"),
    ("ignore_status", False, ht.TBool,
     "Ignores the node offline status for power off"),
    ("power_delay", constants.OOB_POWER_DELAY, ht.TNonNegativeFloat,
     "Time in seconds to wait between powering on nodes"),
    ]
  # Fixme: Make it more specific with all the special cases in LUOobCommand
  OP_RESULT = _TQueryResult


class OpRestrictedCommand(OpCode):
  """Runs a restricted command on node(s).

  """
  OP_PARAMS = [
    _PUseLocking,
    ("nodes", ht.NoDefault, ht.TListOf(ht.TNonEmptyString),
     "Nodes on which the command should be run (at least one)"),
    ("node_uuids", None, ht.TMaybeListOf(ht.TNonEmptyString),
     "Node UUIDs on which the command should be run (at least one)"),
    ("command", ht.NoDefault, ht.TNonEmptyString,
     "Command name (no parameters)"),
    ]

  _RESULT_ITEMS = [
    ht.Comment("success")(ht.TBool),
    ht.Comment("output or error message")(ht.TString),
    ]

  OP_RESULT = \
    ht.TListOf(ht.TAnd(ht.TIsLength(len(_RESULT_ITEMS)),
                       ht.TItems(_RESULT_ITEMS)))


# node opcodes

class OpNodeRemove(OpCode):
  """Remove a node.

  @type node_name: C{str}
  @ivar node_name: The name of the node to remove. If the node still has
                   instances on it, the operation will fail.

  """
  OP_DSC_FIELD = "node_name"
  OP_PARAMS = [
    _PNodeName,
    _PNodeUuid
    ]
  OP_RESULT = ht.TNone


class OpNodeAdd(OpCode):
  """Add a node to the cluster.

  @type node_name: C{str}
  @ivar node_name: The name of the node to add. This can be a short name,
                   but it will be expanded to the FQDN.
  @type primary_ip: IP address
  @ivar primary_ip: The primary IP of the node. This will be ignored when the
                    opcode is submitted, but will be filled during the node
                    add (so it will be visible in the job query).
  @type secondary_ip: IP address
  @ivar secondary_ip: The secondary IP of the node. This needs to be passed
                      if the cluster has been initialized in 'dual-network'
                      mode, otherwise it must not be given.
  @type readd: C{bool}
  @ivar readd: Whether to re-add an existing node to the cluster. If
               this is not passed, then the operation will abort if the node
               name is already in the cluster; use this parameter to 'repair'
               a node that had its configuration broken, or was reinstalled
               without removal from the cluster.
  @type group: C{str}
  @ivar group: The node group to which this node will belong.
  @type vm_capable: C{bool}
  @ivar vm_capable: The vm_capable node attribute
  @type master_capable: C{bool}
  @ivar master_capable: The master_capable node attribute

  """
  OP_DSC_FIELD = "node_name"
  OP_PARAMS = [
    _PNodeName,
    _PHvState,
    _PDiskState,
    ("primary_ip", None, ht.NoType, "Primary IP address"),
    ("secondary_ip", None, ht.TMaybeString, "Secondary IP address"),
    ("readd", False, ht.TBool, "Whether node is re-added to cluster"),
    ("group", None, ht.TMaybeString, "Initial node group"),
    ("master_capable", None, ht.TMaybeBool,
     "Whether node can become master or master candidate"),
    ("vm_capable", None, ht.TMaybeBool,
     "Whether node can host instances"),
    ("ndparams", None, ht.TMaybeDict, "Node parameters"),
    ]
  OP_RESULT = ht.TNone


class OpNodeQuery(OpCode):
  """Compute the list of nodes."""
  OP_PARAMS = [
    _POutputFields,
    _PUseLocking,
    ("names", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Empty list to query all nodes, node names otherwise"),
    ]
  OP_RESULT = _TOldQueryResult


class OpNodeQueryvols(OpCode):
  """Get list of volumes on node."""
  OP_PARAMS = [
    _POutputFields,
    ("nodes", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Empty list to query all nodes, node names otherwise"),
    ]
  OP_RESULT = ht.TListOf(ht.TAny)


class OpNodeQueryStorage(OpCode):
  """Get information on storage for node(s)."""
  OP_PARAMS = [
    _POutputFields,
    _PStorageType,
    ("nodes", ht.EmptyList, ht.TListOf(ht.TNonEmptyString), "List of nodes"),
    ("name", None, ht.TMaybeString, "Storage name"),
    ]
  OP_RESULT = _TOldQueryResult


class OpNodeModifyStorage(OpCode):
  """Modifies the properies of a storage unit"""
  OP_DSC_FIELD = "node_name"
  OP_PARAMS = [
    _PNodeName,
    _PNodeUuid,
    _PStorageType,
    _PStorageName,
    ("changes", ht.NoDefault, ht.TDict, "Requested changes"),
    ]
  OP_RESULT = ht.TNone


class OpRepairNodeStorage(OpCode):
  """Repairs the volume group on a node."""
  OP_DSC_FIELD = "node_name"
  OP_PARAMS = [
    _PNodeName,
    _PNodeUuid,
    _PStorageType,
    _PStorageName,
    _PIgnoreConsistency,
    ]
  OP_RESULT = ht.TNone


class OpNodeSetParams(OpCode):
  """Change the parameters of a node."""
  OP_DSC_FIELD = "node_name"
  OP_PARAMS = [
    _PNodeName,
    _PNodeUuid,
    _PForce,
    _PHvState,
    _PDiskState,
    ("master_candidate", None, ht.TMaybeBool,
     "Whether the node should become a master candidate"),
    ("offline", None, ht.TMaybeBool,
     "Whether the node should be marked as offline"),
    ("drained", None, ht.TMaybeBool,
     "Whether the node should be marked as drained"),
    ("auto_promote", False, ht.TBool,
     "Whether node(s) should be promoted to master candidate if necessary"),
    ("master_capable", None, ht.TMaybeBool,
     "Denote whether node can become master or master candidate"),
    ("vm_capable", None, ht.TMaybeBool,
     "Denote whether node can host instances"),
    ("secondary_ip", None, ht.TMaybeString,
     "Change node's secondary IP address"),
    ("ndparams", None, ht.TMaybeDict, "Set node parameters"),
    ("powered", None, ht.TMaybeBool,
     "Whether the node should be marked as powered"),
    ]
  OP_RESULT = _TSetParamsResult


class OpNodePowercycle(OpCode):
  """Tries to powercycle a node."""
  OP_DSC_FIELD = "node_name"
  OP_PARAMS = [
    _PNodeName,
    _PNodeUuid,
    _PForce,
    ]
  OP_RESULT = ht.TMaybeString


class OpNodeMigrate(OpCode):
  """Migrate all instances from a node."""
  OP_DSC_FIELD = "node_name"
  OP_PARAMS = [
    _PNodeName,
    _PNodeUuid,
    _PMigrationMode,
    _PMigrationLive,
    _PMigrationTargetNode,
    _PMigrationTargetNodeUuid,
    _PAllowRuntimeChgs,
    _PIgnoreIpolicy,
    _PIAllocFromDesc("Iallocator for deciding the target node"
                     " for shared-storage instances"),
    ]
  OP_RESULT = TJobIdListOnly


class OpNodeEvacuate(OpCode):
  """Evacuate instances off a number of nodes."""
  OP_DSC_FIELD = "node_name"
  OP_PARAMS = [
    _PEarlyRelease,
    _PNodeName,
    _PNodeUuid,
    ("remote_node", None, ht.TMaybeString, "New secondary node"),
    ("remote_node_uuid", None, ht.TMaybeString, "New secondary node UUID"),
    _PIAllocFromDesc("Iallocator for computing solution"),
    ("mode", ht.NoDefault, ht.TElemOf(constants.NODE_EVAC_MODES),
     "Node evacuation mode"),
    ]
  OP_RESULT = TJobIdListOnly


# instance opcodes

class OpInstanceCreate(OpCode):
  """Create an instance.

  @ivar instance_name: Instance name
  @ivar mode: Instance creation mode (one of L{constants.INSTANCE_CREATE_MODES})
  @ivar source_handshake: Signed handshake from source (remote import only)
  @ivar source_x509_ca: Source X509 CA in PEM format (remote import only)
  @ivar source_instance_name: Previous name of instance (remote import only)
  @ivar source_shutdown_timeout: Shutdown timeout used for source instance
    (remote import only)

  """
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PForceVariant,
    _PWaitForSync,
    _PNameCheck,
    _PIgnoreIpolicy,
    _POpportunisticLocking,
    ("beparams", ht.EmptyDict, ht.TDict, "Backend parameters for instance"),
    ("disks", ht.NoDefault, ht.TListOf(_TDiskParams),
     "Disk descriptions, for example ``[{\"%s\": 100}, {\"%s\": 5}]``;"
     " each disk definition must contain a ``%s`` value and"
     " can contain an optional ``%s`` value denoting the disk access mode"
     " (%s)" %
     (constants.IDISK_SIZE, constants.IDISK_SIZE, constants.IDISK_SIZE,
      constants.IDISK_MODE,
      " or ".join("``%s``" % i for i in sorted(constants.DISK_ACCESS_SET)))),
    ("disk_template", ht.NoDefault, _BuildDiskTemplateCheck(True),
     "Disk template"),
    ("file_driver", None, ht.TMaybe(ht.TElemOf(constants.FILE_DRIVER)),
     "Driver for file-backed disks"),
    ("file_storage_dir", None, ht.TMaybeString,
     "Directory for storing file-backed disks"),
    ("hvparams", ht.EmptyDict, ht.TDict,
     "Hypervisor parameters for instance, hypervisor-dependent"),
    ("hypervisor", None, ht.TMaybeString, "Hypervisor"),
    _PIAllocFromDesc("Iallocator for deciding which node(s) to use"),
    ("identify_defaults", False, ht.TBool,
     "Reset instance parameters to default if equal"),
    ("ip_check", True, ht.TBool, _PIpCheckDoc),
    ("conflicts_check", True, ht.TBool, "Check for conflicting IPs"),
    ("mode", ht.NoDefault, ht.TElemOf(constants.INSTANCE_CREATE_MODES),
     "Instance creation mode"),
    ("nics", ht.NoDefault, ht.TListOf(_TestNicDef),
     "List of NIC (network interface) definitions, for example"
     " ``[{}, {}, {\"%s\": \"198.51.100.4\"}]``; each NIC definition can"
     " contain the optional values %s" %
     (constants.INIC_IP,
      ", ".join("``%s``" % i for i in sorted(constants.INIC_PARAMS)))),
    ("no_install", None, ht.TMaybeBool,
     "Do not install the OS (will disable automatic start)"),
    ("osparams", ht.EmptyDict, ht.TDict, "OS parameters for instance"),
    ("os_type", None, ht.TMaybeString, "Operating system"),
    ("pnode", None, ht.TMaybeString, "Primary node"),
    ("pnode_uuid", None, ht.TMaybeString, "Primary node UUID"),
    ("snode", None, ht.TMaybeString, "Secondary node"),
    ("snode_uuid", None, ht.TMaybeString, "Secondary node UUID"),
    ("source_handshake", None, ht.TMaybe(ht.TList),
     "Signed handshake from source (remote import only)"),
    ("source_instance_name", None, ht.TMaybeString,
     "Source instance name (remote import only)"),
    ("source_shutdown_timeout", constants.DEFAULT_SHUTDOWN_TIMEOUT,
     ht.TNonNegativeInt,
     "How long source instance was given to shut down (remote import only)"),
    ("source_x509_ca", None, ht.TMaybeString,
     "Source X509 CA in PEM format (remote import only)"),
    ("src_node", None, ht.TMaybeString, "Source node for import"),
    ("src_node_uuid", None, ht.TMaybeString, "Source node UUID for import"),
    ("src_path", None, ht.TMaybeString, "Source directory for import"),
    ("start", True, ht.TBool, "Whether to start instance after creation"),
    ("tags", ht.EmptyList, ht.TListOf(ht.TNonEmptyString), "Instance tags"),
    ]
  OP_RESULT = ht.Comment("instance nodes")(ht.TListOf(ht.TNonEmptyString))


class OpInstanceMultiAlloc(OpCode):
  """Allocates multiple instances.

  """
  OP_PARAMS = [
    _POpportunisticLocking,
    _PIAllocFromDesc("Iallocator used to allocate all the instances"),
    ("instances", ht.EmptyList, ht.TListOf(ht.TInstanceOf(OpInstanceCreate)),
     "List of instance create opcodes describing the instances to allocate"),
    ]
  _JOB_LIST = ht.Comment("List of submitted jobs")(TJobIdList)
  ALLOCATABLE_KEY = "allocatable"
  FAILED_KEY = "allocatable"
  OP_RESULT = ht.TStrictDict(True, True, {
    constants.JOB_IDS_KEY: _JOB_LIST,
    ALLOCATABLE_KEY: ht.TListOf(ht.TNonEmptyString),
    FAILED_KEY: ht.TListOf(ht.TNonEmptyString),
    })

  def __getstate__(self):
    """Generic serializer.

    """
    state = OpCode.__getstate__(self)
    if hasattr(self, "instances"):
      # pylint: disable=E1101
      state["instances"] = [inst.__getstate__() for inst in self.instances]
    return state

  def __setstate__(self, state):
    """Generic unserializer.

    This method just restores from the serialized state the attributes
    of the current instance.

    @param state: the serialized opcode data
    @type state: C{dict}

    """
    if not isinstance(state, dict):
      raise ValueError("Invalid data to __setstate__: expected dict, got %s" %
                       type(state))

    if "instances" in state:
      state["instances"] = map(OpCode.LoadOpCode, state["instances"])

    return OpCode.__setstate__(self, state)

  def Validate(self, set_defaults):
    """Validates this opcode.

    We do this recursively.

    """
    OpCode.Validate(self, set_defaults)

    for inst in self.instances: # pylint: disable=E1101
      inst.Validate(set_defaults)


class OpInstanceReinstall(OpCode):
  """Reinstall an instance's OS."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PForceVariant,
    ("os_type", None, ht.TMaybeString, "Instance operating system"),
    ("osparams", None, ht.TMaybeDict, "Temporary OS parameters"),
    ]
  OP_RESULT = ht.TNone


class OpInstanceRemove(OpCode):
  """Remove an instance."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PShutdownTimeout,
    ("ignore_failures", False, ht.TBool,
     "Whether to ignore failures during removal"),
    ]
  OP_RESULT = ht.TNone


class OpInstanceRename(OpCode):
  """Rename an instance."""
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PNameCheck,
    ("new_name", ht.NoDefault, ht.TNonEmptyString, "New instance name"),
    ("ip_check", False, ht.TBool, _PIpCheckDoc),
    ]
  OP_RESULT = ht.Comment("New instance name")(ht.TNonEmptyString)


class OpInstanceStartup(OpCode):
  """Startup an instance."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PForce,
    _PIgnoreOfflineNodes,
    ("hvparams", ht.EmptyDict, ht.TDict,
     "Temporary hypervisor parameters, hypervisor-dependent"),
    ("beparams", ht.EmptyDict, ht.TDict, "Temporary backend parameters"),
    _PNoRemember,
    _PStartupPaused,
    ]
  OP_RESULT = ht.TNone


class OpInstanceShutdown(OpCode):
  """Shutdown an instance."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PForce,
    _PIgnoreOfflineNodes,
    ("timeout", constants.DEFAULT_SHUTDOWN_TIMEOUT, ht.TNonNegativeInt,
     "How long to wait for instance to shut down"),
    _PNoRemember,
    ]
  OP_RESULT = ht.TNone


class OpInstanceReboot(OpCode):
  """Reboot an instance."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PShutdownTimeout,
    ("ignore_secondaries", False, ht.TBool,
     "Whether to start the instance even if secondary disks are failing"),
    ("reboot_type", ht.NoDefault, ht.TElemOf(constants.REBOOT_TYPES),
     "How to reboot instance"),
    ]
  OP_RESULT = ht.TNone


class OpInstanceReplaceDisks(OpCode):
  """Replace the disks of an instance."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PEarlyRelease,
    _PIgnoreIpolicy,
    ("mode", ht.NoDefault, ht.TElemOf(constants.REPLACE_MODES),
     "Replacement mode"),
    ("disks", ht.EmptyList, ht.TListOf(ht.TNonNegativeInt),
     "Disk indexes"),
    ("remote_node", None, ht.TMaybeString, "New secondary node"),
    ("remote_node_uuid", None, ht.TMaybeString, "New secondary node UUID"),
    _PIAllocFromDesc("Iallocator for deciding new secondary node"),
    ]
  OP_RESULT = ht.TNone


class OpInstanceFailover(OpCode):
  """Failover an instance."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PShutdownTimeout,
    _PIgnoreConsistency,
    _PMigrationTargetNode,
    _PMigrationTargetNodeUuid,
    _PIgnoreIpolicy,
    _PIAllocFromDesc("Iallocator for deciding the target node for"
                     " shared-storage instances"),
    ("cleanup", False, ht.TBool,
     "Whether a previously failed failover should be cleaned up"),
    ]
  OP_RESULT = ht.TNone


class OpInstanceMigrate(OpCode):
  """Migrate an instance.

  This migrates (without shutting down an instance) to its secondary
  node.

  @ivar instance_name: the name of the instance
  @ivar mode: the migration mode (live, non-live or None for auto)

  """
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PMigrationMode,
    _PMigrationLive,
    _PMigrationTargetNode,
    _PMigrationTargetNodeUuid,
    _PAllowRuntimeChgs,
    _PIgnoreIpolicy,
    ("cleanup", False, ht.TBool,
     "Whether a previously failed migration should be cleaned up"),
    _PIAllocFromDesc("Iallocator for deciding the target node for"
                     " shared-storage instances"),
    ("allow_failover", False, ht.TBool,
     "Whether we can fallback to failover if migration is not possible"),
    ]
  OP_RESULT = ht.TNone


class OpInstanceMove(OpCode):
  """Move an instance.

  This move (with shutting down an instance and data copying) to an
  arbitrary node.

  @ivar instance_name: the name of the instance
  @ivar target_node: the destination node

  """
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PShutdownTimeout,
    _PIgnoreIpolicy,
    ("target_node", ht.NoDefault, ht.TNonEmptyString, "Target node"),
    ("target_node_uuid", None, ht.TMaybeString, "Target node UUID"),
    _PIgnoreConsistency,
    ]
  OP_RESULT = ht.TNone


class OpInstanceConsole(OpCode):
  """Connect to an instance's console."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    ]
  OP_RESULT = ht.TDict


class OpInstanceActivateDisks(OpCode):
  """Activate an instance's disks."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    ("ignore_size", False, ht.TBool, "Whether to ignore recorded size"),
    _PWaitForSyncFalse,
    ]
  OP_RESULT = ht.TListOf(ht.TAnd(ht.TIsLength(3),
                                 ht.TItems([ht.TNonEmptyString,
                                            ht.TNonEmptyString,
                                            ht.TNonEmptyString])))


class OpInstanceDeactivateDisks(OpCode):
  """Deactivate an instance's disks."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PForce,
    ]
  OP_RESULT = ht.TNone


class OpInstanceRecreateDisks(OpCode):
  """Recreate an instance's disks."""
  _TDiskChanges = \
    ht.TAnd(ht.TIsLength(2),
            ht.TItems([ht.Comment("Disk index")(ht.TNonNegativeInt),
                       ht.Comment("Parameters")(_TDiskParams)]))

  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    ("disks", ht.EmptyList,
     ht.TOr(ht.TListOf(ht.TNonNegativeInt), ht.TListOf(_TDiskChanges)),
     "List of disk indexes (deprecated) or a list of tuples containing a disk"
     " index and a possibly empty dictionary with disk parameter changes"),
    ("nodes", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "New instance nodes, if relocation is desired"),
    ("node_uuids", None, ht.TMaybeListOf(ht.TNonEmptyString),
     "New instance node UUIDs, if relocation is desired"),
    _PIAllocFromDesc("Iallocator for deciding new nodes"),
    ]
  OP_RESULT = ht.TNone


class OpInstanceQuery(OpCode):
  """Compute the list of instances."""
  OP_PARAMS = [
    _POutputFields,
    _PUseLocking,
    ("names", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Empty list to query all instances, instance names otherwise"),
    ]
  OP_RESULT = _TOldQueryResult


class OpInstanceQueryData(OpCode):
  """Compute the run-time status of instances."""
  OP_PARAMS = [
    _PUseLocking,
    ("instances", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Instance names"),
    ("static", False, ht.TBool,
     "Whether to only return configuration data without querying"
     " nodes"),
    ]
  OP_RESULT = ht.TDictOf(ht.TNonEmptyString, ht.TDict)


def _TestInstSetParamsModList(fn):
  """Generates a check for modification lists.

  """
  # Old format
  # TODO: Remove in version 2.8 including support in LUInstanceSetParams
  old_mod_item_fn = \
    ht.TAnd(ht.TIsLength(2), ht.TItems([
      ht.TOr(ht.TElemOf(constants.DDMS_VALUES), ht.TNonNegativeInt),
      fn,
      ]))

  # New format, supporting adding/removing disks/NICs at arbitrary indices
  mod_item_fn = \
    ht.TAnd(ht.TIsLength(3), ht.TItems([
      ht.TElemOf(constants.DDMS_VALUES_WITH_MODIFY),
      ht.Comment("Device index, can be negative, e.g. -1 for last disk")
                 (ht.TOr(ht.TInt, ht.TString)),
      fn,
      ]))

  return ht.TOr(ht.Comment("Recommended")(ht.TListOf(mod_item_fn)),
                ht.Comment("Deprecated")(ht.TListOf(old_mod_item_fn)))


class OpInstanceSetParams(OpCode):
  """Change the parameters of an instance.

  """
  TestNicModifications = _TestInstSetParamsModList(_TestNicDef)
  TestDiskModifications = _TestInstSetParamsModList(_TDiskParams)

  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PForce,
    _PForceVariant,
    _PIgnoreIpolicy,
    ("nics", ht.EmptyList, TestNicModifications,
     "List of NIC changes: each item is of the form"
     " ``(op, identifier, settings)``, ``op`` is one of ``%s``, ``%s`` or"
     " ``%s``, ``identifier`` can be a zero-based index number (or -1 to refer"
     " to the last position), the NIC's UUID of the NIC's name; a"
     " deprecated version of this parameter used the form ``(op, settings)``,"
     " where ``op`` can be ``%s`` to add a new NIC with the specified"
     " settings, ``%s`` to remove the last NIC or a number to modify the"
     " settings of the NIC with that index" %
     (constants.DDM_ADD, constants.DDM_MODIFY, constants.DDM_REMOVE,
      constants.DDM_ADD, constants.DDM_REMOVE)),
    ("disks", ht.EmptyList, TestDiskModifications,
     "List of disk changes; see ``nics``"),
    ("beparams", ht.EmptyDict, ht.TDict, "Per-instance backend parameters"),
    ("runtime_mem", None, ht.TMaybePositiveInt, "New runtime memory"),
    ("hvparams", ht.EmptyDict, ht.TDict,
     "Per-instance hypervisor parameters, hypervisor-dependent"),
    ("disk_template", None, ht.TMaybe(_BuildDiskTemplateCheck(False)),
     "Disk template for instance"),
    ("pnode", None, ht.TMaybeString, "New primary node"),
    ("pnode_uuid", None, ht.TMaybeString, "New primary node UUID"),
    ("remote_node", None, ht.TMaybeString,
     "Secondary node (used when changing disk template)"),
    ("remote_node_uuid", None, ht.TMaybeString,
     "Secondary node UUID (used when changing disk template)"),
    ("os_name", None, ht.TMaybeString,
     "Change the instance's OS without reinstalling the instance"),
    ("osparams", None, ht.TMaybeDict, "Per-instance OS parameters"),
    ("wait_for_sync", True, ht.TBool,
     "Whether to wait for the disk to synchronize, when changing template"),
    ("offline", None, ht.TMaybeBool, "Whether to mark instance as offline"),
    ("conflicts_check", True, ht.TBool, "Check for conflicting IPs"),
    ]
  OP_RESULT = _TSetParamsResult


class OpInstanceGrowDisk(OpCode):
  """Grow a disk of an instance."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PWaitForSync,
    ("disk", ht.NoDefault, ht.TInt, "Disk index"),
    ("amount", ht.NoDefault, ht.TNonNegativeInt,
     "Amount of disk space to add (megabytes)"),
    ("absolute", False, ht.TBool,
     "Whether the amount parameter is an absolute target or a relative one"),
    ]
  OP_RESULT = ht.TNone


class OpInstanceChangeGroup(OpCode):
  """Moves an instance to another node group."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PEarlyRelease,
    _PIAllocFromDesc("Iallocator for computing solution"),
    _PTargetGroups,
    ]
  OP_RESULT = TJobIdListOnly


# Node group opcodes

class OpGroupAdd(OpCode):
  """Add a node group to the cluster."""
  OP_DSC_FIELD = "group_name"
  OP_PARAMS = [
    _PGroupName,
    _PNodeGroupAllocPolicy,
    _PGroupNodeParams,
    _PDiskParams,
    _PHvState,
    _PDiskState,
    ("ipolicy", None, ht.TMaybeDict,
     "Group-wide :ref:`instance policy <rapi-ipolicy>` specs"),
    ]
  OP_RESULT = ht.TNone


class OpGroupAssignNodes(OpCode):
  """Assign nodes to a node group."""
  OP_DSC_FIELD = "group_name"
  OP_PARAMS = [
    _PGroupName,
    _PForce,
    ("nodes", ht.NoDefault, ht.TListOf(ht.TNonEmptyString),
     "List of nodes to assign"),
    ("node_uuids", None, ht.TMaybeListOf(ht.TNonEmptyString),
     "List of node UUIDs to assign"),
    ]
  OP_RESULT = ht.TNone


class OpGroupQuery(OpCode):
  """Compute the list of node groups."""
  OP_PARAMS = [
    _POutputFields,
    ("names", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Empty list to query all groups, group names otherwise"),
    ]
  OP_RESULT = _TOldQueryResult


class OpGroupSetParams(OpCode):
  """Change the parameters of a node group."""
  OP_DSC_FIELD = "group_name"
  OP_PARAMS = [
    _PGroupName,
    _PNodeGroupAllocPolicy,
    _PGroupNodeParams,
    _PDiskParams,
    _PHvState,
    _PDiskState,
    ("ipolicy", None, ht.TMaybeDict, "Group-wide instance policy specs"),
    ]
  OP_RESULT = _TSetParamsResult


class OpGroupRemove(OpCode):
  """Remove a node group from the cluster."""
  OP_DSC_FIELD = "group_name"
  OP_PARAMS = [
    _PGroupName,
    ]
  OP_RESULT = ht.TNone


class OpGroupRename(OpCode):
  """Rename a node group in the cluster."""
  OP_PARAMS = [
    _PGroupName,
    ("new_name", ht.NoDefault, ht.TNonEmptyString, "New group name"),
    ]
  OP_RESULT = ht.Comment("New group name")(ht.TNonEmptyString)


class OpGroupEvacuate(OpCode):
  """Evacuate a node group in the cluster."""
  OP_DSC_FIELD = "group_name"
  OP_PARAMS = [
    _PGroupName,
    _PEarlyRelease,
    _PIAllocFromDesc("Iallocator for computing solution"),
    _PTargetGroups,
    ]
  OP_RESULT = TJobIdListOnly


# OS opcodes
class OpOsDiagnose(OpCode):
  """Compute the list of guest operating systems."""
  OP_PARAMS = [
    _POutputFields,
    ("names", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Which operating systems to diagnose"),
    ]
  OP_RESULT = _TOldQueryResult


# ExtStorage opcodes
class OpExtStorageDiagnose(OpCode):
  """Compute the list of external storage providers."""
  OP_PARAMS = [
    _POutputFields,
    ("names", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Which ExtStorage Provider to diagnose"),
    ]
  OP_RESULT = _TOldQueryResult


# Exports opcodes
class OpBackupQuery(OpCode):
  """Compute the list of exported images."""
  OP_PARAMS = [
    _PUseLocking,
    ("nodes", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Empty list to query all nodes, node names otherwise"),
    ]
  OP_RESULT = ht.TDictOf(ht.TNonEmptyString,
                         ht.TOr(ht.Comment("False on error")(ht.TBool),
                                ht.TListOf(ht.TNonEmptyString)))


class OpBackupPrepare(OpCode):
  """Prepares an instance export.

  @ivar instance_name: Instance name
  @ivar mode: Export mode (one of L{constants.EXPORT_MODES})

  """
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    ("mode", ht.NoDefault, ht.TElemOf(constants.EXPORT_MODES),
     "Export mode"),
    ]
  OP_RESULT = ht.TMaybeDict


class OpBackupExport(OpCode):
  """Export an instance.

  For local exports, the export destination is the node name. For
  remote exports, the export destination is a list of tuples, each
  consisting of hostname/IP address, port, magic, HMAC and HMAC
  salt. The HMAC is calculated using the cluster domain secret over
  the value "${index}:${hostname}:${port}". The destination X509 CA
  must be a signed certificate.

  @ivar mode: Export mode (one of L{constants.EXPORT_MODES})
  @ivar target_node: Export destination
  @ivar x509_key_name: X509 key to use (remote export only)
  @ivar destination_x509_ca: Destination X509 CA in PEM format (remote export
                             only)

  """
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    _PShutdownTimeout,
    # TODO: Rename target_node as it changes meaning for different export modes
    # (e.g. "destination")
    ("target_node", ht.NoDefault, ht.TOr(ht.TNonEmptyString, ht.TList),
     "Destination information, depends on export mode"),
    ("target_node_uuid", None, ht.TMaybeString,
     "Target node UUID (if local export)"),
    ("shutdown", True, ht.TBool, "Whether to shutdown instance before export"),
    ("remove_instance", False, ht.TBool,
     "Whether to remove instance after export"),
    ("ignore_remove_failures", False, ht.TBool,
     "Whether to ignore failures while removing instances"),
    ("mode", constants.EXPORT_MODE_LOCAL, ht.TElemOf(constants.EXPORT_MODES),
     "Export mode"),
    ("x509_key_name", None, ht.TMaybe(ht.TList),
     "Name of X509 key (remote export only)"),
    ("destination_x509_ca", None, ht.TMaybeString,
     "Destination X509 CA (remote export only)"),
    ]
  OP_RESULT = \
    ht.TAnd(ht.TIsLength(2), ht.TItems([
      ht.Comment("Finalizing status")(ht.TBool),
      ht.Comment("Status for every exported disk")(ht.TListOf(ht.TBool)),
      ]))


class OpBackupRemove(OpCode):
  """Remove an instance's export."""
  OP_DSC_FIELD = "instance_name"
  OP_PARAMS = [
    _PInstanceName,
    _PInstanceUuid,
    ]
  OP_RESULT = ht.TNone


# Tags opcodes
class OpTagsGet(OpCode):
  """Returns the tags of the given object."""
  OP_DSC_FIELD = "name"
  OP_PARAMS = [
    _PTagKind,
    # Not using _PUseLocking as the default is different for historical reasons
    ("use_locking", True, ht.TBool, "Whether to use synchronization"),
    # Name is only meaningful for nodes and instances
    ("name", ht.NoDefault, ht.TMaybeString,
     "Name of object to retrieve tags from"),
    ]
  OP_RESULT = ht.TListOf(ht.TNonEmptyString)


class OpTagsSearch(OpCode):
  """Searches the tags in the cluster for a given pattern."""
  OP_DSC_FIELD = "pattern"
  OP_PARAMS = [
    ("pattern", ht.NoDefault, ht.TNonEmptyString,
     "Search pattern (regular expression)"),
    ]
  OP_RESULT = ht.TListOf(ht.TAnd(ht.TIsLength(2), ht.TItems([
    ht.TNonEmptyString,
    ht.TNonEmptyString,
    ])))


class OpTagsSet(OpCode):
  """Add a list of tags on a given object."""
  OP_PARAMS = [
    _PTagKind,
    _PTags,
    # Name is only meaningful for groups, nodes and instances
    ("name", ht.NoDefault, ht.TMaybeString,
     "Name of object where tag(s) should be added"),
    ]
  OP_RESULT = ht.TNone


class OpTagsDel(OpCode):
  """Remove a list of tags from a given object."""
  OP_PARAMS = [
    _PTagKind,
    _PTags,
    # Name is only meaningful for groups, nodes and instances
    ("name", ht.NoDefault, ht.TMaybeString,
     "Name of object where tag(s) should be deleted"),
    ]
  OP_RESULT = ht.TNone


# Test opcodes
class OpTestDelay(OpCode):
  """Sleeps for a configured amount of time.

  This is used just for debugging and testing.

  Parameters:
    - duration: the time to sleep, in seconds
    - on_master: if true, sleep on the master
    - on_nodes: list of nodes in which to sleep

  If the on_master parameter is true, it will execute a sleep on the
  master (before any node sleep).

  If the on_nodes list is not empty, it will sleep on those nodes
  (after the sleep on the master, if that is enabled).

  As an additional feature, the case of duration < 0 will be reported
  as an execution error, so this opcode can be used as a failure
  generator. The case of duration == 0 will not be treated specially.

  """
  OP_DSC_FIELD = "duration"
  OP_PARAMS = [
    ("duration", ht.NoDefault, ht.TNumber, None),
    ("on_master", True, ht.TBool, None),
    ("on_nodes", ht.EmptyList, ht.TListOf(ht.TNonEmptyString), None),
    ("on_node_uuids", None, ht.TMaybeListOf(ht.TNonEmptyString), None),
    ("repeat", 0, ht.TNonNegativeInt, None),
    ]

  def OP_DSC_FORMATTER(self, value): # pylint: disable=C0103,R0201
    """Custom formatter for duration.

    """
    try:
      v = float(value)
    except TypeError:
      v = value
    return str(v)


class OpTestAllocator(OpCode):
  """Allocator framework testing.

  This opcode has two modes:
    - gather and return allocator input for a given mode (allocate new
      or replace secondary) and a given instance definition (direction
      'in')
    - run a selected allocator for a given operation (as above) and
      return the allocator output (direction 'out')

  """
  OP_DSC_FIELD = "iallocator"
  OP_PARAMS = [
    ("direction", ht.NoDefault,
     ht.TElemOf(constants.VALID_IALLOCATOR_DIRECTIONS), None),
    ("mode", ht.NoDefault, ht.TElemOf(constants.VALID_IALLOCATOR_MODES), None),
    ("name", ht.NoDefault, ht.TNonEmptyString, None),
    ("nics", ht.NoDefault,
     ht.TMaybeListOf(ht.TDictOf(ht.TElemOf([constants.INIC_MAC,
                                            constants.INIC_IP,
                                            "bridge"]),
                                ht.TMaybeString)),
     None),
    ("disks", ht.NoDefault, ht.TMaybe(ht.TList), None),
    ("hypervisor", None, ht.TMaybeString, None),
    _PIAllocFromDesc(None),
    ("tags", ht.EmptyList, ht.TListOf(ht.TNonEmptyString), None),
    ("memory", None, ht.TMaybe(ht.TNonNegativeInt), None),
    ("vcpus", None, ht.TMaybe(ht.TNonNegativeInt), None),
    ("os", None, ht.TMaybeString, None),
    ("disk_template", None, ht.TMaybeString, None),
    ("instances", None, ht.TMaybeListOf(ht.TNonEmptyString), None),
    ("evac_mode", None,
     ht.TMaybe(ht.TElemOf(constants.IALLOCATOR_NEVAC_MODES)), None),
    ("target_groups", None, ht.TMaybeListOf(ht.TNonEmptyString), None),
    ("spindle_use", 1, ht.TNonNegativeInt, None),
    ("count", 1, ht.TNonNegativeInt, None),
    ]


class OpTestJqueue(OpCode):
  """Utility opcode to test some aspects of the job queue.

  """
  OP_PARAMS = [
    ("notify_waitlock", False, ht.TBool, None),
    ("notify_exec", False, ht.TBool, None),
    ("log_messages", ht.EmptyList, ht.TListOf(ht.TString), None),
    ("fail", False, ht.TBool, None),
    ]


class OpTestDummy(OpCode):
  """Utility opcode used by unittests.

  """
  OP_PARAMS = [
    ("result", ht.NoDefault, ht.NoType, None),
    ("messages", ht.NoDefault, ht.NoType, None),
    ("fail", ht.NoDefault, ht.NoType, None),
    ("submit_jobs", None, ht.NoType, None),
    ]
  WITH_LU = False


# Network opcodes
# Add a new network in the cluster
class OpNetworkAdd(OpCode):
  """Add an IP network to the cluster."""
  OP_DSC_FIELD = "network_name"
  OP_PARAMS = [
    _PNetworkName,
    ("network", ht.NoDefault, _TIpNetwork4, "IPv4 subnet"),
    ("gateway", None, ht.TMaybe(_TIpAddress4), "IPv4 gateway"),
    ("network6", None, ht.TMaybe(_TIpNetwork6), "IPv6 subnet"),
    ("gateway6", None, ht.TMaybe(_TIpAddress6), "IPv6 gateway"),
    ("mac_prefix", None, ht.TMaybeString,
     "MAC address prefix that overrides cluster one"),
    ("add_reserved_ips", None, _TMaybeAddr4List,
     "Which IP addresses to reserve"),
    ("conflicts_check", True, ht.TBool,
     "Whether to check for conflicting IP addresses"),
    ("tags", ht.EmptyList, ht.TListOf(ht.TNonEmptyString), "Network tags"),
    ]
  OP_RESULT = ht.TNone


class OpNetworkRemove(OpCode):
  """Remove an existing network from the cluster.
     Must not be connected to any nodegroup.

  """
  OP_DSC_FIELD = "network_name"
  OP_PARAMS = [
    _PNetworkName,
    _PForce,
    ]
  OP_RESULT = ht.TNone


class OpNetworkSetParams(OpCode):
  """Modify Network's parameters except for IPv4 subnet"""
  OP_DSC_FIELD = "network_name"
  OP_PARAMS = [
    _PNetworkName,
    ("gateway", None, ht.TMaybeValueNone(_TIpAddress4), "IPv4 gateway"),
    ("network6", None, ht.TMaybeValueNone(_TIpNetwork6), "IPv6 subnet"),
    ("gateway6", None, ht.TMaybeValueNone(_TIpAddress6), "IPv6 gateway"),
    ("mac_prefix", None, ht.TMaybeValueNone(ht.TString),
     "MAC address prefix that overrides cluster one"),
    ("add_reserved_ips", None, _TMaybeAddr4List,
     "Which external IP addresses to reserve"),
    ("remove_reserved_ips", None, _TMaybeAddr4List,
     "Which external IP addresses to release"),
    ]
  OP_RESULT = ht.TNone


class OpNetworkConnect(OpCode):
  """Connect a Network to a specific Nodegroup with the defined netparams
     (mode, link). Nics in this Network will inherit those params.
     Produce errors if a NIC (that its not already assigned to a network)
     has an IP that is contained in the Network this will produce error unless
     --no-conflicts-check is passed.

  """
  OP_DSC_FIELD = "network_name"
  OP_PARAMS = [
    _PGroupName,
    _PNetworkName,
    ("network_mode", ht.NoDefault, ht.TElemOf(constants.NIC_VALID_MODES),
     "Connectivity mode"),
    ("network_link", ht.NoDefault, ht.TString, "Connectivity link"),
    ("conflicts_check", True, ht.TBool, "Whether to check for conflicting IPs"),
    ]
  OP_RESULT = ht.TNone


class OpNetworkDisconnect(OpCode):
  """Disconnect a Network from a Nodegroup. Produce errors if NICs are
     present in the Network unless --no-conficts-check option is passed.

  """
  OP_DSC_FIELD = "network_name"
  OP_PARAMS = [
    _PGroupName,
    _PNetworkName,
    ]
  OP_RESULT = ht.TNone


class OpNetworkQuery(OpCode):
  """Compute the list of networks."""
  OP_PARAMS = [
    _POutputFields,
    _PUseLocking,
    ("names", ht.EmptyList, ht.TListOf(ht.TNonEmptyString),
     "Empty list to query all groups, group names otherwise"),
    ]
  OP_RESULT = _TOldQueryResult


def _GetOpList():
  """Returns list of all defined opcodes.

  Does not eliminate duplicates by C{OP_ID}.

  """
  return [v for v in globals().values()
          if (isinstance(v, type) and issubclass(v, OpCode) and
              hasattr(v, "OP_ID") and v is not OpCode)]


OP_MAPPING = dict((v.OP_ID, v) for v in _GetOpList())
