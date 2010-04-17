#
#

# Copyright (C) 2009 Google Inc.
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


"""Ganeti confd client

Clients can use the confd client library to send requests to a group of master
candidates running confd. The expected usage is through the asyncore framework,
by sending queries, and asynchronously receiving replies through a callback.

This way the client library doesn't ever need to "wait" on a particular answer,
and can proceed even if some udp packets are lost. It's up to the user to
reschedule queries if they haven't received responses and they need them.

Example usage::

  client = ConfdClient(...) # includes callback specification
  req = confd_client.ConfdClientRequest(type=constants.CONFD_REQ_PING)
  client.SendRequest(req)
  # then make sure your client calls asyncore.loop() or daemon.Mainloop.Run()
  # ... wait ...
  # And your callback will be called by asyncore, when your query gets a
  # response, or when it expires.

You can use the provided ConfdFilterCallback to act as a filter, only passing
"newer" answer to your callback, and filtering out outdated ones, or ones
confirming what you already got.

"""

# pylint: disable-msg=E0203

# E0203: Access to member %r before its definition, since we use
# objects.py which doesn't explicitely initialise its members

import time
import random

from ganeti import utils
from ganeti import constants
from ganeti import objects
from ganeti import serializer
from ganeti import daemon # contains AsyncUDPSocket
from ganeti import errors
from ganeti import confd


class ConfdAsyncUDPClient(daemon.AsyncUDPSocket):
  """Confd udp asyncore client

  This is kept separate from the main ConfdClient to make sure it's easy to
  implement a non-asyncore based client library.

  """
  def __init__(self, client):
    """Constructor for ConfdAsyncUDPClient

    @type client: L{ConfdClient}
    @param client: client library, to pass the datagrams to

    """
    daemon.AsyncUDPSocket.__init__(self)
    self.client = client

  # this method is overriding a daemon.AsyncUDPSocket method
  def handle_datagram(self, payload, ip, port):
    self.client.HandleResponse(payload, ip, port)


class ConfdClient:
  """Send queries to confd, and get back answers.

  Since the confd model works by querying multiple master candidates, and
  getting back answers, this is an asynchronous library. It can either work
  through asyncore or with your own handling.

  """
  def __init__(self, hmac_key, peers, callback, port=None, logger=None):
    """Constructor for ConfdClient

    @type hmac_key: string
    @param hmac_key: hmac key to talk to confd
    @type peers: list
    @param peers: list of peer nodes
    @type callback: f(L{ConfdUpcallPayload})
    @param callback: function to call when getting answers
    @type port: integer
    @keyword port: confd port (default: use GetDaemonPort)
    @type logger: logging.Logger
    @keyword logger: optional logger for internal conditions

    """
    if not callable(callback):
      raise errors.ProgrammerError("callback must be callable")

    self.UpdatePeerList(peers)
    self._hmac_key = hmac_key
    self._socket = ConfdAsyncUDPClient(self)
    self._callback = callback
    self._confd_port = port
    self._logger = logger
    self._requests = {}
    self._expire_requests = []

    if self._confd_port is None:
      self._confd_port = utils.GetDaemonPort(constants.CONFD)

  def UpdatePeerList(self, peers):
    """Update the list of peers

    @type peers: list
    @param peers: list of peer nodes

    """
    # we are actually called from init, so:
    # pylint: disable-msg=W0201
    if not isinstance(peers, list):
      raise errors.ProgrammerError("peers must be a list")
    # make a copy of peers, since we're going to shuffle the list, later
    self._peers = list(peers)

  def _PackRequest(self, request, now=None):
    """Prepare a request to be sent on the wire.

    This function puts a proper salt in a confd request, puts the proper salt,
    and adds the correct magic number.

    """
    if now is None:
      now = time.time()
    tstamp = '%d' % now
    req = serializer.DumpSignedJson(request.ToDict(), self._hmac_key, tstamp)
    return confd.PackMagic(req)

  def _UnpackReply(self, payload):
    in_payload = confd.UnpackMagic(payload)
    (dict_answer, salt) = serializer.LoadSignedJson(in_payload, self._hmac_key)
    answer = objects.ConfdReply.FromDict(dict_answer)
    return answer, salt

  def ExpireRequests(self):
    """Delete all the expired requests.

    """
    now = time.time()
    while self._expire_requests:
      expire_time, rsalt = self._expire_requests[0]
      if now >= expire_time:
        self._expire_requests.pop(0)
        (request, args) = self._requests[rsalt]
        del self._requests[rsalt]
        client_reply = ConfdUpcallPayload(salt=rsalt,
                                          type=UPCALL_EXPIRE,
                                          orig_request=request,
                                          extra_args=args,
                                          client=self,
                                          )
        self._callback(client_reply)
      else:
        break

  def SendRequest(self, request, args=None, coverage=None):
    """Send a confd request to some MCs

    @type request: L{objects.ConfdRequest}
    @param request: the request to send
    @type args: tuple
    @keyword args: additional callback arguments
    @type coverage: integer
    @keyword coverage: number of remote nodes to contact

    """
    if coverage is None:
      coverage = min(len(self._peers), constants.CONFD_DEFAULT_REQ_COVERAGE)

    if coverage > len(self._peers):
      raise errors.ConfdClientError("Not enough MCs known to provide the"
                                    " desired coverage")

    if not request.rsalt:
      raise errors.ConfdClientError("Missing request rsalt")

    self.ExpireRequests()
    if request.rsalt in self._requests:
      raise errors.ConfdClientError("Duplicate request rsalt")

    if request.type not in constants.CONFD_REQS:
      raise errors.ConfdClientError("Invalid request type")

    random.shuffle(self._peers)
    targets = self._peers[:coverage]

    now = time.time()
    payload = self._PackRequest(request, now=now)

    for target in targets:
      try:
        self._socket.enqueue_send(target, self._confd_port, payload)
      except errors.UdpDataSizeError:
        raise errors.ConfdClientError("Request too big")

    self._requests[request.rsalt] = (request, args)
    expire_time = now + constants.CONFD_CLIENT_EXPIRE_TIMEOUT
    self._expire_requests.append((expire_time, request.rsalt))

  def HandleResponse(self, payload, ip, port):
    """Asynchronous handler for a confd reply

    Call the relevant callback associated to the current request.

    """
    try:
      try:
        answer, salt = self._UnpackReply(payload)
      except (errors.SignatureError, errors.ConfdMagicError), err:
        if self._logger:
          self._logger.debug("Discarding broken package: %s" % err)
        return

      try:
        (request, args) = self._requests[salt]
      except KeyError:
        if self._logger:
          self._logger.debug("Discarding unknown (expired?) reply: %s" % err)
        return

      client_reply = ConfdUpcallPayload(salt=salt,
                                        type=UPCALL_REPLY,
                                        server_reply=answer,
                                        orig_request=request,
                                        server_ip=ip,
                                        server_port=port,
                                        extra_args=args,
                                        client=self,
                                       )
      self._callback(client_reply)

    finally:
      self.ExpireRequests()


# UPCALL_REPLY: server reply upcall
# has all ConfdUpcallPayload fields populated
UPCALL_REPLY = 1
# UPCALL_EXPIRE: internal library request expire
# has only salt, type, orig_request and extra_args
UPCALL_EXPIRE = 2
CONFD_UPCALL_TYPES = frozenset([
  UPCALL_REPLY,
  UPCALL_EXPIRE,
  ])


class ConfdUpcallPayload(objects.ConfigObject):
  """Callback argument for confd replies

  @type salt: string
  @ivar salt: salt associated with the query
  @type type: one of confd.client.CONFD_UPCALL_TYPES
  @ivar type: upcall type (server reply, expired request, ...)
  @type orig_request: L{objects.ConfdRequest}
  @ivar orig_request: original request
  @type server_reply: L{objects.ConfdReply}
  @ivar server_reply: server reply
  @type server_ip: string
  @ivar server_ip: answering server ip address
  @type server_port: int
  @ivar server_port: answering server port
  @type extra_args: any
  @ivar extra_args: 'args' argument of the SendRequest function
  @type client: L{ConfdClient}
  @ivar client: current confd client instance

  """
  __slots__ = [
    "salt",
    "type",
    "orig_request",
    "server_reply",
    "server_ip",
    "server_port",
    "extra_args",
    "client",
    ]


class ConfdClientRequest(objects.ConfdRequest):
  """This is the client-side version of ConfdRequest.

  This version of the class helps creating requests, on the client side, by
  filling in some default values.

  """
  def __init__(self, **kwargs):
    objects.ConfdRequest.__init__(self, **kwargs)
    if not self.rsalt:
      self.rsalt = utils.NewUUID()
    if not self.protocol:
      self.protocol = constants.CONFD_PROTOCOL_VERSION
    if self.type not in constants.CONFD_REQS:
      raise errors.ConfdClientError("Invalid request type")


class ConfdFilterCallback:
  """Callback that calls another callback, but filters duplicate results.

  """
  def __init__(self, callback, logger=None):
    """Constructor for ConfdFilterCallback

    @type callback: f(L{ConfdUpcallPayload})
    @param callback: function to call when getting answers
    @type logger: logging.Logger
    @keyword logger: optional logger for internal conditions

    """
    if not callable(callback):
      raise errors.ProgrammerError("callback must be callable")

    self._callback = callback
    self._logger = logger
    # answers contains a dict of salt -> answer
    self._answers = {}

  def _LogFilter(self, salt, new_reply, old_reply):
    if not self._logger:
      return

    if new_reply.serial > old_reply.serial:
      self._logger.debug("Filtering confirming answer, with newer"
                         " serial for query %s" % salt)
    elif new_reply.serial == old_reply.serial:
      if new_reply.answer != old_reply.answer:
        self._logger.warning("Got incoherent answers for query %s"
                             " (serial: %s)" % (salt, new_reply.serial))
      else:
        self._logger.debug("Filtering confirming answer, with same"
                           " serial for query %s" % salt)
    else:
      self._logger.debug("Filtering outdated answer for query %s"
                         " serial: (%d < %d)" % (salt, old_reply.serial,
                                                 new_reply.serial))

  def _HandleExpire(self, up):
    # if we have no answer we have received none, before the expiration.
    if up.salt in self._answers:
      del self._answers[up.salt]

  def _HandleReply(self, up):
    """Handle a single confd reply, and decide whether to filter it.

    @rtype: boolean
    @return: True if the reply should be filtered, False if it should be passed
             on to the up-callback

    """
    filter_upcall = False
    salt = up.salt
    if salt not in self._answers:
      # first answer for a query (don't filter, and record)
      self._answers[salt] = up.server_reply
    elif up.server_reply.serial > self._answers[salt].serial:
      # newer answer (record, and compare contents)
      old_answer = self._answers[salt]
      self._answers[salt] = up.server_reply
      if up.server_reply.answer == old_answer.answer:
        # same content (filter) (version upgrade was unrelated)
        filter_upcall = True
        self._LogFilter(salt, up.server_reply, old_answer)
      # else: different content, pass up a second answer
    else:
      # older or same-version answer (duplicate or outdated, filter)
      filter_upcall = True
      self._LogFilter(salt, up.server_reply, self._answers[salt])

    return filter_upcall

  def __call__(self, up):
    """Filtering callback

    @type up: L{ConfdUpcallPayload}
    @param up: upper callback

    """
    filter_upcall = False
    if up.type == UPCALL_REPLY:
      filter_upcall = self._HandleReply(up)
    elif up.type == UPCALL_EXPIRE:
      self._HandleExpire(up)

    if not filter_upcall:
      self._callback(up)
