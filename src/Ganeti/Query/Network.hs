{-| Implementation of the Ganeti Query2 node group queries.

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

module Ganeti.Query.Network
  ( getGroupConnection
  , getNetworkUuid
  , instIsConnected
  , Runtime
  , fieldsMap
  , collectLiveData
  ) where

-- FIXME: everything except Runtime(..) and fieldsMap
-- is only exported for testing.

import qualified Data.Map as Map
import Data.Maybe (fromMaybe, mapMaybe)
import Data.List (find, foldl', intercalate)

import Ganeti.JSON
import Ganeti.Network
import Ganeti.Objects
import Ganeti.Query.Language
import Ganeti.Query.Common
import Ganeti.Query.Types
import Ganeti.Types

-- | There is no actual runtime.
data Runtime = Runtime

networkFields :: FieldList Network Runtime
networkFields =
  [ (FieldDefinition "name" "Network" QFTText "Name",
     FieldSimple (rsNormal . networkName), QffNormal)
  , (FieldDefinition "network" "Subnet" QFTText "IPv4 subnet",
     FieldSimple (rsNormal . networkNetwork), QffNormal)
  , (FieldDefinition "gateway" "Gateway" QFTOther "IPv4 gateway",
     FieldSimple (rsMaybeUnavail . networkGateway), QffNormal)
  , (FieldDefinition "network6" "IPv6Subnet" QFTOther "IPv6 subnet",
     FieldSimple (rsMaybeUnavail . networkNetwork6), QffNormal)
  , (FieldDefinition "gateway6" "IPv6Gateway" QFTOther "IPv6 gateway",
     FieldSimple (rsMaybeUnavail . networkGateway6), QffNormal)
  , (FieldDefinition "mac_prefix" "MacPrefix" QFTOther "MAC address prefix",
     FieldSimple (rsMaybeUnavail . networkMacPrefix), QffNormal)
  , (FieldDefinition "free_count" "FreeCount" QFTNumber "Number of available\
                                                       \ addresses",
     FieldSimple (rsMaybeNoData . fmap getFreeCount . createAddressPool),
     QffNormal)
  , (FieldDefinition "map" "Map" QFTText "Actual mapping",
     FieldSimple (rsMaybeNoData . fmap getMap . createAddressPool),
     QffNormal)
  , (FieldDefinition "reserved_count" "ReservedCount" QFTNumber
       "Number of reserved addresses",
     FieldSimple (rsMaybeNoData . fmap getReservedCount . createAddressPool),
     QffNormal)
  , (FieldDefinition "group_list" "GroupList" QFTOther
       "List of nodegroups (group name, NIC mode, NIC link)",
     FieldConfig (\cfg -> rsNormal . getGroupConnections cfg . networkUuid),
     QffNormal)
  , (FieldDefinition "group_cnt" "NodeGroups" QFTNumber "Number of nodegroups",
     FieldConfig (\cfg -> rsNormal . length . getGroupConnections cfg
       . networkUuid), QffNormal)
  , (FieldDefinition "inst_list" "InstanceList" QFTOther "List of instances",
     FieldConfig (\cfg -> rsNormal . getInstances cfg . networkUuid),
     QffNormal)
  , (FieldDefinition "inst_cnt" "Instances" QFTNumber "Number of instances",
     FieldConfig (\cfg -> rsNormal . length . getInstances cfg
       . networkUuid), QffNormal)
  , (FieldDefinition "external_reservations" "ExternalReservations" QFTText
     "External reservations",
     FieldSimple getExtReservationsString, QffNormal)
  ] ++
  timeStampFields ++
  uuidFields "Network" ++
  serialFields "Network" ++
  tagsFields

-- | The group fields map.
fieldsMap :: FieldMap Network Runtime
fieldsMap =
  Map.fromList $ map (\v@(f, _, _) -> (fdefName f, v)) networkFields

-- TODO: the following fields are not implemented yet: external_reservations

-- | Given a network's UUID, this function lists all connections from
-- the network to nodegroups including the respective mode and links.
getGroupConnections :: ConfigData -> String -> [(String, String, String)]
getGroupConnections cfg network_uuid =
  mapMaybe (getGroupConnection network_uuid)
  ((Map.elems . fromContainer . configNodegroups) cfg)

-- | Given a network's UUID and a node group, this function assembles
-- a tuple of the group's name, the mode and the link by which the
-- network is connected to the group. Returns 'Nothing' if the network
-- is not connected to the group.
getGroupConnection :: String -> NodeGroup -> Maybe (String, String, String)
getGroupConnection network_uuid group =
  let networks = fromContainer . groupNetworks $ group
  in case Map.lookup network_uuid networks of
    Nothing -> Nothing
    Just net ->
      Just (groupName group, getNicMode net, getNicLink net)

-- | Retrieves the network's mode and formats it human-readable,
-- also in case it is not available.
getNicMode :: PartialNicParams -> String
getNicMode nic_params =
  maybe "-" nICModeToRaw $ nicpModeP nic_params

-- | Retrieves the network's link and formats it human-readable, also in
-- case it it not available.
getNicLink :: PartialNicParams -> String
getNicLink nic_params = fromMaybe "-" (nicpLinkP nic_params)

-- | Retrieves the network's instances' names.
getInstances :: ConfigData -> String -> [String]
getInstances cfg network_uuid =
  map instName (filter (instIsConnected cfg network_uuid)
    ((Map.elems . fromContainer . configInstances) cfg))

-- | Helper function that checks if an instance is linked to the given network.
instIsConnected :: ConfigData -> String -> Instance -> Bool
instIsConnected cfg network_uuid inst =
  network_uuid `elem` mapMaybe (getNetworkUuid cfg)
    (mapMaybe nicNetwork (instNics inst))

-- | Helper function to look up a network's UUID by its name
getNetworkUuid :: ConfigData -> String -> Maybe String
getNetworkUuid cfg name =
  let net = find (\n -> name == fromNonEmpty (networkName n))
               ((Map.elems . fromContainer . configNetworks) cfg)
  in fmap networkUuid net

-- | Computes the reservations list for a network.
--
-- This doesn't use the netmask for validation of the length, instead
-- simply iterating over the reservations string.
getReservations :: Ip4Network -> String -> [Ip4Address]
getReservations (Ip4Network net _) =
  reverse .
  fst .
  foldl' (\(accu, addr) c ->
            let addr' = nextIp4Address addr
                accu' = case c of
                          '1' -> addr:accu
                          '0' -> accu
                          _ -> -- FIXME: the reservations string
                               -- should be a proper type
                               accu
            in (accu', addr')) ([], net)

-- | Computes the external reservations as string for a network.
getExtReservationsString :: Network -> ResultEntry
getExtReservationsString net =
  let addrs = getReservations (networkNetwork net)
              (fromMaybe "" $ networkExtReservations net)
  in rsNormal . intercalate ", " $ map show addrs

-- | Dummy function for collecting live data (which networks don't have).
collectLiveData :: Bool -> ConfigData -> [Network] -> IO [(Network, Runtime)]
collectLiveData _ _ = return . map (\n -> (n, Runtime))
