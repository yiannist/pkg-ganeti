{-# LANGUAGE TemplateHaskell #-}

{-| Implementation of the Ganeti Ssconf interface.

-}

{-

Copyright (C) 2012 Google Inc.

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

module Ganeti.Ssconf
  ( SSKey(..)
  , sSKeyToRaw
  , sSKeyFromRaw
  , getPrimaryIPFamily
  , keyToFilename
  , sSFilePrefix
  ) where

import Ganeti.THH

import Control.Exception
import Control.Monad (liftM)
import Data.Char (isSpace)
import Data.Maybe (fromMaybe)
import Prelude hiding (catch)
import qualified Network.Socket as Socket
import System.FilePath ((</>))
import System.IO.Error (isDoesNotExistError)

import qualified Ganeti.Constants as C
import Ganeti.BasicTypes
import Ganeti.HTools.Utils

-- | Maximum ssconf file size we support.
maxFileSize :: Int
maxFileSize = 131072

-- | ssconf file prefix, re-exported from Constants.
sSFilePrefix :: FilePath
sSFilePrefix = C.ssconfFileprefix

$(declareSADT "SSKey"
  [ ("SSClusterName",          'C.ssClusterName)
  , ("SSClusterTags",          'C.ssClusterTags)
  , ("SSFileStorageDir",       'C.ssFileStorageDir)
  , ("SSSharedFileStorageDir", 'C.ssSharedFileStorageDir)
  , ("SSMasterCandidates",     'C.ssMasterCandidates)
  , ("SSMasterCandidatesIps",  'C.ssMasterCandidatesIps)
  , ("SSMasterIp",             'C.ssMasterIp)
  , ("SSMasterNetdev",         'C.ssMasterNetdev)
  , ("SSMasterNetmask",        'C.ssMasterNetmask)
  , ("SSMasterNode",           'C.ssMasterNode)
  , ("SSNodeList",             'C.ssNodeList)
  , ("SSNodePrimaryIps",       'C.ssNodePrimaryIps)
  , ("SSNodeSecondaryIps",     'C.ssNodeSecondaryIps)
  , ("SSOfflineNodes",         'C.ssOfflineNodes)
  , ("SSOnlineNodes",          'C.ssOnlineNodes)
  , ("SSPrimaryIpFamily",      'C.ssPrimaryIpFamily)
  , ("SSInstanceList",         'C.ssInstanceList)
  , ("SSReleaseVersion",       'C.ssReleaseVersion)
  , ("SSHypervisorList",       'C.ssHypervisorList)
  , ("SSMaintainNodeHealth",   'C.ssMaintainNodeHealth)
  , ("SSUidPool",              'C.ssUidPool)
  , ("SSNodegroups",           'C.ssNodegroups)
  ])

-- | Convert a ssconf key into a (full) file path.
keyToFilename :: Maybe FilePath     -- ^ Optional config path override
              -> SSKey              -- ^ ssconf key
              -> FilePath
keyToFilename optpath key = fromMaybe C.dataDir optpath </>
                            sSFilePrefix ++ sSKeyToRaw key

-- | Runs an IO action while transforming any error into 'Bad'
-- values. It also accepts an optional value to use in case the error
-- is just does not exist.
catchIOErrors :: Maybe a         -- ^ Optional default
              -> IO a            -- ^ Action to run
              -> IO (Result a)
catchIOErrors def action =
  catch (do
          result <- action
          return (Ok result)
        ) (\err -> let bad_result = Bad (show err)
                   in return $ if isDoesNotExistError err
                                 then maybe bad_result Ok def
                                 else bad_result)

-- | Read an ssconf file.
readSSConfFile :: Maybe FilePath            -- ^ Optional config path override
               -> Maybe String              -- ^ Optional default value
               -> SSKey                     -- ^ Desired ssconf key
               -> IO (Result String)
readSSConfFile optpath def key = do
  result <- catchIOErrors def . readFile . keyToFilename optpath $ key
  return (liftM (take maxFileSize) result)

-- | Strip space characthers (including newline). As this is
-- expensive, should only be run on small strings.
rstripSpace :: String -> String
rstripSpace = reverse . dropWhile isSpace . reverse

-- | Parses a string containing an IP family
parseIPFamily :: Int -> Result Socket.Family
parseIPFamily fam | fam == C.ip4Family = Ok Socket.AF_INET
                  | fam == C.ip6Family = Ok Socket.AF_INET6
                  | otherwise = Bad $ "Unknown af_family value: " ++ show fam

-- | Read the primary IP family.
getPrimaryIPFamily :: Maybe FilePath -> IO (Result Socket.Family)
getPrimaryIPFamily optpath = do
  result <- readSSConfFile optpath (Just (show C.ip4Family)) SSPrimaryIpFamily
  return (result >>= return . rstripSpace >>=
          tryRead "Parsing af_family" >>= parseIPFamily)
