{-| Implementation of command-line functions.

This module holds the common command-line related functions for the
binaries, separated into this module since "Ganeti.HTools.Utils" is
used in many other places and this is more IO oriented.

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

module Ganeti.HTools.CLI
    ( Options(..)
    , OptType
    , parseOpts
    , shTemplate
    , defaultLuxiSocket
    , maybePrintNodes
    , maybePrintInsts
    , maybeShowWarnings
    -- * The options
    , oDataFile
    , oDiskMoves
    , oDiskTemplate
    , oDynuFile
    , oEvacMode
    , oExInst
    , oExTags
    , oExecJobs
    , oGroup
    , oIDisk
    , oIMem
    , oIVcpus
    , oInstMoves
    , oLuxiSocket
    , oMachineReadable
    , oMaxCpu
    , oMaxSolLength
    , oMinDisk
    , oMinGain
    , oMinGainLim
    , oMinScore
    , oNoHeaders
    , oNodeSim
    , oOfflineNode
    , oOneline
    , oOutputDir
    , oPrintCommands
    , oPrintInsts
    , oPrintNodes
    , oQuiet
    , oRapiMaster
    , oReplay
    , oSaveCluster
    , oSelInst
    , oShowHelp
    , oShowVer
    , oTieredSpec
    , oVerbose
    ) where

import Control.Monad
import Data.Maybe (fromMaybe)
import qualified Data.Version
import System.Console.GetOpt
import System.IO
import System.Info
import System
import Text.Printf (printf)

import qualified Ganeti.HTools.Version as Version(version)
import qualified Ganeti.Constants as C
import Ganeti.HTools.Types
import Ganeti.HTools.Utils

-- * Constants

-- | The default value for the luxi socket.
--
-- This is re-exported from the "Ganeti.Constants" module.
defaultLuxiSocket :: FilePath
defaultLuxiSocket = C.masterSocket

-- * Data types

-- | Command line options structure.
data Options = Options
    { optDataFile    :: Maybe FilePath -- ^ Path to the cluster data file
    , optDiskMoves   :: Bool           -- ^ Allow disk moves
    , optInstMoves   :: Bool           -- ^ Allow instance moves
    , optDiskTemplate :: DiskTemplate  -- ^ The requested disk template
    , optDynuFile    :: Maybe FilePath -- ^ Optional file with dynamic use data
    , optEvacMode    :: Bool           -- ^ Enable evacuation mode
    , optExInst      :: [String]       -- ^ Instances to be excluded
    , optExTags      :: Maybe [String] -- ^ Tags to use for exclusion
    , optExecJobs    :: Bool           -- ^ Execute the commands via Luxi
    , optGroup       :: Maybe GroupID  -- ^ The UUID of the group to process
    , optSelInst     :: [String]       -- ^ Instances to be excluded
    , optISpec       :: RSpec          -- ^ Requested instance specs
    , optLuxi        :: Maybe FilePath -- ^ Collect data from Luxi
    , optMachineReadable :: Bool       -- ^ Output machine-readable format
    , optMaster      :: String         -- ^ Collect data from RAPI
    , optMaxLength   :: Int            -- ^ Stop after this many steps
    , optMcpu        :: Double         -- ^ Max cpu ratio for nodes
    , optMdsk        :: Double         -- ^ Max disk usage ratio for nodes
    , optMinGain     :: Score          -- ^ Min gain we aim for in a step
    , optMinGainLim  :: Score          -- ^ Limit below which we apply mingain
    , optMinScore    :: Score          -- ^ The minimum score we aim for
    , optNoHeaders   :: Bool           -- ^ Do not show a header line
    , optNodeSim     :: [String]       -- ^ Cluster simulation mode
    , optOffline     :: [String]       -- ^ Names of offline nodes
    , optOneline     :: Bool           -- ^ Switch output to a single line
    , optOutPath     :: FilePath       -- ^ Path to the output directory
    , optSaveCluster :: Maybe FilePath -- ^ Save cluster state to this file
    , optShowCmds    :: Maybe FilePath -- ^ Whether to show the command list
    , optShowHelp    :: Bool           -- ^ Just show the help
    , optShowInsts   :: Bool           -- ^ Whether to show the instance map
    , optShowNodes   :: Maybe [String] -- ^ Whether to show node status
    , optShowVer     :: Bool           -- ^ Just show the program version
    , optTieredSpec  :: Maybe RSpec    -- ^ Requested specs for tiered mode
    , optReplay      :: Maybe String   -- ^ Unittests: RNG state
    , optVerbose     :: Int            -- ^ Verbosity level
    } deriving Show

-- | Default values for the command line options.
defaultOptions :: Options
defaultOptions  = Options
 { optDataFile    = Nothing
 , optDiskMoves   = True
 , optInstMoves   = True
 , optDiskTemplate = DTDrbd8
 , optDynuFile    = Nothing
 , optEvacMode    = False
 , optExInst      = []
 , optExTags      = Nothing
 , optExecJobs    = False
 , optGroup       = Nothing
 , optSelInst     = []
 , optISpec       = RSpec 1 4096 102400
 , optLuxi        = Nothing
 , optMachineReadable = False
 , optMaster      = ""
 , optMaxLength   = -1
 , optMcpu        = defVcpuRatio
 , optMdsk        = defReservedDiskRatio
 , optMinGain     = 1e-2
 , optMinGainLim  = 1e-1
 , optMinScore    = 1e-9
 , optNoHeaders   = False
 , optNodeSim     = []
 , optOffline     = []
 , optOneline     = False
 , optOutPath     = "."
 , optSaveCluster = Nothing
 , optShowCmds    = Nothing
 , optShowHelp    = False
 , optShowInsts   = False
 , optShowNodes   = Nothing
 , optShowVer     = False
 , optTieredSpec  = Nothing
 , optReplay      = Nothing
 , optVerbose     = 1
 }

-- | Abrreviation for the option type.
type OptType = OptDescr (Options -> Result Options)

-- * Command line options

oDataFile :: OptType
oDataFile = Option "t" ["text-data"]
            (ReqArg (\ f o -> Ok o { optDataFile = Just f }) "FILE")
            "the cluster data FILE"

oDiskMoves :: OptType
oDiskMoves = Option "" ["no-disk-moves"]
             (NoArg (\ opts -> Ok opts { optDiskMoves = False}))
             "disallow disk moves from the list of allowed instance changes,\
             \ thus allowing only the 'cheap' failover/migrate operations"

oDiskTemplate :: OptType
oDiskTemplate = Option "" ["disk-template"]
                (ReqArg (\ t opts -> do
                           dt <- dtFromString t
                           return $ opts { optDiskTemplate = dt }) "TEMPLATE")
                "select the desired disk template"

oSelInst :: OptType
oSelInst = Option "" ["select-instances"]
          (ReqArg (\ f opts -> Ok opts { optSelInst = sepSplit ',' f }) "INSTS")
          "only select given instances for any moves"

oInstMoves :: OptType
oInstMoves = Option "" ["no-instance-moves"]
             (NoArg (\ opts -> Ok opts { optInstMoves = False}))
             "disallow instance (primary node) moves from the list of allowed,\
             \ instance changes, thus allowing only slower, but sometimes\
             \ safer, drbd secondary changes"

oDynuFile :: OptType
oDynuFile = Option "U" ["dynu-file"]
            (ReqArg (\ f opts -> Ok opts { optDynuFile = Just f }) "FILE")
            "Import dynamic utilisation data from the given FILE"

oEvacMode :: OptType
oEvacMode = Option "E" ["evac-mode"]
            (NoArg (\opts -> Ok opts { optEvacMode = True }))
            "enable evacuation mode, where the algorithm only moves \
            \ instances away from offline and drained nodes"

oExInst :: OptType
oExInst = Option "" ["exclude-instances"]
          (ReqArg (\ f opts -> Ok opts { optExInst = sepSplit ',' f }) "INSTS")
          "exclude given instances from any moves"

oExTags :: OptType
oExTags = Option "" ["exclusion-tags"]
            (ReqArg (\ f opts -> Ok opts { optExTags = Just $ sepSplit ',' f })
             "TAG,...") "Enable instance exclusion based on given tag prefix"

oExecJobs :: OptType
oExecJobs = Option "X" ["exec"]
             (NoArg (\ opts -> Ok opts { optExecJobs = True}))
             "execute the suggested moves via Luxi (only available when using\
             \ it for data gathering)"

oGroup :: OptType
oGroup = Option "G" ["group"]
            (ReqArg (\ f o -> Ok o { optGroup = Just f }) "ID")
            "the ID of the group to balance"

oIDisk :: OptType
oIDisk = Option "" ["disk"]
         (ReqArg (\ d opts -> do
                    dsk <- annotateResult "--disk option" (parseUnit d)
                    let ospec = optISpec opts
                        nspec = ospec { rspecDsk = dsk }
                    return $ opts { optISpec = nspec }) "DISK")
         "disk size for instances"

oIMem :: OptType
oIMem = Option "" ["memory"]
        (ReqArg (\ m opts -> do
                   mem <- annotateResult "--memory option" (parseUnit m)
                   let ospec = optISpec opts
                       nspec = ospec { rspecMem = mem }
                   return $ opts { optISpec = nspec }) "MEMORY")
        "memory size for instances"

oIVcpus :: OptType
oIVcpus = Option "" ["vcpus"]
          (ReqArg (\ p opts -> do
                     vcpus <- tryRead "--vcpus option" p
                     let ospec = optISpec opts
                         nspec = ospec { rspecCpu = vcpus }
                     return $ opts { optISpec = nspec }) "NUM")
          "number of virtual cpus for instances"

oLuxiSocket :: OptType
oLuxiSocket = Option "L" ["luxi"]
              (OptArg ((\ f opts -> Ok opts { optLuxi = Just f }) .
                       fromMaybe defaultLuxiSocket) "SOCKET")
              "collect data via Luxi, optionally using the given SOCKET path"

oMachineReadable :: OptType
oMachineReadable = Option "" ["machine-readable"]
          (OptArg (\ f opts -> do
                     flag <- parseYesNo True f
                     return $ opts { optMachineReadable = flag }) "CHOICE")
          "enable machine readable output (pass either 'yes' or 'no' to\
          \ explicitely control the flag, or without an argument defaults to\
          \ yes"

oMaxCpu :: OptType
oMaxCpu = Option "" ["max-cpu"]
          (ReqArg (\ n opts -> Ok opts { optMcpu = read n }) "RATIO")
          "maximum virtual-to-physical cpu ratio for nodes (from 1\
          \ upwards) [64]"

oMaxSolLength :: OptType
oMaxSolLength = Option "l" ["max-length"]
                (ReqArg (\ i opts -> Ok opts { optMaxLength = read i }) "N")
                "cap the solution at this many moves (useful for very\
                \ unbalanced clusters)"

oMinDisk :: OptType
oMinDisk = Option "" ["min-disk"]
           (ReqArg (\ n opts -> Ok opts { optMdsk = read n }) "RATIO")
           "minimum free disk space for nodes (between 0 and 1) [0]"

oMinGain :: OptType
oMinGain = Option "g" ["min-gain"]
            (ReqArg (\ g opts -> Ok opts { optMinGain = read g }) "DELTA")
            "minimum gain to aim for in a balancing step before giving up"

oMinGainLim :: OptType
oMinGainLim = Option "" ["min-gain-limit"]
            (ReqArg (\ g opts -> Ok opts { optMinGainLim = read g }) "SCORE")
            "minimum cluster score for which we start checking the min-gain"

oMinScore :: OptType
oMinScore = Option "e" ["min-score"]
            (ReqArg (\ e opts -> Ok opts { optMinScore = read e }) "EPSILON")
            "mininum score to aim for"

oNoHeaders :: OptType
oNoHeaders = Option "" ["no-headers"]
             (NoArg (\ opts -> Ok opts { optNoHeaders = True }))
             "do not show a header line"

oNodeSim :: OptType
oNodeSim = Option "" ["simulate"]
            (ReqArg (\ f o -> Ok o { optNodeSim = f:optNodeSim o }) "SPEC")
            "simulate an empty cluster, given as 'num_nodes,disk,ram,cpu'"

oOfflineNode :: OptType
oOfflineNode = Option "O" ["offline"]
               (ReqArg (\ n o -> Ok o { optOffline = n:optOffline o }) "NODE")
               "set node as offline"

oOneline :: OptType
oOneline = Option "o" ["oneline"]
           (NoArg (\ opts -> Ok opts { optOneline = True }))
           "print the ganeti command list for reaching the solution"

oOutputDir :: OptType
oOutputDir = Option "d" ["output-dir"]
             (ReqArg (\ d opts -> Ok opts { optOutPath = d }) "PATH")
             "directory in which to write output files"

oPrintCommands :: OptType
oPrintCommands = Option "C" ["print-commands"]
                 (OptArg ((\ f opts -> Ok opts { optShowCmds = Just f }) .
                          fromMaybe "-")
                  "FILE")
                 "print the ganeti command list for reaching the solution,\
                 \ if an argument is passed then write the commands to a\
                 \ file named as such"

oPrintInsts :: OptType
oPrintInsts = Option "" ["print-instances"]
              (NoArg (\ opts -> Ok opts { optShowInsts = True }))
              "print the final instance map"

oPrintNodes :: OptType
oPrintNodes = Option "p" ["print-nodes"]
              (OptArg ((\ f opts ->
                            let (prefix, realf) = case f of
                                  '+':rest -> (["+"], rest)
                                  _ -> ([], f)
                                splitted = prefix ++ sepSplit ',' realf
                            in Ok opts { optShowNodes = Just splitted }) .
                       fromMaybe []) "FIELDS")
              "print the final node list"

oQuiet :: OptType
oQuiet = Option "q" ["quiet"]
         (NoArg (\ opts -> Ok opts { optVerbose = optVerbose opts - 1 }))
         "decrease the verbosity level"

oRapiMaster :: OptType
oRapiMaster = Option "m" ["master"]
              (ReqArg (\ m opts -> Ok opts { optMaster = m }) "ADDRESS")
              "collect data via RAPI at the given ADDRESS"

oSaveCluster :: OptType
oSaveCluster = Option "S" ["save"]
            (ReqArg (\ f opts -> Ok opts { optSaveCluster = Just f }) "FILE")
            "Save cluster state at the end of the processing to FILE"

oShowHelp :: OptType
oShowHelp = Option "h" ["help"]
            (NoArg (\ opts -> Ok opts { optShowHelp = True}))
            "show help"

oShowVer :: OptType
oShowVer = Option "V" ["version"]
           (NoArg (\ opts -> Ok opts { optShowVer = True}))
           "show the version of the program"

oTieredSpec :: OptType
oTieredSpec = Option "" ["tiered-alloc"]
             (ReqArg (\ inp opts -> do
                          let sp = sepSplit ',' inp
                          prs <- mapM (\(fn, val) -> fn val) $
                                 zip [ annotateResult "tiered specs memory" .
                                       parseUnit
                                     , annotateResult "tiered specs disk" .
                                       parseUnit
                                     , tryRead "tiered specs cpus"
                                     ] sp
                          tspec <-
                              case prs of
                                [dsk, ram, cpu] -> return $ RSpec cpu ram dsk
                                _ -> Bad $ "Invalid specification: " ++ inp ++
                                     ", expected disk,ram,cpu"
                          return $ opts { optTieredSpec = Just tspec } )
              "TSPEC")
             "enable tiered specs allocation, given as 'disk,ram,cpu'"

oReplay :: OptType
oReplay = Option "" ["replay"]
          (ReqArg (\ stat opts -> Ok opts { optReplay = Just stat } ) "STATE")
          "Pre-seed the random number generator with STATE"

oVerbose :: OptType
oVerbose = Option "v" ["verbose"]
           (NoArg (\ opts -> Ok opts { optVerbose = optVerbose opts + 1 }))
           "increase the verbosity level"

-- * Functions

-- | Helper for parsing a yes\/no command line flag.
parseYesNo :: Bool         -- ^ Default whalue (when we get a @Nothing@)
           -> Maybe String -- ^ Parameter value
           -> Result Bool  -- ^ Resulting boolean value
parseYesNo v Nothing      = return v
parseYesNo _ (Just "yes") = return True
parseYesNo _ (Just "no")  = return False
parseYesNo _ (Just s)     = fail $ "Invalid choice '" ++ s ++
                            "', pass one of 'yes' or 'no'"

-- | Usage info.
usageHelp :: String -> [OptType] -> String
usageHelp progname =
    usageInfo (printf "%s %s\nUsage: %s [OPTION...]"
               progname Version.version progname)

-- | Command line parser, using the 'Options' structure.
parseOpts :: [String]               -- ^ The command line arguments
          -> String                 -- ^ The program name
          -> [OptType]              -- ^ The supported command line options
          -> IO (Options, [String]) -- ^ The resulting options and leftover
                                    -- arguments
parseOpts argv progname options =
    case getOpt Permute options argv of
      (o, n, []) ->
          do
            let (pr, args) = (foldM (flip id) defaultOptions o, n)
            po <- (case pr of
                     Bad msg -> do
                       hPutStrLn stderr "Error while parsing command\
                                        \line arguments:"
                       hPutStrLn stderr msg
                       exitWith $ ExitFailure 1
                     Ok val -> return val)
            when (optShowHelp po) $ do
              putStr $ usageHelp progname options
              exitWith ExitSuccess
            when (optShowVer po) $ do
              printf "%s %s\ncompiled with %s %s\nrunning on %s %s\n"
                     progname Version.version
                     compilerName (Data.Version.showVersion compilerVersion)
                     os arch :: IO ()
              exitWith ExitSuccess
            return (po, args)
      (_, _, errs) -> do
        hPutStrLn stderr $ "Command line error: "  ++ concat errs
        hPutStrLn stderr $ usageHelp progname options
        exitWith $ ExitFailure 2

-- | A shell script template for autogenerated scripts.
shTemplate :: String
shTemplate =
    printf "#!/bin/sh\n\n\
           \# Auto-generated script for executing cluster rebalancing\n\n\
           \# To stop, touch the file /tmp/stop-htools\n\n\
           \set -e\n\n\
           \check() {\n\
           \  if [ -f /tmp/stop-htools ]; then\n\
           \    echo 'Stop requested, exiting'\n\
           \    exit 0\n\
           \  fi\n\
           \}\n\n"

-- | Optionally print the node list.
maybePrintNodes :: Maybe [String]       -- ^ The field list
                -> String               -- ^ Informational message
                -> ([String] -> String) -- ^ Function to generate the listing
                -> IO ()
maybePrintNodes Nothing _ _ = return ()
maybePrintNodes (Just fields) msg fn = do
  hPutStrLn stderr ""
  hPutStrLn stderr (msg ++ " status:")
  hPutStrLn stderr $ fn fields


-- | Optionally print the instance list.
maybePrintInsts :: Bool   -- ^ Whether to print the instance list
                -> String -- ^ Type of the instance map (e.g. initial)
                -> String -- ^ The instance data
                -> IO ()
maybePrintInsts do_print msg instdata =
  when do_print $ do
    hPutStrLn stderr ""
    hPutStrLn stderr $ msg ++ " instance map:"
    hPutStr stderr instdata

-- | Function to display warning messages from parsing the cluster
-- state.
maybeShowWarnings :: [String] -- ^ The warning messages
                  -> IO ()
maybeShowWarnings fix_msgs =
  unless (null fix_msgs) $ do
    hPutStrLn stderr "Warning: cluster has inconsistent data:"
    hPutStrLn stderr . unlines . map (printf "  - %s") $ fix_msgs
