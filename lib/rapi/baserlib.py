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


"""Remote API base resources library.

"""

# pylint: disable-msg=C0103

# C0103: Invalid name, since the R_* names are not conforming

import logging

from ganeti import luxi
from ganeti import rapi
from ganeti import http
from ganeti import ssconf
from ganeti import constants
from ganeti import opcodes
from ganeti import errors


def BuildUriList(ids, uri_format, uri_fields=("name", "uri")):
  """Builds a URI list as used by index resources.

  @param ids: list of ids as strings
  @param uri_format: format to be applied for URI
  @param uri_fields: optional parameter for field IDs

  """
  (field_id, field_uri) = uri_fields

  def _MapId(m_id):
    return { field_id: m_id, field_uri: uri_format % m_id, }

  # Make sure the result is sorted, makes it nicer to look at and simplifies
  # unittests.
  ids.sort()

  return map(_MapId, ids)


def ExtractField(sequence, index):
  """Creates a list containing one column out of a list of lists.

  @param sequence: sequence of lists
  @param index: index of field

  """
  return map(lambda item: item[index], sequence)


def MapFields(names, data):
  """Maps two lists into one dictionary.

  Example::
      >>> MapFields(["a", "b"], ["foo", 123])
      {'a': 'foo', 'b': 123}

  @param names: field names (list of strings)
  @param data: field data (list)

  """
  if len(names) != len(data):
    raise AttributeError("Names and data must have the same length")
  return dict(zip(names, data))


def _Tags_GET(kind, name):
  """Helper function to retrieve tags.

  """
  if kind == constants.TAG_INSTANCE or kind == constants.TAG_NODE:
    if not name:
      raise http.HttpBadRequest("Missing name on tag request")
    cl = GetClient()
    if kind == constants.TAG_INSTANCE:
      fn = cl.QueryInstances
    else:
      fn = cl.QueryNodes
    result = fn(names=[name], fields=["tags"], use_locking=False)
    if not result or not result[0]:
      raise http.HttpBadGateway("Invalid response from tag query")
    tags = result[0][0]
  elif kind == constants.TAG_CLUSTER:
    ssc = ssconf.SimpleStore()
    tags = ssc.GetClusterTags()

  return list(tags)


def _Tags_PUT(kind, tags, name, dry_run):
  """Helper function to set tags.

  """
  return SubmitJob([opcodes.OpAddTags(kind=kind, name=name,
                                      tags=tags, dry_run=dry_run)])


def _Tags_DELETE(kind, tags, name, dry_run):
  """Helper function to delete tags.

  """
  return SubmitJob([opcodes.OpDelTags(kind=kind, name=name,
                                      tags=tags, dry_run=dry_run)])


def MapBulkFields(itemslist, fields):
  """Map value to field name in to one dictionary.

  @param itemslist: a list of items values
  @param fields: a list of items names

  @return: a list of mapped dictionaries

  """
  items_details = []
  for item in itemslist:
    mapped = MapFields(fields, item)
    items_details.append(mapped)
  return items_details


def MakeParamsDict(opts, params):
  """Makes params dictionary out of a option set.

  This function returns a dictionary needed for hv or be parameters. But only
  those fields which provided in the option set. Takes parameters frozensets
  from constants.

  @type opts: dict
  @param opts: selected options
  @type params: frozenset
  @param params: subset of options
  @rtype: dict
  @return: dictionary of options, filtered by given subset.

  """
  result = {}

  for p in params:
    try:
      value = opts[p]
    except KeyError:
      continue
    result[p] = value

  return result


def SubmitJob(op, cl=None):
  """Generic wrapper for submit job, for better http compatibility.

  @type op: list
  @param op: the list of opcodes for the job
  @type cl: None or luxi.Client
  @param cl: optional luxi client to use
  @rtype: string
  @return: the job ID

  """
  try:
    if cl is None:
      cl = GetClient()
    return cl.SubmitJob(op)
  except errors.JobQueueFull:
    raise http.HttpServiceUnavailable("Job queue is full, needs archiving")
  except errors.JobQueueDrainError:
    raise http.HttpServiceUnavailable("Job queue is drained, cannot submit")
  except luxi.NoMasterError, err:
    raise http.HttpBadGateway("Master seems to unreachable: %s" % str(err))
  except luxi.TimeoutError, err:
    raise http.HttpGatewayTimeout("Timeout while talking to the master"
                                  " daemon. Error: %s" % str(err))

def GetClient():
  """Geric wrapper for luxi.Client(), for better http compatiblity.

  """
  try:
    return luxi.Client()
  except luxi.NoMasterError, err:
    raise http.HttpBadGateway("Master seems to unreachable: %s" % str(err))


def FeedbackFn(ts, log_type, log_msg): # pylint: disable-msg=W0613
  """Feedback logging function for http case.

  We don't have a stdout for printing log messages, so log them to the
  http log at least.

  @param ts: the timestamp (unused)

  """
  logging.info("%s: %s", log_type, log_msg)


class R_Generic(object):
  """Generic class for resources.

  """
  # Default permission requirements
  GET_ACCESS = []
  PUT_ACCESS = [rapi.RAPI_ACCESS_WRITE]
  POST_ACCESS = [rapi.RAPI_ACCESS_WRITE]
  DELETE_ACCESS = [rapi.RAPI_ACCESS_WRITE]

  def __init__(self, items, queryargs, req):
    """Generic resource constructor.

    @param items: a list with variables encoded in the URL
    @param queryargs: a dictionary with additional options from URL

    """
    self.items = items
    self.queryargs = queryargs
    self.req = req
    self.sn = None

  def getSerialNumber(self):
    """Get Serial Number.

    """
    return self.sn

  def _checkIntVariable(self, name, default=0):
    """Return the parsed value of an int argument.

    """
    val = self.queryargs.get(name, default)
    if isinstance(val, list):
      if val:
        val = val[0]
      else:
        val = default
    try:
      val = int(val)
    except (ValueError, TypeError):
      raise http.HttpBadRequest("Invalid value for the"
                                " '%s' parameter" % (name,))
    return val

  def _checkStringVariable(self, name, default=None):
    """Return the parsed value of an int argument.

    """
    val = self.queryargs.get(name, default)
    if isinstance(val, list):
      if val:
        val = val[0]
      else:
        val = default
    return val

  def getBodyParameter(self, name, *args):
    """Check and return the value for a given parameter.

    If a second parameter is not given, an error will be returned,
    otherwise this parameter specifies the default value.

    @param name: the required parameter

    """
    if name in self.req.request_body:
      return self.req.request_body[name]
    elif args:
      return args[0]
    else:
      raise http.HttpBadRequest("Required parameter '%s' is missing" %
                                name)

  def useLocking(self):
    """Check if the request specifies locking.

    """
    return self._checkIntVariable('lock')

  def useBulk(self):
    """Check if the request specifies bulk querying.

    """
    return self._checkIntVariable('bulk')

  def useForce(self):
    """Check if the request specifies a forced operation.

    """
    return self._checkIntVariable('force')

  def dryRun(self):
    """Check if the request specifies dry-run mode.

    """
    return self._checkIntVariable('dry-run')
