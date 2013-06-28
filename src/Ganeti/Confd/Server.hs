{-# LANGUAGE BangPatterns #-}

{-| Implementation of the Ganeti confd server functionality.

-}

{-

Copyright (C) 2011, 2012, 2013 Google Inc.

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

module Ganeti.Confd.Server
  ( main
  , checkMain
  , prepMain
  ) where

import Control.Concurrent
import Control.Exception
import Control.Monad (forever, liftM, unless)
import Data.IORef
import Data.List
import qualified Data.Map as M
import Data.Maybe (fromMaybe)
import qualified Network.Socket as S
import System.Exit
import System.IO
import System.Posix.Files
import System.Posix.Types
import qualified Text.JSON as J
import System.INotify

import Ganeti.BasicTypes
import Ganeti.Errors
import Ganeti.Daemon
import Ganeti.JSON
import Ganeti.Objects
import Ganeti.Confd.Types
import Ganeti.Confd.Utils
import Ganeti.Config
import Ganeti.Hash
import Ganeti.Logging
import qualified Ganeti.Constants as C
import qualified Ganeti.Path as Path
import Ganeti.Query.Server (prepQueryD, runQueryD)
import Ganeti.Utils

-- * Types and constants definitions

-- | What we store as configuration.
type CRef = IORef (Result (ConfigData, LinkIpMap))

-- | File stat identifier.
type FStat = (EpochTime, FileID, FileOffset)

-- | Null 'FStat' value.
nullFStat :: FStat
nullFStat = (-1, -1, -1)

-- | A small type alias for readability.
type StatusAnswer = (ConfdReplyStatus, J.JSValue)

-- | Reload model data type.
data ReloadModel = ReloadNotify      -- ^ We are using notifications
                 | ReloadPoll Int    -- ^ We are using polling
                   deriving (Eq, Show)

-- | Server state data type.
data ServerState = ServerState
  { reloadModel  :: ReloadModel
  , reloadTime   :: Integer      -- ^ Reload time (epoch) in microseconds
  , reloadFStat  :: FStat
  }

-- | Maximum no-reload poll rounds before reverting to inotify.
maxIdlePollRounds :: Int
maxIdlePollRounds = 3

-- | Reload timeout in microseconds.
watchInterval :: Int
watchInterval = C.confdConfigReloadTimeout * 1000000

-- | Ratelimit timeout in microseconds.
pollInterval :: Int
pollInterval = C.confdConfigReloadRatelimit

-- | Ratelimit timeout in microseconds, as an 'Integer'.
reloadRatelimit :: Integer
reloadRatelimit = fromIntegral C.confdConfigReloadRatelimit

-- | Initial poll round.
initialPoll :: ReloadModel
initialPoll = ReloadPoll 0

-- | Reload status data type.
data ConfigReload = ConfigToDate    -- ^ No need to reload
                  | ConfigReloaded  -- ^ Configuration reloaded
                  | ConfigIOError   -- ^ Error during configuration reload
                    deriving (Eq)

-- | Unknown entry standard response.
queryUnknownEntry :: StatusAnswer
queryUnknownEntry = (ReplyStatusError, J.showJSON ConfdErrorUnknownEntry)

{- not used yet
-- | Internal error standard response.
queryInternalError :: StatusAnswer
queryInternalError = (ReplyStatusError, J.showJSON ConfdErrorInternal)
-}

-- | Argument error standard response.
queryArgumentError :: StatusAnswer
queryArgumentError = (ReplyStatusError, J.showJSON ConfdErrorArgument)

-- | Converter from specific error to a string format.
gntErrorToResult :: ErrorResult a -> Result a
gntErrorToResult (Bad err) = Bad (show err)
gntErrorToResult (Ok x) = Ok x

-- * Confd base functionality

-- | Computes the node role.
nodeRole :: ConfigData -> String -> Result ConfdNodeRole
nodeRole cfg name =
  let cmaster = clusterMasterNode . configCluster $ cfg
      mnode = M.lookup name . fromContainer . configNodes $ cfg
  in case mnode of
       Nothing -> Bad "Node not found"
       Just node | cmaster == name -> Ok NodeRoleMaster
                 | nodeDrained node -> Ok NodeRoleDrained
                 | nodeOffline node -> Ok NodeRoleOffline
                 | nodeMasterCandidate node -> Ok NodeRoleCandidate
       _ -> Ok NodeRoleRegular

-- | Does an instance ip -> instance -> primary node -> primary ip
-- transformation.
getNodePipByInstanceIp :: ConfigData
                       -> LinkIpMap
                       -> String
                       -> String
                       -> StatusAnswer
getNodePipByInstanceIp cfg linkipmap link instip =
  case M.lookup instip (M.findWithDefault M.empty link linkipmap) of
    Nothing -> queryUnknownEntry
    Just instname ->
      case getInstPrimaryNode cfg instname of
        Bad _ -> queryUnknownEntry -- either instance or node not found
        Ok node -> (ReplyStatusOk, J.showJSON (nodePrimaryIp node))

-- | Builds the response to a given query.
buildResponse :: (ConfigData, LinkIpMap) -> ConfdRequest -> Result StatusAnswer
buildResponse (cfg, _) (ConfdRequest { confdRqType = ReqPing }) =
  return (ReplyStatusOk, J.showJSON (configVersion cfg))

buildResponse cdata req@(ConfdRequest { confdRqType = ReqClusterMaster }) =
  case confdRqQuery req of
    EmptyQuery -> return (ReplyStatusOk, J.showJSON master_name)
    PlainQuery _ -> return queryArgumentError
    DictQuery reqq -> do
      mnode <- gntErrorToResult $ getNode cfg master_name
      let fvals = map (\field -> case field of
                                   ReqFieldName -> master_name
                                   ReqFieldIp -> clusterMasterIp cluster
                                   ReqFieldMNodePip -> nodePrimaryIp mnode
                      ) (confdReqQFields reqq)
      return (ReplyStatusOk, J.showJSON fvals)
    where master_name = clusterMasterNode cluster
          cluster = configCluster cfg
          cfg = fst cdata

buildResponse cdata req@(ConfdRequest { confdRqType = ReqNodeRoleByName }) = do
  node_name <- case confdRqQuery req of
                 PlainQuery str -> return str
                 _ -> fail $ "Invalid query type " ++ show (confdRqQuery req)
  role <- nodeRole (fst cdata) node_name
  return (ReplyStatusOk, J.showJSON role)

buildResponse cdata (ConfdRequest { confdRqType = ReqNodePipList }) =
  -- note: we use foldlWithKey because that's present accross more
  -- versions of the library
  return (ReplyStatusOk, J.showJSON $
          M.foldlWithKey (\accu _ n -> nodePrimaryIp n:accu) []
          (fromContainer . configNodes . fst $ cdata))

buildResponse cdata (ConfdRequest { confdRqType = ReqMcPipList }) =
  -- note: we use foldlWithKey because that's present accross more
  -- versions of the library
  return (ReplyStatusOk, J.showJSON $
          M.foldlWithKey (\accu _ n -> if nodeMasterCandidate n
                                         then nodePrimaryIp n:accu
                                         else accu) []
          (fromContainer . configNodes . fst $ cdata))

buildResponse (cfg, linkipmap)
              req@(ConfdRequest { confdRqType = ReqInstIpsList }) = do
  link <- case confdRqQuery req of
            PlainQuery str -> return str
            EmptyQuery -> return (getDefaultNicLink cfg)
            _ -> fail "Invalid query type"
  return (ReplyStatusOk, J.showJSON $ getInstancesIpByLink linkipmap link)

buildResponse cdata (ConfdRequest { confdRqType = ReqNodePipByInstPip
                                  , confdRqQuery = DictQuery query}) =
  let (cfg, linkipmap) = cdata
      link = fromMaybe (getDefaultNicLink cfg) (confdReqQLink query)
  in case confdReqQIp query of
       Just ip -> return $ getNodePipByInstanceIp cfg linkipmap link ip
       Nothing -> return (ReplyStatusOk,
                          J.showJSON $
                           map (getNodePipByInstanceIp cfg linkipmap link)
                           (confdReqQIpList query))

buildResponse _ (ConfdRequest { confdRqType = ReqNodePipByInstPip }) =
  return queryArgumentError

buildResponse cdata req@(ConfdRequest { confdRqType = ReqNodeDrbd }) = do
  let cfg = fst cdata
  node_name <- case confdRqQuery req of
                 PlainQuery str -> return str
                 _ -> fail $ "Invalid query type " ++ show (confdRqQuery req)
  node <- gntErrorToResult $ getNode cfg node_name
  let minors = concatMap (getInstMinorsForNode (nodeName node)) .
               M.elems . fromContainer . configInstances $ cfg
      encoded = [J.JSArray [J.showJSON a, J.showJSON b, J.showJSON c,
                             J.showJSON d, J.showJSON e, J.showJSON f] |
                 (a, b, c, d, e, f) <- minors]
  return (ReplyStatusOk, J.showJSON encoded)

-- | Creates a ConfdReply from a given answer.
serializeResponse :: Result StatusAnswer -> ConfdReply
serializeResponse r =
    let (status, result) = case r of
                    Bad err -> (ReplyStatusError, J.showJSON err)
                    Ok (code, val) -> (code, val)
    in ConfdReply { confdReplyProtocol = 1
                  , confdReplyStatus   = status
                  , confdReplyAnswer   = result
                  , confdReplySerial   = 0 }

-- * Configuration handling

-- ** Helper functions

-- | Helper function for logging transition into polling mode.
moveToPolling :: String -> INotify -> FilePath -> CRef -> MVar ServerState
              -> IO ReloadModel
moveToPolling msg inotify path cref mstate = do
  logInfo $ "Moving to polling mode: " ++ msg
  let inotiaction = addNotifier inotify path cref mstate
  _ <- forkIO $ onPollTimer inotiaction path cref mstate
  return initialPoll

-- | Helper function for logging transition into inotify mode.
moveToNotify :: IO ReloadModel
moveToNotify = do
  logInfo "Moving to inotify mode"
  return ReloadNotify

-- ** Configuration loading

-- | (Re)loads the configuration.
updateConfig :: FilePath -> CRef -> IO ()
updateConfig path r = do
  newcfg <- loadConfig path
  let !newdata = case newcfg of
                   Ok !cfg -> Ok (cfg, buildLinkIpInstnameMap cfg)
                   Bad _ -> Bad "Cannot load configuration"
  writeIORef r newdata
  case newcfg of
    Ok cfg -> logInfo ("Loaded new config, serial " ++
                       show (configSerial cfg))
    Bad msg -> logError $ "Failed to load config: " ++ msg
  return ()

-- | Wrapper over 'updateConfig' that handles IO errors.
safeUpdateConfig :: FilePath -> FStat -> CRef -> IO (FStat, ConfigReload)
safeUpdateConfig path oldfstat cref =
  Control.Exception.catch
        (do
          nt <- needsReload oldfstat path
          case nt of
            Nothing -> return (oldfstat, ConfigToDate)
            Just nt' -> do
                    updateConfig path cref
                    return (nt', ConfigReloaded)
        ) (\e -> do
             let msg = "Failure during configuration update: " ++
                       show (e::IOError)
             writeIORef cref (Bad msg)
             return (nullFStat, ConfigIOError)
          )

-- | Computes the file cache data from a FileStatus structure.
buildFileStatus :: FileStatus -> FStat
buildFileStatus ofs =
    let modt = modificationTime ofs
        inum = fileID ofs
        fsize = fileSize ofs
    in (modt, inum, fsize)

-- | Wrapper over 'buildFileStatus'. This reads the data from the
-- filesystem and then builds our cache structure.
getFStat :: FilePath -> IO FStat
getFStat p = liftM buildFileStatus (getFileStatus p)

-- | Check if the file needs reloading
needsReload :: FStat -> FilePath -> IO (Maybe FStat)
needsReload oldstat path = do
  newstat <- getFStat path
  return $ if newstat /= oldstat
             then Just newstat
             else Nothing

-- ** Watcher threads

-- $watcher
-- We have three threads/functions that can mutate the server state:
--
-- 1. the long-interval watcher ('onWatcherTimer')
--
-- 2. the polling watcher ('onPollTimer')
--
-- 3. the inotify event handler ('onInotify')
--
-- All of these will mutate the server state under 'modifyMVar' or
-- 'modifyMVar_', so that server transitions are more or less
-- atomic. The inotify handler remains active during polling mode, but
-- checks for polling mode and doesn't do anything in this case (this
-- check is needed even if we would unregister the event handler due
-- to how events are serialised).

-- | Long-interval reload watcher.
--
-- This is on top of the inotify-based triggered reload.
onWatcherTimer :: IO Bool -> FilePath -> CRef -> MVar ServerState -> IO ()
onWatcherTimer inotiaction path cref state = do
  threadDelay watchInterval
  logDebug "Watcher timer fired"
  modifyMVar_ state (onWatcherInner path cref)
  _ <- inotiaction
  onWatcherTimer inotiaction path cref state

-- | Inner onWatcher handler.
--
-- This mutates the server state under a modifyMVar_ call. It never
-- changes the reload model, just does a safety reload and tried to
-- re-establish the inotify watcher.
onWatcherInner :: FilePath -> CRef -> ServerState -> IO ServerState
onWatcherInner path cref state  = do
  (newfstat, _) <- safeUpdateConfig path (reloadFStat state) cref
  return state { reloadFStat = newfstat }

-- | Short-interval (polling) reload watcher.
--
-- This is only active when we're in polling mode; it will
-- automatically exit when it detects that the state has changed to
-- notification.
onPollTimer :: IO Bool -> FilePath -> CRef -> MVar ServerState -> IO ()
onPollTimer inotiaction path cref state = do
  threadDelay pollInterval
  logDebug "Poll timer fired"
  continue <- modifyMVar state (onPollInner inotiaction path cref)
  if continue
    then onPollTimer inotiaction path cref state
    else logDebug "Inotify watch active, polling thread exiting"

-- | Inner onPoll handler.
--
-- This again mutates the state under a modifyMVar call, and also
-- returns whether the thread should continue or not.
onPollInner :: IO Bool -> FilePath -> CRef -> ServerState
              -> IO (ServerState, Bool)
onPollInner _ _ _ state@(ServerState { reloadModel = ReloadNotify } ) =
  return (state, False)
onPollInner inotiaction path cref
            state@(ServerState { reloadModel = ReloadPoll pround } ) = do
  (newfstat, reload) <- safeUpdateConfig path (reloadFStat state) cref
  let state' = state { reloadFStat = newfstat }
  -- compute new poll model based on reload data; however, failure to
  -- re-establish the inotifier means we stay on polling
  newmode <- case reload of
               ConfigToDate ->
                 if pround >= maxIdlePollRounds
                   then do -- try to switch to notify
                     result <- inotiaction
                     if result
                       then moveToNotify
                       else return initialPoll
                   else return (ReloadPoll (pround + 1))
               _ -> return initialPoll
  let continue = case newmode of
                   ReloadNotify -> False
                   _            -> True
  return (state' { reloadModel = newmode }, continue)

-- the following hint is because hlint doesn't understand our const
-- (return False) is so that we can give a signature to 'e'
{-# ANN addNotifier "HLint: ignore Evaluate" #-}
-- | Setup inotify watcher.
--
-- This tries to setup the watch descriptor; in case of any IO errors,
-- it will return False.
addNotifier :: INotify -> FilePath -> CRef -> MVar ServerState -> IO Bool
addNotifier inotify path cref mstate =
  Control.Exception.catch
        (addWatch inotify [CloseWrite] path
                    (onInotify inotify path cref mstate) >> return True)
        (\e -> const (return False) (e::IOError))

-- | Inotify event handler.
onInotify :: INotify -> String -> CRef -> MVar ServerState -> Event -> IO ()
onInotify inotify path cref mstate Ignored = do
  logDebug "File lost, trying to re-establish notifier"
  modifyMVar_ mstate $ \state -> do
    result <- addNotifier inotify path cref mstate
    (newfstat, _) <- safeUpdateConfig path (reloadFStat state) cref
    let state' = state { reloadFStat = newfstat }
    if result
      then return state' -- keep notify
      else do
        mode <- moveToPolling "cannot re-establish inotify watch" inotify
                  path cref mstate
        return state' { reloadModel = mode }

onInotify inotify path cref mstate _ =
  modifyMVar_ mstate $ \state ->
    if reloadModel state == ReloadNotify
       then do
         ctime <- getCurrentTimeUSec
         (newfstat, _) <- safeUpdateConfig path (reloadFStat state) cref
         let state' = state { reloadFStat = newfstat, reloadTime = ctime }
         if abs (reloadTime state - ctime) < reloadRatelimit
           then do
             mode <- moveToPolling "too many reloads" inotify path cref mstate
             return state' { reloadModel = mode }
           else return state'
      else return state

-- ** Client input/output handlers

-- | Main loop for a given client.
responder :: CRef -> S.Socket -> HashKey -> String -> S.SockAddr -> IO ()
responder cfgref socket hmac msg peer = do
  ctime <- getCurrentTime
  case parseRequest hmac msg ctime of
    Ok (origmsg, rq) -> do
              logDebug $ "Processing request: " ++ rStripSpace origmsg
              mcfg <- readIORef cfgref
              let response = respondInner mcfg hmac rq
              _ <- S.sendTo socket response peer
              return ()
    Bad err -> logInfo $ "Failed to parse incoming message: " ++ err
  return ()

-- | Inner helper function for a given client. This generates the
-- final encoded message (as a string), ready to be sent out to the
-- client.
respondInner :: Result (ConfigData, LinkIpMap) -> HashKey
             -> ConfdRequest -> String
respondInner cfg hmac rq =
  let rsalt = confdRqRsalt rq
      innermsg = serializeResponse (cfg >>= flip buildResponse rq)
      innerserialised = J.encodeStrict innermsg
      outermsg = signMessage hmac rsalt innerserialised
      outerserialised = confdMagicFourcc ++ J.encodeStrict outermsg
  in outerserialised

-- | Main listener loop.
listener :: S.Socket -> HashKey
         -> (S.Socket -> HashKey -> String -> S.SockAddr -> IO ())
         -> IO ()
listener s hmac resp = do
  (msg, _, peer) <- S.recvFrom s 4096
  if confdMagicFourcc `isPrefixOf` msg
    then forkIO (resp s hmac (drop 4 msg) peer) >> return ()
    else logDebug "Invalid magic code!" >> return ()
  return ()

-- | Extract the configuration from our IORef.
configReader :: CRef -> IO (Result ConfigData)
configReader cref = do
  cdata <- readIORef cref
  return $ liftM fst cdata

-- | Type alias for prepMain results
type PrepResult = (S.Socket, (FilePath, S.Socket),
                   IORef (Result (ConfigData, LinkIpMap)))

-- | Check function for confd.
checkMain :: CheckFn (S.Family, S.SockAddr)
checkMain opts = do
  parseresult <- parseAddress opts C.defaultConfdPort
  case parseresult of
    Bad msg -> do
      hPutStrLn stderr $ "parsing bind address: " ++ msg
      return . Left $ ExitFailure 1
    Ok v -> return $ Right v

-- | Prepare function for confd.
prepMain :: PrepFn (S.Family, S.SockAddr) PrepResult
prepMain _ (af_family, bindaddr) = do
  s <- S.socket af_family S.Datagram S.defaultProtocol
  S.bindSocket s bindaddr
  -- prepare the queryd listener
  query_data <- prepQueryD Nothing
  cref <- newIORef (Bad "Configuration not yet loaded")
  return (s, query_data, cref)

-- | Main function.
main :: MainFn (S.Family, S.SockAddr) PrepResult
main _ _ (s, query_data, cref) = do
  -- Inotify setup
  inotify <- initINotify
  -- try to load the configuration, if possible
  conf_file <- Path.clusterConfFile
  (fstat, reloaded) <- safeUpdateConfig conf_file nullFStat cref
  ctime <- getCurrentTime
  statemvar <- newMVar $ ServerState ReloadNotify ctime fstat
  let inotiaction = addNotifier inotify conf_file cref statemvar
  has_inotify <- if reloaded == ConfigReloaded
                   then inotiaction
                   else return False
  if has_inotify
    then logInfo "Starting up in inotify mode"
    else do
      -- inotify was not enabled, we need to update the reload model
      logInfo "Starting up in polling mode"
      modifyMVar_ statemvar
        (\state -> return state { reloadModel = initialPoll })
  hmac <- getClusterHmac
  -- fork the timeout timer
  _ <- forkIO $ onWatcherTimer inotiaction conf_file cref statemvar
  -- fork the polling timer
  unless has_inotify $ do
    _ <- forkIO $ onPollTimer inotiaction conf_file cref statemvar
    return ()
  -- launch the queryd listener
  _ <- forkIO $ runQueryD query_data (configReader cref)
  -- and finally enter the responder loop
  forever $ listener s hmac (responder cref)
