#
#

# Copyright (C) 2013 Google Inc.
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

"""Utility functions for security features of Ganeti.

"""

import logging
import OpenSSL
import os
import uuid as uuid_module

from ganeti.utils import io
from ganeti.utils import x509
from ganeti import constants
from ganeti import errors
from ganeti import pathutils


def UuidToInt(uuid):
  uuid_obj = uuid_module.UUID(uuid)
  return uuid_obj.int # pylint: disable=E1101


def AddNodeToCandidateCerts(node_uuid, cert_digest, candidate_certs,
                            info_fn=logging.info, warn_fn=logging.warn):
  """Adds an entry to the candidate certificate map.

  @type node_uuid: string
  @param node_uuid: the node's UUID
  @type cert_digest: string
  @param cert_digest: the digest of the node's client SSL certificate
  @type candidate_certs: dict of strings to strings
  @param candidate_certs: map of node UUIDs to the digests of their client
      SSL certificates, will be manipulated in this function
  @type info_fn: function
  @param info_fn: logging function for information messages
  @type warn_fn: function
  @param warn_fn: logging function for warning messages

  """
  assert candidate_certs is not None

  if node_uuid in candidate_certs:
    old_cert_digest = candidate_certs[node_uuid]
    if old_cert_digest == cert_digest:
      info_fn("Certificate digest for node %s already in config."
              "Not doing anything." % node_uuid)
      return
    else:
      warn_fn("Overriding differing certificate digest for node %s"
              % node_uuid)
  candidate_certs[node_uuid] = cert_digest


def RemoveNodeFromCandidateCerts(node_uuid, candidate_certs,
                                 warn_fn=logging.warn):
  """Removes the entry of the given node in the certificate map.

  @type node_uuid: string
  @param node_uuid: the node's UUID
  @type candidate_certs: dict of strings to strings
  @param candidate_certs: map of node UUIDs to the digests of their client
      SSL certificates, will be manipulated in this function
  @type warn_fn: function
  @param warn_fn: logging function for warning messages

  """
  if node_uuid not in candidate_certs:
    warn_fn("Cannot remove certifcate for node %s, because it's not in the"
            "candidate map." % node_uuid)
    return
  del candidate_certs[node_uuid]


def GetCertificateDigest(cert_filename=pathutils.NODED_CLIENT_CERT_FILE):
  """Reads the SSL certificate and returns the sha1 digest.

  """
  cert_plain = io.ReadFile(cert_filename)
  cert = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM,
                                         cert_plain)
  return cert.digest("sha1")


def GenerateNewSslCert(new_cert, cert_filename, serial_no, log_msg,
                       uid=-1, gid=-1):
  """Creates a new SSL certificate and backups the old one.

  @type new_cert: boolean
  @param new_cert: whether a new certificate should be created
  @type cert_filename: string
  @param cert_filename: filename of the certificate file
  @type serial_no: int
  @param serial_no: serial number of the certificate
  @type log_msg: string
  @param log_msg: log message to be written on certificate creation
  @type uid: int
  @param uid: the user ID of the user who will be owner of the certificate file
  @type gid: int
  @param gid: the group ID of the group who will own the certificate file

  """
  cert_exists = os.path.exists(cert_filename)
  if new_cert or not cert_exists:
    if cert_exists:
      io.CreateBackup(cert_filename)

    logging.debug(log_msg)
    x509.GenerateSelfSignedSslCert(cert_filename, serial_no, uid=uid, gid=gid)


def VerifyCertificate(filename):
  """Verifies a SSL certificate.

  @type filename: string
  @param filename: Path to PEM file

  """
  try:
    cert = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM,
                                           io.ReadFile(filename))
  except Exception, err: # pylint: disable=W0703
    return (constants.CV_ERROR,
            "Failed to load X509 certificate %s: %s" % (filename, err))

  (errcode, msg) = \
    x509.VerifyX509Certificate(cert, constants.SSL_CERT_EXPIRATION_WARN,
                                constants.SSL_CERT_EXPIRATION_ERROR)

  if msg:
    fnamemsg = "While verifying %s: %s" % (filename, msg)
  else:
    fnamemsg = None

  if errcode is None:
    return (None, fnamemsg)
  elif errcode == x509.CERT_WARNING:
    return (constants.CV_WARNING, fnamemsg)
  elif errcode == x509.CERT_ERROR:
    return (constants.CV_ERROR, fnamemsg)

  raise errors.ProgrammerError("Unhandled certificate error code %r" % errcode)
