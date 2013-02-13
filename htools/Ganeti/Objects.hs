{-# LANGUAGE TemplateHaskell #-}

{-| Implementation of the Ganeti config objects.

Some object fields are not implemented yet, and as such they are
commented out below.

-}

{-

Copyright (C) 2011, 2012 Google Inc.

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

module Ganeti.Objects
  ( NICMode(..)
  , PartialNICParams(..)
  , FilledNICParams(..)
  , fillNICParams
  , PartialNIC(..)
  , DiskMode(..)
  , DiskType(..)
  , DiskLogicalId(..)
  , Disk(..)
  , DiskTemplate(..)
  , PartialBEParams(..)
  , FilledBEParams(..)
  , fillBEParams
  , Instance(..)
  , toDictInstance
  , PartialNDParams(..)
  , FilledNDParams(..)
  , fillNDParams
  , Node(..)
  , AllocPolicy(..)
  , NodeGroup(..)
  , Cluster(..)
  , ConfigData(..)
  ) where

import Data.Maybe
import Text.JSON (makeObj, showJSON, readJSON, JSON, JSValue(..))
import qualified Text.JSON as J

import qualified Ganeti.Constants as C
import Ganeti.HTools.JSON

import Ganeti.THH

-- * NIC definitions

$(declareSADT "NICMode"
  [ ("NMBridged", 'C.nicModeBridged)
  , ("NMRouted",  'C.nicModeRouted)
  ])
$(makeJSONInstance ''NICMode)

$(buildParam "NIC" "nicp"
  [ simpleField "mode" [t| NICMode |]
  , simpleField "link" [t| String  |]
  ])

$(buildObject "PartialNIC" "nic"
  [ simpleField "mac" [t| String |]
  , optionalField $ simpleField "ip" [t| String |]
  , simpleField "nicparams" [t| PartialNICParams |]
  ])

-- * Disk definitions

$(declareSADT "DiskMode"
  [ ("DiskRdOnly", 'C.diskRdonly)
  , ("DiskRdWr",   'C.diskRdwr)
  ])
$(makeJSONInstance ''DiskMode)

$(declareSADT "DiskType"
  [ ("LD_LV",       'C.ldLv)
  , ("LD_DRBD8",    'C.ldDrbd8)
  , ("LD_FILE",     'C.ldFile)
  , ("LD_BLOCKDEV", 'C.ldBlockdev)
  , ("LD_RADOS",    'C.ldRbd)
  ])
$(makeJSONInstance ''DiskType)

-- | The file driver type.
$(declareSADT "FileDriver"
  [ ("FileLoop",   'C.fdLoop)
  , ("FileBlktap", 'C.fdBlktap)
  ])
$(makeJSONInstance ''FileDriver)

-- | The persistent block driver type. Currently only one type is allowed.
$(declareSADT "BlockDriver"
  [ ("BlockDrvManual", 'C.blockdevDriverManual)
  ])
$(makeJSONInstance ''BlockDriver)

-- | Constant for the dev_type key entry in the disk config.
devType :: String
devType = "dev_type"

-- | The disk configuration type. This includes the disk type itself,
-- for a more complete consistency. Note that since in the Python
-- code-base there's no authoritative place where we document the
-- logical id, this is probably a good reference point.
data DiskLogicalId
  = LIDPlain String String  -- ^ Volume group, logical volume
  | LIDDrbd8 String String Int Int Int String
  -- ^ NodeA, NodeB, Port, MinorA, MinorB, Secret
  | LIDFile FileDriver String -- ^ Driver, path
  | LIDBlockDev BlockDriver String -- ^ Driver, path (must be under /dev)
  | LIDRados String String -- ^ Unused, path
    deriving (Read, Show, Eq)

-- | Mapping from a logical id to a disk type.
lidDiskType :: DiskLogicalId -> DiskType
lidDiskType (LIDPlain {}) = LD_LV
lidDiskType (LIDDrbd8 {}) = LD_DRBD8
lidDiskType (LIDFile  {}) = LD_FILE
lidDiskType (LIDBlockDev {}) = LD_BLOCKDEV
lidDiskType (LIDRados {}) = LD_RADOS

-- | Builds the extra disk_type field for a given logical id.
lidEncodeType :: DiskLogicalId -> [(String, JSValue)]
lidEncodeType v = [(devType, showJSON . lidDiskType $ v)]

-- | Custom encoder for DiskLogicalId (logical id only).
encodeDLId :: DiskLogicalId -> JSValue
encodeDLId (LIDPlain vg lv) = JSArray [showJSON vg, showJSON lv]
encodeDLId (LIDDrbd8 nodeA nodeB port minorA minorB key) =
  JSArray [ showJSON nodeA, showJSON nodeB, showJSON port
          , showJSON minorA, showJSON minorB, showJSON key ]
encodeDLId (LIDRados pool name) = JSArray [showJSON pool, showJSON name]
encodeDLId (LIDFile driver name) = JSArray [showJSON driver, showJSON name]
encodeDLId (LIDBlockDev driver name) = JSArray [showJSON driver, showJSON name]

-- | Custom encoder for DiskLogicalId, composing both the logical id
-- and the extra disk_type field.
encodeFullDLId :: DiskLogicalId -> (JSValue, [(String, JSValue)])
encodeFullDLId v = (encodeDLId v, lidEncodeType v)

-- | Custom decoder for DiskLogicalId. This is manual for now, since
-- we don't have yet automation for separate-key style fields.
decodeDLId :: [(String, JSValue)] -> JSValue -> J.Result DiskLogicalId
decodeDLId obj lid = do
  dtype <- fromObj obj devType
  case dtype of
    LD_DRBD8 ->
      case lid of
        JSArray [nA, nB, p, mA, mB, k] -> do
          nA' <- readJSON nA
          nB' <- readJSON nB
          p'  <- readJSON p
          mA' <- readJSON mA
          mB' <- readJSON mB
          k'  <- readJSON k
          return $ LIDDrbd8 nA' nB' p' mA' mB' k'
        _ -> fail $ "Can't read logical_id for DRBD8 type"
    LD_LV ->
      case lid of
        JSArray [vg, lv] -> do
          vg' <- readJSON vg
          lv' <- readJSON lv
          return $ LIDPlain vg' lv'
        _ -> fail $ "Can't read logical_id for plain type"
    LD_FILE ->
      case lid of
        JSArray [driver, path] -> do
          driver' <- readJSON driver
          path'   <- readJSON path
          return $ LIDFile driver' path'
        _ -> fail $ "Can't read logical_id for file type"
    LD_BLOCKDEV ->
      case lid of
        JSArray [driver, path] -> do
          driver' <- readJSON driver
          path'   <- readJSON path
          return $ LIDBlockDev driver' path'
        _ -> fail $ "Can't read logical_id for blockdev type"
    LD_RADOS ->
      case lid of
        JSArray [driver, path] -> do
          driver' <- readJSON driver
          path'   <- readJSON path
          return $ LIDRados driver' path'
        _ -> fail $ "Can't read logical_id for rdb type"

-- | Disk data structure.
--
-- This is declared manually as it's a recursive structure, and our TH
-- code currently can't build it.
data Disk = Disk
  { diskLogicalId  :: DiskLogicalId
--  , diskPhysicalId :: String
  , diskChildren   :: [Disk]
  , diskIvName     :: String
  , diskSize       :: Int
  , diskMode       :: DiskMode
  } deriving (Read, Show, Eq)

$(buildObjectSerialisation "Disk"
  [ customField 'decodeDLId 'encodeFullDLId $
      simpleField "logical_id"    [t| DiskLogicalId   |]
--  , simpleField "physical_id" [t| String   |]
  , defaultField  [| [] |] $ simpleField "children" [t| [Disk] |]
  , defaultField [| "" |] $ simpleField "iv_name" [t| String |]
  , simpleField "size" [t| Int |]
  , defaultField [| DiskRdWr |] $ simpleField "mode" [t| DiskMode |]
  ])

-- * Instance definitions

-- | Instance disk template type. **Copied from HTools/Types.hs**
$(declareSADT "DiskTemplate"
  [ ("DTDiskless",   'C.dtDiskless)
  , ("DTFile",       'C.dtFile)
  , ("DTSharedFile", 'C.dtSharedFile)
  , ("DTPlain",      'C.dtPlain)
  , ("DTBlock",      'C.dtBlock)
  , ("DTDrbd8",      'C.dtDrbd8)
  ])
$(makeJSONInstance ''DiskTemplate)

$(declareSADT "AdminState"
  [ ("AdminOffline", 'C.adminstOffline)
  , ("AdminDown",    'C.adminstDown)
  , ("AdminUp",      'C.adminstUp)
  ])
$(makeJSONInstance ''AdminState)

$(buildParam "BE" "bep" $
  [ simpleField "minmem"       [t| Int  |]
  , simpleField "maxmem"       [t| Int  |]
  , simpleField "vcpus"        [t| Int  |]
  , simpleField "auto_balance" [t| Bool |]
  ])

$(buildObject "Instance" "inst" $
  [ simpleField "name"           [t| String             |]
  , simpleField "primary_node"   [t| String             |]
  , simpleField "os"             [t| String             |]
  , simpleField "hypervisor"     [t| String             |]
--  , simpleField "hvparams"     [t| [(String, String)] |]
  , simpleField "beparams"       [t| PartialBEParams |]
--  , simpleField "osparams"     [t| [(String, String)] |]
  , simpleField "admin_state"    [t| AdminState         |]
  , simpleField "nics"           [t| [PartialNIC]              |]
  , simpleField "disks"          [t| [Disk]             |]
  , simpleField "disk_template"  [t| DiskTemplate       |]
  , optionalField $ simpleField "network_port" [t| Int |]
  ]
  ++ timeStampFields
  ++ uuidFields
  ++ serialFields)

-- * Node definitions

$(buildParam "ND" "ndp" $
  [ simpleField "oob_program" [t| String |]
  ])

$(buildObject "Node" "node" $
  [ simpleField "name"             [t| String |]
  , simpleField "primary_ip"       [t| String |]
  , simpleField "secondary_ip"     [t| String |]
  , simpleField "master_candidate" [t| Bool   |]
  , simpleField "offline"          [t| Bool   |]
  , simpleField "drained"          [t| Bool   |]
  , simpleField "group"            [t| String |]
  , simpleField "master_capable"   [t| Bool   |]
  , simpleField "vm_capable"       [t| Bool   |]
--  , simpleField "ndparams"       [t| PartialNDParams |]
  , simpleField "powered"          [t| Bool   |]
  ]
  ++ timeStampFields
  ++ uuidFields
  ++ serialFields)

-- * NodeGroup definitions

-- | The Group allocation policy type.
--
-- Note that the order of constructors is important as the automatic
-- Ord instance will order them in the order they are defined, so when
-- changing this data type be careful about the interaction with the
-- desired sorting order.
--
-- FIXME: COPIED from Types.hs; we need to eliminate this duplication later
$(declareSADT "AllocPolicy"
  [ ("AllocPreferred",   'C.allocPolicyPreferred)
  , ("AllocLastResort",  'C.allocPolicyLastResort)
  , ("AllocUnallocable", 'C.allocPolicyUnallocable)
  ])
$(makeJSONInstance ''AllocPolicy)

$(buildObject "NodeGroup" "group" $
  [ simpleField "name"         [t| String |]
  , defaultField  [| [] |] $ simpleField "members" [t| [String] |]
--  , simpleField "ndparams"   [t| PartialNDParams |]
  , simpleField "alloc_policy" [t| AllocPolicy |]
  ]
  ++ timeStampFields
  ++ uuidFields
  ++ serialFields)

-- * Cluster definitions
$(buildObject "Cluster" "cluster" $
  [ simpleField "rsahostkeypub"             [t| String   |]
  , simpleField "highest_used_port"         [t| Int      |]
  , simpleField "tcpudp_port_pool"          [t| [Int]    |]
  , simpleField "mac_prefix"                [t| String   |]
  , simpleField "volume_group_name"         [t| String   |]
  , simpleField "reserved_lvs"              [t| [String] |]
--  , simpleField "drbd_usermode_helper"      [t| String   |]
-- , simpleField "default_bridge"          [t| String   |]
-- , simpleField "default_hypervisor"      [t| String   |]
  , simpleField "master_node"               [t| String   |]
  , simpleField "master_ip"                 [t| String   |]
  , simpleField "master_netdev"             [t| String   |]
-- , simpleField "master_netmask"          [t| String   |]
  , simpleField "cluster_name"              [t| String   |]
  , simpleField "file_storage_dir"          [t| String   |]
-- , simpleField "shared_file_storage_dir" [t| String   |]
  , simpleField "enabled_hypervisors"       [t| [String] |]
-- , simpleField "hvparams"                [t| [(String, [(String, String)])] |]
-- , simpleField "os_hvp"                  [t| [(String, String)] |]
  , containerField $ simpleField "beparams" [t| FilledBEParams |]
-- , simpleField "osparams"                [t| [(String, String)] |]
  , containerField $ simpleField "nicparams" [t| FilledNICParams    |]
--  , simpleField "ndparams"                  [t| FilledNDParams |]
  , simpleField "candidate_pool_size"       [t| Int                |]
  , simpleField "modify_etc_hosts"          [t| Bool               |]
  , simpleField "modify_ssh_setup"          [t| Bool               |]
  , simpleField "maintain_node_health"      [t| Bool               |]
  , simpleField "uid_pool"                  [t| [Int]              |]
  , simpleField "default_iallocator"        [t| String             |]
  , simpleField "hidden_os"                 [t| [String]           |]
  , simpleField "blacklisted_os"            [t| [String]           |]
  , simpleField "primary_ip_family"         [t| Int                |]
  , simpleField "prealloc_wipe_disks"       [t| Bool               |]
 ]
 ++ serialFields)

-- * ConfigData definitions

$(buildObject "ConfigData" "config" $
--  timeStampFields ++
  [ simpleField "version"       [t| Int                |]
  , simpleField "cluster"       [t| Cluster            |]
  , containerField $ simpleField "nodes"      [t| Node     |]
  , containerField $ simpleField "nodegroups" [t| NodeGroup |]
  , containerField $ simpleField "instances"  [t| Instance |]
  ]
  ++ serialFields)
