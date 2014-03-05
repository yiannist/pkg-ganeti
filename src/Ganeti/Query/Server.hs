{-# LANGUAGE BangPatterns #-}

{-| Implementation of the Ganeti Query2 server.

-}

{-

Copyright (C) 2012, 2013 Google Inc.

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

module Ganeti.Query.Server
  ( main
  , checkMain
  , prepMain
  ) where

import Control.Applicative
import Control.Concurrent
import Control.Exception
import Control.Monad (forever)
import Data.Bits (bitSize)
import qualified Data.Set as Set (toList)
import Data.IORef
import qualified Network.Socket as S
import qualified Text.JSON as J
import Text.JSON (showJSON, JSValue(..))
import System.Info (arch)

import qualified Ganeti.Constants as C
import qualified Ganeti.ConstantUtils as ConstantUtils (unFrozenSet)
import Ganeti.Errors
import qualified Ganeti.Path as Path
import Ganeti.Daemon
import Ganeti.Objects
import qualified Ganeti.Config as Config
import Ganeti.ConfigReader
import Ganeti.BasicTypes
import Ganeti.Logging
import Ganeti.Luxi
import qualified Ganeti.Query.Language as Qlang
import qualified Ganeti.Query.Cluster as QCluster
import Ganeti.Query.Query
import Ganeti.Query.Filter (makeSimpleFilter)
import Ganeti.Types
import qualified Ganeti.Version as Version

-- | Helper for classic queries.
handleClassicQuery :: ConfigData      -- ^ Cluster config
                   -> Qlang.ItemType  -- ^ Query type
                   -> [Either String Integer] -- ^ Requested names
                                              -- (empty means all)
                   -> [String]        -- ^ Requested fields
                   -> Bool            -- ^ Whether to do sync queries or not
                   -> IO (GenericResult GanetiException JSValue)
handleClassicQuery _ _ _ _ True =
  return . Bad $ OpPrereqError "Sync queries are not allowed" ECodeInval
handleClassicQuery cfg qkind names fields _ = do
  let simpleNameFilter field = makeSimpleFilter (field qkind) names
      flt = Qlang.OrFilter $ map simpleNameFilter [nameField, uuidField]
  qr <- query cfg True (Qlang.Query qkind fields flt)
  return $ showJSON <$> (qr >>= queryCompat)

-- | Minimal wrapper to handle the missing config case.
handleCallWrapper :: Result ConfigData -> LuxiOp -> IO (ErrorResult JSValue)
handleCallWrapper (Bad msg) _ =
  return . Bad . ConfigurationError $
           "I do not have access to a valid configuration, cannot\
           \ process queries: " ++ msg
handleCallWrapper (Ok config) op = handleCall config op

-- | Actual luxi operation handler.
handleCall :: ConfigData -> LuxiOp -> IO (ErrorResult JSValue)
handleCall cdata QueryClusterInfo =
  let cluster = configCluster cdata
      master = QCluster.clusterMasterNodeName cdata
      hypervisors = clusterEnabledHypervisors cluster
      diskTemplates = clusterEnabledDiskTemplates cluster
      def_hv = case hypervisors of
                 x:_ -> showJSON x
                 [] -> JSNull
      bits = show (bitSize (0::Int)) ++ "bits"
      arch_tuple = [bits, arch]
      obj = [ ("software_version", showJSON C.releaseVersion)
            , ("protocol_version", showJSON C.protocolVersion)
            , ("config_version", showJSON C.configVersion)
            , ("os_api_version", showJSON . maximum .
                                 Set.toList . ConstantUtils.unFrozenSet $
                                 C.osApiVersions)
            , ("export_version", showJSON C.exportVersion)
            , ("vcs_version", showJSON Version.version)
            , ("architecture", showJSON arch_tuple)
            , ("name", showJSON $ clusterClusterName cluster)
            , ("master", showJSON (case master of
                                     Ok name -> name
                                     _ -> undefined))
            , ("default_hypervisor", def_hv)
            , ("enabled_hypervisors", showJSON hypervisors)
            , ("hvparams", showJSON $ clusterHvparams cluster)
            , ("os_hvp", showJSON $ clusterOsHvp cluster)
            , ("beparams", showJSON $ clusterBeparams cluster)
            , ("osparams", showJSON $ clusterOsparams cluster)
            , ("ipolicy", showJSON $ clusterIpolicy cluster)
            , ("nicparams", showJSON $ clusterNicparams cluster)
            , ("ndparams", showJSON $ clusterNdparams cluster)
            , ("diskparams", showJSON $ clusterDiskparams cluster)
            , ("candidate_pool_size",
               showJSON $ clusterCandidatePoolSize cluster)
            , ("master_netdev",  showJSON $ clusterMasterNetdev cluster)
            , ("master_netmask", showJSON $ clusterMasterNetmask cluster)
            , ("use_external_mip_script",
               showJSON $ clusterUseExternalMipScript cluster)
            , ("volume_group_name",
               maybe JSNull showJSON (clusterVolumeGroupName cluster))
            , ("drbd_usermode_helper",
               maybe JSNull showJSON (clusterDrbdUsermodeHelper cluster))
            , ("file_storage_dir", showJSON $ clusterFileStorageDir cluster)
            , ("shared_file_storage_dir",
               showJSON $ clusterSharedFileStorageDir cluster)
            , ("maintain_node_health",
               showJSON $ clusterMaintainNodeHealth cluster)
            , ("ctime", showJSON $ clusterCtime cluster)
            , ("mtime", showJSON $ clusterMtime cluster)
            , ("uuid", showJSON $ clusterUuid cluster)
            , ("tags", showJSON $ clusterTags cluster)
            , ("uid_pool", showJSON $ clusterUidPool cluster)
            , ("default_iallocator",
               showJSON $ clusterDefaultIallocator cluster)
            , ("reserved_lvs", showJSON $ clusterReservedLvs cluster)
            , ("primary_ip_version",
               showJSON . ipFamilyToVersion $ clusterPrimaryIpFamily cluster)
            , ("prealloc_wipe_disks",
               showJSON $ clusterPreallocWipeDisks cluster)
            , ("hidden_os", showJSON $ clusterHiddenOs cluster)
            , ("blacklisted_os", showJSON $ clusterBlacklistedOs cluster)
            , ("enabled_disk_templates", showJSON diskTemplates)
            ]

  in case master of
    Ok _ -> return . Ok . J.makeObj $ obj
    Bad ex -> return $ Bad ex

handleCall cfg (QueryTags kind name) = do
  let tags = case kind of
               TagKindCluster  -> Ok . clusterTags $ configCluster cfg
               TagKindGroup    -> groupTags   <$> Config.getGroup    cfg name
               TagKindNode     -> nodeTags    <$> Config.getNode     cfg name
               TagKindInstance -> instTags    <$> Config.getInstance cfg name
               TagKindNetwork  -> networkTags <$> Config.getNetwork  cfg name
  return (J.showJSON <$> tags)

handleCall cfg (Query qkind qfields qfilter) = do
  result <- query cfg True (Qlang.Query qkind qfields qfilter)
  return $ J.showJSON <$> result

handleCall _ (QueryFields qkind qfields) = do
  let result = queryFields (Qlang.QueryFields qkind qfields)
  return $ J.showJSON <$> result

handleCall cfg (QueryNodes names fields lock) =
  handleClassicQuery cfg (Qlang.ItemTypeOpCode Qlang.QRNode)
    (map Left names) fields lock

handleCall cfg (QueryGroups names fields lock) =
  handleClassicQuery cfg (Qlang.ItemTypeOpCode Qlang.QRGroup)
    (map Left names) fields lock

handleCall cfg (QueryJobs names fields) =
  handleClassicQuery cfg (Qlang.ItemTypeLuxi Qlang.QRJob)
    (map (Right . fromIntegral . fromJobId) names)  fields False

handleCall cfg (QueryNetworks names fields lock) =
  handleClassicQuery cfg (Qlang.ItemTypeOpCode Qlang.QRNetwork)
    (map Left names) fields lock

handleCall _ op =
  return . Bad $
    GenericError ("Luxi call '" ++ strOfOp op ++ "' not implemented")

-- | Given a decoded luxi request, executes it and sends the luxi
-- response back to the client.
handleClientMsg :: Client -> ConfigReader -> LuxiOp -> IO Bool
handleClientMsg client creader args = do
  cfg <- creader
  logDebug $ "Request: " ++ show args
  call_result <- handleCallWrapper cfg args
  (!status, !rval) <-
    case call_result of
      Bad err -> do
        logWarning $ "Failed to execute request " ++ show args ++ ": "
                     ++ show err
        return (False, showJSON err)
      Ok result -> do
        -- only log the first 2,000 chars of the result
        logDebug $ "Result (truncated): " ++ take 2000 (J.encode result)
        logInfo $ "Successfully handled " ++ strOfOp args
        return (True, result)
  sendMsg client $ buildResponse status rval
  return True

-- | Handles one iteration of the client protocol: receives message,
-- checks it for validity and decodes it, returns response.
handleClient :: Client -> ConfigReader -> IO Bool
handleClient client creader = do
  !msg <- recvMsgExt client
  logDebug $ "Received message: " ++ show msg
  case msg of
    RecvConnClosed -> logDebug "Connection closed" >> return False
    RecvError err -> logWarning ("Error during message receiving: " ++ err) >>
                     return False
    RecvOk payload ->
      case validateCall payload >>= decodeCall of
        Bad err -> do
             let errmsg = "Failed to parse request: " ++ err
             logWarning errmsg
             sendMsg client $ buildResponse False (showJSON errmsg)
             return False
        Ok args -> handleClientMsg client creader args

-- | Main client loop: runs one loop of 'handleClient', and if that
-- doesn't report a finished (closed) connection, restarts itself.
clientLoop :: Client -> ConfigReader -> IO ()
clientLoop client creader = do
  result <- handleClient client creader
  if result
    then clientLoop client creader
    else closeClient client

-- | Main listener loop: accepts clients, forks an I/O thread to handle
-- that client.
listener :: ConfigReader -> S.Socket -> IO ()
listener creader socket = do
  client <- acceptClient socket
  _ <- forkIO $ clientLoop client creader
  return ()

-- | Type alias for prepMain results
type PrepResult = (FilePath, S.Socket, IORef (Result ConfigData))

-- | Check function for luxid.
checkMain :: CheckFn ()
checkMain _ = return $ Right ()

-- | Prepare function for luxid.
prepMain :: PrepFn () PrepResult
prepMain _ _ = do
  socket_path <- Path.defaultQuerySocket
  cleanupSocket socket_path
  s <- describeError "binding to the Luxi socket"
         Nothing (Just socket_path) $ getServer True socket_path
  cref <- newIORef (Bad "Configuration not yet loaded")
  return (socket_path, s, cref)

-- | Main function.
main :: MainFn () PrepResult
main _ _ (socket_path, server, cref) = do
  initConfigReader id cref
  let creader = readIORef cref

  finally
    (forever $ listener creader server)
    (closeServer socket_path server)
