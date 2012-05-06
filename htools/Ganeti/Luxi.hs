{-| Implementation of the Ganeti LUXI interface.

-}

{-

Copyright (C) 2009, 2010, 2011 Google Inc.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
02110-1301, USA.

-}

module Ganeti.Luxi
    ( LuxiOp(..)
    , Client
    , getClient
    , closeClient
    , callMethod
    , submitManyJobs
    , queryJobsStatus
    ) where

import Data.IORef
import Control.Monad
import Text.JSON (encodeStrict, decodeStrict)
import qualified Text.JSON as J
import Text.JSON.Types
import System.Timeout
import qualified Network.Socket as S

import Ganeti.HTools.Utils
import Ganeti.HTools.Types

import Ganeti.Jobs (JobStatus)
import Ganeti.OpCodes (OpCode)

-- * Utility functions

-- | Wrapper over System.Timeout.timeout that fails in the IO monad.
withTimeout :: Int -> String -> IO a -> IO a
withTimeout secs descr action = do
    result <- timeout (secs * 1000000) action
    (case result of
       Nothing -> fail $ "Timeout in " ++ descr
       Just v -> return v)

-- * Generic protocol functionality

-- | Currently supported Luxi operations.
data LuxiOp = QueryInstances [String] [String] Bool
            | QueryNodes [String] [String] Bool
            | QueryGroups [String] [String] Bool
            | QueryJobs [Int] [String]
            | QueryExports [String] Bool
            | QueryConfigValues [String]
            | QueryClusterInfo
            | QueryTags String String
            | SubmitJob [OpCode]
            | SubmitManyJobs [[OpCode]]
            | WaitForJobChange Int [String] JSValue JSValue Int
            | ArchiveJob Int
            | AutoArchiveJobs Int Int
            | CancelJob Int
            | SetDrainFlag Bool
            | SetWatcherPause Double
              deriving (Show, Read)

-- | The serialisation of LuxiOps into strings in messages.
strOfOp :: LuxiOp -> String
strOfOp QueryNodes {}        = "QueryNodes"
strOfOp QueryGroups {}       = "QueryGroups"
strOfOp QueryInstances {}    = "QueryInstances"
strOfOp QueryJobs {}         = "QueryJobs"
strOfOp QueryExports {}      = "QueryExports"
strOfOp QueryConfigValues {} = "QueryConfigValues"
strOfOp QueryClusterInfo {}  = "QueryClusterInfo"
strOfOp QueryTags {}         = "QueryTags"
strOfOp SubmitManyJobs {}    = "SubmitManyJobs"
strOfOp WaitForJobChange {}  = "WaitForJobChange"
strOfOp SubmitJob {}         = "SubmitJob"
strOfOp ArchiveJob {}        = "ArchiveJob"
strOfOp AutoArchiveJobs {}   = "AutoArchiveJobs"
strOfOp CancelJob {}         = "CancelJob"
strOfOp SetDrainFlag {}      = "SetDrainFlag"
strOfOp SetWatcherPause {}   = "SetWatcherPause"

-- | The end-of-message separator.
eOM :: Char
eOM = '\3'

-- | Valid keys in the requests and responses.
data MsgKeys = Method
             | Args
             | Success
             | Result

-- | The serialisation of MsgKeys into strings in messages.
strOfKey :: MsgKeys -> String
strOfKey Method = "method"
strOfKey Args = "args"
strOfKey Success = "success"
strOfKey Result = "result"

-- | Luxi client encapsulation.
data Client = Client { socket :: S.Socket   -- ^ The socket of the client
                     , rbuf :: IORef String -- ^ Already received buffer
                     }

-- | Connects to the master daemon and returns a luxi Client.
getClient :: String -> IO Client
getClient path = do
    s <- S.socket S.AF_UNIX S.Stream S.defaultProtocol
    withTimeout connTimeout "creating luxi connection" $
                S.connect s (S.SockAddrUnix path)
    rf <- newIORef ""
    return Client { socket=s, rbuf=rf}

-- | Closes the client socket.
closeClient :: Client -> IO ()
closeClient = S.sClose . socket

-- | Sends a message over a luxi transport.
sendMsg :: Client -> String -> IO ()
sendMsg s buf =
    let _send obuf = do
          sbytes <- withTimeout queryTimeout
                    "sending luxi message" $
                    S.send (socket s) obuf
          unless (sbytes == length obuf) $ _send (drop sbytes obuf)
    in _send (buf ++ [eOM])

-- | Waits for a message over a luxi transport.
recvMsg :: Client -> IO String
recvMsg s = do
  let _recv obuf = do
              nbuf <- withTimeout queryTimeout "reading luxi response" $
                      S.recv (socket s) 4096
              let (msg, remaining) = break (eOM ==) nbuf
              (if null remaining
               then _recv (obuf ++ msg)
               else return (obuf ++ msg, tail remaining))
  cbuf <- readIORef $ rbuf s
  let (imsg, ibuf) = break (eOM ==) cbuf
  (msg, nbuf) <-
      (if null ibuf      -- if old buffer didn't contain a full message
       then _recv cbuf   -- then we read from network
       else return (imsg, tail ibuf)) -- else we return data from our buffer
  writeIORef (rbuf s) nbuf
  return msg

-- | Compute the serialized form of a Luxi operation.
opToArgs :: LuxiOp -> JSValue
opToArgs (QueryNodes names fields lock) = J.showJSON (names, fields, lock)
opToArgs (QueryGroups names fields lock) = J.showJSON (names, fields, lock)
opToArgs (QueryInstances names fields lock) = J.showJSON (names, fields, lock)
opToArgs (QueryJobs ids fields) = J.showJSON (map show ids, fields)
opToArgs (QueryExports nodes lock) = J.showJSON (nodes, lock)
opToArgs (QueryConfigValues fields) = J.showJSON fields
opToArgs (QueryClusterInfo) = J.showJSON ()
opToArgs (QueryTags kind name) =  J.showJSON (kind, name)
opToArgs (SubmitJob j) = J.showJSON j
opToArgs (SubmitManyJobs ops) = J.showJSON ops
-- This is special, since the JSON library doesn't export an instance
-- of a 5-tuple
opToArgs (WaitForJobChange a b c d e) =
    JSArray [ J.showJSON a, J.showJSON b, J.showJSON c
            , J.showJSON d, J.showJSON e]
opToArgs (ArchiveJob a) = J.showJSON (show a)
opToArgs (AutoArchiveJobs a b) = J.showJSON (a, b)
opToArgs (CancelJob a) = J.showJSON (show a)
opToArgs (SetDrainFlag flag) = J.showJSON flag
opToArgs (SetWatcherPause duration) = J.showJSON [duration]

-- | Serialize a request to String.
buildCall :: LuxiOp  -- ^ The method
          -> String  -- ^ The serialized form
buildCall lo =
    let ja = [ (strOfKey Method, JSString $ toJSString $ strOfOp lo::JSValue)
             , (strOfKey Args, opToArgs lo::JSValue)
             ]
        jo = toJSObject ja
    in encodeStrict jo

-- | Check that luxi responses contain the required keys and that the
-- call was successful.
validateResult :: String -> Result JSValue
validateResult s = do
  oarr <- fromJResult "Parsing LUXI response"
          (decodeStrict s)::Result (JSObject JSValue)
  let arr = J.fromJSObject oarr
  status <- fromObj arr (strOfKey Success)::Result Bool
  let rkey = strOfKey Result
  (if status
   then fromObj arr rkey
   else fromObj arr rkey >>= fail)

-- | Generic luxi method call.
callMethod :: LuxiOp -> Client -> IO (Result JSValue)
callMethod method s = do
  sendMsg s $ buildCall method
  result <- recvMsg s
  let rval = validateResult result
  return rval

-- | Specialized submitManyJobs call.
submitManyJobs :: Client -> [[OpCode]] -> IO (Result [String])
submitManyJobs s jobs = do
  rval <- callMethod (SubmitManyJobs jobs) s
  -- map each result (status, payload) pair into a nice Result ADT
  return $ case rval of
             Bad x -> Bad x
             Ok (JSArray r) ->
                 mapM (\v -> case v of
                               JSArray [JSBool True, JSString x] ->
                                   Ok (fromJSString x)
                               JSArray [JSBool False, JSString x] ->
                                   Bad (fromJSString x)
                               _ -> Bad "Unknown result from the master daemon"
                      ) r
             x -> Bad ("Cannot parse response from Ganeti: " ++ show x)

-- | Custom queryJobs call.
queryJobsStatus :: Client -> [String] -> IO (Result [JobStatus])
queryJobsStatus s jids = do
  rval <- callMethod (QueryJobs (map read jids) ["status"]) s
  return $ case rval of
             Bad x -> Bad x
             Ok y -> case J.readJSON y::(J.Result [[JobStatus]]) of
                       J.Ok vals -> if any null vals
                                    then Bad "Missing job status field"
                                    else Ok (map head vals)
                       J.Error x -> Bad x
