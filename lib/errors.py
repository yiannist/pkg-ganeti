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


"""Ganeti exception handling"""


class GenericError(Exception):
  """Base exception for Ganeti.

  """
  pass


class LVMError(GenericError):
  """LVM-related exception.

  This exception codifies problems with LVM setup.

  """
  pass


class LockError(GenericError):
  """Lock error exception.

  This signifies problems in the locking subsystem.

  """
  pass


class HypervisorError(GenericError):
  """Hypervisor-related exception.

  This is raised in case we can't communicate with the hypervisor
  properly.

  """
  pass


class ProgrammerError(GenericError):
  """Programming-related error.

  This is raised in cases we determine that the calling conventions
  have been violated, meaning we got some desynchronisation between
  parts of our code. It signifies a real programming bug.

  """
  pass


class BlockDeviceError(GenericError):
  """Block-device related exception.

  This is raised in case we can't setup the instance's block devices
  properly.

  """
  pass


class ConfigurationError(GenericError):
  """Configuration related exception.

  Things like having an instance with a primary node that doesn't
  exist in the config or such raise this exception.

  """
  pass


class RemoteError(GenericError):
  """Programming-related error on remote call.

  This is raised when an unhandled error occurs in a call to a
  remote node.  It usually signifies a real programming bug.

  """
  pass


class InvalidOS(GenericError):
  """Missing OS on node.

  This is raised when an OS exists on the master (or is otherwise
  requested to the code) but not on the target node.

  This exception has three arguments:
    - the name of the os
    - the source directory, if any
    - the reason why we consider this an invalid OS (text of error message)

  """


class ParameterError(GenericError):
  """A passed parameter to a command is invalid.

  This is raised when the parameter passed to a request function is
  invalid. Correct code should have verified this before passing the
  request structure.

  The argument to this exception should be the parameter name.

  """
  pass


class OpPrereqError(GenericError):
  """Prerequisites for the OpCode are not fulfilled.

  """


class OpExecError(GenericError):
  """Error during OpCode execution.

  """


class OpRetryError(OpExecError):
  """Error during OpCode execution, action can be retried.

  """


class OpCodeUnknown(GenericError):
  """Unknown opcode submitted.

  This signifies a mismatch between the definitions on the client and
  server side.

  """


class JobLost(GenericError):
  """Submitted job lost.

  The job was submitted but it cannot be found in the current job
  list.

  """


class ResolverError(GenericError):
  """Host name cannot be resolved.

  This is not a normal situation for Ganeti, as we rely on having a
  working resolver.

  The non-resolvable hostname is available as the first element of the
  args tuple; the other two elements of the tuple are the first two
  args of the socket.gaierror exception (error code and description).

  """


class HooksFailure(GenericError):
  """A generic hook failure.

  This signifies usually a setup misconfiguration.

  """


class HooksAbort(HooksFailure):
  """A required hook has failed.

  This caused an abort of the operation in the initial phase. This
  exception always has an attribute args which is a list of tuples of:
    - node: the source node on which this hooks has failed
    - script: the name of the script which aborted the run

  """


class UnitParseError(GenericError):
  """Unable to parse size unit.

  """

class TypeEnforcementError(GenericError):
  """Unable to enforce data type.

  """

class SshKeyError(GenericError):
  """Invalid SSH key.

  """


class TagError(GenericError):
  """Generic tag error.

  The argument to this exception will show the exact error.

  """


class CommandError(GenericError):
  """External command error.

  """


class QuitGanetiException(Exception):
  """Signal that Ganeti that it must quit.

  This is not necessarily an error (and thus not a subclass of GenericError),
  but it's an exceptional circumstance and it is thus treated. This instance
  should be instantiated with two values. The first one will specify whether an
  error should returned to the caller, and the second one will be the returned
  result (either as an error or as a normal result).

  Examples::

    # Return a result of "True" to the caller, but quit ganeti afterwards
    raise QuitGanetiException(False, True)
    # Send an error to the caller, and quit ganeti
    raise QuitGanetiException(True, "Fatal safety violation, shutting down")

  """


class JobQueueError(GenericError):
  """Job queue error.

  """


class JobQueueDrainError(JobQueueError):
  """Job queue is marked for drain error.

  This is raised when a job submission attempt is made but the queue
  is marked for drain.

  """


class JobQueueFull(JobQueueError):
  """Job queue full error.

  Raised when job queue size reached its hard limit.

  """


# errors should be added above


def GetErrorClass(name):
  """Return the class of an exception.

  Given the class name, return the class itself.

  @type name: str
  @param name: the exception name
  @rtype: class
  @return: the actual class, or None if not found

  """
  item = globals().get(name, None)
  if item is not None:
    if not (isinstance(item, type(Exception)) and
            issubclass(item, GenericError)):
      item = None
  return item


def EncodeException(err):
  """Encodes an exception into a format that L{MaybeRaise} will recognise.

  The passed L{err} argument will be formatted as a tuple (exception
  name, arguments) that the MaybeRaise function will recognise.

  @type err: GenericError child
  @param err: usually a child of GenericError (but any exception
      will be accepted)
  @rtype: tuple
  @return: tuple of (exception name, exception arguments)

  """
  return (err.__class__.__name__, err.args)


def MaybeRaise(result):
  """If this looks like an encoded Ganeti exception, raise it.

  This function tries to parse the passed argument and if it looks
  like an encoding done by EncodeException, it will re-raise it.

  """
  tlt = (tuple, list)
  if (isinstance(result, tlt) and len(result) == 2 and
      isinstance(result[1], tlt)):
    # custom ganeti errors
    err_class = GetErrorClass(result[0])
    if err_class is not None:
      raise err_class, tuple(result[1])
