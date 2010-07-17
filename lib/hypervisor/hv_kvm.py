#
#

# Copyright (C) 2008 Google Inc.
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


"""KVM hypervisor

"""

import errno
import os
import os.path
import re
import tempfile
import time
import logging
import pwd
from cStringIO import StringIO

from ganeti import utils
from ganeti import constants
from ganeti import errors
from ganeti import serializer
from ganeti import objects
from ganeti import uidpool
from ganeti import ssconf
from ganeti.hypervisor import hv_base


class KVMHypervisor(hv_base.BaseHypervisor):
  """KVM hypervisor interface"""
  CAN_MIGRATE = True

  _ROOT_DIR = constants.RUN_GANETI_DIR + "/kvm-hypervisor"
  _PIDS_DIR = _ROOT_DIR + "/pid" # contains live instances pids
  _UIDS_DIR = _ROOT_DIR + "/uid" # contains instances reserved uids
  _CTRL_DIR = _ROOT_DIR + "/ctrl" # contains instances control sockets
  _CONF_DIR = _ROOT_DIR + "/conf" # contains instances startup data
  # KVM instances with chroot enabled are started in empty chroot directories.
  _CHROOT_DIR = _ROOT_DIR + "/chroot" # for empty chroot directories
  # After an instance is stopped, its chroot directory is removed.
  # If the chroot directory is not empty, it can't be removed.
  # A non-empty chroot directory indicates a possible security incident.
  # To support forensics, the non-empty chroot directory is quarantined in
  # a separate directory, called 'chroot-quarantine'.
  _CHROOT_QUARANTINE_DIR = _ROOT_DIR + "/chroot-quarantine"
  _DIRS = [_ROOT_DIR, _PIDS_DIR, _UIDS_DIR, _CTRL_DIR, _CONF_DIR,
           _CHROOT_DIR, _CHROOT_QUARANTINE_DIR]

  PARAMETERS = {
    constants.HV_KERNEL_PATH: hv_base.OPT_FILE_CHECK,
    constants.HV_INITRD_PATH: hv_base.OPT_FILE_CHECK,
    constants.HV_ROOT_PATH: hv_base.NO_CHECK,
    constants.HV_KERNEL_ARGS: hv_base.NO_CHECK,
    constants.HV_ACPI: hv_base.NO_CHECK,
    constants.HV_SERIAL_CONSOLE: hv_base.NO_CHECK,
    constants.HV_VNC_BIND_ADDRESS:
      (False, lambda x: (utils.IsValidIP(x) or utils.IsNormAbsPath(x)),
       "the VNC bind address must be either a valid IP address or an absolute"
       " pathname", None, None),
    constants.HV_VNC_TLS: hv_base.NO_CHECK,
    constants.HV_VNC_X509: hv_base.OPT_DIR_CHECK,
    constants.HV_VNC_X509_VERIFY: hv_base.NO_CHECK,
    constants.HV_VNC_PASSWORD_FILE: hv_base.OPT_FILE_CHECK,
    constants.HV_CDROM_IMAGE_PATH: hv_base.OPT_FILE_CHECK,
    constants.HV_BOOT_ORDER:
      hv_base.ParamInSet(True, constants.HT_KVM_VALID_BO_TYPES),
    constants.HV_NIC_TYPE:
      hv_base.ParamInSet(True, constants.HT_KVM_VALID_NIC_TYPES),
    constants.HV_DISK_TYPE:
      hv_base.ParamInSet(True, constants.HT_KVM_VALID_DISK_TYPES),
    constants.HV_USB_MOUSE:
      hv_base.ParamInSet(False, constants.HT_KVM_VALID_MOUSE_TYPES),
    constants.HV_MIGRATION_PORT: hv_base.NET_PORT_CHECK,
    constants.HV_MIGRATION_BANDWIDTH: hv_base.NO_CHECK,
    constants.HV_MIGRATION_DOWNTIME: hv_base.NO_CHECK,
    constants.HV_USE_LOCALTIME: hv_base.NO_CHECK,
    constants.HV_DISK_CACHE:
      hv_base.ParamInSet(True, constants.HT_VALID_CACHE_TYPES),
    constants.HV_SECURITY_MODEL:
      hv_base.ParamInSet(True, constants.HT_KVM_VALID_SM_TYPES),
    constants.HV_SECURITY_DOMAIN: hv_base.NO_CHECK,
    constants.HV_KVM_FLAG:
      hv_base.ParamInSet(False, constants.HT_KVM_FLAG_VALUES),
    constants.HV_VHOST_NET: hv_base.NO_CHECK,
    constants.HV_KVM_USE_CHROOT: hv_base.NO_CHECK,
    }

  _MIGRATION_STATUS_RE = re.compile('Migration\s+status:\s+(\w+)',
                                    re.M | re.I)
  _MIGRATION_INFO_MAX_BAD_ANSWERS = 5
  _MIGRATION_INFO_RETRY_DELAY = 2

  _KVM_NETWORK_SCRIPT = constants.SYSCONFDIR + "/ganeti/kvm-vif-bridge"

  ANCILLARY_FILES = [
    _KVM_NETWORK_SCRIPT,
    ]

  def __init__(self):
    hv_base.BaseHypervisor.__init__(self)
    # Let's make sure the directories we need exist, even if the RUN_DIR lives
    # in a tmpfs filesystem or has been otherwise wiped out.
    dirs = [(dname, constants.RUN_DIRS_MODE) for dname in self._DIRS]
    utils.EnsureDirs(dirs)

  @classmethod
  def _InstancePidFile(cls, instance_name):
    """Returns the instance pidfile.

    """
    return utils.PathJoin(cls._PIDS_DIR, instance_name)

  @classmethod
  def _InstanceUidFile(cls, instance_name):
    """Returns the instance uidfile.

    """
    return utils.PathJoin(cls._UIDS_DIR, instance_name)

  @classmethod
  def _InstancePidInfo(cls, pid):
    """Check pid file for instance information.

    Check that a pid file is associated with an instance, and retrieve
    information from its command line.

    @type pid: string or int
    @param pid: process id of the instance to check
    @rtype: tuple
    @return: (instance_name, memory, vcpus)
    @raise errors.HypervisorError: when an instance cannot be found

    """
    alive = utils.IsProcessAlive(pid)
    if not alive:
      raise errors.HypervisorError("Cannot get info for pid %s" % pid)

    cmdline_file = utils.PathJoin("/proc", str(pid), "cmdline")
    try:
      cmdline = utils.ReadFile(cmdline_file)
    except EnvironmentError, err:
      raise errors.HypervisorError("Can't open cmdline file for pid %s: %s" %
                                   (pid, err))

    instance = None
    memory = 0
    vcpus = 0

    arg_list = cmdline.split('\x00')
    while arg_list:
      arg =  arg_list.pop(0)
      if arg == "-name":
        instance = arg_list.pop(0)
      elif arg == "-m":
        memory = int(arg_list.pop(0))
      elif arg == "-smp":
        vcpus = int(arg_list.pop(0))

    if instance is None:
      raise errors.HypervisorError("Pid %s doesn't contain a ganeti kvm"
                                   " instance" % pid)

    return (instance, memory, vcpus)

  def _InstancePidAlive(self, instance_name):
    """Returns the instance pidfile, pid, and liveness.

    @type instance_name: string
    @param instance_name: instance name
    @rtype: tuple
    @return: (pid file name, pid, liveness)

    """
    pidfile = self._InstancePidFile(instance_name)
    pid = utils.ReadPidFile(pidfile)

    alive = False
    try:
      cmd_instance = self._InstancePidInfo(pid)[0]
      alive = (cmd_instance == instance_name)
    except errors.HypervisorError:
      pass

    return (pidfile, pid, alive)

  def _CheckDown(self, instance_name):
    """Raises an error unless the given instance is down.

    """
    alive = self._InstancePidAlive(instance_name)[2]
    if alive:
      raise errors.HypervisorError("Failed to start instance %s: %s" %
                                   (instance_name, "already running"))

  @classmethod
  def _InstanceMonitor(cls, instance_name):
    """Returns the instance monitor socket name

    """
    return utils.PathJoin(cls._CTRL_DIR, "%s.monitor" % instance_name)

  @classmethod
  def _InstanceSerial(cls, instance_name):
    """Returns the instance serial socket name

    """
    return utils.PathJoin(cls._CTRL_DIR, "%s.serial" % instance_name)

  @staticmethod
  def _SocatUnixConsoleParams():
    """Returns the correct parameters for socat

    If we have a new-enough socat we can use raw mode with an escape character.

    """
    if constants.SOCAT_USE_ESCAPE:
      return "raw,echo=0,escape=%s" % constants.SOCAT_ESCAPE_CODE
    else:
      return "echo=0,icanon=0"

  @classmethod
  def _InstanceKVMRuntime(cls, instance_name):
    """Returns the instance KVM runtime filename

    """
    return utils.PathJoin(cls._CONF_DIR, "%s.runtime" % instance_name)

  @classmethod
  def _InstanceChrootDir(cls, instance_name):
    """Returns the name of the KVM chroot dir of the instance

    """
    return utils.PathJoin(cls._CHROOT_DIR, instance_name)

  @classmethod
  def _TryReadUidFile(cls, uid_file):
    """Try to read a uid file

    """
    if os.path.exists(uid_file):
      try:
        uid = int(utils.ReadOneLineFile(uid_file))
        return uid
      except EnvironmentError:
        logging.warning("Can't read uid file", exc_info=True)
      except (TypeError, ValueError):
        logging.warning("Can't parse uid file contents", exc_info=True)
    return None

  @classmethod
  def _RemoveInstanceRuntimeFiles(cls, pidfile, instance_name):
    """Removes an instance's rutime sockets/files/dirs.

    """
    utils.RemoveFile(pidfile)
    utils.RemoveFile(cls._InstanceMonitor(instance_name))
    utils.RemoveFile(cls._InstanceSerial(instance_name))
    utils.RemoveFile(cls._InstanceKVMRuntime(instance_name))
    uid_file = cls._InstanceUidFile(instance_name)
    uid = cls._TryReadUidFile(uid_file)
    utils.RemoveFile(uid_file)
    if uid is not None:
      uidpool.ReleaseUid(uid)
    try:
      chroot_dir = cls._InstanceChrootDir(instance_name)
      utils.RemoveDir(chroot_dir)
    except OSError, err:
      if err.errno == errno.ENOTEMPTY:
        # The chroot directory is expected to be empty, but it isn't.
        new_chroot_dir = tempfile.mkdtemp(dir=cls._CHROOT_QUARANTINE_DIR,
                                          prefix="%s-%s-" %
                                          (instance_name,
                                           utils.TimestampForFilename()))
        logging.warning("The chroot directory of instance %s can not be"
                        " removed as it is not empty. Moving it to the"
                        " quarantine instead. Please investigate the"
                        " contents (%s) and clean up manually",
                        instance_name, new_chroot_dir)
        utils.RenameFile(chroot_dir, new_chroot_dir)
      else:
        raise

  def _WriteNetScript(self, instance, seq, nic):
    """Write a script to connect a net interface to the proper bridge.

    This can be used by any qemu-type hypervisor.

    @param instance: instance we're acting on
    @type instance: instance object
    @param seq: nic sequence number
    @type seq: int
    @param nic: nic we're acting on
    @type nic: nic object
    @return: netscript file name
    @rtype: string

    """
    script = StringIO()
    script.write("#!/bin/sh\n")
    script.write("# this is autogenerated by Ganeti, please do not edit\n#\n")
    script.write("PATH=$PATH:/sbin:/usr/sbin\n")
    script.write("export INSTANCE=%s\n" % instance.name)
    script.write("export MAC=%s\n" % nic.mac)
    if nic.ip:
      script.write("export IP=%s\n" % nic.ip)
    script.write("export MODE=%s\n" % nic.nicparams[constants.NIC_MODE])
    if nic.nicparams[constants.NIC_LINK]:
      script.write("export LINK=%s\n" % nic.nicparams[constants.NIC_LINK])
    if nic.nicparams[constants.NIC_MODE] == constants.NIC_MODE_BRIDGED:
      script.write("export BRIDGE=%s\n" % nic.nicparams[constants.NIC_LINK])
    script.write("export INTERFACE=$1\n")
    if instance.tags:
      script.write("export TAGS=\"%s\"\n" % " ".join(instance.tags))
    # TODO: make this configurable at ./configure time
    script.write("if [ -x '%s' ]; then\n" % self._KVM_NETWORK_SCRIPT)
    script.write("  # Execute the user-specific vif file\n")
    script.write("  %s\n" % self._KVM_NETWORK_SCRIPT)
    script.write("else\n")
    script.write("  ifconfig $INTERFACE 0.0.0.0 up\n")
    if nic.nicparams[constants.NIC_MODE] == constants.NIC_MODE_BRIDGED:
      script.write("  # Connect the interface to the bridge\n")
      script.write("  brctl addif $BRIDGE $INTERFACE\n")
    elif nic.nicparams[constants.NIC_MODE] == constants.NIC_MODE_ROUTED:
      if not nic.ip:
        raise errors.HypervisorError("nic/%d is routed, but has no ip." % seq)
      script.write("  # Route traffic targeted at the IP to the interface\n")
      if nic.nicparams[constants.NIC_LINK]:
        script.write("  while ip rule del dev $INTERFACE; do :; done\n")
        script.write("  ip rule add dev $INTERFACE table $LINK\n")
        script.write("  ip route replace $IP table $LINK proto static"
                     " dev $INTERFACE\n")
      else:
        script.write("  ip route replace $IP proto static"
                     " dev $INTERFACE\n")
      interface_v4_conf = "/proc/sys/net/ipv4/conf/$INTERFACE"
      interface_v6_conf = "/proc/sys/net/ipv6/conf/$INTERFACE"
      script.write("  if [ -d %s ]; then\n" % interface_v4_conf)
      script.write("    echo 1 > %s/proxy_arp\n" % interface_v4_conf)
      script.write("    echo 1 > %s/forwarding\n" % interface_v4_conf)
      script.write("  fi\n")
      script.write("  if [ -d %s ]; then\n" % interface_v6_conf)
      script.write("    echo 1 > %s/proxy_ndp\n" % interface_v6_conf)
      script.write("    echo 1 > %s/forwarding\n" % interface_v6_conf)
      script.write("  fi\n")
    script.write("fi\n\n")
    # As much as we'd like to put this in our _ROOT_DIR, that will happen to be
    # mounted noexec sometimes, so we'll have to find another place.
    (tmpfd, tmpfile_name) = tempfile.mkstemp()
    tmpfile = os.fdopen(tmpfd, 'w')
    try:
      tmpfile.write(script.getvalue())
    finally:
      tmpfile.close()
    os.chmod(tmpfile_name, 0755)
    return tmpfile_name

  def ListInstances(self):
    """Get the list of running instances.

    We can do this by listing our live instances directory and
    checking whether the associated kvm process is still alive.

    """
    result = []
    for name in os.listdir(self._PIDS_DIR):
      if self._InstancePidAlive(name)[2]:
        result.append(name)
    return result

  def GetInstanceInfo(self, instance_name):
    """Get instance properties.

    @type instance_name: string
    @param instance_name: the instance name
    @rtype: tuple of strings
    @return: (name, id, memory, vcpus, stat, times)

    """
    _, pid, alive = self._InstancePidAlive(instance_name)
    if not alive:
      return None

    _, memory, vcpus = self._InstancePidInfo(pid)
    stat = "---b-"
    times = "0"

    return (instance_name, pid, memory, vcpus, stat, times)

  def GetAllInstancesInfo(self):
    """Get properties of all instances.

    @return: list of tuples (name, id, memory, vcpus, stat, times)

    """
    data = []
    for name in os.listdir(self._PIDS_DIR):
      try:
        info = self.GetInstanceInfo(name)
      except errors.HypervisorError:
        continue
      if info:
        data.append(info)
    return data

  def _GenerateKVMRuntime(self, instance, block_devices):
    """Generate KVM information to start an instance.

    """
    pidfile  = self._InstancePidFile(instance.name)
    kvm = constants.KVM_PATH
    kvm_cmd = [kvm]
    # used just by the vnc server, if enabled
    kvm_cmd.extend(['-name', instance.name])
    kvm_cmd.extend(['-m', instance.beparams[constants.BE_MEMORY]])
    kvm_cmd.extend(['-smp', instance.beparams[constants.BE_VCPUS]])
    kvm_cmd.extend(['-pidfile', pidfile])
    kvm_cmd.extend(['-daemonize'])
    if not instance.hvparams[constants.HV_ACPI]:
      kvm_cmd.extend(['-no-acpi'])

    hvp = instance.hvparams
    boot_disk = hvp[constants.HV_BOOT_ORDER] == constants.HT_BO_DISK
    boot_cdrom = hvp[constants.HV_BOOT_ORDER] == constants.HT_BO_CDROM
    boot_network = hvp[constants.HV_BOOT_ORDER] == constants.HT_BO_NETWORK

    if hvp[constants.HV_KVM_FLAG] == constants.HT_KVM_ENABLED:
      kvm_cmd.extend(["-enable-kvm"])
    elif hvp[constants.HV_KVM_FLAG] == constants.HT_KVM_DISABLED:
      kvm_cmd.extend(["-disable-kvm"])

    if boot_network:
      kvm_cmd.extend(['-boot', 'n'])

    disk_type = hvp[constants.HV_DISK_TYPE]
    if disk_type == constants.HT_DISK_PARAVIRTUAL:
      if_val = ',if=virtio'
    else:
      if_val = ',if=%s' % disk_type
    # Cache mode
    disk_cache = hvp[constants.HV_DISK_CACHE]
    if disk_cache != constants.HT_CACHE_DEFAULT:
      cache_val = ",cache=%s" % disk_cache
    else:
      cache_val = ""
    for cfdev, dev_path in block_devices:
      if cfdev.mode != constants.DISK_RDWR:
        raise errors.HypervisorError("Instance has read-only disks which"
                                     " are not supported by KVM")
      # TODO: handle FD_LOOP and FD_BLKTAP (?)
      if boot_disk:
        kvm_cmd.extend(['-boot', 'c'])
        if disk_type != constants.HT_DISK_IDE:
          boot_val = ',boot=on'
        else:
          boot_val = ''
        # We only boot from the first disk
        boot_disk = False
      else:
        boot_val = ''

      drive_val = 'file=%s,format=raw%s%s%s' % (dev_path, if_val, boot_val,
                                                cache_val)
      kvm_cmd.extend(['-drive', drive_val])

    iso_image = hvp[constants.HV_CDROM_IMAGE_PATH]
    if iso_image:
      options = ',format=raw,media=cdrom'
      if boot_cdrom:
        kvm_cmd.extend(['-boot', 'd'])
        if disk_type != constants.HT_DISK_IDE:
          options = '%s,boot=on' % options
      else:
        if disk_type == constants.HT_DISK_PARAVIRTUAL:
          if_val = ',if=virtio'
        else:
          if_val = ',if=%s' % disk_type
        options = '%s%s' % (options, if_val)
      drive_val = 'file=%s%s' % (iso_image, options)
      kvm_cmd.extend(['-drive', drive_val])

    kernel_path = hvp[constants.HV_KERNEL_PATH]
    if kernel_path:
      kvm_cmd.extend(['-kernel', kernel_path])
      initrd_path = hvp[constants.HV_INITRD_PATH]
      if initrd_path:
        kvm_cmd.extend(['-initrd', initrd_path])
      root_append = ['root=%s' % hvp[constants.HV_ROOT_PATH],
                     hvp[constants.HV_KERNEL_ARGS]]
      if hvp[constants.HV_SERIAL_CONSOLE]:
        root_append.append('console=ttyS0,38400')
      kvm_cmd.extend(['-append', ' '.join(root_append)])

    mouse_type = hvp[constants.HV_USB_MOUSE]
    if mouse_type:
      kvm_cmd.extend(['-usb'])
      kvm_cmd.extend(['-usbdevice', mouse_type])

    vnc_bind_address = hvp[constants.HV_VNC_BIND_ADDRESS]
    if vnc_bind_address:
      if utils.IsValidIP(vnc_bind_address):
        if instance.network_port > constants.VNC_BASE_PORT:
          display = instance.network_port - constants.VNC_BASE_PORT
          if vnc_bind_address == '0.0.0.0':
            vnc_arg = ':%d' % (display)
          else:
            vnc_arg = '%s:%d' % (vnc_bind_address, display)
        else:
          logging.error("Network port is not a valid VNC display (%d < %d)."
                        " Not starting VNC", instance.network_port,
                        constants.VNC_BASE_PORT)
          vnc_arg = 'none'

        # Only allow tls and other option when not binding to a file, for now.
        # kvm/qemu gets confused otherwise about the filename to use.
        vnc_append = ''
        if hvp[constants.HV_VNC_TLS]:
          vnc_append = '%s,tls' % vnc_append
          if hvp[constants.HV_VNC_X509_VERIFY]:
            vnc_append = '%s,x509verify=%s' % (vnc_append,
                                               hvp[constants.HV_VNC_X509])
          elif hvp[constants.HV_VNC_X509]:
            vnc_append = '%s,x509=%s' % (vnc_append,
                                         hvp[constants.HV_VNC_X509])
        if hvp[constants.HV_VNC_PASSWORD_FILE]:
          vnc_append = '%s,password' % vnc_append

        vnc_arg = '%s%s' % (vnc_arg, vnc_append)

      else:
        vnc_arg = 'unix:%s/%s.vnc' % (vnc_bind_address, instance.name)

      kvm_cmd.extend(['-vnc', vnc_arg])

      # Also add a tablet USB device to act as a mouse
      # This solves various mouse alignment issues
      kvm_cmd.extend(['-usbdevice', 'tablet'])
    else:
      kvm_cmd.extend(['-nographic'])

    monitor_dev = ("unix:%s,server,nowait" %
                   self._InstanceMonitor(instance.name))
    kvm_cmd.extend(['-monitor', monitor_dev])
    if hvp[constants.HV_SERIAL_CONSOLE]:
      serial_dev = ('unix:%s,server,nowait' %
                    self._InstanceSerial(instance.name))
      kvm_cmd.extend(['-serial', serial_dev])
    else:
      kvm_cmd.extend(['-serial', 'none'])

    if hvp[constants.HV_USE_LOCALTIME]:
      kvm_cmd.extend(['-localtime'])

    if hvp[constants.HV_KVM_USE_CHROOT]:
      kvm_cmd.extend(['-chroot', self._InstanceChrootDir(instance.name)])

    # Save the current instance nics, but defer their expansion as parameters,
    # as we'll need to generate executable temp files for them.
    kvm_nics = instance.nics
    hvparams = hvp

    return (kvm_cmd, kvm_nics, hvparams)

  def _WriteKVMRuntime(self, instance_name, data):
    """Write an instance's KVM runtime

    """
    try:
      utils.WriteFile(self._InstanceKVMRuntime(instance_name),
                      data=data)
    except EnvironmentError, err:
      raise errors.HypervisorError("Failed to save KVM runtime file: %s" % err)

  def _ReadKVMRuntime(self, instance_name):
    """Read an instance's KVM runtime

    """
    try:
      file_content = utils.ReadFile(self._InstanceKVMRuntime(instance_name))
    except EnvironmentError, err:
      raise errors.HypervisorError("Failed to load KVM runtime file: %s" % err)
    return file_content

  def _SaveKVMRuntime(self, instance, kvm_runtime):
    """Save an instance's KVM runtime

    """
    kvm_cmd, kvm_nics, hvparams = kvm_runtime
    serialized_nics = [nic.ToDict() for nic in kvm_nics]
    serialized_form = serializer.Dump((kvm_cmd, serialized_nics, hvparams))
    self._WriteKVMRuntime(instance.name, serialized_form)

  def _LoadKVMRuntime(self, instance, serialized_runtime=None):
    """Load an instance's KVM runtime

    """
    if not serialized_runtime:
      serialized_runtime = self._ReadKVMRuntime(instance.name)
    loaded_runtime = serializer.Load(serialized_runtime)
    kvm_cmd, serialized_nics, hvparams = loaded_runtime
    kvm_nics = [objects.NIC.FromDict(snic) for snic in serialized_nics]
    return (kvm_cmd, kvm_nics, hvparams)

  def _RunKVMCmd(self, name, kvm_cmd):
    """Run the KVM cmd and check for errors

    @type name: string
    @param name: instance name
    @type kvm_cmd: list of strings
    @param kvm_cmd: runcmd input for kvm

    """
    result = utils.RunCmd(kvm_cmd)
    if result.failed:
      raise errors.HypervisorError("Failed to start instance %s: %s (%s)" %
                                   (name, result.fail_reason, result.output))
    if not self._InstancePidAlive(name)[2]:
      raise errors.HypervisorError("Failed to start instance %s" % name)

  def _ExecuteKVMRuntime(self, instance, kvm_runtime, incoming=None):
    """Execute a KVM cmd, after completing it with some last minute data

    @type incoming: tuple of strings
    @param incoming: (target_host_ip, port)

    """
    # Small _ExecuteKVMRuntime hv parameters programming howto:
    #  - conf_hvp contains the parameters as configured on ganeti. they might
    #    have changed since the instance started; only use them if the change
    #    won't affect the inside of the instance (which hasn't been rebooted).
    #  - up_hvp contains the parameters as they were when the instance was
    #    started, plus any new parameter which has been added between ganeti
    #    versions: it is paramount that those default to a value which won't
    #    affect the inside of the instance as well.
    conf_hvp = instance.hvparams
    name = instance.name
    self._CheckDown(name)

    temp_files = []

    kvm_cmd, kvm_nics, up_hvp = kvm_runtime
    up_hvp = objects.FillDict(conf_hvp, up_hvp)

    # We know it's safe to run as a different user upon migration, so we'll use
    # the latest conf, from conf_hvp.
    security_model = conf_hvp[constants.HV_SECURITY_MODEL]
    if security_model == constants.HT_SM_USER:
      kvm_cmd.extend(["-runas", conf_hvp[constants.HV_SECURITY_DOMAIN]])

    # We have reasons to believe changing something like the nic driver/type
    # upon migration won't exactly fly with the instance kernel, so for nic
    # related parameters we'll use up_hvp
    if not kvm_nics:
      kvm_cmd.extend(["-net", "none"])
    else:
      tap_extra = ""
      nic_type = up_hvp[constants.HV_NIC_TYPE]
      if nic_type == constants.HT_NIC_PARAVIRTUAL:
        nic_model = "model=virtio"
        if up_hvp[constants.HV_VHOST_NET]:
          tap_extra = ",vhost=on"
      else:
        nic_model = "model=%s" % nic_type

      for nic_seq, nic in enumerate(kvm_nics):
        nic_val = "nic,vlan=%s,macaddr=%s,%s" % (nic_seq, nic.mac, nic_model)
        script = self._WriteNetScript(instance, nic_seq, nic)
        tap_val = "tap,vlan=%s,script=%s%s" % (nic_seq, script, tap_extra)
        kvm_cmd.extend(["-net", nic_val])
        kvm_cmd.extend(["-net", tap_val])
        temp_files.append(script)

    if incoming:
      target, port = incoming
      kvm_cmd.extend(['-incoming', 'tcp:%s:%s' % (target, port)])

    # Changing the vnc password doesn't bother the guest that much. At most it
    # will surprise people who connect to it. Whether positively or negatively
    # it's debatable.
    vnc_pwd_file = conf_hvp[constants.HV_VNC_PASSWORD_FILE]
    vnc_pwd = None
    if vnc_pwd_file:
      try:
        vnc_pwd = utils.ReadOneLineFile(vnc_pwd_file, strict=True)
      except EnvironmentError, err:
        raise errors.HypervisorError("Failed to open VNC password file %s: %s"
                                     % (vnc_pwd_file, err))

    if conf_hvp[constants.HV_KVM_USE_CHROOT]:
      utils.EnsureDirs([(self._InstanceChrootDir(name),
                         constants.SECURE_DIR_MODE)])

    if security_model == constants.HT_SM_POOL:
      ss = ssconf.SimpleStore()
      uid_pool = uidpool.ParseUidPool(ss.GetUidPool(), separator="\n")
      all_uids = set(uidpool.ExpandUidPool(uid_pool))
      uid = uidpool.RequestUnusedUid(all_uids)
      try:
        username = pwd.getpwuid(uid.GetUid()).pw_name
        kvm_cmd.extend(["-runas", username])
        self._RunKVMCmd(name, kvm_cmd)
      except:
        uidpool.ReleaseUid(uid)
        raise
      else:
        uid.Unlock()
        utils.WriteFile(self._InstanceUidFile(name), data=str(uid))
    else:
      self._RunKVMCmd(name, kvm_cmd)

    if vnc_pwd:
      change_cmd = 'change vnc password %s' % vnc_pwd
      self._CallMonitorCommand(instance.name, change_cmd)

    for filename in temp_files:
      utils.RemoveFile(filename)

  def StartInstance(self, instance, block_devices):
    """Start an instance.

    """
    self._CheckDown(instance.name)
    kvm_runtime = self._GenerateKVMRuntime(instance, block_devices)
    self._SaveKVMRuntime(instance, kvm_runtime)
    self._ExecuteKVMRuntime(instance, kvm_runtime)

  def _CallMonitorCommand(self, instance_name, command):
    """Invoke a command on the instance monitor.

    """
    socat = ("echo %s | %s STDIO UNIX-CONNECT:%s" %
             (utils.ShellQuote(command),
              constants.SOCAT_PATH,
              utils.ShellQuote(self._InstanceMonitor(instance_name))))
    result = utils.RunCmd(socat)
    if result.failed:
      msg = ("Failed to send command '%s' to instance %s."
             " output: %s, error: %s, fail_reason: %s" %
             (command, instance_name,
              result.stdout, result.stderr, result.fail_reason))
      raise errors.HypervisorError(msg)

    return result

  def StopInstance(self, instance, force=False, retry=False, name=None):
    """Stop an instance.

    """
    if name is not None and not force:
      raise errors.HypervisorError("Cannot shutdown cleanly by name only")
    if name is None:
      name = instance.name
      acpi = instance.hvparams[constants.HV_ACPI]
    else:
      acpi = False
    _, pid, alive = self._InstancePidAlive(name)
    if pid > 0 and alive:
      if force or not acpi:
        utils.KillProcess(pid)
      else:
        self._CallMonitorCommand(name, 'system_powerdown')

  def CleanupInstance(self, instance_name):
    """Cleanup after a stopped instance

    """
    pidfile, pid, alive = self._InstancePidAlive(instance_name)
    if pid > 0 and alive:
      raise errors.HypervisorError("Cannot cleanup a live instance")
    self._RemoveInstanceRuntimeFiles(pidfile, instance_name)

  def RebootInstance(self, instance):
    """Reboot an instance.

    """
    # For some reason if we do a 'send-key ctrl-alt-delete' to the control
    # socket the instance will stop, but now power up again. So we'll resort
    # to shutdown and restart.
    _, _, alive = self._InstancePidAlive(instance.name)
    if not alive:
      raise errors.HypervisorError("Failed to reboot instance %s:"
                                   " not running" % instance.name)
    # StopInstance will delete the saved KVM runtime so:
    # ...first load it...
    kvm_runtime = self._LoadKVMRuntime(instance)
    # ...now we can safely call StopInstance...
    if not self.StopInstance(instance):
      self.StopInstance(instance, force=True)
    # ...and finally we can save it again, and execute it...
    self._SaveKVMRuntime(instance, kvm_runtime)
    self._ExecuteKVMRuntime(instance, kvm_runtime)

  def MigrationInfo(self, instance):
    """Get instance information to perform a migration.

    @type instance: L{objects.Instance}
    @param instance: instance to be migrated
    @rtype: string
    @return: content of the KVM runtime file

    """
    return self._ReadKVMRuntime(instance.name)

  def AcceptInstance(self, instance, info, target):
    """Prepare to accept an instance.

    @type instance: L{objects.Instance}
    @param instance: instance to be accepted
    @type info: string
    @param info: content of the KVM runtime file on the source node
    @type target: string
    @param target: target host (usually ip), on this node

    """
    kvm_runtime = self._LoadKVMRuntime(instance, serialized_runtime=info)
    incoming_address = (target, instance.hvparams[constants.HV_MIGRATION_PORT])
    self._ExecuteKVMRuntime(instance, kvm_runtime, incoming=incoming_address)

  def FinalizeMigration(self, instance, info, success):
    """Finalize an instance migration.

    Stop the incoming mode KVM.

    @type instance: L{objects.Instance}
    @param instance: instance whose migration is being finalized

    """
    if success:
      self._WriteKVMRuntime(instance.name, info)
    else:
      self.StopInstance(instance, force=True)

  def MigrateInstance(self, instance, target, live):
    """Migrate an instance to a target node.

    The migration will not be attempted if the instance is not
    currently running.

    @type instance: L{objects.Instance}
    @param instance: the instance to be migrated
    @type target: string
    @param target: ip address of the target node
    @type live: boolean
    @param live: perform a live migration

    """
    instance_name = instance.name
    port = instance.hvparams[constants.HV_MIGRATION_PORT]
    pidfile, pid, alive = self._InstancePidAlive(instance_name)
    if not alive:
      raise errors.HypervisorError("Instance not running, cannot migrate")

    if not utils.TcpPing(target, port, live_port_needed=True):
      raise errors.HypervisorError("Remote host %s not listening on port"
                                   " %s, cannot migrate" % (target, port))

    if not live:
      self._CallMonitorCommand(instance_name, 'stop')

    migrate_command = ('migrate_set_speed %dm' %
        instance.hvparams[constants.HV_MIGRATION_BANDWIDTH])
    self._CallMonitorCommand(instance_name, migrate_command)

    migrate_command = ('migrate_set_downtime %dms' %
        instance.hvparams[constants.HV_MIGRATION_DOWNTIME])
    self._CallMonitorCommand(instance_name, migrate_command)

    migrate_command = 'migrate -d tcp:%s:%s' % (target, port)
    self._CallMonitorCommand(instance_name, migrate_command)

    info_command = 'info migrate'
    done = False
    broken_answers = 0
    while not done:
      result = self._CallMonitorCommand(instance_name, info_command)
      match = self._MIGRATION_STATUS_RE.search(result.stdout)
      if not match:
        broken_answers += 1
        if not result.stdout:
          logging.info("KVM: empty 'info migrate' result")
        else:
          logging.warning("KVM: unknown 'info migrate' result: %s",
                          result.stdout)
        time.sleep(self._MIGRATION_INFO_RETRY_DELAY)
      else:
        status = match.group(1)
        if status == 'completed':
          done = True
        elif status == 'active':
          # reset the broken answers count
          broken_answers = 0
          time.sleep(self._MIGRATION_INFO_RETRY_DELAY)
        elif status == 'failed' or status == 'cancelled':
          if not live:
            self._CallMonitorCommand(instance_name, 'cont')
          raise errors.HypervisorError("Migration %s at the kvm level" %
                                       status)
        else:
          logging.warning("KVM: unknown migration status '%s'", status)
          broken_answers += 1
          time.sleep(self._MIGRATION_INFO_RETRY_DELAY)
      if broken_answers >= self._MIGRATION_INFO_MAX_BAD_ANSWERS:
        raise errors.HypervisorError("Too many 'info migrate' broken answers")

    utils.KillProcess(pid)
    self._RemoveInstanceRuntimeFiles(pidfile, instance_name)

  def GetNodeInfo(self):
    """Return information about the node.

    This is just a wrapper over the base GetLinuxNodeInfo method.

    @return: a dict with the following keys (values in MiB):
          - memory_total: the total memory size on the node
          - memory_free: the available memory on the node for instances
          - memory_dom0: the memory used by the node itself, if available

    """
    return self.GetLinuxNodeInfo()

  @classmethod
  def GetShellCommandForConsole(cls, instance, hvparams, beparams):
    """Return a command for connecting to the console of an instance.

    """
    if hvparams[constants.HV_SERIAL_CONSOLE]:
      shell_command = ("%s STDIO,%s UNIX-CONNECT:%s" %
                       (constants.SOCAT_PATH, cls._SocatUnixConsoleParams(),
                        utils.ShellQuote(cls._InstanceSerial(instance.name))))
    else:
      shell_command = "echo 'No serial shell for instance %s'" % instance.name

    vnc_bind_address = hvparams[constants.HV_VNC_BIND_ADDRESS]
    if vnc_bind_address:
      if instance.network_port > constants.VNC_BASE_PORT:
        display = instance.network_port - constants.VNC_BASE_PORT
        vnc_command = ("echo 'Instance has VNC listening on %s:%d"
                       " (display: %d)'" % (vnc_bind_address,
                                            instance.network_port,
                                            display))
        shell_command = "%s; %s" % (vnc_command, shell_command)

    return shell_command

  def Verify(self):
    """Verify the hypervisor.

    Check that the binary exists.

    """
    if not os.path.exists(constants.KVM_PATH):
      return "The kvm binary ('%s') does not exist." % constants.KVM_PATH
    if not os.path.exists(constants.SOCAT_PATH):
      return "The socat binary ('%s') does not exist." % constants.SOCAT_PATH


  @classmethod
  def CheckParameterSyntax(cls, hvparams):
    """Check the given parameters for validity.

    @type hvparams:  dict
    @param hvparams: dictionary with parameter names/value
    @raise errors.HypervisorError: when a parameter is not valid

    """
    super(KVMHypervisor, cls).CheckParameterSyntax(hvparams)

    kernel_path = hvparams[constants.HV_KERNEL_PATH]
    if kernel_path:
      if not hvparams[constants.HV_ROOT_PATH]:
        raise errors.HypervisorError("Need a root partition for the instance,"
                                     " if a kernel is defined")

    if (hvparams[constants.HV_VNC_X509_VERIFY] and
        not hvparams[constants.HV_VNC_X509]):
      raise errors.HypervisorError("%s must be defined, if %s is" %
                                   (constants.HV_VNC_X509,
                                    constants.HV_VNC_X509_VERIFY))

    boot_order = hvparams[constants.HV_BOOT_ORDER]
    if (boot_order == constants.HT_BO_CDROM and
        not hvparams[constants.HV_CDROM_IMAGE_PATH]):
      raise errors.HypervisorError("Cannot boot from cdrom without an"
                                   " ISO path")

    security_model = hvparams[constants.HV_SECURITY_MODEL]
    if security_model == constants.HT_SM_USER:
      if not hvparams[constants.HV_SECURITY_DOMAIN]:
        raise errors.HypervisorError("A security domain (user to run kvm as)"
                                     " must be specified")
    elif (security_model == constants.HT_SM_NONE or
          security_model == constants.HT_SM_POOL):
      if hvparams[constants.HV_SECURITY_DOMAIN]:
        raise errors.HypervisorError("Cannot have a security domain when the"
                                     " security model is 'none' or 'pool'")

  @classmethod
  def ValidateParameters(cls, hvparams):
    """Check the given parameters for validity.

    @type hvparams:  dict
    @param hvparams: dictionary with parameter names/value
    @raise errors.HypervisorError: when a parameter is not valid

    """
    super(KVMHypervisor, cls).ValidateParameters(hvparams)

    security_model = hvparams[constants.HV_SECURITY_MODEL]
    if security_model == constants.HT_SM_USER:
      username = hvparams[constants.HV_SECURITY_DOMAIN]
      try:
        pwd.getpwnam(username)
      except KeyError:
        raise errors.HypervisorError("Unknown security domain user %s"
                                     % username)

  @classmethod
  def PowercycleNode(cls):
    """KVM powercycle, just a wrapper over Linux powercycle.

    """
    cls.LinuxPowercycle()
