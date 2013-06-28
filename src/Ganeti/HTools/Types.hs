{-# LANGUAGE TemplateHaskell #-}

{-| Some common types.

-}

{-

Copyright (C) 2009, 2010, 2011, 2012 Google Inc.

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

module Ganeti.HTools.Types
  ( Idx
  , Ndx
  , Gdx
  , NameAssoc
  , Score
  , Weight
  , GroupID
  , defaultGroupID
  , AllocPolicy(..)
  , allocPolicyFromRaw
  , allocPolicyToRaw
  , InstanceStatus(..)
  , instanceStatusFromRaw
  , instanceStatusToRaw
  , RSpec(..)
  , AllocInfo(..)
  , AllocStats
  , DynUtil(..)
  , zeroUtil
  , baseUtil
  , addUtil
  , subUtil
  , defReservedDiskRatio
  , unitMem
  , unitCpu
  , unitDsk
  , unknownField
  , Placement
  , IMove(..)
  , DiskTemplate(..)
  , diskTemplateToRaw
  , diskTemplateFromRaw
  , MirrorType(..)
  , templateMirrorType
  , MoveJob
  , JobSet
  , Element(..)
  , FailMode(..)
  , FailStats
  , OpResult
  , opToResult
  , EvacMode(..)
  , ISpec(..)
  , IPolicy(..)
  , defIPolicy
  , rspecFromISpec
  , AutoRepairType(..)
  , autoRepairTypeToRaw
  , autoRepairTypeFromRaw
  , AutoRepairResult(..)
  , autoRepairResultToRaw
  , autoRepairResultFromRaw
  , AutoRepairPolicy(..)
  , AutoRepairSuspendTime(..)
  , AutoRepairData(..)
  , AutoRepairStatus(..)
  ) where

import qualified Data.Map as M
import System.Time (ClockTime)

import qualified Ganeti.Constants as C
import qualified Ganeti.THH as THH
import Ganeti.BasicTypes
import Ganeti.Types

-- | The instance index type.
type Idx = Int

-- | The node index type.
type Ndx = Int

-- | The group index type.
type Gdx = Int

-- | The type used to hold name-to-idx mappings.
type NameAssoc = M.Map String Int

-- | A separate name for the cluster score type.
type Score = Double

-- | A separate name for a weight metric.
type Weight = Double

-- | The Group UUID type.
type GroupID = String

-- | Default group UUID (just a string, not a real UUID).
defaultGroupID :: GroupID
defaultGroupID = "00000000-0000-0000-0000-000000000000"

-- | Mirroring type.
data MirrorType = MirrorNone     -- ^ No mirroring/movability
                | MirrorInternal -- ^ DRBD-type mirroring
                | MirrorExternal -- ^ Shared-storage type mirroring
                  deriving (Eq, Show)

-- | Correspondence between disk template and mirror type.
templateMirrorType :: DiskTemplate -> MirrorType
templateMirrorType DTDiskless   = MirrorExternal
templateMirrorType DTFile       = MirrorNone
templateMirrorType DTSharedFile = MirrorExternal
templateMirrorType DTPlain      = MirrorNone
templateMirrorType DTBlock      = MirrorExternal
templateMirrorType DTDrbd8      = MirrorInternal
templateMirrorType DTRbd        = MirrorExternal
templateMirrorType DTExt        = MirrorExternal

-- | The resource spec type.
data RSpec = RSpec
  { rspecCpu  :: Int  -- ^ Requested VCPUs
  , rspecMem  :: Int  -- ^ Requested memory
  , rspecDsk  :: Int  -- ^ Requested disk
  } deriving (Show, Eq)

-- | Allocation stats type. This is used instead of 'RSpec' (which was
-- used at first), because we need to track more stats. The actual
-- data can refer either to allocated, or available, etc. values
-- depending on the context. See also
-- 'Cluster.computeAllocationDelta'.
data AllocInfo = AllocInfo
  { allocInfoVCpus :: Int    -- ^ VCPUs
  , allocInfoNCpus :: Double -- ^ Normalised CPUs
  , allocInfoMem   :: Int    -- ^ Memory
  , allocInfoDisk  :: Int    -- ^ Disk
  } deriving (Show, Eq)

-- | Currently used, possibly to allocate, unallocable.
type AllocStats = (AllocInfo, AllocInfo, AllocInfo)

-- | Instance specification type.
$(THH.buildObject "ISpec" "iSpec"
  [ THH.renameField "MemorySize" $ THH.simpleField C.ispecMemSize    [t| Int |]
  , THH.renameField "CpuCount"   $ THH.simpleField C.ispecCpuCount   [t| Int |]
  , THH.renameField "DiskSize"   $ THH.simpleField C.ispecDiskSize   [t| Int |]
  , THH.renameField "DiskCount"  $ THH.simpleField C.ispecDiskCount  [t| Int |]
  , THH.renameField "NicCount"   $ THH.simpleField C.ispecNicCount   [t| Int |]
  , THH.renameField "SpindleUse" $ THH.simpleField C.ispecSpindleUse [t| Int |]
  ])

-- | The default minimum ispec.
defMinISpec :: ISpec
defMinISpec = ISpec { iSpecMemorySize = C.ipolicyDefaultsMinMemorySize
                    , iSpecCpuCount   = C.ipolicyDefaultsMinCpuCount
                    , iSpecDiskSize   = C.ipolicyDefaultsMinDiskSize
                    , iSpecDiskCount  = C.ipolicyDefaultsMinDiskCount
                    , iSpecNicCount   = C.ipolicyDefaultsMinNicCount
                    , iSpecSpindleUse = C.ipolicyDefaultsMinSpindleUse
                    }

-- | The default standard ispec.
defStdISpec :: ISpec
defStdISpec = ISpec { iSpecMemorySize = C.ipolicyDefaultsStdMemorySize
                    , iSpecCpuCount   = C.ipolicyDefaultsStdCpuCount
                    , iSpecDiskSize   = C.ipolicyDefaultsStdDiskSize
                    , iSpecDiskCount  = C.ipolicyDefaultsStdDiskCount
                    , iSpecNicCount   = C.ipolicyDefaultsStdNicCount
                    , iSpecSpindleUse = C.ipolicyDefaultsStdSpindleUse
                    }

-- | The default max ispec.
defMaxISpec :: ISpec
defMaxISpec = ISpec { iSpecMemorySize = C.ipolicyDefaultsMaxMemorySize
                    , iSpecCpuCount   = C.ipolicyDefaultsMaxCpuCount
                    , iSpecDiskSize   = C.ipolicyDefaultsMaxDiskSize
                    , iSpecDiskCount  = C.ipolicyDefaultsMaxDiskCount
                    , iSpecNicCount   = C.ipolicyDefaultsMaxNicCount
                    , iSpecSpindleUse = C.ipolicyDefaultsMaxSpindleUse
                    }

-- | Instance policy type.
$(THH.buildObject "IPolicy" "iPolicy"
  [ THH.renameField "StdSpec" $ THH.simpleField C.ispecsStd [t| ISpec |]
  , THH.renameField "MinSpec" $ THH.simpleField C.ispecsMin [t| ISpec |]
  , THH.renameField "MaxSpec" $ THH.simpleField C.ispecsMax [t| ISpec |]
  , THH.renameField "DiskTemplates" $
      THH.simpleField C.ipolicyDts [t| [DiskTemplate] |]
  , THH.renameField "VcpuRatio" $
      THH.simpleField C.ipolicyVcpuRatio [t| Double |]
  , THH.renameField "SpindleRatio" $
      THH.simpleField C.ipolicySpindleRatio [t| Double |]
  ])

-- | Converts an ISpec type to a RSpec one.
rspecFromISpec :: ISpec -> RSpec
rspecFromISpec ispec = RSpec { rspecCpu = iSpecCpuCount ispec
                             , rspecMem = iSpecMemorySize ispec
                             , rspecDsk = iSpecDiskSize ispec
                             }

-- | The default instance policy.
defIPolicy :: IPolicy
defIPolicy = IPolicy { iPolicyStdSpec = defStdISpec
                     , iPolicyMinSpec = defMinISpec
                     , iPolicyMaxSpec = defMaxISpec
                     -- hardcoding here since Constants.hs exports the
                     -- string values, not the actual type; and in
                     -- htools, we are mostly looking at DRBD
                     , iPolicyDiskTemplates = [minBound..maxBound]
                     , iPolicyVcpuRatio = C.ipolicyDefaultsVcpuRatio
                     , iPolicySpindleRatio = C.ipolicyDefaultsSpindleRatio
                     }

-- | The dynamic resource specs of a machine (i.e. load or load
-- capacity, as opposed to size).
data DynUtil = DynUtil
  { cpuWeight :: Weight -- ^ Standardised CPU usage
  , memWeight :: Weight -- ^ Standardised memory load
  , dskWeight :: Weight -- ^ Standardised disk I\/O usage
  , netWeight :: Weight -- ^ Standardised network usage
  } deriving (Show, Eq)

-- | Initial empty utilisation.
zeroUtil :: DynUtil
zeroUtil = DynUtil { cpuWeight = 0, memWeight = 0
                   , dskWeight = 0, netWeight = 0 }

-- | Base utilisation (used when no actual utilisation data is
-- supplied).
baseUtil :: DynUtil
baseUtil = DynUtil { cpuWeight = 1, memWeight = 1
                   , dskWeight = 1, netWeight = 1 }

-- | Sum two utilisation records.
addUtil :: DynUtil -> DynUtil -> DynUtil
addUtil (DynUtil a1 a2 a3 a4) (DynUtil b1 b2 b3 b4) =
  DynUtil (a1+b1) (a2+b2) (a3+b3) (a4+b4)

-- | Substracts one utilisation record from another.
subUtil :: DynUtil -> DynUtil -> DynUtil
subUtil (DynUtil a1 a2 a3 a4) (DynUtil b1 b2 b3 b4) =
  DynUtil (a1-b1) (a2-b2) (a3-b3) (a4-b4)

-- | The description of an instance placement. It contains the
-- instance index, the new primary and secondary node, the move being
-- performed and the score of the cluster after the move.
type Placement = (Idx, Ndx, Ndx, IMove, Score)

-- | An instance move definition.
data IMove = Failover                -- ^ Failover the instance (f)
           | FailoverToAny Ndx       -- ^ Failover to a random node
                                     -- (fa:np), for shared storage
           | ReplacePrimary Ndx      -- ^ Replace primary (f, r:np, f)
           | ReplaceSecondary Ndx    -- ^ Replace secondary (r:ns)
           | ReplaceAndFailover Ndx  -- ^ Replace secondary, failover (r:np, f)
           | FailoverAndReplace Ndx  -- ^ Failover, replace secondary (f, r:ns)
             deriving (Show)

-- | Formatted solution output for one move (involved nodes and
-- commands.
type MoveJob = ([Ndx], Idx, IMove, [String])

-- | Unknown field in table output.
unknownField :: String
unknownField = "<unknown field>"

-- | A list of command elements.
type JobSet = [MoveJob]

-- | Default max disk usage ratio.
defReservedDiskRatio :: Double
defReservedDiskRatio = 0

-- | Base memory unit.
unitMem :: Int
unitMem = 64

-- | Base disk unit.
unitDsk :: Int
unitDsk = 256

-- | Base vcpus unit.
unitCpu :: Int
unitCpu = 1

-- | Reason for an operation's falure.
data FailMode = FailMem  -- ^ Failed due to not enough RAM
              | FailDisk -- ^ Failed due to not enough disk
              | FailCPU  -- ^ Failed due to not enough CPU capacity
              | FailN1   -- ^ Failed due to not passing N1 checks
              | FailTags -- ^ Failed due to tag exclusion
                deriving (Eq, Enum, Bounded, Show)

-- | List with failure statistics.
type FailStats = [(FailMode, Int)]

-- | Either-like data-type customized for our failure modes.
--
-- The failure values for this monad track the specific allocation
-- failures, so this is not a general error-monad (compare with the
-- 'Result' data type). One downside is that this type cannot encode a
-- generic failure mode, hence our way to build a FailMode from string
-- will instead raise an exception.
type OpResult = GenericResult FailMode

-- | 'FromString' instance for 'FailMode' designed to catch unintended
-- use as a general monad.
instance FromString FailMode where
  mkFromString v = error $ "Programming error: OpResult used as generic monad"
                           ++ v

-- | Conversion from 'OpResult' to 'Result'.
opToResult :: OpResult a -> Result a
opToResult (Bad f) = Bad $ show f
opToResult (Ok v) = Ok v

-- | A generic class for items that have updateable names and indices.
class Element a where
  -- | Returns the name of the element
  nameOf  :: a -> String
  -- | Returns all the known names of the element
  allNames :: a -> [String]
  -- | Returns the index of the element
  idxOf   :: a -> Int
  -- | Updates the alias of the element
  setAlias :: a -> String -> a
  -- | Compute the alias by stripping a given suffix (domain) from
  -- the name
  computeAlias :: String -> a -> a
  computeAlias dom e = setAlias e alias
    where alias = take (length name - length dom) name
          name = nameOf e
  -- | Updates the index of the element
  setIdx  :: a -> Int -> a

-- | The iallocator node-evacuate evac_mode type.
$(THH.declareSADT "EvacMode"
       [ ("ChangePrimary",   'C.iallocatorNevacPri)
       , ("ChangeSecondary", 'C.iallocatorNevacSec)
       , ("ChangeAll",       'C.iallocatorNevacAll)
       ])
$(THH.makeJSONInstance ''EvacMode)

-- | The repair modes for the auto-repair tool.
$(THH.declareSADT "AutoRepairType"
       -- Order is important here: from least destructive to most.
       [ ("ArFixStorage", 'C.autoRepairFixStorage)
       , ("ArMigrate",    'C.autoRepairMigrate)
       , ("ArFailover",   'C.autoRepairFailover)
       , ("ArReinstall",  'C.autoRepairReinstall)
       ])

-- | The possible auto-repair results.
$(THH.declareSADT "AutoRepairResult"
       [ ("ArSuccess", 'C.autoRepairSuccess)
       , ("ArFailure", 'C.autoRepairFailure)
       , ("ArEnoperm", 'C.autoRepairEnoperm)
       ])

-- | The possible auto-repair policy for a given instance.
data AutoRepairPolicy
  = ArEnabled AutoRepairType          -- ^ Auto-repair explicitly enabled
  | ArSuspended AutoRepairSuspendTime -- ^ Suspended temporarily, or forever
  | ArNotEnabled                      -- ^ Auto-repair not explicitly enabled
  deriving (Eq, Show)

-- | The suspend timeout for 'ArSuspended'.
data AutoRepairSuspendTime = Forever         -- ^ Permanently suspended
                           | Until ClockTime -- ^ Suspended up to a certain time
                           deriving (Eq, Show)

-- | The possible auto-repair states for any given instance.
data AutoRepairStatus
  = ArHealthy                      -- ^ No problems detected with the instance
  | ArNeedsRepair AutoRepairData   -- ^ Instance has problems, no action taken
  | ArPendingRepair AutoRepairData -- ^ Repair jobs ongoing for the instance
  | ArFailedRepair AutoRepairData  -- ^ Some repair jobs for the instance failed

-- | The data accompanying a repair operation (future, pending, or failed).
data AutoRepairData = AutoRepairData { arType :: AutoRepairType
                                     , arUuid :: String
                                     , arTime :: ClockTime
                                     , arJobs :: [JobId]
                                     , arResult :: Maybe AutoRepairResult
                                     }
