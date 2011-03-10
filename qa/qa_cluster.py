#
#

# Copyright (C) 2007, 2010, 2011 Google Inc.
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


"""Cluster related QA tests.

"""

import tempfile
import os.path

from ganeti import constants
from ganeti import utils

import qa_config
import qa_utils
import qa_error

from qa_utils import AssertEqual, AssertCommand


def _RemoveFileFromAllNodes(filename):
  """Removes a file from all nodes.

  """
  for node in qa_config.get("nodes"):
    AssertCommand(["rm", "-f", filename], node=node)


def _CheckFileOnAllNodes(filename, content):
  """Verifies the content of the given file on all nodes.

  """
  cmd = utils.ShellQuoteArgs(["cat", filename])
  for node in qa_config.get("nodes"):
    AssertEqual(qa_utils.GetCommandOutput(node["primary"], cmd), content)


def TestClusterInit(rapi_user, rapi_secret):
  """gnt-cluster init"""
  master = qa_config.GetMasterNode()

  rapi_dir = os.path.dirname(constants.RAPI_USERS_FILE)

  # First create the RAPI credentials
  fh = tempfile.NamedTemporaryFile()
  try:
    fh.write("%s %s write\n" % (rapi_user, rapi_secret))
    fh.flush()

    tmpru = qa_utils.UploadFile(master["primary"], fh.name)
    try:
      AssertCommand(["mkdir", "-p", rapi_dir])
      AssertCommand(["mv", tmpru, constants.RAPI_USERS_FILE])
    finally:
      AssertCommand(["rm", "-f", tmpru])
  finally:
    fh.close()

  # Initialize cluster
  cmd = ['gnt-cluster', 'init']

  cmd.append("--primary-ip-version=%d" %
             qa_config.get("primary_ip_version", 4))

  if master.get('secondary', None):
    cmd.append('--secondary-ip=%s' % master['secondary'])

  bridge = qa_config.get('bridge', None)
  if bridge:
    cmd.append('--bridge=%s' % bridge)
    cmd.append('--master-netdev=%s' % bridge)

  htype = qa_config.get('enabled-hypervisors', None)
  if htype:
    cmd.append('--enabled-hypervisors=%s' % htype)

  cmd.append(qa_config.get('name'))

  AssertCommand(cmd)


def TestClusterRename():
  """gnt-cluster rename"""
  cmd = ['gnt-cluster', 'rename', '-f']

  original_name = qa_config.get('name')
  rename_target = qa_config.get('rename', None)
  if rename_target is None:
    print qa_utils.FormatError('"rename" entry is missing')
    return

  cmd_verify = ['gnt-cluster', 'verify']

  for data in [
    cmd + [rename_target],
    cmd_verify,
    cmd + [original_name],
    cmd_verify,
    ]:
    AssertCommand(data)


def TestClusterOob():
  """out-of-band framework"""
  oob_path_exists = "/tmp/ganeti-qa-oob-does-exist-%s" % utils.NewUUID()

  AssertCommand(["gnt-cluster", "verify"])
  AssertCommand(["gnt-cluster", "modify", "--node-parameters",
                 "oob_program=/tmp/ganeti-qa-oob-does-not-exist-%s" %
                 utils.NewUUID()])

  AssertCommand(["gnt-cluster", "verify"], fail=True)

  AssertCommand(["touch", oob_path_exists])
  AssertCommand(["chmod", "0400", oob_path_exists])
  AssertCommand(["gnt-cluster", "copyfile", oob_path_exists])

  try:
    AssertCommand(["gnt-cluster", "modify", "--node-parameters",
                   "oob_program=%s" % oob_path_exists])

    AssertCommand(["gnt-cluster", "verify"], fail=True)

    AssertCommand(["chmod", "0500", oob_path_exists])
    AssertCommand(["gnt-cluster", "copyfile", oob_path_exists])

    AssertCommand(["gnt-cluster", "verify"])
  finally:
    AssertCommand(["gnt-cluster", "command", "rm", oob_path_exists])

  AssertCommand(["gnt-cluster", "modify", "--node-parameters",
                 "oob_program="])


def TestClusterVerify():
  """gnt-cluster verify"""
  AssertCommand(["gnt-cluster", "verify"])
  AssertCommand(["gnt-cluster", "verify-disks"])


def TestJobqueue():
  """gnt-debug test-jobqueue"""
  AssertCommand(["gnt-debug", "test-jobqueue"])


def TestClusterReservedLvs():
  """gnt-cluster reserved lvs"""
  CVERIFY = ["gnt-cluster", "verify"]
  for fail, cmd in [
    (False, CVERIFY),
    (False, ["gnt-cluster", "modify", "--reserved-lvs", ""]),
    (False, ["lvcreate", "-L1G", "-nqa-test", "xenvg"]),
    (True,  CVERIFY),
    (False, ["gnt-cluster", "modify", "--reserved-lvs",
             "xenvg/qa-test,.*/other-test"]),
    (False, CVERIFY),
    (False, ["gnt-cluster", "modify", "--reserved-lvs", ".*/qa-.*"]),
    (False, CVERIFY),
    (False, ["gnt-cluster", "modify", "--reserved-lvs", ""]),
    (True,  CVERIFY),
    (False, ["lvremove", "-f", "xenvg/qa-test"]),
    (False, CVERIFY),
    ]:
    AssertCommand(cmd, fail=fail)


def TestClusterModifyBe():
  """gnt-cluster modify -B"""
  for fail, cmd in [
    # mem
    (False, ["gnt-cluster", "modify", "-B", "memory=256"]),
    (False, ["sh", "-c", "gnt-cluster info|grep '^ *memory: 256$'"]),
    (True,  ["gnt-cluster", "modify", "-B", "memory=a"]),
    (False, ["gnt-cluster", "modify", "-B", "memory=128"]),
    (False, ["sh", "-c", "gnt-cluster info|grep '^ *memory: 128$'"]),
    # vcpus
    (False, ["gnt-cluster", "modify", "-B", "vcpus=4"]),
    (False, ["sh", "-c", "gnt-cluster info|grep '^ *vcpus: 4$'"]),
    (True,  ["gnt-cluster", "modify", "-B", "vcpus=a"]),
    (False, ["gnt-cluster", "modify", "-B", "vcpus=1"]),
    (False, ["sh", "-c", "gnt-cluster info|grep '^ *vcpus: 1$'"]),
    # auto_balance
    (False, ["gnt-cluster", "modify", "-B", "auto_balance=False"]),
    (False, ["sh", "-c", "gnt-cluster info|grep '^ *auto_balance: False$'"]),
    (True,  ["gnt-cluster", "modify", "-B", "auto_balance=1"]),
    (False, ["gnt-cluster", "modify", "-B", "auto_balance=True"]),
    (False, ["sh", "-c", "gnt-cluster info|grep '^ *auto_balance: True$'"]),
    ]:
    AssertCommand(cmd, fail=fail)


def TestClusterInfo():
  """gnt-cluster info"""
  AssertCommand(["gnt-cluster", "info"])


def TestClusterGetmaster():
  """gnt-cluster getmaster"""
  AssertCommand(["gnt-cluster", "getmaster"])


def TestClusterVersion():
  """gnt-cluster version"""
  AssertCommand(["gnt-cluster", "version"])


def TestClusterRenewCrypto():
  """gnt-cluster renew-crypto"""
  master = qa_config.GetMasterNode()

  # Conflicting options
  cmd = ["gnt-cluster", "renew-crypto", "--force",
         "--new-cluster-certificate", "--new-confd-hmac-key"]
  conflicting = [
    ["--new-rapi-certificate", "--rapi-certificate=/dev/null"],
    ["--new-cluster-domain-secret", "--cluster-domain-secret=/dev/null"],
    ]
  for i in conflicting:
    AssertCommand(cmd+i, fail=True)

  # Invalid RAPI certificate
  cmd = ["gnt-cluster", "renew-crypto", "--force",
         "--rapi-certificate=/dev/null"]
  AssertCommand(cmd, fail=True)

  rapi_cert_backup = qa_utils.BackupFile(master["primary"],
                                         constants.RAPI_CERT_FILE)
  try:
    # Custom RAPI certificate
    fh = tempfile.NamedTemporaryFile()

    # Ensure certificate doesn't cause "gnt-cluster verify" to complain
    validity = constants.SSL_CERT_EXPIRATION_WARN * 3

    utils.GenerateSelfSignedSslCert(fh.name, validity=validity)

    tmpcert = qa_utils.UploadFile(master["primary"], fh.name)
    try:
      AssertCommand(["gnt-cluster", "renew-crypto", "--force",
                     "--rapi-certificate=%s" % tmpcert])
    finally:
      AssertCommand(["rm", "-f", tmpcert])

    # Custom cluster domain secret
    cds_fh = tempfile.NamedTemporaryFile()
    cds_fh.write(utils.GenerateSecret())
    cds_fh.write("\n")
    cds_fh.flush()

    tmpcds = qa_utils.UploadFile(master["primary"], cds_fh.name)
    try:
      AssertCommand(["gnt-cluster", "renew-crypto", "--force",
                     "--cluster-domain-secret=%s" % tmpcds])
    finally:
      AssertCommand(["rm", "-f", tmpcds])

    # Normal case
    AssertCommand(["gnt-cluster", "renew-crypto", "--force",
                   "--new-cluster-certificate", "--new-confd-hmac-key",
                   "--new-rapi-certificate", "--new-cluster-domain-secret"])

    # Restore RAPI certificate
    AssertCommand(["gnt-cluster", "renew-crypto", "--force",
                   "--rapi-certificate=%s" % rapi_cert_backup])
  finally:
    AssertCommand(["rm", "-f", rapi_cert_backup])


def TestClusterBurnin():
  """Burnin"""
  master = qa_config.GetMasterNode()

  options = qa_config.get('options', {})
  disk_template = options.get('burnin-disk-template', 'drbd')
  parallel = options.get('burnin-in-parallel', False)
  check_inst = options.get('burnin-check-instances', False)
  do_rename = options.get('burnin-rename', '')
  do_reboot = options.get('burnin-reboot', True)
  reboot_types = options.get("reboot-types", constants.REBOOT_TYPES)

  # Get as many instances as we need
  instances = []
  try:
    try:
      num = qa_config.get('options', {}).get('burnin-instances', 1)
      for _ in range(0, num):
        instances.append(qa_config.AcquireInstance())
    except qa_error.OutOfInstancesError:
      print "Not enough instances, continuing anyway."

    if len(instances) < 1:
      raise qa_error.Error("Burnin needs at least one instance")

    script = qa_utils.UploadFile(master['primary'], '../tools/burnin')
    try:
      # Run burnin
      cmd = [script,
             '--os=%s' % qa_config.get('os'),
             '--disk-size=%s' % ",".join(qa_config.get('disk')),
             '--disk-growth=%s' % ",".join(qa_config.get('disk-growth')),
             '--disk-template=%s' % disk_template]
      if parallel:
        cmd.append('--parallel')
        cmd.append('--early-release')
      if check_inst:
        cmd.append('--http-check')
      if do_rename:
        cmd.append('--rename=%s' % do_rename)
      if not do_reboot:
        cmd.append('--no-reboot')
      else:
        cmd.append('--reboot-types=%s' % ",".join(reboot_types))
      cmd += [inst['name'] for inst in instances]
      AssertCommand(cmd)
    finally:
      AssertCommand(["rm", "-f", script])

  finally:
    for inst in instances:
      qa_config.ReleaseInstance(inst)


def TestClusterMasterFailover():
  """gnt-cluster master-failover"""
  master = qa_config.GetMasterNode()
  failovermaster = qa_config.AcquireNode(exclude=master)

  cmd = ["gnt-cluster", "master-failover"]
  try:
    AssertCommand(cmd, node=failovermaster)
    AssertCommand(cmd, node=master)
  finally:
    qa_config.ReleaseNode(failovermaster)


def TestClusterCopyfile():
  """gnt-cluster copyfile"""
  master = qa_config.GetMasterNode()

  uniqueid = utils.NewUUID()

  # Create temporary file
  f = tempfile.NamedTemporaryFile()
  f.write(uniqueid)
  f.flush()
  f.seek(0)

  # Upload file to master node
  testname = qa_utils.UploadFile(master['primary'], f.name)
  try:
    # Copy file to all nodes
    AssertCommand(["gnt-cluster", "copyfile", testname])
    _CheckFileOnAllNodes(testname, uniqueid)
  finally:
    _RemoveFileFromAllNodes(testname)


def TestClusterCommand():
  """gnt-cluster command"""
  uniqueid = utils.NewUUID()
  rfile = "/tmp/gnt%s" % utils.NewUUID()
  rcmd = utils.ShellQuoteArgs(['echo', '-n', uniqueid])
  cmd = utils.ShellQuoteArgs(['gnt-cluster', 'command',
                              "%s >%s" % (rcmd, rfile)])

  try:
    AssertCommand(cmd)
    _CheckFileOnAllNodes(rfile, uniqueid)
  finally:
    _RemoveFileFromAllNodes(rfile)


def TestClusterDestroy():
  """gnt-cluster destroy"""
  AssertCommand(["gnt-cluster", "destroy", "--yes-do-it"])
