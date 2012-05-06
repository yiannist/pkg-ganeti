{-| Implementation of the RAPI client interface.

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

{-# LANGUAGE BangPatterns, CPP #-}

module Ganeti.HTools.Rapi
    (
      loadData
    , parseData
    ) where

import Data.Maybe (fromMaybe)
#ifndef NO_CURL
import Network.Curl
import Network.Curl.Types ()
#endif
import Control.Monad
import Text.JSON (JSObject, fromJSObject, decodeStrict)
import Text.JSON.Types (JSValue(..))
import Text.Printf (printf)

import Ganeti.HTools.Utils
import Ganeti.HTools.Loader
import Ganeti.HTools.Types
import qualified Ganeti.HTools.Group as Group
import qualified Ganeti.HTools.Node as Node
import qualified Ganeti.HTools.Instance as Instance
import qualified Ganeti.Constants as C

-- | Read an URL via curl and return the body if successful.
getUrl :: (Monad m) => String -> IO (m String)

#ifdef NO_CURL
getUrl _ = return $ fail "RAPI/curl backend disabled at compile time"

#else

-- | The curl options we use.
curlOpts :: [CurlOption]
curlOpts = [ CurlSSLVerifyPeer False
           , CurlSSLVerifyHost 0
           , CurlTimeout (fromIntegral queryTimeout)
           , CurlConnectTimeout (fromIntegral connTimeout)
           ]

getUrl url = do
  (code, !body) <- curlGetString url curlOpts
  return (case code of
            CurlOK -> return body
            _ -> fail $ printf "Curl error for '%s', error %s"
                 url (show code))
#endif

-- | Append the default port if not passed in.
formatHost :: String -> String
formatHost master =
    if ':' `elem` master then  master
    else "https://" ++ master ++ ":" ++ show C.defaultRapiPort

-- | Parse a instance list in JSON format.
getInstances :: NameAssoc
             -> String
             -> Result [(String, Instance.Instance)]
getInstances ktn body =
    loadJSArray "Parsing instance data" body >>=
    mapM (parseInstance ktn . fromJSObject)

-- | Parse a node list in JSON format.
getNodes :: NameAssoc -> String -> Result [(String, Node.Node)]
getNodes ktg body = loadJSArray "Parsing node data" body >>=
                mapM (parseNode ktg . fromJSObject)

-- | Parse a group list in JSON format.
getGroups :: String -> Result [(String, Group.Group)]
getGroups body = loadJSArray "Parsing group data" body >>=
                mapM (parseGroup . fromJSObject)

-- | Construct an instance from a JSON object.
parseInstance :: NameAssoc
              -> JSRecord
              -> Result (String, Instance.Instance)
parseInstance ktn a = do
  name <- tryFromObj "Parsing new instance" a "name"
  let owner_name = "Instance '" ++ name ++ "', error while parsing data"
  let extract s x = tryFromObj owner_name x s
  disk <- extract "disk_usage" a
  beparams <- liftM fromJSObject (extract "beparams" a)
  omem <- extract "oper_ram" a
  mem <- (case omem of
            JSRational _ _ -> annotateResult owner_name (fromJVal omem)
            _ -> extract "memory" beparams)
  vcpus <- extract "vcpus" beparams
  pnode <- extract "pnode" a >>= lookupNode ktn name
  snodes <- extract "snodes" a
  snode <- (if null snodes then return Node.noSecondary
            else readEitherString (head snodes) >>= lookupNode ktn name)
  running <- extract "status" a
  tags <- extract "tags" a
  auto_balance <- extract "auto_balance" beparams
  dt <- extract "disk_template" a
  let inst = Instance.create name mem disk vcpus running tags
             auto_balance pnode snode dt
  return (name, inst)

-- | Construct a node from a JSON object.
parseNode :: NameAssoc -> JSRecord -> Result (String, Node.Node)
parseNode ktg a = do
  name <- tryFromObj "Parsing new node" a "name"
  let desc = "Node '" ++ name ++ "', error while parsing data"
      extract s = tryFromObj desc a s
  offline <- extract "offline"
  drained <- extract "drained"
  vm_cap  <- annotateResult desc $ maybeFromObj a "vm_capable"
  let vm_cap' = fromMaybe True vm_cap
  guuid   <- annotateResult desc $ maybeFromObj a "group.uuid"
  guuid' <-  lookupGroup ktg name (fromMaybe defaultGroupID guuid)
  node <- (if offline || drained || not vm_cap'
           then return $ Node.create name 0 0 0 0 0 0 True guuid'
           else do
             mtotal  <- extract "mtotal"
             mnode   <- extract "mnode"
             mfree   <- extract "mfree"
             dtotal  <- extract "dtotal"
             dfree   <- extract "dfree"
             ctotal  <- extract "ctotal"
             return $ Node.create name mtotal mnode mfree
                    dtotal dfree ctotal False guuid')
  return (name, node)

-- | Construct a group from a JSON object.
parseGroup :: JSRecord -> Result (String, Group.Group)
parseGroup a = do
  name <- tryFromObj "Parsing new group" a "name"
  let extract s = tryFromObj ("Group '" ++ name ++ "'") a s
  uuid <- extract "uuid"
  apol <- extract "alloc_policy"
  return (uuid, Group.create name uuid apol)

-- | Loads the raw cluster data from an URL.
readData :: String -- ^ Cluster or URL to use as source
         -> IO (Result String, Result String, Result String, Result String)
readData master = do
  let url = formatHost master
  group_body <- getUrl $ printf "%s/2/groups?bulk=1" url
  node_body <- getUrl $ printf "%s/2/nodes?bulk=1" url
  inst_body <- getUrl $ printf "%s/2/instances?bulk=1" url
  tags_body <- getUrl $ printf "%s/2/tags" url
  return (group_body, node_body, inst_body, tags_body)

-- | Builds the cluster data from the raw Rapi content.
parseData :: (Result String, Result String, Result String, Result String)
          -> Result ClusterData
parseData (group_body, node_body, inst_body, tags_body) = do
  group_data <- group_body >>= getGroups
  let (group_names, group_idx) = assignIndices group_data
  node_data <- node_body >>= getNodes group_names
  let (node_names, node_idx) = assignIndices node_data
  inst_data <- inst_body >>= getInstances node_names
  let (_, inst_idx) = assignIndices inst_data
  tags_data <- tags_body >>= (fromJResult "Parsing tags data" . decodeStrict)
  return (ClusterData group_idx node_idx inst_idx tags_data)

-- | Top level function for data loading.
loadData :: String -- ^ Cluster or URL to use as source
         -> IO (Result ClusterData)
loadData = fmap parseData . readData
