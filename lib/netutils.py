#
#

# Copyright (C) 2010 Google Inc.
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


"""Ganeti network utility module.

This module holds functions that can be used in both daemons (all) and
the command line scripts.

"""


import errno
import re
import socket
import struct
import IN

from ganeti import constants
from ganeti import errors

# Structure definition for getsockopt(SOL_SOCKET, SO_PEERCRED, ...):
# struct ucred { pid_t pid; uid_t uid; gid_t gid; };
#
# The GNU C Library defines gid_t and uid_t to be "unsigned int" and
# pid_t to "int".
#
# IEEE Std 1003.1-2008:
# "nlink_t, uid_t, gid_t, and id_t shall be integer types"
# "blksize_t, pid_t, and ssize_t shall be signed integer types"
_STRUCT_UCRED = "iII"
_STRUCT_UCRED_SIZE = struct.calcsize(_STRUCT_UCRED)


def GetSocketCredentials(sock):
  """Returns the credentials of the foreign process connected to a socket.

  @param sock: Unix socket
  @rtype: tuple; (number, number, number)
  @return: The PID, UID and GID of the connected foreign process.

  """
  peercred = sock.getsockopt(socket.SOL_SOCKET, IN.SO_PEERCRED,
                             _STRUCT_UCRED_SIZE)
  return struct.unpack(_STRUCT_UCRED, peercred)


def GetHostInfo(name=None):
  """Lookup host name and raise an OpPrereqError for failures"""

  try:
    return HostInfo(name)
  except errors.ResolverError, err:
    raise errors.OpPrereqError("The given name (%s) does not resolve: %s" %
                               (err[0], err[2]), errors.ECODE_RESOLVER)


class HostInfo:
  """Class implementing resolver and hostname functionality

  """
  _VALID_NAME_RE = re.compile("^[a-z0-9._-]{1,255}$")

  def __init__(self, name=None):
    """Initialize the host name object.

    If the name argument is not passed, it will use this system's
    name.

    """
    if name is None:
      name = self.SysName()

    self.query = name
    self.name, self.aliases, self.ipaddrs = self.LookupHostname(name)
    self.ip = self.ipaddrs[0]

  def ShortName(self):
    """Returns the hostname without domain.

    """
    return self.name.split('.')[0]

  @staticmethod
  def SysName():
    """Return the current system's name.

    This is simply a wrapper over C{socket.gethostname()}.

    """
    return socket.gethostname()

  @staticmethod
  def LookupHostname(hostname):
    """Look up hostname

    @type hostname: str
    @param hostname: hostname to look up

    @rtype: tuple
    @return: a tuple (name, aliases, ipaddrs) as returned by
        C{socket.gethostbyname_ex}
    @raise errors.ResolverError: in case of errors in resolving

    """
    try:
      result = socket.gethostbyname_ex(hostname)
    except (socket.gaierror, socket.herror, socket.error), err:
      # hostname not found in DNS, or other socket exception in the
      # (code, description format)
      raise errors.ResolverError(hostname, err.args[0], err.args[1])

    return result

  @classmethod
  def NormalizeName(cls, hostname):
    """Validate and normalize the given hostname.

    @attention: the validation is a bit more relaxed than the standards
        require; most importantly, we allow underscores in names
    @raise errors.OpPrereqError: when the name is not valid

    """
    hostname = hostname.lower()
    if (not cls._VALID_NAME_RE.match(hostname) or
        # double-dots, meaning empty label
        ".." in hostname or
        # empty initial label
        hostname.startswith(".")):
      raise errors.OpPrereqError("Invalid hostname '%s'" % hostname,
                                 errors.ECODE_INVAL)
    if hostname.endswith("."):
      hostname = hostname.rstrip(".")
    return hostname


def _GenericIsValidIP(family, ip):
  """Generic internal version of ip validation.

  @type family: int
  @param family: socket.AF_INET | socket.AF_INET6
  @type ip: str
  @param ip: the address to be checked
  @rtype: boolean
  @return: True if ip is valid, False otherwise

  """
  try:
    socket.inet_pton(family, ip)
    return True
  except socket.error:
    return False


def IsValidIP4(ip):
  """Verifies an IPv4 address.

  This function checks if the given address is a valid IPv4 address.

  @type ip: str
  @param ip: the address to be checked
  @rtype: boolean
  @return: True if ip is valid, False otherwise

  """
  return _GenericIsValidIP(socket.AF_INET, ip)


def IsValidIP6(ip):
  """Verifies an IPv6 address.

  This function checks if the given address is a valid IPv6 address.

  @type ip: str
  @param ip: the address to be checked
  @rtype: boolean
  @return: True if ip is valid, False otherwise

  """
  return _GenericIsValidIP(socket.AF_INET6, ip)


def IsValidIP(ip):
  """Verifies an IP address.

  This function checks if the given IP address (both IPv4 and IPv6) is valid.

  @type ip: str
  @param ip: the address to be checked
  @rtype: boolean
  @return: True if ip is valid, False otherwise

  """
  return IsValidIP4(ip) or IsValidIP6(ip)


def GetAddressFamily(ip):
  """Get the address family of the given address.

  @type ip: str
  @param ip: ip address whose family will be returned
  @rtype: int
  @return: socket.AF_INET or socket.AF_INET6
  @raise errors.GenericError: for invalid addresses

  """
  if IsValidIP6(ip):
    return socket.AF_INET6
  elif IsValidIP4(ip):
    return socket.AF_INET
  else:
    raise errors.GenericError("Address %s not valid" % ip)


def TcpPing(target, port, timeout=10, live_port_needed=False, source=None):
  """Simple ping implementation using TCP connect(2).

  Check if the given IP is reachable by doing attempting a TCP connect
  to it.

  @type target: str
  @param target: the IP or hostname to ping
  @type port: int
  @param port: the port to connect to
  @type timeout: int
  @param timeout: the timeout on the connection attempt
  @type live_port_needed: boolean
  @param live_port_needed: whether a closed port will cause the
      function to return failure, as if there was a timeout
  @type source: str or None
  @param source: if specified, will cause the connect to be made
      from this specific source address; failures to bind other
      than C{EADDRNOTAVAIL} will be ignored

  """
  try:
    family = GetAddressFamily(target)
  except errors.GenericError:
    return False

  sock = socket.socket(family, socket.SOCK_STREAM)
  success = False

  if source is not None:
    try:
      sock.bind((source, 0))
    except socket.error, (errcode, _):
      if errcode == errno.EADDRNOTAVAIL:
        success = False

  sock.settimeout(timeout)

  try:
    sock.connect((target, port))
    sock.close()
    success = True
  except socket.timeout:
    success = False
  except socket.error, (errcode, _):
    success = (not live_port_needed) and (errcode == errno.ECONNREFUSED)

  return success


def OwnIpAddress(address):
  """Check if the current host has the the given IP address.

  This is done by trying to bind the given address. We return True if we
  succeed or false if a socket.error is raised.

  @type address: string
  @param address: the address to check
  @rtype: bool
  @return: True if we own the address

  """
  family = GetAddressFamily(address)
  s = socket.socket(family, socket.SOCK_DGRAM)
  success = False
  try:
    try:
      s.bind((address, 0))
      success = True
    except socket.error:
      success = False
  finally:
    s.close()
  return success


def GetDaemonPort(daemon_name):
  """Get the daemon port for this cluster.

  Note that this routine does not read a ganeti-specific file, but
  instead uses C{socket.getservbyname} to allow pre-customization of
  this parameter outside of Ganeti.

  @type daemon_name: string
  @param daemon_name: daemon name (in constants.DAEMONS_PORTS)
  @rtype: int

  """
  if daemon_name not in constants.DAEMONS_PORTS:
    raise errors.ProgrammerError("Unknown daemon: %s" % daemon_name)

  (proto, default_port) = constants.DAEMONS_PORTS[daemon_name]
  try:
    port = socket.getservbyname(daemon_name, proto)
  except socket.error:
    port = default_port

  return port
