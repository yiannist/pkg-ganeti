{-# LANGUAGE TemplateHaskell #-}

{-| Some common Ganeti types.

This holds types common to both core work, and to htools. Types that
are very core specific (e.g. configuration objects) should go in
'Ganeti.Objects', while types that are specific to htools in-memory
representation should go into 'Ganeti.HTools.Types'.

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

module Ganeti.Types
  ( AllocPolicy(..)
  , allocPolicyFromRaw
  , allocPolicyToRaw
  , InstanceStatus(..)
  , instanceStatusFromRaw
  , instanceStatusToRaw
  , DiskTemplate(..)
  , diskTemplateToRaw
  , diskTemplateFromRaw
  , NonNegative
  , fromNonNegative
  , mkNonNegative
  , Positive
  , fromPositive
  , mkPositive
  , Negative
  , fromNegative
  , mkNegative
  , NonEmpty
  , fromNonEmpty
  , mkNonEmpty
  , NonEmptyString
  , MigrationMode(..)
  , VerifyOptionalChecks(..)
  , DdmSimple(..)
  , DdmFull(..)
  , CVErrorCode(..)
  , cVErrorCodeToRaw
  , Hypervisor(..)
  , hypervisorToRaw
  , OobCommand(..)
  , StorageType(..)
  , storageTypeToRaw
  , NodeEvacMode(..)
  , FileDriver(..)
  , InstCreateMode(..)
  , RebootType(..)
  , ExportMode(..)
  , IAllocatorTestDir(..)
  , IAllocatorMode(..)
  , iAllocatorModeToRaw
  , NICMode(..)
  , nICModeToRaw
  , JobStatus(..)
  , jobStatusToRaw
  , jobStatusFromRaw
  , FinalizedJobStatus(..)
  , finalizedJobStatusToRaw
  , JobId
  , fromJobId
  , makeJobId
  , makeJobIdS
  , RelativeJobId
  , JobIdDep(..)
  , JobDependency(..)
  , OpSubmitPriority(..)
  , opSubmitPriorityToRaw
  , parseSubmitPriority
  , fmtSubmitPriority
  , OpStatus(..)
  , opStatusToRaw
  , opStatusFromRaw
  , ELogType(..)
  , ReasonElem
  , ReasonTrail
  , StorageUnit(..)
  , StorageUnitRaw(..)
  , StorageKey
  , addParamsToStorageUnit
  , diskTemplateToStorageType
  ) where

import Control.Monad (liftM)
import qualified Text.JSON as JSON
import Text.JSON (JSON, readJSON, showJSON)
import Data.Ratio (numerator, denominator)

import qualified Ganeti.Constants as C
import qualified Ganeti.THH as THH
import Ganeti.JSON
import Ganeti.Utils

-- * Generic types

-- | Type that holds a non-negative value.
newtype NonNegative a = NonNegative { fromNonNegative :: a }
  deriving (Show, Eq)

-- | Smart constructor for 'NonNegative'.
mkNonNegative :: (Monad m, Num a, Ord a, Show a) => a -> m (NonNegative a)
mkNonNegative i | i >= 0 = return (NonNegative i)
                | otherwise = fail $ "Invalid value for non-negative type '" ++
                              show i ++ "'"

instance (JSON.JSON a, Num a, Ord a, Show a) => JSON.JSON (NonNegative a) where
  showJSON = JSON.showJSON . fromNonNegative
  readJSON v = JSON.readJSON v >>= mkNonNegative

-- | Type that holds a positive value.
newtype Positive a = Positive { fromPositive :: a }
  deriving (Show, Eq)

-- | Smart constructor for 'Positive'.
mkPositive :: (Monad m, Num a, Ord a, Show a) => a -> m (Positive a)
mkPositive i | i > 0 = return (Positive i)
             | otherwise = fail $ "Invalid value for positive type '" ++
                           show i ++ "'"

instance (JSON.JSON a, Num a, Ord a, Show a) => JSON.JSON (Positive a) where
  showJSON = JSON.showJSON . fromPositive
  readJSON v = JSON.readJSON v >>= mkPositive

-- | Type that holds a negative value.
newtype Negative a = Negative { fromNegative :: a }
  deriving (Show, Eq)

-- | Smart constructor for 'Negative'.
mkNegative :: (Monad m, Num a, Ord a, Show a) => a -> m (Negative a)
mkNegative i | i < 0 = return (Negative i)
             | otherwise = fail $ "Invalid value for negative type '" ++
                           show i ++ "'"

instance (JSON.JSON a, Num a, Ord a, Show a) => JSON.JSON (Negative a) where
  showJSON = JSON.showJSON . fromNegative
  readJSON v = JSON.readJSON v >>= mkNegative

-- | Type that holds a non-null list.
newtype NonEmpty a = NonEmpty { fromNonEmpty :: [a] }
  deriving (Show, Eq)

-- | Smart constructor for 'NonEmpty'.
mkNonEmpty :: (Monad m) => [a] -> m (NonEmpty a)
mkNonEmpty [] = fail "Received empty value for non-empty list"
mkNonEmpty xs = return (NonEmpty xs)

instance (JSON.JSON a) => JSON.JSON (NonEmpty a) where
  showJSON = JSON.showJSON . fromNonEmpty
  readJSON v = JSON.readJSON v >>= mkNonEmpty

-- | A simple type alias for non-empty strings.
type NonEmptyString = NonEmpty Char

-- * Ganeti types

-- | Instance disk template type.
$(THH.declareSADT "DiskTemplate"
       [ ("DTDiskless",   'C.dtDiskless)
       , ("DTFile",       'C.dtFile)
       , ("DTSharedFile", 'C.dtSharedFile)
       , ("DTPlain",      'C.dtPlain)
       , ("DTBlock",      'C.dtBlock)
       , ("DTDrbd8",      'C.dtDrbd8)
       , ("DTRbd",        'C.dtRbd)
       , ("DTExt",        'C.dtExt)
       ])
$(THH.makeJSONInstance ''DiskTemplate)

instance HasStringRepr DiskTemplate where
  fromStringRepr = diskTemplateFromRaw
  toStringRepr = diskTemplateToRaw

-- | The Group allocation policy type.
--
-- Note that the order of constructors is important as the automatic
-- Ord instance will order them in the order they are defined, so when
-- changing this data type be careful about the interaction with the
-- desired sorting order.
$(THH.declareSADT "AllocPolicy"
       [ ("AllocPreferred",   'C.allocPolicyPreferred)
       , ("AllocLastResort",  'C.allocPolicyLastResort)
       , ("AllocUnallocable", 'C.allocPolicyUnallocable)
       ])
$(THH.makeJSONInstance ''AllocPolicy)

-- | The Instance real state type. FIXME: this could be improved to
-- just wrap a /NormalState AdminStatus | ErrorState ErrorCondition/.
$(THH.declareSADT "InstanceStatus"
       [ ("StatusDown",    'C.inststAdmindown)
       , ("StatusOffline", 'C.inststAdminoffline)
       , ("ErrorDown",     'C.inststErrordown)
       , ("ErrorUp",       'C.inststErrorup)
       , ("NodeDown",      'C.inststNodedown)
       , ("NodeOffline",   'C.inststNodeoffline)
       , ("Running",       'C.inststRunning)
       , ("WrongNode",     'C.inststWrongnode)
       ])
$(THH.makeJSONInstance ''InstanceStatus)

-- | Migration mode.
$(THH.declareSADT "MigrationMode"
     [ ("MigrationLive",    'C.htMigrationLive)
     , ("MigrationNonLive", 'C.htMigrationNonlive)
     ])
$(THH.makeJSONInstance ''MigrationMode)

-- | Verify optional checks.
$(THH.declareSADT "VerifyOptionalChecks"
     [ ("VerifyNPlusOneMem", 'C.verifyNplusoneMem)
     ])
$(THH.makeJSONInstance ''VerifyOptionalChecks)

-- | Cluster verify error codes.
$(THH.declareSADT "CVErrorCode"
  [ ("CvECLUSTERCFG",                  'C.cvEclustercfgCode)
  , ("CvECLUSTERCERT",                 'C.cvEclustercertCode)
  , ("CvECLUSTERFILECHECK",            'C.cvEclusterfilecheckCode)
  , ("CvECLUSTERDANGLINGNODES",        'C.cvEclusterdanglingnodesCode)
  , ("CvECLUSTERDANGLINGINST",         'C.cvEclusterdanglinginstCode)
  , ("CvEINSTANCEBADNODE",             'C.cvEinstancebadnodeCode)
  , ("CvEINSTANCEDOWN",                'C.cvEinstancedownCode)
  , ("CvEINSTANCELAYOUT",              'C.cvEinstancelayoutCode)
  , ("CvEINSTANCEMISSINGDISK",         'C.cvEinstancemissingdiskCode)
  , ("CvEINSTANCEFAULTYDISK",          'C.cvEinstancefaultydiskCode)
  , ("CvEINSTANCEWRONGNODE",           'C.cvEinstancewrongnodeCode)
  , ("CvEINSTANCESPLITGROUPS",         'C.cvEinstancesplitgroupsCode)
  , ("CvEINSTANCEPOLICY",              'C.cvEinstancepolicyCode)
  , ("CvENODEDRBD",                    'C.cvEnodedrbdCode)
  , ("CvENODEDRBDHELPER",              'C.cvEnodedrbdhelperCode)
  , ("CvENODEFILECHECK",               'C.cvEnodefilecheckCode)
  , ("CvENODEHOOKS",                   'C.cvEnodehooksCode)
  , ("CvENODEHV",                      'C.cvEnodehvCode)
  , ("CvENODELVM",                     'C.cvEnodelvmCode)
  , ("CvENODEN1",                      'C.cvEnoden1Code)
  , ("CvENODENET",                     'C.cvEnodenetCode)
  , ("CvENODEOS",                      'C.cvEnodeosCode)
  , ("CvENODEORPHANINSTANCE",          'C.cvEnodeorphaninstanceCode)
  , ("CvENODEORPHANLV",                'C.cvEnodeorphanlvCode)
  , ("CvENODERPC",                     'C.cvEnoderpcCode)
  , ("CvENODESSH",                     'C.cvEnodesshCode)
  , ("CvENODEVERSION",                 'C.cvEnodeversionCode)
  , ("CvENODESETUP",                   'C.cvEnodesetupCode)
  , ("CvENODETIME",                    'C.cvEnodetimeCode)
  , ("CvENODEOOBPATH",                 'C.cvEnodeoobpathCode)
  , ("CvENODEUSERSCRIPTS",             'C.cvEnodeuserscriptsCode)
  , ("CvENODEFILESTORAGEPATHS",        'C.cvEnodefilestoragepathsCode)
  , ("CvENODEFILESTORAGEPATHUNUSABLE", 'C.cvEnodefilestoragepathunusableCode)
  , ("CvENODESHAREDFILESTORAGEPATHUNUSABLE",
     'C.cvEnodesharedfilestoragepathunusableCode)
  ])
$(THH.makeJSONInstance ''CVErrorCode)

-- | Dynamic device modification, just add\/remove version.
$(THH.declareSADT "DdmSimple"
     [ ("DdmSimpleAdd",    'C.ddmAdd)
     , ("DdmSimpleRemove", 'C.ddmRemove)
     ])
$(THH.makeJSONInstance ''DdmSimple)

-- | Dynamic device modification, all operations version.
$(THH.declareSADT "DdmFull"
     [ ("DdmFullAdd",    'C.ddmAdd)
     , ("DdmFullRemove", 'C.ddmRemove)
     , ("DdmFullModify", 'C.ddmModify)
     ])
$(THH.makeJSONInstance ''DdmFull)

-- | Hypervisor type definitions.
$(THH.declareSADT "Hypervisor"
  [ ( "Kvm",    'C.htKvm )
  , ( "XenPvm", 'C.htXenPvm )
  , ( "Chroot", 'C.htChroot )
  , ( "XenHvm", 'C.htXenHvm )
  , ( "Lxc",    'C.htLxc )
  , ( "Fake",   'C.htFake )
  ])
$(THH.makeJSONInstance ''Hypervisor)

-- | Oob command type.
$(THH.declareSADT "OobCommand"
  [ ("OobHealth",      'C.oobHealth)
  , ("OobPowerCycle",  'C.oobPowerCycle)
  , ("OobPowerOff",    'C.oobPowerOff)
  , ("OobPowerOn",     'C.oobPowerOn)
  , ("OobPowerStatus", 'C.oobPowerStatus)
  ])
$(THH.makeJSONInstance ''OobCommand)

-- | Storage type.
$(THH.declareSADT "StorageType"
  [ ("StorageFile", 'C.stFile)
  , ("StorageLvmPv", 'C.stLvmPv)
  , ("StorageLvmVg", 'C.stLvmVg)
  , ("StorageDiskless", 'C.stDiskless)
  , ("StorageBlock", 'C.stBlock)
  , ("StorageRados", 'C.stRados)
  , ("StorageExt", 'C.stExt)
  ])
$(THH.makeJSONInstance ''StorageType)

-- | Storage keys are identifiers for storage units. Their content varies
-- depending on the storage type, for example a storage key for LVM storage
-- is the volume group name.
type StorageKey = String

-- | Storage parameters
type SPExclusiveStorage = Bool

-- | Storage units without storage-type-specific parameters
data StorageUnitRaw = SURaw StorageType StorageKey

-- | Full storage unit with storage-type-specific parameters
data StorageUnit = SUFile StorageKey
                 | SULvmPv StorageKey SPExclusiveStorage
                 | SULvmVg StorageKey SPExclusiveStorage
                 | SUDiskless StorageKey
                 | SUBlock StorageKey
                 | SURados StorageKey
                 | SUExt StorageKey
                 deriving (Eq)

instance Show StorageUnit where
  show (SUFile key) = showSUSimple StorageFile key
  show (SULvmPv key es) = showSULvm StorageLvmPv key es
  show (SULvmVg key es) = showSULvm StorageLvmVg key es
  show (SUDiskless key) = showSUSimple StorageDiskless key
  show (SUBlock key) = showSUSimple StorageBlock key
  show (SURados key) = showSUSimple StorageRados key
  show (SUExt key) = showSUSimple StorageExt key

instance JSON StorageUnit where
  showJSON (SUFile key) = showJSON (StorageFile, key, []::[String])
  showJSON (SULvmPv key es) = showJSON (StorageLvmPv, key, [es])
  showJSON (SULvmVg key es) = showJSON (StorageLvmVg, key, [es])
  showJSON (SUDiskless key) = showJSON (StorageDiskless, key, []::[String])
  showJSON (SUBlock key) = showJSON (StorageBlock, key, []::[String])
  showJSON (SURados key) = showJSON (StorageRados, key, []::[String])
  showJSON (SUExt key) = showJSON (StorageExt, key, []::[String])
-- FIXME: add readJSON implementation
  readJSON = fail "Not implemented"

-- | Composes a string representation of storage types without
-- storage parameters
showSUSimple :: StorageType -> StorageKey -> String
showSUSimple st sk = show (storageTypeToRaw st, sk, []::[String])

-- | Composes a string representation of the LVM storage types
showSULvm :: StorageType -> StorageKey -> SPExclusiveStorage -> String
showSULvm st sk es = show (storageTypeToRaw st, sk, [es])

-- | Mapping fo disk templates to storage type
-- FIXME: This is semantically the same as the constant
-- C.diskTemplatesStorageType, remove this when python constants
-- are generated from haskell constants
diskTemplateToStorageType :: DiskTemplate -> StorageType
diskTemplateToStorageType DTExt = StorageExt
diskTemplateToStorageType DTFile = StorageFile
diskTemplateToStorageType DTSharedFile = StorageFile
diskTemplateToStorageType DTDrbd8 = StorageLvmVg
diskTemplateToStorageType DTPlain = StorageLvmVg
diskTemplateToStorageType DTRbd = StorageRados
diskTemplateToStorageType DTDiskless = StorageDiskless
diskTemplateToStorageType DTBlock = StorageBlock

-- | Equips a raw storage unit with its parameters
addParamsToStorageUnit :: SPExclusiveStorage -> StorageUnitRaw -> StorageUnit
addParamsToStorageUnit _ (SURaw StorageBlock key) = SUBlock key
addParamsToStorageUnit _ (SURaw StorageDiskless key) = SUDiskless key
addParamsToStorageUnit _ (SURaw StorageExt key) = SUExt key
addParamsToStorageUnit _ (SURaw StorageFile key) = SUFile key
addParamsToStorageUnit es (SURaw StorageLvmPv key) = SULvmPv key es
addParamsToStorageUnit es (SURaw StorageLvmVg key) = SULvmVg key es
addParamsToStorageUnit _ (SURaw StorageRados key) = SURados key

-- | Node evac modes.
$(THH.declareSADT "NodeEvacMode"
  [ ("NEvacPrimary",   'C.iallocatorNevacPri)
  , ("NEvacSecondary", 'C.iallocatorNevacSec)
  , ("NEvacAll",       'C.iallocatorNevacAll)
  ])
$(THH.makeJSONInstance ''NodeEvacMode)

-- | The file driver type.
$(THH.declareSADT "FileDriver"
  [ ("FileLoop",   'C.fdLoop)
  , ("FileBlktap", 'C.fdBlktap)
  ])
$(THH.makeJSONInstance ''FileDriver)

-- | The instance create mode.
$(THH.declareSADT "InstCreateMode"
  [ ("InstCreate",       'C.instanceCreate)
  , ("InstImport",       'C.instanceImport)
  , ("InstRemoteImport", 'C.instanceRemoteImport)
  ])
$(THH.makeJSONInstance ''InstCreateMode)

-- | Reboot type.
$(THH.declareSADT "RebootType"
  [ ("RebootSoft", 'C.instanceRebootSoft)
  , ("RebootHard", 'C.instanceRebootHard)
  , ("RebootFull", 'C.instanceRebootFull)
  ])
$(THH.makeJSONInstance ''RebootType)

-- | Export modes.
$(THH.declareSADT "ExportMode"
  [ ("ExportModeLocal",  'C.exportModeLocal)
  , ("ExportModeRemove", 'C.exportModeRemote)
  ])
$(THH.makeJSONInstance ''ExportMode)

-- | IAllocator run types (OpTestIAllocator).
$(THH.declareSADT "IAllocatorTestDir"
  [ ("IAllocatorDirIn",  'C.iallocatorDirIn)
  , ("IAllocatorDirOut", 'C.iallocatorDirOut)
  ])
$(THH.makeJSONInstance ''IAllocatorTestDir)

-- | IAllocator mode. FIXME: use this in "HTools.Backend.IAlloc".
$(THH.declareSADT "IAllocatorMode"
  [ ("IAllocatorAlloc",       'C.iallocatorModeAlloc)
  , ("IAllocatorMultiAlloc",  'C.iallocatorModeMultiAlloc)
  , ("IAllocatorReloc",       'C.iallocatorModeReloc)
  , ("IAllocatorNodeEvac",    'C.iallocatorModeNodeEvac)
  , ("IAllocatorChangeGroup", 'C.iallocatorModeChgGroup)
  ])
$(THH.makeJSONInstance ''IAllocatorMode)

-- | Network mode.
$(THH.declareSADT "NICMode"
  [ ("NMBridged", 'C.nicModeBridged)
  , ("NMRouted",  'C.nicModeRouted)
  , ("NMOvs",     'C.nicModeOvs)
  ])
$(THH.makeJSONInstance ''NICMode)

-- | The JobStatus data type. Note that this is ordered especially
-- such that greater\/lesser comparison on values of this type makes
-- sense.
$(THH.declareSADT "JobStatus"
       [ ("JOB_STATUS_QUEUED",    'C.jobStatusQueued)
       , ("JOB_STATUS_WAITING",   'C.jobStatusWaiting)
       , ("JOB_STATUS_CANCELING", 'C.jobStatusCanceling)
       , ("JOB_STATUS_RUNNING",   'C.jobStatusRunning)
       , ("JOB_STATUS_CANCELED",  'C.jobStatusCanceled)
       , ("JOB_STATUS_SUCCESS",   'C.jobStatusSuccess)
       , ("JOB_STATUS_ERROR",     'C.jobStatusError)
       ])
$(THH.makeJSONInstance ''JobStatus)

-- | Finalized job status.
$(THH.declareSADT "FinalizedJobStatus"
  [ ("JobStatusCanceled",   'C.jobStatusCanceled)
  , ("JobStatusSuccessful", 'C.jobStatusSuccess)
  , ("JobStatusFailed",     'C.jobStatusError)
  ])
$(THH.makeJSONInstance ''FinalizedJobStatus)

-- | The Ganeti job type.
newtype JobId = JobId { fromJobId :: Int }
  deriving (Show, Eq)

-- | Builds a job ID.
makeJobId :: (Monad m) => Int -> m JobId
makeJobId i | i >= 0 = return $ JobId i
            | otherwise = fail $ "Invalid value for job ID ' " ++ show i ++ "'"

-- | Builds a job ID from a string.
makeJobIdS :: (Monad m) => String -> m JobId
makeJobIdS s = tryRead "parsing job id" s >>= makeJobId

-- | Parses a job ID.
parseJobId :: (Monad m) => JSON.JSValue -> m JobId
parseJobId (JSON.JSString x) = makeJobIdS $ JSON.fromJSString x
parseJobId (JSON.JSRational _ x) =
  if denominator x /= 1
    then fail $ "Got fractional job ID from master daemon?! Value:" ++ show x
    -- FIXME: potential integer overflow here on 32-bit platforms
    else makeJobId . fromIntegral . numerator $ x
parseJobId x = fail $ "Wrong type/value for job id: " ++ show x

instance JSON.JSON JobId where
  showJSON = JSON.showJSON . fromJobId
  readJSON = parseJobId

-- | Relative job ID type alias.
type RelativeJobId = Negative Int

-- | Job ID dependency.
data JobIdDep = JobDepRelative RelativeJobId
              | JobDepAbsolute JobId
                deriving (Show, Eq)

instance JSON.JSON JobIdDep where
  showJSON (JobDepRelative i) = showJSON i
  showJSON (JobDepAbsolute i) = showJSON i
  readJSON v =
    case JSON.readJSON v::JSON.Result (Negative Int) of
      -- first try relative dependency, usually most common
      JSON.Ok r -> return $ JobDepRelative r
      JSON.Error _ -> liftM JobDepAbsolute (parseJobId v)

-- | Job Dependency type.
data JobDependency = JobDependency JobIdDep [FinalizedJobStatus]
                     deriving (Show, Eq)

instance JSON JobDependency where
  showJSON (JobDependency dep status) = showJSON (dep, status)
  readJSON = liftM (uncurry JobDependency) . readJSON

-- | Valid opcode priorities for submit.
$(THH.declareIADT "OpSubmitPriority"
  [ ("OpPrioLow",    'C.opPrioLow)
  , ("OpPrioNormal", 'C.opPrioNormal)
  , ("OpPrioHigh",   'C.opPrioHigh)
  ])
$(THH.makeJSONInstance ''OpSubmitPriority)

-- | Parse submit priorities from a string.
parseSubmitPriority :: (Monad m) => String -> m OpSubmitPriority
parseSubmitPriority "low"    = return OpPrioLow
parseSubmitPriority "normal" = return OpPrioNormal
parseSubmitPriority "high"   = return OpPrioHigh
parseSubmitPriority str      = fail $ "Unknown priority '" ++ str ++ "'"

-- | Format a submit priority as string.
fmtSubmitPriority :: OpSubmitPriority -> String
fmtSubmitPriority OpPrioLow    = "low"
fmtSubmitPriority OpPrioNormal = "normal"
fmtSubmitPriority OpPrioHigh   = "high"

-- | Our ADT for the OpCode status at runtime (while in a job).
$(THH.declareSADT "OpStatus"
  [ ("OP_STATUS_QUEUED",    'C.opStatusQueued)
  , ("OP_STATUS_WAITING",   'C.opStatusWaiting)
  , ("OP_STATUS_CANCELING", 'C.opStatusCanceling)
  , ("OP_STATUS_RUNNING",   'C.opStatusRunning)
  , ("OP_STATUS_CANCELED",  'C.opStatusCanceled)
  , ("OP_STATUS_SUCCESS",   'C.opStatusSuccess)
  , ("OP_STATUS_ERROR",     'C.opStatusError)
  ])
$(THH.makeJSONInstance ''OpStatus)

-- | Type for the job message type.
$(THH.declareSADT "ELogType"
  [ ("ELogMessage",      'C.elogMessage)
  , ("ELogRemoteImport", 'C.elogRemoteImport)
  , ("ELogJqueueTest",   'C.elogJqueueTest)
  ])
$(THH.makeJSONInstance ''ELogType)

-- | Type of one element of a reason trail.
type ReasonElem = (String, String, Integer)

-- | Type representing a reason trail.
type ReasonTrail = [ReasonElem]
