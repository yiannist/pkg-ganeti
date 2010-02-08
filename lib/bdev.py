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


"""Block device abstraction"""

import re
import time
import errno
import pyparsing as pyp
import os
import logging

from ganeti import utils
from ganeti import errors
from ganeti import constants


def _IgnoreError(fn, *args, **kwargs):
  """Executes the given function, ignoring BlockDeviceErrors.

  This is used in order to simplify the execution of cleanup or
  rollback functions.

  @rtype: boolean
  @return: True when fn didn't raise an exception, False otherwise

  """
  try:
    fn(*args, **kwargs)
    return True
  except errors.BlockDeviceError, err:
    logging.warning("Caught BlockDeviceError but ignoring: %s" % str(err))
    return False


def _ThrowError(msg, *args):
  """Log an error to the node daemon and the raise an exception.

  @type msg: string
  @param msg: the text of the exception
  @raise errors.BlockDeviceError

  """
  if args:
    msg = msg % args
  logging.error(msg)
  raise errors.BlockDeviceError(msg)


class BlockDev(object):
  """Block device abstract class.

  A block device can be in the following states:
    - not existing on the system, and by `Create()` it goes into:
    - existing but not setup/not active, and by `Assemble()` goes into:
    - active read-write and by `Open()` it goes into
    - online (=used, or ready for use)

  A device can also be online but read-only, however we are not using
  the readonly state (LV has it, if needed in the future) and we are
  usually looking at this like at a stack, so it's easier to
  conceptualise the transition from not-existing to online and back
  like a linear one.

  The many different states of the device are due to the fact that we
  need to cover many device types:
    - logical volumes are created, lvchange -a y $lv, and used
    - drbd devices are attached to a local disk/remote peer and made primary

  A block device is identified by three items:
    - the /dev path of the device (dynamic)
    - a unique ID of the device (static)
    - it's major/minor pair (dynamic)

  Not all devices implement both the first two as distinct items. LVM
  logical volumes have their unique ID (the pair volume group, logical
  volume name) in a 1-to-1 relation to the dev path. For DRBD devices,
  the /dev path is again dynamic and the unique id is the pair (host1,
  dev1), (host2, dev2).

  You can get to a device in two ways:
    - creating the (real) device, which returns you
      an attached instance (lvcreate)
    - attaching of a python instance to an existing (real) device

  The second point, the attachement to a device, is different
  depending on whether the device is assembled or not. At init() time,
  we search for a device with the same unique_id as us. If found,
  good. It also means that the device is already assembled. If not,
  after assembly we'll have our correct major/minor.

  """
  def __init__(self, unique_id, children, size):
    self._children = children
    self.dev_path = None
    self.unique_id = unique_id
    self.major = None
    self.minor = None
    self.attached = False
    self.size = size

  def Assemble(self):
    """Assemble the device from its components.

    Implementations of this method by child classes must ensure that:
      - after the device has been assembled, it knows its major/minor
        numbers; this allows other devices (usually parents) to probe
        correctly for their children
      - calling this method on an existing, in-use device is safe
      - if the device is already configured (and in an OK state),
        this method is idempotent

    """
    pass

  def Attach(self):
    """Find a device which matches our config and attach to it.

    """
    raise NotImplementedError

  def Close(self):
    """Notifies that the device will no longer be used for I/O.

    """
    raise NotImplementedError

  @classmethod
  def Create(cls, unique_id, children, size):
    """Create the device.

    If the device cannot be created, it will return None
    instead. Error messages go to the logging system.

    Note that for some devices, the unique_id is used, and for other,
    the children. The idea is that these two, taken together, are
    enough for both creation and assembly (later).

    """
    raise NotImplementedError

  def Remove(self):
    """Remove this device.

    This makes sense only for some of the device types: LV and file
    storage. Also note that if the device can't attach, the removal
    can't be completed.

    """
    raise NotImplementedError

  def Rename(self, new_id):
    """Rename this device.

    This may or may not make sense for a given device type.

    """
    raise NotImplementedError

  def Open(self, force=False):
    """Make the device ready for use.

    This makes the device ready for I/O. For now, just the DRBD
    devices need this.

    The force parameter signifies that if the device has any kind of
    --force thing, it should be used, we know what we are doing.

    """
    raise NotImplementedError

  def Shutdown(self):
    """Shut down the device, freeing its children.

    This undoes the `Assemble()` work, except for the child
    assembling; as such, the children on the device are still
    assembled after this call.

    """
    raise NotImplementedError

  def SetSyncSpeed(self, speed):
    """Adjust the sync speed of the mirror.

    In case this is not a mirroring device, this is no-op.

    """
    result = True
    if self._children:
      for child in self._children:
        result = result and child.SetSyncSpeed(speed)
    return result

  def GetSyncStatus(self):
    """Returns the sync status of the device.

    If this device is a mirroring device, this function returns the
    status of the mirror.

    If sync_percent is None, it means the device is not syncing.

    If estimated_time is None, it means we can't estimate
    the time needed, otherwise it's the time left in seconds.

    If is_degraded is True, it means the device is missing
    redundancy. This is usually a sign that something went wrong in
    the device setup, if sync_percent is None.

    The ldisk parameter represents the degradation of the local
    data. This is only valid for some devices, the rest will always
    return False (not degraded).

    @rtype: tuple
    @return: (sync_percent, estimated_time, is_degraded, ldisk)

    """
    return None, None, False, False


  def CombinedSyncStatus(self):
    """Calculate the mirror status recursively for our children.

    The return value is the same as for `GetSyncStatus()` except the
    minimum percent and maximum time are calculated across our
    children.

    """
    min_percent, max_time, is_degraded, ldisk = self.GetSyncStatus()
    if self._children:
      for child in self._children:
        c_percent, c_time, c_degraded, c_ldisk = child.GetSyncStatus()
        if min_percent is None:
          min_percent = c_percent
        elif c_percent is not None:
          min_percent = min(min_percent, c_percent)
        if max_time is None:
          max_time = c_time
        elif c_time is not None:
          max_time = max(max_time, c_time)
        is_degraded = is_degraded or c_degraded
        ldisk = ldisk or c_ldisk
    return min_percent, max_time, is_degraded, ldisk


  def SetInfo(self, text):
    """Update metadata with info text.

    Only supported for some device types.

    """
    for child in self._children:
      child.SetInfo(text)

  def Grow(self, amount):
    """Grow the block device.

    @param amount: the amount (in mebibytes) to grow with

    """
    raise NotImplementedError

  def GetActualSize(self):
    """Return the actual disk size.

    @note: the device needs to be active when this is called

    """
    assert self.attached, "BlockDevice not attached in GetActualSize()"
    result = utils.RunCmd(["blockdev", "--getsize64", self.dev_path])
    if result.failed:
      _ThrowError("blockdev failed (%s): %s",
                  result.fail_reason, result.output)
    try:
      sz = int(result.output.strip())
    except (ValueError, TypeError), err:
      _ThrowError("Failed to parse blockdev output: %s", str(err))
    return sz

  def __repr__(self):
    return ("<%s: unique_id: %s, children: %s, %s:%s, %s>" %
            (self.__class__, self.unique_id, self._children,
             self.major, self.minor, self.dev_path))


class LogicalVolume(BlockDev):
  """Logical Volume block device.

  """
  def __init__(self, unique_id, children, size):
    """Attaches to a LV device.

    The unique_id is a tuple (vg_name, lv_name)

    """
    super(LogicalVolume, self).__init__(unique_id, children, size)
    if not isinstance(unique_id, (tuple, list)) or len(unique_id) != 2:
      raise ValueError("Invalid configuration data %s" % str(unique_id))
    self._vg_name, self._lv_name = unique_id
    self.dev_path = "/dev/%s/%s" % (self._vg_name, self._lv_name)
    self._degraded = True
    self.major = self.minor = self.pe_size = self.stripe_count = None
    self.Attach()

  @classmethod
  def Create(cls, unique_id, children, size):
    """Create a new logical volume.

    """
    if not isinstance(unique_id, (tuple, list)) or len(unique_id) != 2:
      raise errors.ProgrammerError("Invalid configuration data %s" %
                                   str(unique_id))
    vg_name, lv_name = unique_id
    pvs_info = cls.GetPVInfo(vg_name)
    if not pvs_info:
      _ThrowError("Can't compute PV info for vg %s", vg_name)
    pvs_info.sort()
    pvs_info.reverse()

    pvlist = [ pv[1] for pv in pvs_info ]
    free_size = sum([ pv[0] for pv in pvs_info ])
    current_pvs = len(pvlist)
    stripes = min(current_pvs, constants.LVM_STRIPECOUNT)

    # The size constraint should have been checked from the master before
    # calling the create function.
    if free_size < size:
      _ThrowError("Not enough free space: required %s,"
                  " available %s", size, free_size)
    cmd = ["lvcreate", "-L%dm" % size, "-n%s" % lv_name]
    # If the free space is not well distributed, we won't be able to
    # create an optimally-striped volume; in that case, we want to try
    # with N, N-1, ..., 2, and finally 1 (non-stripped) number of
    # stripes
    for stripes_arg in range(stripes, 0, -1):
      result = utils.RunCmd(cmd + ["-i%d" % stripes_arg] + [vg_name] + pvlist)
      if not result.failed:
        break
    if result.failed:
      _ThrowError("LV create failed (%s): %s",
                  result.fail_reason, result.output)
    return LogicalVolume(unique_id, children, size)

  @staticmethod
  def GetPVInfo(vg_name):
    """Get the free space info for PVs in a volume group.

    @param vg_name: the volume group name

    @rtype: list
    @return: list of tuples (free_space, name) with free_space in mebibytes

    """
    command = ["pvs", "--noheadings", "--nosuffix", "--units=m",
               "-opv_name,vg_name,pv_free,pv_attr", "--unbuffered",
               "--separator=:"]
    result = utils.RunCmd(command)
    if result.failed:
      logging.error("Can't get the PV information: %s - %s",
                    result.fail_reason, result.output)
      return None
    data = []
    for line in result.stdout.splitlines():
      fields = line.strip().split(':')
      if len(fields) != 4:
        logging.error("Can't parse pvs output: line '%s'", line)
        return None
      # skip over pvs from another vg or ones which are not allocatable
      if fields[1] != vg_name or fields[3][0] != 'a':
        continue
      data.append((float(fields[2]), fields[0]))

    return data

  def Remove(self):
    """Remove this logical volume.

    """
    if not self.minor and not self.Attach():
      # the LV does not exist
      return
    result = utils.RunCmd(["lvremove", "-f", "%s/%s" %
                           (self._vg_name, self._lv_name)])
    if result.failed:
      _ThrowError("Can't lvremove: %s - %s", result.fail_reason, result.output)

  def Rename(self, new_id):
    """Rename this logical volume.

    """
    if not isinstance(new_id, (tuple, list)) or len(new_id) != 2:
      raise errors.ProgrammerError("Invalid new logical id '%s'" % new_id)
    new_vg, new_name = new_id
    if new_vg != self._vg_name:
      raise errors.ProgrammerError("Can't move a logical volume across"
                                   " volume groups (from %s to to %s)" %
                                   (self._vg_name, new_vg))
    result = utils.RunCmd(["lvrename", new_vg, self._lv_name, new_name])
    if result.failed:
      _ThrowError("Failed to rename the logical volume: %s", result.output)
    self._lv_name = new_name
    self.dev_path = "/dev/%s/%s" % (self._vg_name, self._lv_name)

  def Attach(self):
    """Attach to an existing LV.

    This method will try to see if an existing and active LV exists
    which matches our name. If so, its major/minor will be
    recorded.

    """
    self.attached = False
    result = utils.RunCmd(["lvs", "--noheadings", "--separator=,",
                           "--units=m", "--nosuffix",
                           "-olv_attr,lv_kernel_major,lv_kernel_minor,"
                           "vg_extent_size,stripes", self.dev_path])
    if result.failed:
      logging.error("Can't find LV %s: %s, %s",
                    self.dev_path, result.fail_reason, result.output)
      return False
    # the output can (and will) have multiple lines for multi-segment
    # LVs, as the 'stripes' parameter is a segment one, so we take
    # only the last entry, which is the one we're interested in; note
    # that with LVM2 anyway the 'stripes' value must be constant
    # across segments, so this is a no-op actually
    out = result.stdout.splitlines()
    if not out: # totally empty result? splitlines() returns at least
                # one line for any non-empty string
      logging.error("Can't parse LVS output, no lines? Got '%s'", str(out))
      return False
    out = out[-1].strip().rstrip(',')
    out = out.split(",")
    if len(out) != 5:
      logging.error("Can't parse LVS output, len(%s) != 5", str(out))
      return False

    status, major, minor, pe_size, stripes = out
    if len(status) != 6:
      logging.error("lvs lv_attr is not 6 characters (%s)", status)
      return False

    try:
      major = int(major)
      minor = int(minor)
    except (TypeError, ValueError), err:
      logging.error("lvs major/minor cannot be parsed: %s", str(err))

    try:
      pe_size = int(float(pe_size))
    except (TypeError, ValueError), err:
      logging.error("Can't parse vg extent size: %s", err)
      return False

    try:
      stripes = int(stripes)
    except (TypeError, ValueError), err:
      logging.error("Can't parse the number of stripes: %s", err)
      return False

    self.major = major
    self.minor = minor
    self.pe_size = pe_size
    self.stripe_count = stripes
    self._degraded = status[0] == 'v' # virtual volume, i.e. doesn't backing
                                      # storage
    self.attached = True
    return True

  def Assemble(self):
    """Assemble the device.

    We always run `lvchange -ay` on the LV to ensure it's active before
    use, as there were cases when xenvg was not active after boot
    (also possibly after disk issues).

    """
    result = utils.RunCmd(["lvchange", "-ay", self.dev_path])
    if result.failed:
      _ThrowError("Can't activate lv %s: %s", self.dev_path, result.output)

  def Shutdown(self):
    """Shutdown the device.

    This is a no-op for the LV device type, as we don't deactivate the
    volumes on shutdown.

    """
    pass

  def GetSyncStatus(self):
    """Returns the sync status of the device.

    If this device is a mirroring device, this function returns the
    status of the mirror.

    For logical volumes, sync_percent and estimated_time are always
    None (no recovery in progress, as we don't handle the mirrored LV
    case). The is_degraded parameter is the inverse of the ldisk
    parameter.

    For the ldisk parameter, we check if the logical volume has the
    'virtual' type, which means it's not backed by existing storage
    anymore (read from it return I/O error). This happens after a
    physical disk failure and subsequent 'vgreduce --removemissing' on
    the volume group.

    The status was already read in Attach, so we just return it.

    @rtype: tuple
    @return: (sync_percent, estimated_time, is_degraded, ldisk)

    """
    return None, None, self._degraded, self._degraded

  def Open(self, force=False):
    """Make the device ready for I/O.

    This is a no-op for the LV device type.

    """
    pass

  def Close(self):
    """Notifies that the device will no longer be used for I/O.

    This is a no-op for the LV device type.

    """
    pass

  def Snapshot(self, size):
    """Create a snapshot copy of an lvm block device.

    """
    snap_name = self._lv_name + ".snap"

    # remove existing snapshot if found
    snap = LogicalVolume((self._vg_name, snap_name), None, size)
    _IgnoreError(snap.Remove)

    pvs_info = self.GetPVInfo(self._vg_name)
    if not pvs_info:
      _ThrowError("Can't compute PV info for vg %s", self._vg_name)
    pvs_info.sort()
    pvs_info.reverse()
    free_size, pv_name = pvs_info[0]
    if free_size < size:
      _ThrowError("Not enough free space: required %s,"
                  " available %s", size, free_size)

    result = utils.RunCmd(["lvcreate", "-L%dm" % size, "-s",
                           "-n%s" % snap_name, self.dev_path])
    if result.failed:
      _ThrowError("command: %s error: %s - %s",
                  result.cmd, result.fail_reason, result.output)

    return snap_name

  def SetInfo(self, text):
    """Update metadata with info text.

    """
    BlockDev.SetInfo(self, text)

    # Replace invalid characters
    text = re.sub('^[^A-Za-z0-9_+.]', '_', text)
    text = re.sub('[^-A-Za-z0-9_+.]', '_', text)

    # Only up to 128 characters are allowed
    text = text[:128]

    result = utils.RunCmd(["lvchange", "--addtag", text,
                           self.dev_path])
    if result.failed:
      _ThrowError("Command: %s error: %s - %s", result.cmd, result.fail_reason,
                  result.output)

  def Grow(self, amount):
    """Grow the logical volume.

    """
    if self.pe_size is None or self.stripe_count is None:
      if not self.Attach():
        _ThrowError("Can't attach to LV during Grow()")
    full_stripe_size = self.pe_size * self.stripe_count
    rest = amount % full_stripe_size
    if rest != 0:
      amount += full_stripe_size - rest
    # we try multiple algorithms since the 'best' ones might not have
    # space available in the right place, but later ones might (since
    # they have less constraints); also note that only recent LVM
    # supports 'cling'
    for alloc_policy in "contiguous", "cling", "normal":
      result = utils.RunCmd(["lvextend", "--alloc", alloc_policy,
                             "-L", "+%dm" % amount, self.dev_path])
      if not result.failed:
        return
    _ThrowError("Can't grow LV %s: %s", self.dev_path, result.output)


class DRBD8Status(object):
  """A DRBD status representation class.

  Note that this doesn't support unconfigured devices (cs:Unconfigured).

  """
  UNCONF_RE = re.compile(r"\s*[0-9]+:\s*cs:Unconfigured$")
  LINE_RE = re.compile(r"\s*[0-9]+:\s*cs:(\S+)\s+(?:st|ro):([^/]+)/(\S+)"
                       "\s+ds:([^/]+)/(\S+)\s+.*$")
  SYNC_RE = re.compile(r"^.*\ssync'ed:\s*([0-9.]+)%.*"
                       "\sfinish: ([0-9]+):([0-9]+):([0-9]+)\s.*$")

  CS_UNCONFIGURED = "Unconfigured"
  CS_STANDALONE = "StandAlone"
  CS_WFCONNECTION = "WFConnection"
  CS_WFREPORTPARAMS = "WFReportParams"
  CS_CONNECTED = "Connected"
  CS_STARTINGSYNCS = "StartingSyncS"
  CS_STARTINGSYNCT = "StartingSyncT"
  CS_WFBITMAPS = "WFBitMapS"
  CS_WFBITMAPT = "WFBitMapT"
  CS_WFSYNCUUID = "WFSyncUUID"
  CS_SYNCSOURCE = "SyncSource"
  CS_SYNCTARGET = "SyncTarget"
  CS_PAUSEDSYNCS = "PausedSyncS"
  CS_PAUSEDSYNCT = "PausedSyncT"
  CSET_SYNC = frozenset([
    CS_WFREPORTPARAMS,
    CS_STARTINGSYNCS,
    CS_STARTINGSYNCT,
    CS_WFBITMAPS,
    CS_WFBITMAPT,
    CS_WFSYNCUUID,
    CS_SYNCSOURCE,
    CS_SYNCTARGET,
    CS_PAUSEDSYNCS,
    CS_PAUSEDSYNCT,
    ])

  DS_DISKLESS = "Diskless"
  DS_ATTACHING = "Attaching" # transient state
  DS_FAILED = "Failed" # transient state, next: diskless
  DS_NEGOTIATING = "Negotiating" # transient state
  DS_INCONSISTENT = "Inconsistent" # while syncing or after creation
  DS_OUTDATED = "Outdated"
  DS_DUNKNOWN = "DUnknown" # shown for peer disk when not connected
  DS_CONSISTENT = "Consistent"
  DS_UPTODATE = "UpToDate" # normal state

  RO_PRIMARY = "Primary"
  RO_SECONDARY = "Secondary"
  RO_UNKNOWN = "Unknown"

  def __init__(self, procline):
    u = self.UNCONF_RE.match(procline)
    if u:
      self.cstatus = self.CS_UNCONFIGURED
      self.lrole = self.rrole = self.ldisk = self.rdisk = None
    else:
      m = self.LINE_RE.match(procline)
      if not m:
        raise errors.BlockDeviceError("Can't parse input data '%s'" % procline)
      self.cstatus = m.group(1)
      self.lrole = m.group(2)
      self.rrole = m.group(3)
      self.ldisk = m.group(4)
      self.rdisk = m.group(5)

    # end reading of data from the LINE_RE or UNCONF_RE

    self.is_standalone = self.cstatus == self.CS_STANDALONE
    self.is_wfconn = self.cstatus == self.CS_WFCONNECTION
    self.is_connected = self.cstatus == self.CS_CONNECTED
    self.is_primary = self.lrole == self.RO_PRIMARY
    self.is_secondary = self.lrole == self.RO_SECONDARY
    self.peer_primary = self.rrole == self.RO_PRIMARY
    self.peer_secondary = self.rrole == self.RO_SECONDARY
    self.both_primary = self.is_primary and self.peer_primary
    self.both_secondary = self.is_secondary and self.peer_secondary

    self.is_diskless = self.ldisk == self.DS_DISKLESS
    self.is_disk_uptodate = self.ldisk == self.DS_UPTODATE

    self.is_in_resync = self.cstatus in self.CSET_SYNC
    self.is_in_use = self.cstatus != self.CS_UNCONFIGURED

    m = self.SYNC_RE.match(procline)
    if m:
      self.sync_percent = float(m.group(1))
      hours = int(m.group(2))
      minutes = int(m.group(3))
      seconds = int(m.group(4))
      self.est_time = hours * 3600 + minutes * 60 + seconds
    else:
      # we have (in this if branch) no percent information, but if
      # we're resyncing we need to 'fake' a sync percent information,
      # as this is how cmdlib determines if it makes sense to wait for
      # resyncing or not
      if self.is_in_resync:
        self.sync_percent = 0
      else:
        self.sync_percent = None
      self.est_time = None


class BaseDRBD(BlockDev): # pylint: disable-msg=W0223
  """Base DRBD class.

  This class contains a few bits of common functionality between the
  0.7 and 8.x versions of DRBD.

  """
  _VERSION_RE = re.compile(r"^version: (\d+)\.(\d+)\.(\d+)"
                           r" \(api:(\d+)/proto:(\d+)(?:-(\d+))?\)")

  _DRBD_MAJOR = 147
  _ST_UNCONFIGURED = "Unconfigured"
  _ST_WFCONNECTION = "WFConnection"
  _ST_CONNECTED = "Connected"

  _STATUS_FILE = "/proc/drbd"

  @staticmethod
  def _GetProcData(filename=_STATUS_FILE):
    """Return data from /proc/drbd.

    """
    try:
      stat = open(filename, "r")
      try:
        data = stat.read().splitlines()
      finally:
        stat.close()
    except EnvironmentError, err:
      if err.errno == errno.ENOENT:
        _ThrowError("The file %s cannot be opened, check if the module"
                    " is loaded (%s)", filename, str(err))
      else:
        _ThrowError("Can't read the DRBD proc file %s: %s", filename, str(err))
    if not data:
      _ThrowError("Can't read any data from %s", filename)
    return data

  @staticmethod
  def _MassageProcData(data):
    """Transform the output of _GetProdData into a nicer form.

    @return: a dictionary of minor: joined lines from /proc/drbd
        for that minor

    """
    lmatch = re.compile("^ *([0-9]+):.*$")
    results = {}
    old_minor = old_line = None
    for line in data:
      if not line: # completely empty lines, as can be returned by drbd8.0+
        continue
      lresult = lmatch.match(line)
      if lresult is not None:
        if old_minor is not None:
          results[old_minor] = old_line
        old_minor = int(lresult.group(1))
        old_line = line
      else:
        if old_minor is not None:
          old_line += " " + line.strip()
    # add last line
    if old_minor is not None:
      results[old_minor] = old_line
    return results

  @classmethod
  def _GetVersion(cls):
    """Return the DRBD version.

    This will return a dict with keys:
      - k_major
      - k_minor
      - k_point
      - api
      - proto
      - proto2 (only on drbd > 8.2.X)

    """
    proc_data = cls._GetProcData()
    first_line = proc_data[0].strip()
    version = cls._VERSION_RE.match(first_line)
    if not version:
      raise errors.BlockDeviceError("Can't parse DRBD version from '%s'" %
                                    first_line)

    values = version.groups()
    retval = {'k_major': int(values[0]),
              'k_minor': int(values[1]),
              'k_point': int(values[2]),
              'api': int(values[3]),
              'proto': int(values[4]),
             }
    if values[5] is not None:
      retval['proto2'] = values[5]

    return retval

  @staticmethod
  def _DevPath(minor):
    """Return the path to a drbd device for a given minor.

    """
    return "/dev/drbd%d" % minor

  @classmethod
  def GetUsedDevs(cls):
    """Compute the list of used DRBD devices.

    """
    data = cls._GetProcData()

    used_devs = {}
    valid_line = re.compile("^ *([0-9]+): cs:([^ ]+).*$")
    for line in data:
      match = valid_line.match(line)
      if not match:
        continue
      minor = int(match.group(1))
      state = match.group(2)
      if state == cls._ST_UNCONFIGURED:
        continue
      used_devs[minor] = state, line

    return used_devs

  def _SetFromMinor(self, minor):
    """Set our parameters based on the given minor.

    This sets our minor variable and our dev_path.

    """
    if minor is None:
      self.minor = self.dev_path = None
      self.attached = False
    else:
      self.minor = minor
      self.dev_path = self._DevPath(minor)
      self.attached = True

  @staticmethod
  def _CheckMetaSize(meta_device):
    """Check if the given meta device looks like a valid one.

    This currently only check the size, which must be around
    128MiB.

    """
    result = utils.RunCmd(["blockdev", "--getsize", meta_device])
    if result.failed:
      _ThrowError("Failed to get device size: %s - %s",
                  result.fail_reason, result.output)
    try:
      sectors = int(result.stdout)
    except (TypeError, ValueError):
      _ThrowError("Invalid output from blockdev: '%s'", result.stdout)
    bytes = sectors * 512
    if bytes < 128 * 1024 * 1024: # less than 128MiB
      _ThrowError("Meta device too small (%.2fMib)", (bytes / 1024 / 1024))
    # the maximum *valid* size of the meta device when living on top
    # of LVM is hard to compute: it depends on the number of stripes
    # and the PE size; e.g. a 2-stripe, 64MB PE will result in a 128MB
    # (normal size), but an eight-stripe 128MB PE will result in a 1GB
    # size meta device; as such, we restrict it to 1GB (a little bit
    # too generous, but making assumptions about PE size is hard)
    if bytes > 1024 * 1024 * 1024:
      _ThrowError("Meta device too big (%.2fMiB)", (bytes / 1024 / 1024))

  def Rename(self, new_id):
    """Rename a device.

    This is not supported for drbd devices.

    """
    raise errors.ProgrammerError("Can't rename a drbd device")


class DRBD8(BaseDRBD):
  """DRBD v8.x block device.

  This implements the local host part of the DRBD device, i.e. it
  doesn't do anything to the supposed peer. If you need a fully
  connected DRBD pair, you need to use this class on both hosts.

  The unique_id for the drbd device is the (local_ip, local_port,
  remote_ip, remote_port) tuple, and it must have two children: the
  data device and the meta_device. The meta device is checked for
  valid size and is zeroed on create.

  """
  _MAX_MINORS = 255
  _PARSE_SHOW = None

  # timeout constants
  _NET_RECONFIG_TIMEOUT = 60

  def __init__(self, unique_id, children, size):
    if children and children.count(None) > 0:
      children = []
    super(DRBD8, self).__init__(unique_id, children, size)
    self.major = self._DRBD_MAJOR
    version = self._GetVersion()
    if version['k_major'] != 8 :
      _ThrowError("Mismatch in DRBD kernel version and requested ganeti"
                  " usage: kernel is %s.%s, ganeti wants 8.x",
                  version['k_major'], version['k_minor'])

    if len(children) not in (0, 2):
      raise ValueError("Invalid configuration data %s" % str(children))
    if not isinstance(unique_id, (tuple, list)) or len(unique_id) != 6:
      raise ValueError("Invalid configuration data %s" % str(unique_id))
    (self._lhost, self._lport,
     self._rhost, self._rport,
     self._aminor, self._secret) = unique_id
    if (self._lhost is not None and self._lhost == self._rhost and
        self._lport == self._rport):
      raise ValueError("Invalid configuration data, same local/remote %s" %
                       (unique_id,))
    self.Attach()

  @classmethod
  def _InitMeta(cls, minor, dev_path):
    """Initialize a meta device.

    This will not work if the given minor is in use.

    """
    result = utils.RunCmd(["drbdmeta", "--force", cls._DevPath(minor),
                           "v08", dev_path, "0", "create-md"])
    if result.failed:
      _ThrowError("Can't initialize meta device: %s", result.output)

  @classmethod
  def _FindUnusedMinor(cls):
    """Find an unused DRBD device.

    This is specific to 8.x as the minors are allocated dynamically,
    so non-existing numbers up to a max minor count are actually free.

    """
    data = cls._GetProcData()

    unused_line = re.compile("^ *([0-9]+): cs:Unconfigured$")
    used_line = re.compile("^ *([0-9]+): cs:")
    highest = None
    for line in data:
      match = unused_line.match(line)
      if match:
        return int(match.group(1))
      match = used_line.match(line)
      if match:
        minor = int(match.group(1))
        highest = max(highest, minor)
    if highest is None: # there are no minors in use at all
      return 0
    if highest >= cls._MAX_MINORS:
      logging.error("Error: no free drbd minors!")
      raise errors.BlockDeviceError("Can't find a free DRBD minor")
    return highest + 1

  @classmethod
  def _GetShowParser(cls):
    """Return a parser for `drbd show` output.

    This will either create or return an already-create parser for the
    output of the command `drbd show`.

    """
    if cls._PARSE_SHOW is not None:
      return cls._PARSE_SHOW

    # pyparsing setup
    lbrace = pyp.Literal("{").suppress()
    rbrace = pyp.Literal("}").suppress()
    semi = pyp.Literal(";").suppress()
    # this also converts the value to an int
    number = pyp.Word(pyp.nums).setParseAction(lambda s, l, t: int(t[0]))

    comment = pyp.Literal ("#") + pyp.Optional(pyp.restOfLine)
    defa = pyp.Literal("_is_default").suppress()
    dbl_quote = pyp.Literal('"').suppress()

    keyword = pyp.Word(pyp.alphanums + '-')

    # value types
    value = pyp.Word(pyp.alphanums + '_-/.:')
    quoted = dbl_quote + pyp.CharsNotIn('"') + dbl_quote
    addr_type = (pyp.Optional(pyp.Literal("ipv4")).suppress() +
                 pyp.Optional(pyp.Literal("ipv6")).suppress())
    addr_port = (addr_type + pyp.Word(pyp.nums + '.') +
                 pyp.Literal(':').suppress() + number)
    # meta device, extended syntax
    meta_value = ((value ^ quoted) + pyp.Literal('[').suppress() +
                  number + pyp.Word(']').suppress())
    # device name, extended syntax
    device_value = pyp.Literal("minor").suppress() + number

    # a statement
    stmt = (~rbrace + keyword + ~lbrace +
            pyp.Optional(addr_port ^ value ^ quoted ^ meta_value ^
                         device_value) +
            pyp.Optional(defa) + semi +
            pyp.Optional(pyp.restOfLine).suppress())

    # an entire section
    section_name = pyp.Word(pyp.alphas + '_')
    section = section_name + lbrace + pyp.ZeroOrMore(pyp.Group(stmt)) + rbrace

    bnf = pyp.ZeroOrMore(pyp.Group(section ^ stmt))
    bnf.ignore(comment)

    cls._PARSE_SHOW = bnf

    return bnf

  @classmethod
  def _GetShowData(cls, minor):
    """Return the `drbdsetup show` data for a minor.

    """
    result = utils.RunCmd(["drbdsetup", cls._DevPath(minor), "show"])
    if result.failed:
      logging.error("Can't display the drbd config: %s - %s",
                    result.fail_reason, result.output)
      return None
    return result.stdout

  @classmethod
  def _GetDevInfo(cls, out):
    """Parse details about a given DRBD minor.

    This return, if available, the local backing device (as a path)
    and the local and remote (ip, port) information from a string
    containing the output of the `drbdsetup show` command as returned
    by _GetShowData.

    """
    data = {}
    if not out:
      return data

    bnf = cls._GetShowParser()
    # run pyparse

    try:
      results = bnf.parseString(out)
    except pyp.ParseException, err:
      _ThrowError("Can't parse drbdsetup show output: %s", str(err))

    # and massage the results into our desired format
    for section in results:
      sname = section[0]
      if sname == "_this_host":
        for lst in section[1:]:
          if lst[0] == "disk":
            data["local_dev"] = lst[1]
          elif lst[0] == "meta-disk":
            data["meta_dev"] = lst[1]
            data["meta_index"] = lst[2]
          elif lst[0] == "address":
            data["local_addr"] = tuple(lst[1:])
      elif sname == "_remote_host":
        for lst in section[1:]:
          if lst[0] == "address":
            data["remote_addr"] = tuple(lst[1:])
    return data

  def _MatchesLocal(self, info):
    """Test if our local config matches with an existing device.

    The parameter should be as returned from `_GetDevInfo()`. This
    method tests if our local backing device is the same as the one in
    the info parameter, in effect testing if we look like the given
    device.

    """
    if self._children:
      backend, meta = self._children
    else:
      backend = meta = None

    if backend is not None:
      retval = ("local_dev" in info and info["local_dev"] == backend.dev_path)
    else:
      retval = ("local_dev" not in info)

    if meta is not None:
      retval = retval and ("meta_dev" in info and
                           info["meta_dev"] == meta.dev_path)
      retval = retval and ("meta_index" in info and
                           info["meta_index"] == 0)
    else:
      retval = retval and ("meta_dev" not in info and
                           "meta_index" not in info)
    return retval

  def _MatchesNet(self, info):
    """Test if our network config matches with an existing device.

    The parameter should be as returned from `_GetDevInfo()`. This
    method tests if our network configuration is the same as the one
    in the info parameter, in effect testing if we look like the given
    device.

    """
    if (((self._lhost is None and not ("local_addr" in info)) and
         (self._rhost is None and not ("remote_addr" in info)))):
      return True

    if self._lhost is None:
      return False

    if not ("local_addr" in info and
            "remote_addr" in info):
      return False

    retval = (info["local_addr"] == (self._lhost, self._lport))
    retval = (retval and
              info["remote_addr"] == (self._rhost, self._rport))
    return retval

  @classmethod
  def _AssembleLocal(cls, minor, backend, meta, size):
    """Configure the local part of a DRBD device.

    """
    args = ["drbdsetup", cls._DevPath(minor), "disk",
            backend, meta, "0",
            "-e", "detach",
            "--create-device"]
    if size:
      args.extend(["-d", "%sm" % size])
    result = utils.RunCmd(args)
    if result.failed:
      _ThrowError("drbd%d: can't attach local disk: %s", minor, result.output)

  @classmethod
  def _AssembleNet(cls, minor, net_info, protocol,
                   dual_pri=False, hmac=None, secret=None):
    """Configure the network part of the device.

    """
    lhost, lport, rhost, rport = net_info
    if None in net_info:
      # we don't want network connection and actually want to make
      # sure its shutdown
      cls._ShutdownNet(minor)
      return

    # Workaround for a race condition. When DRBD is doing its dance to
    # establish a connection with its peer, it also sends the
    # synchronization speed over the wire. In some cases setting the
    # sync speed only after setting up both sides can race with DRBD
    # connecting, hence we set it here before telling DRBD anything
    # about its peer.
    cls._SetMinorSyncSpeed(minor, constants.SYNC_SPEED)

    args = ["drbdsetup", cls._DevPath(minor), "net",
            "%s:%s" % (lhost, lport), "%s:%s" % (rhost, rport), protocol,
            "-A", "discard-zero-changes",
            "-B", "consensus",
            "--create-device",
            ]
    if dual_pri:
      args.append("-m")
    if hmac and secret:
      args.extend(["-a", hmac, "-x", secret])
    result = utils.RunCmd(args)
    if result.failed:
      _ThrowError("drbd%d: can't setup network: %s - %s",
                  minor, result.fail_reason, result.output)

    timeout = time.time() + 10
    ok = False
    while time.time() < timeout:
      info = cls._GetDevInfo(cls._GetShowData(minor))
      if not "local_addr" in info or not "remote_addr" in info:
        time.sleep(1)
        continue
      if (info["local_addr"] != (lhost, lport) or
          info["remote_addr"] != (rhost, rport)):
        time.sleep(1)
        continue
      ok = True
      break
    if not ok:
      _ThrowError("drbd%d: timeout while configuring network", minor)

  def AddChildren(self, devices):
    """Add a disk to the DRBD device.

    """
    if self.minor is None:
      _ThrowError("drbd%d: can't attach to dbrd8 during AddChildren",
                  self._aminor)
    if len(devices) != 2:
      _ThrowError("drbd%d: need two devices for AddChildren", self.minor)
    info = self._GetDevInfo(self._GetShowData(self.minor))
    if "local_dev" in info:
      _ThrowError("drbd%d: already attached to a local disk", self.minor)
    backend, meta = devices
    if backend.dev_path is None or meta.dev_path is None:
      _ThrowError("drbd%d: children not ready during AddChildren", self.minor)
    backend.Open()
    meta.Open()
    self._CheckMetaSize(meta.dev_path)
    self._InitMeta(self._FindUnusedMinor(), meta.dev_path)

    self._AssembleLocal(self.minor, backend.dev_path, meta.dev_path, self.size)
    self._children = devices

  def RemoveChildren(self, devices):
    """Detach the drbd device from local storage.

    """
    if self.minor is None:
      _ThrowError("drbd%d: can't attach to drbd8 during RemoveChildren",
                  self._aminor)
    # early return if we don't actually have backing storage
    info = self._GetDevInfo(self._GetShowData(self.minor))
    if "local_dev" not in info:
      return
    if len(self._children) != 2:
      _ThrowError("drbd%d: we don't have two children: %s", self.minor,
                  self._children)
    if self._children.count(None) == 2: # we don't actually have children :)
      logging.warning("drbd%d: requested detach while detached", self.minor)
      return
    if len(devices) != 2:
      _ThrowError("drbd%d: we need two children in RemoveChildren", self.minor)
    for child, dev in zip(self._children, devices):
      if dev != child.dev_path:
        _ThrowError("drbd%d: mismatch in local storage (%s != %s) in"
                    " RemoveChildren", self.minor, dev, child.dev_path)

    self._ShutdownLocal(self.minor)
    self._children = []

  @classmethod
  def _SetMinorSyncSpeed(cls, minor, kbytes):
    """Set the speed of the DRBD syncer.

    This is the low-level implementation.

    @type minor: int
    @param minor: the drbd minor whose settings we change
    @type kbytes: int
    @param kbytes: the speed in kbytes/second
    @rtype: boolean
    @return: the success of the operation

    """
    result = utils.RunCmd(["drbdsetup", cls._DevPath(minor), "syncer",
                           "-r", "%d" % kbytes, "--create-device"])
    if result.failed:
      logging.error("Can't change syncer rate: %s - %s",
                    result.fail_reason, result.output)
    return not result.failed

  def SetSyncSpeed(self, kbytes):
    """Set the speed of the DRBD syncer.

    @type kbytes: int
    @param kbytes: the speed in kbytes/second
    @rtype: boolean
    @return: the success of the operation

    """
    if self.minor is None:
      logging.info("Not attached during SetSyncSpeed")
      return False
    children_result = super(DRBD8, self).SetSyncSpeed(kbytes)
    return self._SetMinorSyncSpeed(self.minor, kbytes) and children_result

  def GetProcStatus(self):
    """Return device data from /proc.

    """
    if self.minor is None:
      _ThrowError("drbd%d: GetStats() called while not attached", self._aminor)
    proc_info = self._MassageProcData(self._GetProcData())
    if self.minor not in proc_info:
      _ThrowError("drbd%d: can't find myself in /proc", self.minor)
    return DRBD8Status(proc_info[self.minor])

  def GetSyncStatus(self):
    """Returns the sync status of the device.


    If sync_percent is None, it means all is ok
    If estimated_time is None, it means we can't estimate
    the time needed, otherwise it's the time left in seconds.


    We set the is_degraded parameter to True on two conditions:
    network not connected or local disk missing.

    We compute the ldisk parameter based on whether we have a local
    disk or not.

    @rtype: tuple
    @return: (sync_percent, estimated_time, is_degraded, ldisk)

    """
    if self.minor is None and not self.Attach():
      _ThrowError("drbd%d: can't Attach() in GetSyncStatus", self._aminor)
    stats = self.GetProcStatus()
    ldisk = not stats.is_disk_uptodate
    is_degraded = not stats.is_connected
    return stats.sync_percent, stats.est_time, is_degraded or ldisk, ldisk

  def Open(self, force=False):
    """Make the local state primary.

    If the 'force' parameter is given, the '-o' option is passed to
    drbdsetup. Since this is a potentially dangerous operation, the
    force flag should be only given after creation, when it actually
    is mandatory.

    """
    if self.minor is None and not self.Attach():
      logging.error("DRBD cannot attach to a device during open")
      return False
    cmd = ["drbdsetup", self.dev_path, "primary"]
    if force:
      cmd.append("-o")
    result = utils.RunCmd(cmd)
    if result.failed:
      _ThrowError("drbd%d: can't make drbd device primary: %s", self.minor,
                  result.output)

  def Close(self):
    """Make the local state secondary.

    This will, of course, fail if the device is in use.

    """
    if self.minor is None and not self.Attach():
      _ThrowError("drbd%d: can't Attach() in Close()", self._aminor)
    result = utils.RunCmd(["drbdsetup", self.dev_path, "secondary"])
    if result.failed:
      _ThrowError("drbd%d: can't switch drbd device to secondary: %s",
                  self.minor, result.output)

  def DisconnectNet(self):
    """Removes network configuration.

    This method shutdowns the network side of the device.

    The method will wait up to a hardcoded timeout for the device to
    go into standalone after the 'disconnect' command before
    re-configuring it, as sometimes it takes a while for the
    disconnect to actually propagate and thus we might issue a 'net'
    command while the device is still connected. If the device will
    still be attached to the network and we time out, we raise an
    exception.

    """
    if self.minor is None:
      _ThrowError("drbd%d: disk not attached in re-attach net", self._aminor)

    if None in (self._lhost, self._lport, self._rhost, self._rport):
      _ThrowError("drbd%d: DRBD disk missing network info in"
                  " DisconnectNet()", self.minor)

    ever_disconnected = _IgnoreError(self._ShutdownNet, self.minor)
    timeout_limit = time.time() + self._NET_RECONFIG_TIMEOUT
    sleep_time = 0.100 # we start the retry time at 100 milliseconds
    while time.time() < timeout_limit:
      status = self.GetProcStatus()
      if status.is_standalone:
        break
      # retry the disconnect, it seems possible that due to a
      # well-time disconnect on the peer, my disconnect command might
      # be ignored and forgotten
      ever_disconnected = _IgnoreError(self._ShutdownNet, self.minor) or \
                          ever_disconnected
      time.sleep(sleep_time)
      sleep_time = min(2, sleep_time * 1.5)

    if not status.is_standalone:
      if ever_disconnected:
        msg = ("drbd%d: device did not react to the"
               " 'disconnect' command in a timely manner")
      else:
        msg = "drbd%d: can't shutdown network, even after multiple retries"
      _ThrowError(msg, self.minor)

    reconfig_time = time.time() - timeout_limit + self._NET_RECONFIG_TIMEOUT
    if reconfig_time > 15: # hardcoded alert limit
      logging.info("drbd%d: DisconnectNet: detach took %.3f seconds",
                   self.minor, reconfig_time)

  def AttachNet(self, multimaster):
    """Reconnects the network.

    This method connects the network side of the device with a
    specified multi-master flag. The device needs to be 'Standalone'
    but have valid network configuration data.

    Args:
      - multimaster: init the network in dual-primary mode

    """
    if self.minor is None:
      _ThrowError("drbd%d: device not attached in AttachNet", self._aminor)

    if None in (self._lhost, self._lport, self._rhost, self._rport):
      _ThrowError("drbd%d: missing network info in AttachNet()", self.minor)

    status = self.GetProcStatus()

    if not status.is_standalone:
      _ThrowError("drbd%d: device is not standalone in AttachNet", self.minor)

    self._AssembleNet(self.minor,
                      (self._lhost, self._lport, self._rhost, self._rport),
                      constants.DRBD_NET_PROTOCOL, dual_pri=multimaster,
                      hmac=constants.DRBD_HMAC_ALG, secret=self._secret)

  def Attach(self):
    """Check if our minor is configured.

    This doesn't do any device configurations - it only checks if the
    minor is in a state different from Unconfigured.

    Note that this function will not change the state of the system in
    any way (except in case of side-effects caused by reading from
    /proc).

    """
    used_devs = self.GetUsedDevs()
    if self._aminor in used_devs:
      minor = self._aminor
    else:
      minor = None

    self._SetFromMinor(minor)
    return minor is not None

  def Assemble(self):
    """Assemble the drbd.

    Method:
      - if we have a configured device, we try to ensure that it matches
        our config
      - if not, we create it from zero

    """
    super(DRBD8, self).Assemble()

    self.Attach()
    if self.minor is None:
      # local device completely unconfigured
      self._FastAssemble()
    else:
      # we have to recheck the local and network status and try to fix
      # the device
      self._SlowAssemble()

  def _SlowAssemble(self):
    """Assembles the DRBD device from a (partially) configured device.

    In case of partially attached (local device matches but no network
    setup), we perform the network attach. If successful, we re-test
    the attach if can return success.

    """
    net_data = (self._lhost, self._lport, self._rhost, self._rport)
    for minor in (self._aminor,):
      info = self._GetDevInfo(self._GetShowData(minor))
      match_l = self._MatchesLocal(info)
      match_r = self._MatchesNet(info)

      if match_l and match_r:
        # everything matches
        break

      if match_l and not match_r and "local_addr" not in info:
        # disk matches, but not attached to network, attach and recheck
        self._AssembleNet(minor, net_data, constants.DRBD_NET_PROTOCOL,
                          hmac=constants.DRBD_HMAC_ALG, secret=self._secret)
        if self._MatchesNet(self._GetDevInfo(self._GetShowData(minor))):
          break
        else:
          _ThrowError("drbd%d: network attach successful, but 'drbdsetup"
                      " show' disagrees", minor)

      if match_r and "local_dev" not in info:
        # no local disk, but network attached and it matches
        self._AssembleLocal(minor, self._children[0].dev_path,
                            self._children[1].dev_path, self.size)
        if self._MatchesNet(self._GetDevInfo(self._GetShowData(minor))):
          break
        else:
          _ThrowError("drbd%d: disk attach successful, but 'drbdsetup"
                      " show' disagrees", minor)

      # this case must be considered only if we actually have local
      # storage, i.e. not in diskless mode, because all diskless
      # devices are equal from the point of view of local
      # configuration
      if (match_l and "local_dev" in info and
          not match_r and "local_addr" in info):
        # strange case - the device network part points to somewhere
        # else, even though its local storage is ours; as we own the
        # drbd space, we try to disconnect from the remote peer and
        # reconnect to our correct one
        try:
          self._ShutdownNet(minor)
        except errors.BlockDeviceError, err:
          _ThrowError("drbd%d: device has correct local storage, wrong"
                      " remote peer and is unable to disconnect in order"
                      " to attach to the correct peer: %s", minor, str(err))
        # note: _AssembleNet also handles the case when we don't want
        # local storage (i.e. one or more of the _[lr](host|port) is
        # None)
        self._AssembleNet(minor, net_data, constants.DRBD_NET_PROTOCOL,
                          hmac=constants.DRBD_HMAC_ALG, secret=self._secret)
        if self._MatchesNet(self._GetDevInfo(self._GetShowData(minor))):
          break
        else:
          _ThrowError("drbd%d: network attach successful, but 'drbdsetup"
                      " show' disagrees", minor)

    else:
      minor = None

    self._SetFromMinor(minor)
    if minor is None:
      _ThrowError("drbd%d: cannot activate, unknown or unhandled reason",
                  self._aminor)

  def _FastAssemble(self):
    """Assemble the drbd device from zero.

    This is run when in Assemble we detect our minor is unused.

    """
    minor = self._aminor
    if self._children and self._children[0] and self._children[1]:
      self._AssembleLocal(minor, self._children[0].dev_path,
                          self._children[1].dev_path, self.size)
    if self._lhost and self._lport and self._rhost and self._rport:
      self._AssembleNet(minor,
                        (self._lhost, self._lport, self._rhost, self._rport),
                        constants.DRBD_NET_PROTOCOL,
                        hmac=constants.DRBD_HMAC_ALG, secret=self._secret)
    self._SetFromMinor(minor)

  @classmethod
  def _ShutdownLocal(cls, minor):
    """Detach from the local device.

    I/Os will continue to be served from the remote device. If we
    don't have a remote device, this operation will fail.

    """
    result = utils.RunCmd(["drbdsetup", cls._DevPath(minor), "detach"])
    if result.failed:
      _ThrowError("drbd%d: can't detach local disk: %s", minor, result.output)

  @classmethod
  def _ShutdownNet(cls, minor):
    """Disconnect from the remote peer.

    This fails if we don't have a local device.

    """
    result = utils.RunCmd(["drbdsetup", cls._DevPath(minor), "disconnect"])
    if result.failed:
      _ThrowError("drbd%d: can't shutdown network: %s", minor, result.output)

  @classmethod
  def _ShutdownAll(cls, minor):
    """Deactivate the device.

    This will, of course, fail if the device is in use.

    """
    result = utils.RunCmd(["drbdsetup", cls._DevPath(minor), "down"])
    if result.failed:
      _ThrowError("drbd%d: can't shutdown drbd device: %s",
                  minor, result.output)

  def Shutdown(self):
    """Shutdown the DRBD device.

    """
    if self.minor is None and not self.Attach():
      logging.info("drbd%d: not attached during Shutdown()", self._aminor)
      return
    minor = self.minor
    self.minor = None
    self.dev_path = None
    self._ShutdownAll(minor)

  def Remove(self):
    """Stub remove for DRBD devices.

    """
    self.Shutdown()

  @classmethod
  def Create(cls, unique_id, children, size):
    """Create a new DRBD8 device.

    Since DRBD devices are not created per se, just assembled, this
    function only initializes the metadata.

    """
    if len(children) != 2:
      raise errors.ProgrammerError("Invalid setup for the drbd device")
    # check that the minor is unused
    aminor = unique_id[4]
    proc_info = cls._MassageProcData(cls._GetProcData())
    if aminor in proc_info:
      status = DRBD8Status(proc_info[aminor])
      in_use = status.is_in_use
    else:
      in_use = False
    if in_use:
      _ThrowError("drbd%d: minor is already in use at Create() time", aminor)
    meta = children[1]
    meta.Assemble()
    if not meta.Attach():
      _ThrowError("drbd%d: can't attach to meta device '%s'",
                  aminor, meta)
    cls._CheckMetaSize(meta.dev_path)
    cls._InitMeta(aminor, meta.dev_path)
    return cls(unique_id, children, size)

  def Grow(self, amount):
    """Resize the DRBD device and its backing storage.

    """
    if self.minor is None:
      _ThrowError("drbd%d: Grow called while not attached", self._aminor)
    if len(self._children) != 2 or None in self._children:
      _ThrowError("drbd%d: cannot grow diskless device", self.minor)
    self._children[0].Grow(amount)
    result = utils.RunCmd(["drbdsetup", self.dev_path, "resize", "-s",
                           "%dm" % (self.size + amount)])
    if result.failed:
      _ThrowError("drbd%d: resize failed: %s", self.minor, result.output)


class FileStorage(BlockDev):
  """File device.

  This class represents the a file storage backend device.

  The unique_id for the file device is a (file_driver, file_path) tuple.

  """
  def __init__(self, unique_id, children, size):
    """Initalizes a file device backend.

    """
    if children:
      raise errors.BlockDeviceError("Invalid setup for file device")
    super(FileStorage, self).__init__(unique_id, children, size)
    if not isinstance(unique_id, (tuple, list)) or len(unique_id) != 2:
      raise ValueError("Invalid configuration data %s" % str(unique_id))
    self.driver = unique_id[0]
    self.dev_path = unique_id[1]
    self.Attach()

  def Assemble(self):
    """Assemble the device.

    Checks whether the file device exists, raises BlockDeviceError otherwise.

    """
    if not os.path.exists(self.dev_path):
      _ThrowError("File device '%s' does not exist" % self.dev_path)

  def Shutdown(self):
    """Shutdown the device.

    This is a no-op for the file type, as we don't deactivate
    the file on shutdown.

    """
    pass

  def Open(self, force=False):
    """Make the device ready for I/O.

    This is a no-op for the file type.

    """
    pass

  def Close(self):
    """Notifies that the device will no longer be used for I/O.

    This is a no-op for the file type.

    """
    pass

  def Remove(self):
    """Remove the file backing the block device.

    @rtype: boolean
    @return: True if the removal was successful

    """
    try:
      os.remove(self.dev_path)
    except OSError, err:
      if err.errno != errno.ENOENT:
        _ThrowError("Can't remove file '%s': %s", self.dev_path, err)

  def Attach(self):
    """Attach to an existing file.

    Check if this file already exists.

    @rtype: boolean
    @return: True if file exists

    """
    self.attached = os.path.exists(self.dev_path)
    return self.attached

  def GetActualSize(self):
    """Return the actual disk size.

    @note: the device needs to be active when this is called

    """
    assert self.attached, "BlockDevice not attached in GetActualSize()"
    try:
      st = os.stat(self.dev_path)
      return st.st_size
    except OSError, err:
      _ThrowError("Can't stat %s: %s", self.dev_path, err)

  @classmethod
  def Create(cls, unique_id, children, size):
    """Create a new file.

    @param size: the size of file in MiB

    @rtype: L{bdev.FileStorage}
    @return: an instance of FileStorage

    """
    if not isinstance(unique_id, (tuple, list)) or len(unique_id) != 2:
      raise ValueError("Invalid configuration data %s" % str(unique_id))
    dev_path = unique_id[1]
    if os.path.exists(dev_path):
      _ThrowError("File already existing: %s", dev_path)
    try:
      f = open(dev_path, 'w')
      f.truncate(size * 1024 * 1024)
      f.close()
    except IOError, err:
      _ThrowError("Error in file creation: %", str(err))

    return FileStorage(unique_id, children, size)


DEV_MAP = {
  constants.LD_LV: LogicalVolume,
  constants.LD_DRBD8: DRBD8,
  constants.LD_FILE: FileStorage,
  }


def FindDevice(dev_type, unique_id, children, size):
  """Search for an existing, assembled device.

  This will succeed only if the device exists and is assembled, but it
  does not do any actions in order to activate the device.

  """
  if dev_type not in DEV_MAP:
    raise errors.ProgrammerError("Invalid block device type '%s'" % dev_type)
  device = DEV_MAP[dev_type](unique_id, children, size)
  if not device.attached:
    return None
  return device


def Assemble(dev_type, unique_id, children, size):
  """Try to attach or assemble an existing device.

  This will attach to assemble the device, as needed, to bring it
  fully up. It must be safe to run on already-assembled devices.

  """
  if dev_type not in DEV_MAP:
    raise errors.ProgrammerError("Invalid block device type '%s'" % dev_type)
  device = DEV_MAP[dev_type](unique_id, children, size)
  device.Assemble()
  return device


def Create(dev_type, unique_id, children, size):
  """Create a device.

  """
  if dev_type not in DEV_MAP:
    raise errors.ProgrammerError("Invalid block device type '%s'" % dev_type)
  device = DEV_MAP[dev_type].Create(unique_id, children, size)
  return device
