{-| Implementation of the runtime configuration details.

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

module Ganeti.Runtime
  ( GanetiDaemon(..)
  , MiscGroup(..)
  , GanetiGroup(..)
  , RuntimeEnts
  , daemonName
  , daemonOnlyOnMaster
  , daemonLogBase
  , daemonUser
  , daemonGroup
  , ExtraLogReason(..)
  , daemonLogFile
  , daemonsExtraLogbase
  , daemonsExtraLogFile
  , daemonPidFile
  , getEnts
  , verifyDaemonUser
  ) where

import Control.Exception
import Control.Monad
import qualified Data.Map as M
import System.Exit
import System.FilePath
import System.IO
import System.IO.Error
import System.Posix.Types
import System.Posix.User
import Text.Printf

import qualified Ganeti.ConstantUtils as ConstantUtils
import qualified Ganeti.Path as Path
import Ganeti.BasicTypes

import AutoConf

data GanetiDaemon = GanetiMasterd
                  | GanetiNoded
                  | GanetiRapi
                  | GanetiConfd
                  | GanetiLuxid
                  | GanetiMond
                    deriving (Show, Enum, Bounded, Eq, Ord)

data MiscGroup = DaemonsGroup
               | AdminGroup
                 deriving (Show, Enum, Bounded, Eq, Ord)

data GanetiGroup = DaemonGroup GanetiDaemon
                 | ExtraGroup MiscGroup
                   deriving (Show, Eq, Ord)

type RuntimeEnts = (M.Map GanetiDaemon UserID, M.Map GanetiGroup GroupID)

-- | Returns the daemon name for a given daemon.
daemonName :: GanetiDaemon -> String
daemonName GanetiMasterd = "ganeti-masterd"
daemonName GanetiNoded   = "ganeti-noded"
daemonName GanetiRapi    = "ganeti-rapi"
daemonName GanetiConfd   = "ganeti-confd"
daemonName GanetiLuxid   = "ganeti-luxid"
daemonName GanetiMond    = "ganeti-mond"

-- | Returns whether the daemon only runs on the master node.
daemonOnlyOnMaster :: GanetiDaemon -> Bool
daemonOnlyOnMaster GanetiMasterd = True
daemonOnlyOnMaster GanetiNoded   = False
daemonOnlyOnMaster GanetiRapi    = False
daemonOnlyOnMaster GanetiConfd   = False
daemonOnlyOnMaster GanetiLuxid   = True
daemonOnlyOnMaster GanetiMond    = False

-- | Returns the log file base for a daemon.
daemonLogBase :: GanetiDaemon -> String
daemonLogBase GanetiMasterd = "master-daemon"
daemonLogBase GanetiNoded   = "node-daemon"
daemonLogBase GanetiRapi    = "rapi-daemon"
daemonLogBase GanetiConfd   = "conf-daemon"
daemonLogBase GanetiLuxid   = "luxi-daemon"
daemonLogBase GanetiMond    = "monitoring-daemon"

-- | Returns the configured user name for a daemon.
daemonUser :: GanetiDaemon -> String
daemonUser GanetiMasterd = AutoConf.masterdUser
daemonUser GanetiNoded   = AutoConf.nodedUser
daemonUser GanetiRapi    = AutoConf.rapiUser
daemonUser GanetiConfd   = AutoConf.confdUser
daemonUser GanetiLuxid   = AutoConf.luxidUser
daemonUser GanetiMond    = AutoConf.mondUser

-- | Returns the configured group for a daemon.
daemonGroup :: GanetiGroup -> String
daemonGroup (DaemonGroup GanetiMasterd) = AutoConf.masterdGroup
daemonGroup (DaemonGroup GanetiNoded)   = AutoConf.nodedGroup
daemonGroup (DaemonGroup GanetiRapi)    = AutoConf.rapiGroup
daemonGroup (DaemonGroup GanetiConfd)   = AutoConf.confdGroup
daemonGroup (DaemonGroup GanetiLuxid)   = AutoConf.luxidGroup
daemonGroup (DaemonGroup GanetiMond)    = AutoConf.mondGroup
daemonGroup (ExtraGroup  DaemonsGroup)  = AutoConf.daemonsGroup
daemonGroup (ExtraGroup  AdminGroup)    = AutoConf.adminGroup

data ExtraLogReason = AccessLog | ErrorLog

-- | Some daemons might require more than one logfile.  Specifically,
-- right now only the Haskell http library "snap", used by the
-- monitoring daemon, requires multiple log files.
daemonsExtraLogbase :: GanetiDaemon -> ExtraLogReason -> String
daemonsExtraLogbase daemon AccessLog = daemonLogBase daemon ++ "-access"
daemonsExtraLogbase daemon ErrorLog = daemonLogBase daemon ++ "-error"

-- | Returns the log file for a daemon.
daemonLogFile :: GanetiDaemon -> IO FilePath
daemonLogFile daemon = do
  logDir <- Path.logDir
  return $ logDir </> daemonLogBase daemon <.> "log"

-- | Returns the extra log files for a daemon.
daemonsExtraLogFile :: GanetiDaemon -> ExtraLogReason -> IO FilePath
daemonsExtraLogFile daemon logreason = do
  logDir <- Path.logDir
  return $ logDir </> daemonsExtraLogbase daemon logreason <.> "log"

-- | Returns the pid file name for a daemon.
daemonPidFile :: GanetiDaemon -> IO FilePath
daemonPidFile daemon = do
  runDir <- Path.runDir
  return $ runDir </> daemonName daemon <.> "pid"

-- | All groups list. A bit hacking, as we can't enforce it's complete
-- at compile time.
allGroups :: [GanetiGroup]
allGroups = map DaemonGroup [minBound..maxBound] ++
            map ExtraGroup  [minBound..maxBound]

ignoreDoesNotExistErrors :: IO a -> IO (Result a)
ignoreDoesNotExistErrors value = do
  result <- tryJust (\e -> if isDoesNotExistError e
                             then Just (show e)
                             else Nothing) value
  return $ eitherToResult result

-- | Computes the group/user maps.
getEnts :: IO (Result RuntimeEnts)
getEnts = do
  users <- mapM (\daemon -> do
                   entry <- ignoreDoesNotExistErrors .
                            getUserEntryForName .
                            daemonUser $ daemon
                   return (entry >>= \e -> return (daemon, userID e))
                ) [minBound..maxBound]
  groups <- mapM (\group -> do
                    entry <- ignoreDoesNotExistErrors .
                             getGroupEntryForName .
                             daemonGroup $ group
                    return (entry >>= \e -> return (group, groupID e))
                 ) allGroups
  return $ do -- 'Result' monad
    users'  <- sequence users
    groups' <- sequence groups
    let usermap = M.fromList users'
        groupmap = M.fromList groups'
    return (usermap, groupmap)


-- | Checks whether a daemon runs as the right user.
verifyDaemonUser :: GanetiDaemon -> RuntimeEnts -> IO ()
verifyDaemonUser daemon ents = do
  myuid <- getEffectiveUserID
  -- note: we use directly ! as lookup failues shouldn't happen, due
  -- to the above map construction
  checkUidMatch (daemonName daemon) ((M.!) (fst ents) daemon) myuid

-- | Check that two UIDs are matching or otherwise exit.
checkUidMatch :: String -> UserID -> UserID -> IO ()
checkUidMatch name expected actual =
  when (expected /= actual) $ do
    hPrintf stderr "%s started using wrong user ID (%d), \
                   \expected %d\n" name
              (fromIntegral actual::Int)
              (fromIntegral expected::Int) :: IO ()
    exitWith $ ExitFailure ConstantUtils.exitFailure
