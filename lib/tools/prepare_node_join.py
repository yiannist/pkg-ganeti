#
#

# Copyright (C) 2012 Google Inc.
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

"""Script to prepare a node for joining a cluster.

"""

import os
import os.path
import optparse
import sys
import logging
import OpenSSL

from ganeti import cli
from ganeti import constants
from ganeti import errors
from ganeti import pathutils
from ganeti import utils
from ganeti import serializer
from ganeti import ht
from ganeti import ssh
from ganeti import ssconf


_SSH_KEY_LIST_ITEM = \
  ht.TAnd(ht.TIsLength(3),
          ht.TItems([
            ht.TElemOf(constants.SSHK_ALL),
            ht.Comment("public")(ht.TNonEmptyString),
            ht.Comment("private")(ht.TNonEmptyString),
          ]))

_SSH_KEY_LIST = ht.TListOf(_SSH_KEY_LIST_ITEM)

_DATA_CHECK = ht.TStrictDict(False, True, {
  constants.SSHS_CLUSTER_NAME: ht.TNonEmptyString,
  constants.SSHS_NODE_DAEMON_CERTIFICATE: ht.TNonEmptyString,
  constants.SSHS_SSH_HOST_KEY: _SSH_KEY_LIST,
  constants.SSHS_SSH_ROOT_KEY: _SSH_KEY_LIST,
  })


class JoinError(errors.GenericError):
  """Local class for reporting errors.

  """


def ParseOptions():
  """Parses the options passed to the program.

  @return: Options and arguments

  """
  program = os.path.basename(sys.argv[0])

  parser = optparse.OptionParser(usage="%prog [--dry-run]",
                                 prog=program)
  parser.add_option(cli.DEBUG_OPT)
  parser.add_option(cli.VERBOSE_OPT)
  parser.add_option(cli.DRY_RUN_OPT)

  (opts, args) = parser.parse_args()

  return VerifyOptions(parser, opts, args)


def VerifyOptions(parser, opts, args):
  """Verifies options and arguments for correctness.

  """
  if args:
    parser.error("No arguments are expected")

  return opts


def _VerifyCertificate(cert_pem, _check_fn=utils.CheckNodeCertificate):
  """Verifies a certificate against the local node daemon certificate.

  @type cert_pem: string
  @param cert_pem: Certificate in PEM format (no key)

  """
  try:
    OpenSSL.crypto.load_privatekey(OpenSSL.crypto.FILETYPE_PEM, cert_pem)
  except OpenSSL.crypto.Error, err:
    pass
  else:
    raise JoinError("No private key may be given")

  try:
    cert = \
      OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, cert_pem)
  except Exception, err:
    raise errors.X509CertError("(stdin)",
                               "Unable to load certificate: %s" % err)

  _check_fn(cert)


def VerifyCertificate(data, _verify_fn=_VerifyCertificate):
  """Verifies cluster certificate.

  @type data: dict

  """
  cert = data.get(constants.SSHS_NODE_DAEMON_CERTIFICATE)
  if cert:
    _verify_fn(cert)


def VerifyClusterName(data, _verify_fn=ssconf.VerifyClusterName):
  """Verifies cluster name.

  @type data: dict

  """
  name = data.get(constants.SSHS_CLUSTER_NAME)
  if name:
    _verify_fn(name)
  else:
    raise JoinError("Cluster name must be specified")


def _UpdateKeyFiles(keys, dry_run, keyfiles):
  """Updates SSH key files.

  @type keys: sequence of tuple; (string, string, string)
  @param keys: Keys to write, tuples consist of key type
    (L{constants.SSHK_ALL}), public and private key
  @type dry_run: boolean
  @param dry_run: Whether to perform a dry run
  @type keyfiles: dict; (string as key, tuple with (string, string) as values)
  @param keyfiles: Mapping from key types (L{constants.SSHK_ALL}) to file
    names; value tuples consist of public key filename and private key filename

  """
  assert set(keyfiles) == constants.SSHK_ALL

  for (kind, private_key, public_key) in keys:
    (private_file, public_file) = keyfiles[kind]

    logging.debug("Writing %s ...", private_file)
    utils.WriteFile(private_file, data=private_key, mode=0600,
                    backup=True, dry_run=dry_run)

    logging.debug("Writing %s ...", public_file)
    utils.WriteFile(public_file, data=public_key, mode=0644,
                    backup=True, dry_run=dry_run)


def UpdateSshDaemon(data, dry_run, _runcmd_fn=utils.RunCmd,
                    _keyfiles=None):
  """Updates SSH daemon's keys.

  Unless C{dry_run} is set, the daemon is restarted at the end.

  @type data: dict
  @param data: Input data
  @type dry_run: boolean
  @param dry_run: Whether to perform a dry run

  """
  keys = data.get(constants.SSHS_SSH_HOST_KEY)
  if not keys:
    return

  if _keyfiles is None:
    _keyfiles = constants.SSH_DAEMON_KEYFILES

  logging.info("Updating SSH daemon key files")
  _UpdateKeyFiles(keys, dry_run, _keyfiles)

  if dry_run:
    logging.info("This is a dry run, not restarting SSH daemon")
  else:
    result = _runcmd_fn([pathutils.DAEMON_UTIL, "reload-ssh-keys"],
                        interactive=True)
    if result.failed:
      raise JoinError("Could not reload SSH keys, command '%s'"
                      " had exitcode %s and error %s" %
                       (result.cmd, result.exit_code, result.output))


def UpdateSshRoot(data, dry_run, _homedir_fn=None):
  """Updates root's SSH keys.

  Root's C{authorized_keys} file is also updated with new public keys.

  @type data: dict
  @param data: Input data
  @type dry_run: boolean
  @param dry_run: Whether to perform a dry run

  """
  keys = data.get(constants.SSHS_SSH_ROOT_KEY)
  if not keys:
    return

  (auth_keys_file, keyfiles) = \
    ssh.GetAllUserFiles(constants.SSH_LOGIN_USER, mkdir=True,
                        _homedir_fn=_homedir_fn)

  _UpdateKeyFiles(keys, dry_run, keyfiles)

  if dry_run:
    logging.info("This is a dry run, not modifying %s", auth_keys_file)
  else:
    for (_, _, public_key) in keys:
      utils.AddAuthorizedKey(auth_keys_file, public_key)


def LoadData(raw):
  """Parses and verifies input data.

  @rtype: dict

  """
  return serializer.LoadAndVerifyJson(raw, _DATA_CHECK)


def Main():
  """Main routine.

  """
  opts = ParseOptions()

  utils.SetupToolLogging(opts.debug, opts.verbose)

  try:
    data = LoadData(sys.stdin.read())

    # Check if input data is correct
    VerifyClusterName(data)
    VerifyCertificate(data)

    # Update SSH files
    UpdateSshDaemon(data, opts.dry_run)
    UpdateSshRoot(data, opts.dry_run)

    logging.info("Setup finished successfully")
  except Exception, err: # pylint: disable=W0703
    logging.debug("Caught unhandled exception", exc_info=True)

    (retcode, message) = cli.FormatError(err)
    logging.error(message)

    return retcode
  else:
    return constants.EXIT_SUCCESS
