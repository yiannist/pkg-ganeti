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


"""Ganeti small utilities

"""


import sys
import os
import sha
import time
import subprocess
import re
import socket
import tempfile
import shutil
import errno
import pwd
import itertools
import select
import fcntl
import resource

from cStringIO import StringIO

from ganeti import logger
from ganeti import errors
from ganeti import constants


_locksheld = []
_re_shell_unquoted = re.compile('^[-.,=:/_+@A-Za-z0-9]+$')

debug = False

class RunResult(object):
  """Simple class for holding the result of running external programs.

  Instance variables:
    exit_code: the exit code of the program, or None (if the program
               didn't exit())
    signal: numeric signal that caused the program to finish, or None
            (if the program wasn't terminated by a signal)
    stdout: the standard output of the program
    stderr: the standard error of the program
    failed: a Boolean value which is True in case the program was
            terminated by a signal or exited with a non-zero exit code
    fail_reason: a string detailing the termination reason

  """
  __slots__ = ["exit_code", "signal", "stdout", "stderr",
               "failed", "fail_reason", "cmd"]


  def __init__(self, exit_code, signal, stdout, stderr, cmd):
    self.cmd = cmd
    self.exit_code = exit_code
    self.signal = signal
    self.stdout = stdout
    self.stderr = stderr
    self.failed = (signal is not None or exit_code != 0)

    if self.signal is not None:
      self.fail_reason = "terminated by signal %s" % self.signal
    elif self.exit_code is not None:
      self.fail_reason = "exited with exit code %s" % self.exit_code
    else:
      self.fail_reason = "unable to determine termination reason"

    if debug and self.failed:
      logger.Debug("Command '%s' failed (%s); output: %s" %
                   (self.cmd, self.fail_reason, self.output))

  def _GetOutput(self):
    """Returns the combined stdout and stderr for easier usage.

    """
    return self.stdout + self.stderr

  output = property(_GetOutput, None, None, "Return full output")


def _GetLockFile(subsystem):
  """Compute the file name for a given lock name."""
  return "%s/ganeti_lock_%s" % (constants.LOCK_DIR, subsystem)


def Lock(name, max_retries=None, debug=False, autoclean=True):
  """Lock a given subsystem.

  In case the lock is already held by an alive process, the function
  will sleep indefintely and poll with a one second interval.

  When the optional integer argument 'max_retries' is passed with a
  non-zero value, the function will sleep only for this number of
  times, and then it will will raise a LockError if the lock can't be
  acquired. Passing in a negative number will cause only one try to
  get the lock. Passing a positive number will make the function retry
  for approximately that number of seconds.

  """
  lockfile = _GetLockFile(name)

  if name in _locksheld:
    raise errors.LockError('Lock "%s" already held!' % (name,))

  errcount = 0
  cleanupcount = 0

  retries = 0
  while True:
    try:
      fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_SYNC)
      break
    except OSError, creat_err:
      if creat_err.errno != errno.EEXIST:
        raise errors.LockError("Can't create the lock file. Error '%s'." %
                               str(creat_err))

      try:
        pf = open(lockfile, 'r')
      except IOError, open_err:
        errcount += 1
        if errcount >= 5:
          raise errors.LockError("Lock file exists but cannot be opened."
                                 " Error: '%s'." % str(open_err))
        time.sleep(1)
        continue

      try:
        pid = int(pf.read())
      except ValueError:
        raise errors.LockError("Invalid pid string in %s" %
                               (lockfile,))

      if not IsProcessAlive(pid):
        if autoclean:
          cleanupcount += 1
          if cleanupcount >= 5:
            raise errors.LockError, ("Too many stale lock cleanups! Check"
                                     " what process is dying.")
          logger.Error('Stale lockfile %s for pid %d, autocleaned.' %
                       (lockfile, pid))
          RemoveFile(lockfile)
          continue
        else:
          raise errors.LockError("Stale lockfile %s for pid %d?" %
                                 (lockfile, pid))

      if max_retries and max_retries <= retries:
        raise errors.LockError("Can't acquire lock during the specified"
                               " time, aborting.")
      if retries == 5 and (debug or sys.stdin.isatty()):
        logger.ToStderr("Waiting for '%s' lock from pid %d..." % (name, pid))

      time.sleep(1)
      retries += 1
      continue

  os.write(fd, '%d\n' % (os.getpid(),))
  os.close(fd)

  _locksheld.append(name)


def Unlock(name):
  """Unlock a given subsystem.

  """
  lockfile = _GetLockFile(name)

  try:
    fd = os.open(lockfile, os.O_RDONLY)
  except OSError:
    raise errors.LockError('Lock "%s" not held.' % (name,))

  f = os.fdopen(fd, 'r')
  pid_str = f.read()

  try:
    pid = int(pid_str)
  except ValueError:
    raise errors.LockError('Unable to determine PID of locking process.')

  if pid != os.getpid():
    raise errors.LockError('Lock not held by me (%d != %d)' %
                           (os.getpid(), pid,))

  os.unlink(lockfile)
  _locksheld.remove(name)


def LockCleanup():
  """Remove all locks.

  """
  for lock in _locksheld:
    Unlock(lock)


def RunCmd(cmd):
  """Execute a (shell) command.

  The command should not read from its standard input, as it will be
  closed.

  @param cmd: Command to run
  @type  cmd: string or list
  @return: `RunResult` instance
  @rtype: RunResult

  """
  if isinstance(cmd, list):
    cmd = [str(val) for val in cmd]
    strcmd = " ".join(cmd)
    shell = False
  else:
    strcmd = cmd
    shell = True
  env = os.environ.copy()
  env["LC_ALL"] = "C"
  poller = select.poll()
  child = subprocess.Popen(cmd, shell=shell,
                           stderr=subprocess.PIPE,
                           stdout=subprocess.PIPE,
                           stdin=subprocess.PIPE,
                           close_fds=True, env=env)

  child.stdin.close()
  poller.register(child.stdout, select.POLLIN)
  poller.register(child.stderr, select.POLLIN)
  out = StringIO()
  err = StringIO()
  fdmap = {
    child.stdout.fileno(): (out, child.stdout),
    child.stderr.fileno(): (err, child.stderr),
    }
  for fd in fdmap:
    status = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, status | os.O_NONBLOCK)

  while fdmap:
    for fd, event in poller.poll():
      if event & select.POLLIN or event & select.POLLPRI:
        data = fdmap[fd][1].read()
        # no data from read signifies EOF (the same as POLLHUP)
        if not data:
          poller.unregister(fd)
          del fdmap[fd]
          continue
        fdmap[fd][0].write(data)
      if (event & select.POLLNVAL or event & select.POLLHUP or
          event & select.POLLERR):
        poller.unregister(fd)
        del fdmap[fd]

  out = out.getvalue()
  err = err.getvalue()

  status = child.wait()
  if status >= 0:
    exitcode = status
    signal = None
  else:
    exitcode = None
    signal = -status

  return RunResult(exitcode, signal, out, err, strcmd)


def RunCmdUnlocked(cmd):
  """Execute a shell command without the 'cmd' lock.

  This variant of `RunCmd()` drops the 'cmd' lock before running the
  command and re-aquires it afterwards, thus it can be used to call
  other ganeti commands.

  The argument and return values are the same as for the `RunCmd()`
  function.

  Args:
    cmd - command to run. (str)

  Returns:
    `RunResult`

  """
  Unlock('cmd')
  ret = RunCmd(cmd)
  Lock('cmd')

  return ret


def RemoveFile(filename):
  """Remove a file ignoring some errors.

  Remove a file, ignoring non-existing ones or directories. Other
  errors are passed.

  """
  try:
    os.unlink(filename)
  except OSError, err:
    if err.errno not in (errno.ENOENT, errno.EISDIR):
      raise


def _FingerprintFile(filename):
  """Compute the fingerprint of a file.

  If the file does not exist, a None will be returned
  instead.

  Args:
    filename - Filename (str)

  """
  if not (os.path.exists(filename) and os.path.isfile(filename)):
    return None

  f = open(filename)

  fp = sha.sha()
  while True:
    data = f.read(4096)
    if not data:
      break

    fp.update(data)

  return fp.hexdigest()


def FingerprintFiles(files):
  """Compute fingerprints for a list of files.

  Args:
    files - array of filenames.  ( [str, ...] )

  Return value:
    dictionary of filename: fingerprint for the files that exist

  """
  ret = {}

  for filename in files:
    cksum = _FingerprintFile(filename)
    if cksum:
      ret[filename] = cksum

  return ret


def CheckDict(target, template, logname=None):
  """Ensure a dictionary has a required set of keys.

  For the given dictionaries `target` and `template`, ensure target
  has all the keys from template. Missing keys are added with values
  from template.

  Args:
    target   - the dictionary to check
    template - template dictionary
    logname  - a caller-chosen string to identify the debug log
               entry; if None, no logging will be done

  Returns value:
    None

  """
  missing = []
  for k in template:
    if k not in target:
      missing.append(k)
      target[k] = template[k]

  if missing and logname:
    logger.Debug('%s missing keys %s' %
                 (logname, ', '.join(missing)))


def IsProcessAlive(pid):
  """Check if a given pid exists on the system.

  Returns: true or false, depending on if the pid exists or not

  Remarks: zombie processes treated as not alive

  """
  try:
    f = open("/proc/%d/status" % pid)
  except IOError, err:
    if err.errno in (errno.ENOENT, errno.ENOTDIR):
      return False

  alive = True
  try:
    data = f.readlines()
    if len(data) > 1:
      state = data[1].split()
      if len(state) > 1 and state[1] == "Z":
        alive = False
  finally:
    f.close()

  return alive


def MatchNameComponent(key, name_list):
  """Try to match a name against a list.

  This function will try to match a name like test1 against a list
  like ['test1.example.com', 'test2.example.com', ...]. Against this
  list, 'test1' as well as 'test1.example' will match, but not
  'test1.ex'. A multiple match will be considered as no match at all
  (e.g. 'test1' against ['test1.example.com', 'test1.example.org']).

  Args:
    key: the name to be searched
    name_list: the list of strings against which to search the key

  Returns:
    None if there is no match *or* if there are multiple matches
    otherwise the element from the list which matches

  """
  mo = re.compile("^%s(\..*)?$" % re.escape(key))
  names_filtered = [name for name in name_list if mo.match(name) is not None]
  if len(names_filtered) != 1:
    return None
  return names_filtered[0]


class HostInfo:
  """Class implementing resolver and hostname functionality

  """
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

    This is simply a wrapper over socket.gethostname()

    """
    return socket.gethostname()

  @staticmethod
  def LookupHostname(hostname):
    """Look up hostname

    Args:
      hostname: hostname to look up

    Returns:
      a tuple (name, aliases, ipaddrs) as returned by socket.gethostbyname_ex
      in case of errors in resolving, we raise a ResolverError

    """
    try:
      result = socket.gethostbyname_ex(hostname)
    except socket.gaierror, err:
      # hostname not found in DNS
      raise errors.ResolverError(hostname, err.args[0], err.args[1])

    return result


def ListVolumeGroups():
  """List volume groups and their size

  Returns:
     Dictionary with keys volume name and values the size of the volume

  """
  command = "vgs --noheadings --units m --nosuffix -o name,size"
  result = RunCmd(command)
  retval = {}
  if result.failed:
    return retval

  for line in result.stdout.splitlines():
    try:
      name, size = line.split()
      size = int(float(size))
    except (IndexError, ValueError), err:
      logger.Error("Invalid output from vgs (%s): %s" % (err, line))
      continue

    retval[name] = size

  return retval


def BridgeExists(bridge):
  """Check whether the given bridge exists in the system

  Returns:
     True if it does, false otherwise.

  """
  return os.path.isdir("/sys/class/net/%s/bridge" % bridge)


def NiceSort(name_list):
  """Sort a list of strings based on digit and non-digit groupings.

  Given a list of names ['a1', 'a10', 'a11', 'a2'] this function will
  sort the list in the logical order ['a1', 'a2', 'a10', 'a11'].

  The sort algorithm breaks each name in groups of either only-digits
  or no-digits. Only the first eight such groups are considered, and
  after that we just use what's left of the string.

  Return value
    - a copy of the list sorted according to our algorithm

  """
  _SORTER_BASE = "(\D+|\d+)"
  _SORTER_FULL = "^%s%s?%s?%s?%s?%s?%s?%s?.*$" % (_SORTER_BASE, _SORTER_BASE,
                                                  _SORTER_BASE, _SORTER_BASE,
                                                  _SORTER_BASE, _SORTER_BASE,
                                                  _SORTER_BASE, _SORTER_BASE)
  _SORTER_RE = re.compile(_SORTER_FULL)
  _SORTER_NODIGIT = re.compile("^\D*$")
  def _TryInt(val):
    """Attempts to convert a variable to integer."""
    if val is None or _SORTER_NODIGIT.match(val):
      return val
    rval = int(val)
    return rval

  to_sort = [([_TryInt(grp) for grp in _SORTER_RE.match(name).groups()], name)
             for name in name_list]
  to_sort.sort()
  return [tup[1] for tup in to_sort]


def CheckDaemonAlive(pid_file, process_string):
  """Check wether the specified daemon is alive.

  Args:
   - pid_file: file to read the daemon pid from, the file is
               expected to contain only a single line containing
               only the PID
   - process_string: a substring that we expect to find in
                     the command line of the daemon process

  Returns:
   - True if the daemon is judged to be alive (that is:
      - the PID file exists, is readable and contains a number
      - a process of the specified PID is running
      - that process contains the specified string in its
        command line
      - the process is not in state Z (zombie))
   - False otherwise

  """
  try:
    pid_file = file(pid_file, 'r')
    try:
      pid = int(pid_file.readline())
    finally:
      pid_file.close()

    cmdline_file_path = "/proc/%s/cmdline" % (pid)
    cmdline_file = open(cmdline_file_path, 'r')
    try:
      cmdline = cmdline_file.readline()
    finally:
      cmdline_file.close()

    if not process_string in cmdline:
      return False

    stat_file_path =  "/proc/%s/stat" % (pid)
    stat_file = open(stat_file_path, 'r')
    try:
      process_state = stat_file.readline().split()[2]
    finally:
      stat_file.close()

    if process_state == 'Z':
      return False

  except (IndexError, IOError, ValueError):
    return False

  return True


def TryConvert(fn, val):
  """Try to convert a value ignoring errors.

  This function tries to apply function `fn` to `val`. If no
  ValueError or TypeError exceptions are raised, it will return the
  result, else it will return the original value. Any other exceptions
  are propagated to the caller.

  """
  try:
    nv = fn(val)
  except (ValueError, TypeError), err:
    nv = val
  return nv


def IsValidIP(ip):
  """Verifies the syntax of an IP address.

  This function checks if the ip address passes is valid or not based
  on syntax (not ip range, class calculations or anything).

  """
  unit = "(0|[1-9]\d{0,2})"
  return re.match("^%s\.%s\.%s\.%s$" % (unit, unit, unit, unit), ip)


def IsValidShellParam(word):
  """Verifies is the given word is safe from the shell's p.o.v.

  This means that we can pass this to a command via the shell and be
  sure that it doesn't alter the command line and is passed as such to
  the actual command.

  Note that we are overly restrictive here, in order to be on the safe
  side.

  """
  return bool(re.match("^[-a-zA-Z0-9._+/:%@]+$", word))


def BuildShellCmd(template, *args):
  """Build a safe shell command line from the given arguments.

  This function will check all arguments in the args list so that they
  are valid shell parameters (i.e. they don't contain shell
  metacharaters). If everything is ok, it will return the result of
  template % args.

  """
  for word in args:
    if not IsValidShellParam(word):
      raise errors.ProgrammerError("Shell argument '%s' contains"
                                   " invalid characters" % word)
  return template % args


def FormatUnit(value):
  """Formats an incoming number of MiB with the appropriate unit.

  Value needs to be passed as a numeric type. Return value is always a string.

  """
  if value < 1024:
    return "%dM" % round(value, 0)

  elif value < (1024 * 1024):
    return "%0.1fG" % round(float(value) / 1024, 1)

  else:
    return "%0.1fT" % round(float(value) / 1024 / 1024, 1)


def ParseUnit(input_string):
  """Tries to extract number and scale from the given string.

  Input must be in the format NUMBER+ [DOT NUMBER+] SPACE* [UNIT]. If no unit
  is specified, it defaults to MiB. Return value is always an int in MiB.

  """
  m = re.match('^([.\d]+)\s*([a-zA-Z]+)?$', input_string)
  if not m:
    raise errors.UnitParseError("Invalid format")

  value = float(m.groups()[0])

  unit = m.groups()[1]
  if unit:
    lcunit = unit.lower()
  else:
    lcunit = 'm'

  if lcunit in ('m', 'mb', 'mib'):
    # Value already in MiB
    pass

  elif lcunit in ('g', 'gb', 'gib'):
    value *= 1024

  elif lcunit in ('t', 'tb', 'tib'):
    value *= 1024 * 1024

  else:
    raise errors.UnitParseError("Unknown unit: %s" % unit)

  # Make sure we round up
  if int(value) < value:
    value += 1

  # Round up to the next multiple of 4
  value = int(value)
  if value % 4:
    value += 4 - value % 4

  return value


def AddAuthorizedKey(file_name, key):
  """Adds an SSH public key to an authorized_keys file.

  Args:
    file_name: Path to authorized_keys file
    key: String containing key
  """
  key_fields = key.split()

  f = open(file_name, 'a+')
  try:
    nl = True
    for line in f:
      # Ignore whitespace changes
      if line.split() == key_fields:
        break
      nl = line.endswith('\n')
    else:
      if not nl:
        f.write("\n")
      f.write(key.rstrip('\r\n'))
      f.write("\n")
      f.flush()
  finally:
    f.close()


def RemoveAuthorizedKey(file_name, key):
  """Removes an SSH public key from an authorized_keys file.

  Args:
    file_name: Path to authorized_keys file
    key: String containing key
  """
  key_fields = key.split()

  fd, tmpname = tempfile.mkstemp(dir=os.path.dirname(file_name))
  try:
    out = os.fdopen(fd, 'w')
    try:
      f = open(file_name, 'r')
      try:
        for line in f:
          # Ignore whitespace changes while comparing lines
          if line.split() != key_fields:
            out.write(line)

        out.flush()
        os.rename(tmpname, file_name)
      finally:
        f.close()
    finally:
      out.close()
  except:
    RemoveFile(tmpname)
    raise


def SetEtcHostsEntry(file_name, ip, hostname, aliases):
  """Sets the name of an IP address and hostname in /etc/hosts.

  """
  # Ensure aliases are unique
  aliases = UniqueSequence([hostname] + aliases)[1:]

  fd, tmpname = tempfile.mkstemp(dir=os.path.dirname(file_name))
  try:
    out = os.fdopen(fd, 'w')
    try:
      f = open(file_name, 'r')
      try:
        written = False
        for line in f:
          fields = line.split()
          if fields and not fields[0].startswith('#') and ip == fields[0]:
            continue
          out.write(line)

        out.write("%s\t%s" % (ip, hostname))
        if aliases:
          out.write(" %s" % ' '.join(aliases))
        out.write('\n')

        out.flush()
        os.fsync(out)
        os.rename(tmpname, file_name)
      finally:
        f.close()
    finally:
      out.close()
  except:
    RemoveFile(tmpname)
    raise


def RemoveEtcHostsEntry(file_name, hostname):
  """Removes a hostname from /etc/hosts.

  IP addresses without names are removed from the file.
  """
  fd, tmpname = tempfile.mkstemp(dir=os.path.dirname(file_name))
  try:
    out = os.fdopen(fd, 'w')
    try:
      f = open(file_name, 'r')
      try:
        for line in f:
          fields = line.split()
          if len(fields) > 1 and not fields[0].startswith('#'):
            names = fields[1:]
            if hostname in names:
              while hostname in names:
                names.remove(hostname)
              if names:
                out.write("%s %s\n" % (fields[0], ' '.join(names)))
              continue

          out.write(line)

        out.flush()
        os.fsync(out)
        os.rename(tmpname, file_name)
      finally:
        f.close()
    finally:
      out.close()
  except:
    RemoveFile(tmpname)
    raise


def CreateBackup(file_name):
  """Creates a backup of a file.

  Returns: the path to the newly created backup file.

  """
  if not os.path.isfile(file_name):
    raise errors.ProgrammerError("Can't make a backup of a non-file '%s'" %
                                file_name)

  prefix = '%s.backup-%d.' % (os.path.basename(file_name), int(time.time()))
  dir_name = os.path.dirname(file_name)

  fsrc = open(file_name, 'rb')
  try:
    (fd, backup_name) = tempfile.mkstemp(prefix=prefix, dir=dir_name)
    fdst = os.fdopen(fd, 'wb')
    try:
      shutil.copyfileobj(fsrc, fdst)
    finally:
      fdst.close()
  finally:
    fsrc.close()

  return backup_name


def ShellQuote(value):
  """Quotes shell argument according to POSIX.

  """
  if _re_shell_unquoted.match(value):
    return value
  else:
    return "'%s'" % value.replace("'", "'\\''")


def ShellQuoteArgs(args):
  """Quotes all given shell arguments and concatenates using spaces.

  """
  return ' '.join([ShellQuote(i) for i in args])


def TcpPing(target, port, timeout=10, live_port_needed=False, source=None):
  """Simple ping implementation using TCP connect(2).

  Try to do a TCP connect(2) from an optional source IP to the
  specified target IP and the specified target port. If the optional
  parameter live_port_needed is set to true, requires the remote end
  to accept the connection. The timeout is specified in seconds and
  defaults to 10 seconds. If the source optional argument is not
  passed, the source address selection is left to the kernel,
  otherwise we try to connect using the passed address (failures to
  bind other than EADDRNOTAVAIL will be ignored).

  """
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

  sucess = False

  if source is not None:
    try:
      sock.bind((source, 0))
    except socket.error, (errcode, errstring):
      if errcode == errno.EADDRNOTAVAIL:
        success = False

  sock.settimeout(timeout)

  try:
    sock.connect((target, port))
    sock.close()
    success = True
  except socket.timeout:
    success = False
  except socket.error, (errcode, errstring):
    success = (not live_port_needed) and (errcode == errno.ECONNREFUSED)

  return success


def ListVisibleFiles(path):
  """Returns a list of all visible files in a directory.

  """
  files = [i for i in os.listdir(path) if not i.startswith(".")]
  files.sort()
  return files


def GetHomeDir(user, default=None):
  """Try to get the homedir of the given user.

  The user can be passed either as a string (denoting the name) or as
  an integer (denoting the user id). If the user is not found, the
  'default' argument is returned, which defaults to None.

  """
  try:
    if isinstance(user, basestring):
      result = pwd.getpwnam(user)
    elif isinstance(user, (int, long)):
      result = pwd.getpwuid(user)
    else:
      raise errors.ProgrammerError("Invalid type passed to GetHomeDir (%s)" %
                                   type(user))
  except KeyError:
    return default
  return result.pw_dir


def NewUUID():
  """Returns a random UUID.

  """
  f = open("/proc/sys/kernel/random/uuid", "r")
  try:
    return f.read(128).rstrip("\n")
  finally:
    f.close()


def WriteFile(file_name, fn=None, data=None,
              mode=None, uid=-1, gid=-1,
              atime=None, mtime=None, close=True,
              prewrite=None, postwrite=None):
  """(Over)write a file atomically.

  The file_name and either fn (a function taking one argument, the
  file descriptor, and which should write the data to it) or data (the
  contents of the file) must be passed. The other arguments are
  optional and allow setting the file mode, owner and group, and the
  mtime/atime of the file.

  If the function doesn't raise an exception, it has succeeded and the
  target file has the new contents. If the file has raised an
  exception, an existing target file should be unmodified and the
  temporary file should be removed.

  Args:
    file_name: New filename
    fn: Content writing function, called with file descriptor as parameter
    data: Content as string
    mode: File mode
    uid: Owner
    gid: Group
    atime: Access time
    mtime: Modification time
    close: Whether to close file after writing it
    prewrite: Function object called before writing content
    postwrite: Function object called after writing content

  Returns:
    None if "close" parameter evaluates to True, otherwise file descriptor.

  """
  if not os.path.isabs(file_name):
    raise errors.ProgrammerError("Path passed to WriteFile is not"
                                 " absolute: '%s'" % file_name)

  if [fn, data].count(None) != 1:
    raise errors.ProgrammerError("fn or data required")

  if [atime, mtime].count(None) == 1:
    raise errors.ProgrammerError("Both atime and mtime must be either"
                                 " set or None")

  dir_name, base_name = os.path.split(file_name)
  fd, new_name = tempfile.mkstemp('.new', base_name, dir_name)
  # here we need to make sure we remove the temp file, if any error
  # leaves it in place
  try:
    if uid != -1 or gid != -1:
      os.chown(new_name, uid, gid)
    if mode:
      os.chmod(new_name, mode)
    if callable(prewrite):
      prewrite(fd)
    if data is not None:
      os.write(fd, data)
    else:
      fn(fd)
    if callable(postwrite):
      postwrite(fd)
    os.fsync(fd)
    if atime is not None and mtime is not None:
      os.utime(new_name, (atime, mtime))
    os.rename(new_name, file_name)
  finally:
    if close:
      os.close(fd)
      result = None
    else:
      result = fd
    RemoveFile(new_name)

  return result


def all(seq, pred=bool):
  "Returns True if pred(x) is True for every element in the iterable"
  for elem in itertools.ifilterfalse(pred, seq):
    return False
  return True


def any(seq, pred=bool):
  "Returns True if pred(x) is True for at least one element in the iterable"
  for elem in itertools.ifilter(pred, seq):
    return True
  return False


def UniqueSequence(seq):
  """Returns a list with unique elements.

  Element order is preserved.
  """
  seen = set()
  return [i for i in seq if i not in seen and not seen.add(i)]


def IsValidMac(mac):
  """Predicate to check if a MAC address is valid.

  Checks wether the supplied MAC address is formally correct, only
  accepts colon separated format.
  """
  mac_check = re.compile("^([0-9a-f]{2}(:|$)){6}$")
  return mac_check.match(mac) is not None


def TestDelay(duration):
  """Sleep for a fixed amount of time.

  """
  if duration < 0:
    return False
  time.sleep(duration)
  return True


def Daemonize(logfile, noclose_fds=None):
  """Daemonize the current process.

  This detaches the current process from the controlling terminal and
  runs it in the background as a daemon.

  """
  UMASK = 077
  WORKDIR = "/"
  # Default maximum for the number of available file descriptors.
  if 'SC_OPEN_MAX' in os.sysconf_names:
    try:
      MAXFD = os.sysconf('SC_OPEN_MAX')
      if MAXFD < 0:
        MAXFD = 1024
    except OSError:
      MAXFD = 1024
  else:
    MAXFD = 1024

  # this might fail
  pid = os.fork()
  if (pid == 0):  # The first child.
    os.setsid()
    # this might fail
    pid = os.fork() # Fork a second child.
    if (pid == 0):  # The second child.
      os.chdir(WORKDIR)
      os.umask(UMASK)
    else:
      # exit() or _exit()?  See below.
      os._exit(0) # Exit parent (the first child) of the second child.
  else:
    os._exit(0) # Exit parent of the first child.
  maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
  if (maxfd == resource.RLIM_INFINITY):
    maxfd = MAXFD

  # Iterate through and close all file descriptors.
  for fd in range(0, maxfd):
    if noclose_fds and fd in noclose_fds:
      continue
    try:
      os.close(fd)
    except OSError: # ERROR, fd wasn't open to begin with (ignored)
      pass
  os.open(logfile, os.O_RDWR|os.O_CREAT|os.O_APPEND, 0600)
  # Duplicate standard input to standard output and standard error.
  os.dup2(0, 1)     # standard output (1)
  os.dup2(0, 2)     # standard error (2)
  return 0


def FindFile(name, search_path, test=os.path.exists):
  """Look for a filesystem object in a given path.

  This is an abstract method to search for filesystem object (files,
  dirs) under a given search path.

  Args:
    - name: the name to look for
    - search_path: list of directory names
    - test: the test which the full path must satisfy
      (defaults to os.path.exists)

  Returns:
    - full path to the item if found
    - None otherwise

  """
  for dir_name in search_path:
    item_name = os.path.sep.join([dir_name, name])
    if test(item_name):
      return item_name
  return None
