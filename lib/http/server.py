#
#

# Copyright (C) 2007, 2008 Google Inc.
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

"""HTTP server module.

"""

import BaseHTTPServer
import cgi
import logging
import os
import socket
import time
import signal
import asyncore

from ganeti import http


WEEKDAYNAME = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
MONTHNAME = [None,
             'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# Default error message
DEFAULT_ERROR_CONTENT_TYPE = "text/html"
DEFAULT_ERROR_MESSAGE = """\
<html>
<head>
<title>Error response</title>
</head>
<body>
<h1>Error response</h1>
<p>Error code %(code)d.
<p>Message: %(message)s.
<p>Error code explanation: %(code)s = %(explain)s.
</body>
</html>
"""


def _DateTimeHeader(gmnow=None):
  """Return the current date and time formatted for a message header.

  The time MUST be in the GMT timezone.

  """
  if gmnow is None:
    gmnow = time.gmtime()
  (year, month, day, hh, mm, ss, wd, _, _) = gmnow
  return ("%s, %02d %3s %4d %02d:%02d:%02d GMT" %
          (WEEKDAYNAME[wd], day, MONTHNAME[month], year, hh, mm, ss))


class _HttpServerRequest(object):
  """Data structure for HTTP request on server side.

  """
  def __init__(self, request_msg):
    # Request attributes
    self.request_method = request_msg.start_line.method
    self.request_path = request_msg.start_line.path
    self.request_headers = request_msg.headers
    self.request_body = request_msg.decoded_body

    # Response attributes
    self.resp_headers = {}

    # Private data for request handler (useful in combination with
    # authentication)
    self.private = None


class _HttpServerToClientMessageWriter(http.HttpMessageWriter):
  """Writes an HTTP response to client.

  """
  def __init__(self, sock, request_msg, response_msg, write_timeout):
    """Writes the response to the client.

    @type sock: socket
    @param sock: Target socket
    @type request_msg: http.HttpMessage
    @param request_msg: Request message, required to determine whether
        response may have a message body
    @type response_msg: http.HttpMessage
    @param response_msg: Response message
    @type write_timeout: float
    @param write_timeout: Write timeout for socket

    """
    self._request_msg = request_msg
    self._response_msg = response_msg
    http.HttpMessageWriter.__init__(self, sock, response_msg, write_timeout)

  def HasMessageBody(self):
    """Logic to detect whether response should contain a message body.

    """
    if self._request_msg.start_line:
      request_method = self._request_msg.start_line.method
    else:
      request_method = None

    response_code = self._response_msg.start_line.code

    # RFC2616, section 4.3: "A message-body MUST NOT be included in a request
    # if the specification of the request method (section 5.1.1) does not allow
    # sending an entity-body in requests"
    #
    # RFC2616, section 9.4: "The HEAD method is identical to GET except that
    # the server MUST NOT return a message-body in the response."
    #
    # RFC2616, section 10.2.5: "The 204 response MUST NOT include a
    # message-body [...]"
    #
    # RFC2616, section 10.3.5: "The 304 response MUST NOT contain a
    # message-body, [...]"

    return (http.HttpMessageWriter.HasMessageBody(self) and
            (request_method is not None and
             request_method != http.HTTP_HEAD) and
            response_code >= http.HTTP_OK and
            response_code not in (http.HTTP_NO_CONTENT,
                                  http.HTTP_NOT_MODIFIED))


class _HttpClientToServerMessageReader(http.HttpMessageReader):
  """Reads an HTTP request sent by client.

  """
  # Length limits
  START_LINE_LENGTH_MAX = 4096
  HEADER_LENGTH_MAX = 4096

  def ParseStartLine(self, start_line):
    """Parses the start line sent by client.

    Example: "GET /index.html HTTP/1.1"

    @type start_line: string
    @param start_line: Start line

    """
    # Empty lines are skipped when reading
    assert start_line

    logging.debug("HTTP request: %s", start_line)

    words = start_line.split()

    if len(words) == 3:
      [method, path, version] = words
      if version[:5] != 'HTTP/':
        raise http.HttpBadRequest("Bad request version (%r)" % version)

      try:
        base_version_number = version.split("/", 1)[1]
        version_number = base_version_number.split(".")

        # RFC 2145 section 3.1 says there can be only one "." and
        #   - major and minor numbers MUST be treated as
        #      separate integers;
        #   - HTTP/2.4 is a lower version than HTTP/2.13, which in
        #      turn is lower than HTTP/12.3;
        #   - Leading zeros MUST be ignored by recipients.
        if len(version_number) != 2:
          raise http.HttpBadRequest("Bad request version (%r)" % version)

        version_number = (int(version_number[0]), int(version_number[1]))
      except (ValueError, IndexError):
        raise http.HttpBadRequest("Bad request version (%r)" % version)

      if version_number >= (2, 0):
        raise http.HttpVersionNotSupported("Invalid HTTP Version (%s)" %
                                      base_version_number)

    elif len(words) == 2:
      version = http.HTTP_0_9
      [method, path] = words
      if method != http.HTTP_GET:
        raise http.HttpBadRequest("Bad HTTP/0.9 request type (%r)" % method)

    else:
      raise http.HttpBadRequest("Bad request syntax (%r)" % start_line)

    return http.HttpClientToServerStartLine(method, path, version)


class HttpServerRequestExecutor(object):
  """Implements server side of HTTP.

  This class implements the server side of HTTP. It's based on code of
  Python's BaseHTTPServer, from both version 2.4 and 3k. It does not
  support non-ASCII character encodings. Keep-alive connections are
  not supported.

  """
  # The default request version.  This only affects responses up until
  # the point where the request line is parsed, so it mainly decides what
  # the client gets back when sending a malformed request line.
  # Most web servers default to HTTP 0.9, i.e. don't send a status line.
  default_request_version = http.HTTP_0_9

  # Error message settings
  error_message_format = DEFAULT_ERROR_MESSAGE
  error_content_type = DEFAULT_ERROR_CONTENT_TYPE

  responses = BaseHTTPServer.BaseHTTPRequestHandler.responses

  # Timeouts in seconds for socket layer
  WRITE_TIMEOUT = 10
  READ_TIMEOUT = 10
  CLOSE_TIMEOUT = 1

  def __init__(self, server, sock, client_addr):
    """Initializes this class.

    """
    self.server = server
    self.sock = sock
    self.client_addr = client_addr

    self.request_msg = http.HttpMessage()
    self.response_msg = http.HttpMessage()

    self.response_msg.start_line = \
      http.HttpServerToClientStartLine(version=self.default_request_version,
                                       code=None, reason=None)

    # Disable Python's timeout
    self.sock.settimeout(None)

    # Operate in non-blocking mode
    self.sock.setblocking(0)

    logging.debug("Connection from %s:%s", client_addr[0], client_addr[1])
    try:
      request_msg_reader = None
      force_close = True
      try:
        # Do the secret SSL handshake
        if self.server.using_ssl:
          self.sock.set_accept_state()
          try:
            http.Handshake(self.sock, self.WRITE_TIMEOUT)
          except http.HttpSessionHandshakeUnexpectedEOF:
            # Ignore rest
            return

        try:
          try:
            request_msg_reader = self._ReadRequest()
            self._HandleRequest()

            # Only wait for client to close if we didn't have any exception.
            force_close = False
          except http.HttpException, err:
            self._SetErrorStatus(err)
        finally:
          # Try to send a response
          self._SendResponse()
      finally:
        http.ShutdownConnection(sock, self.CLOSE_TIMEOUT, self.WRITE_TIMEOUT,
                                request_msg_reader, force_close)

      self.sock.close()
      self.sock = None
    finally:
      logging.debug("Disconnected %s:%s", client_addr[0], client_addr[1])

  def _ReadRequest(self):
    """Reads a request sent by client.

    """
    try:
      request_msg_reader = \
        _HttpClientToServerMessageReader(self.sock, self.request_msg,
                                         self.READ_TIMEOUT)
    except http.HttpSocketTimeout:
      raise http.HttpError("Timeout while reading request")
    except socket.error, err:
      raise http.HttpError("Error reading request: %s" % err)

    self.response_msg.start_line.version = self.request_msg.start_line.version

    return request_msg_reader

  def _HandleRequest(self):
    """Calls the handler function for the current request.

    """
    handler_context = _HttpServerRequest(self.request_msg)

    try:
      try:
        # Authentication, etc.
        self.server.PreHandleRequest(handler_context)

        # Call actual request handler
        result = self.server.HandleRequest(handler_context)
      except (http.HttpException, KeyboardInterrupt, SystemExit):
        raise
      except Exception, err:
        logging.exception("Caught exception")
        raise http.HttpInternalServerError(message=str(err))
      except:
        logging.exception("Unknown exception")
        raise http.HttpInternalServerError(message="Unknown error")

      # TODO: Content-type
      encoder = http.HttpJsonConverter()
      self.response_msg.start_line.code = http.HTTP_OK
      self.response_msg.body = encoder.Encode(result)
      self.response_msg.headers = handler_context.resp_headers
      self.response_msg.headers[http.HTTP_CONTENT_TYPE] = encoder.CONTENT_TYPE
    finally:
      # No reason to keep this any longer, even for exceptions
      handler_context.private = None

  def _SendResponse(self):
    """Sends the response to the client.

    """
    if self.response_msg.start_line.code is None:
      return

    if not self.response_msg.headers:
      self.response_msg.headers = {}

    self.response_msg.headers.update({
      # TODO: Keep-alive is not supported
      http.HTTP_CONNECTION: "close",
      http.HTTP_DATE: _DateTimeHeader(),
      http.HTTP_SERVER: http.HTTP_GANETI_VERSION,
      })

    # Get response reason based on code
    response_code = self.response_msg.start_line.code
    if response_code in self.responses:
      response_reason = self.responses[response_code][0]
    else:
      response_reason = ""
    self.response_msg.start_line.reason = response_reason

    logging.info("%s:%s %s %s", self.client_addr[0], self.client_addr[1],
                 self.request_msg.start_line, response_code)

    try:
      _HttpServerToClientMessageWriter(self.sock, self.request_msg,
                                       self.response_msg, self.WRITE_TIMEOUT)
    except http.HttpSocketTimeout:
      raise http.HttpError("Timeout while sending response")
    except socket.error, err:
      raise http.HttpError("Error sending response: %s" % err)

  def _SetErrorStatus(self, err):
    """Sets the response code and body from a HttpException.

    @type err: HttpException
    @param err: Exception instance

    """
    try:
      (shortmsg, longmsg) = self.responses[err.code]
    except KeyError:
      shortmsg = longmsg = "Unknown"

    if err.message:
      message = err.message
    else:
      message = shortmsg

    values = {
      "code": err.code,
      "message": cgi.escape(message),
      "explain": longmsg,
      }

    self.response_msg.start_line.code = err.code

    headers = {}
    if err.headers:
      headers.update(err.headers)
    headers[http.HTTP_CONTENT_TYPE] = self.error_content_type
    self.response_msg.headers = headers

    self.response_msg.body = self._FormatErrorMessage(values)

  def _FormatErrorMessage(self, values):
    """Formats the body of an error message.

    @type values: dict
    @param values: dictionary with keys code, message and explain.
    @rtype: string
    @return: the body of the message

    """
    return self.error_message_format % values

class HttpServer(http.HttpBase, asyncore.dispatcher):
  """Generic HTTP server class

  Users of this class must subclass it and override the HandleRequest function.

  """
  MAX_CHILDREN = 20

  def __init__(self, mainloop, local_address, port,
               ssl_params=None, ssl_verify_peer=False,
               request_executor_class=None):
    """Initializes the HTTP server

    @type mainloop: ganeti.daemon.Mainloop
    @param mainloop: Mainloop used to poll for I/O events
    @type local_address: string
    @param local_address: Local IP address to bind to
    @type port: int
    @param port: TCP port to listen on
    @type ssl_params: HttpSslParams
    @param ssl_params: SSL key and certificate
    @type ssl_verify_peer: bool
    @param ssl_verify_peer: Whether to require client certificate
        and compare it with our certificate
    @type request_executor_class: class
    @param request_executor_class: an class derived from the
        HttpServerRequestExecutor class

    """
    http.HttpBase.__init__(self)
    asyncore.dispatcher.__init__(self)

    if request_executor_class is None:
      self.request_executor = HttpServerRequestExecutor
    else:
      self.request_executor = request_executor_class

    self.mainloop = mainloop
    self.local_address = local_address
    self.port = port

    self.socket = self._CreateSocket(ssl_params, ssl_verify_peer)

    # Allow port to be reused
    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    self._children = []
    self.set_socket(self.socket)
    self.accepting = True
    mainloop.RegisterSignal(self)

  def Start(self):
    self.socket.bind((self.local_address, self.port))
    self.socket.listen(1024)

  def Stop(self):
    self.socket.close()

  def handle_accept(self):
    self._IncomingConnection()

  def OnSignal(self, signum):
    if signum == signal.SIGCHLD:
      self._CollectChildren(True)

  def _CollectChildren(self, quick):
    """Checks whether any child processes are done

    @type quick: bool
    @param quick: Whether to only use non-blocking functions

    """
    if not quick:
      # Don't wait for other processes if it should be a quick check
      while len(self._children) > self.MAX_CHILDREN:
        try:
          # Waiting without a timeout brings us into a potential DoS situation.
          # As soon as too many children run, we'll not respond to new
          # requests. The real solution would be to add a timeout for children
          # and killing them after some time.
          pid, _ = os.waitpid(0, 0)
        except os.error:
          pid = None
        if pid and pid in self._children:
          self._children.remove(pid)

    for child in self._children:
      try:
        pid, _ = os.waitpid(child, os.WNOHANG)
      except os.error:
        pid = None
      if pid and pid in self._children:
        self._children.remove(pid)

  def _IncomingConnection(self):
    """Called for each incoming connection

    """
    # pylint: disable-msg=W0212
    (connection, client_addr) = self.socket.accept()

    self._CollectChildren(False)

    pid = os.fork()
    if pid == 0:
      # Child process
      try:
        # The client shouldn't keep the listening socket open. If the parent
        # process is restarted, it would fail when there's already something
        # listening (in this case its own child from a previous run) on the
        # same port.
        try:
          self.socket.close()
        except socket.error:
          pass
        self.socket = None

        self.request_executor(self, connection, client_addr)
      except Exception: # pylint: disable-msg=W0703
        logging.exception("Error while handling request from %s:%s",
                          client_addr[0], client_addr[1])
        os._exit(1)
      os._exit(0)
    else:
      self._children.append(pid)

  def PreHandleRequest(self, req):
    """Called before handling a request.

    Can be overridden by a subclass.

    """

  def HandleRequest(self, req):
    """Handles a request.

    Must be overridden by subclass.

    """
    raise NotImplementedError()
